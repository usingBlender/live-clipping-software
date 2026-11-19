"""Candidate processing: transcribe -> LLM plan -> render, one at a time on a worker thread."""
import json
import queue
import shutil
import threading
import time
from pathlib import Path

from clipper import plan as planmod
from clipper import render as rendermod
from clipper import sfx as sfxmod
from clipper.transcribe import caption_words, transcribe
from common.bus import bus, log


class Worker:
    def __init__(self, store, db, notifier, base_url_fn, token_fn=lambda cid: ""):
        self.store, self.db, self.notifier, self.base_url, self.token = store, db, notifier, base_url_fn, token_fn
        self.q: queue.Queue = queue.Queue()
        self.current = None
        self.processed = 0
        threading.Thread(target=self._run, name="clip-worker", daemon=True).start()
        threading.Thread(target=self._inbox, name="clip-inbox", daemon=True).start()

    @property
    def cfg(self):
        return self.store.cfg

    def submit(self, cand: dict):
        self.db.upsert(id=cand["clip_id"], streamer=cand["streamer"], created=cand.get("created", time.time()),
                       status="queued", score=cand.get("score"), source_path=cand["source_path"],
                       candidate_json=cand)
        self.q.put(cand)
        bus.publish("clip.queued", {"clip_id": cand["clip_id"], "streamer": cand["streamer"],
                                    "score": cand.get("score"), "queue": self.q.qsize()})

    def status(self):
        return {"current": self.current, "queued": self.q.qsize(), "processed": self.processed}

    def requeue_unfinished(self):
        """After a restart, re-run candidates that were queued/processing when the app stopped."""
        for st in ("queued", "processing"):
            for row in self.db.list(status=st, limit=50):
                try:
                    if st == "processing" and row.get("output_path") and Path(row["output_path"]).exists():
                        self.db.upsert(id=row["id"], status="pending")   # an editor re-render was interrupted
                        continue
                    cand = json.loads(row["candidate_json"])
                    if Path(cand["source_path"]).exists():
                        self.q.put(cand)
                except Exception:  # noqa: BLE001
                    pass

    def _inbox(self):
        """Candidates dropped as JSON into data_dir/candidates/inbox/ are queued (used by tools/test_candidate)."""
        inbox = self.cfg["data_dir"] / "candidates" / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        while True:
            for f in sorted(inbox.glob("*.json")):
                try:
                    cand = json.loads(f.read_text(encoding="utf-8"))
                    f.unlink()
                    self.submit(cand)
                except Exception as e:  # noqa: BLE001
                    log(f"[inbox] {f.name}: {e}", "warn")
            time.sleep(3)

    def _run(self):
        while True:
            cand = self.q.get()
            if cand.get("_job") == "rerender":
                self.current = {"clip_id": cand["clip_id"], "streamer": cand["streamer"], "stage": "rendering",
                                "since": time.time()}
                try:
                    self._rerender(cand["clip_id"], cand["edit"])
                except Exception as e:  # noqa: BLE001
                    log(f"[editor] {cand['clip_id']} render failed: {e}", "error")
                    try:
                        p = json.loads(self.db.get(cand["clip_id"]).get("plan_json") or "{}")
                        p["edit_error"] = f"Your edit couldn't be rendered: {str(e)[:250]}"
                        self.db.upsert(id=cand["clip_id"], status="pending", plan_json=p)
                    except Exception:  # noqa: BLE001
                        self.db.upsert(id=cand["clip_id"], status="pending")
                    bus.publish("clip.edit_failed", {"clip_id": cand["clip_id"], "reason": str(e)[:300]})
                finally:
                    self.current = None
                continue
            self.current = {"clip_id": cand["clip_id"], "streamer": cand["streamer"], "stage": "transcribing",
                            "since": time.time()}
            try:
                self.process(cand)
            except Exception as e:  # noqa: BLE001
                log(f"[clip] {cand['clip_id']} failed: {e}", "error")
                self.db.upsert(id=cand["clip_id"], status="error", reason=str(e)[:500])
                bus.publish("clip.error", {"clip_id": cand["clip_id"], "reason": str(e)[:300]})
            finally:
                self.current = None
                self.processed += 1

    def _stage(self, cid, stage):
        if self.current:
            self.current["stage"] = stage
        bus.publish("clip.stage", {"clip_id": cid, "stage": stage}, keep=False)

    def process(self, cand: dict) -> dict:
        cfg, cid = self.cfg, cand["clip_id"]
        if not Path(cand["source_path"]).exists():
            raise FileNotFoundError("source file missing")
        t0 = time.time()
        try:
            s = self.store.streamer(cand["streamer"])
        except KeyError:
            s = {"layout": "fit_blur"}
        lang = s.get("language") or cfg["clipper"].get("language", "pt")
        self.db.upsert(id=cid, status="processing")
        self._stage(cid, "transcribing")
        tr = transcribe(cfg, cand["source_path"], lang)
        try:   # kept for the editor (captions of any line can be restored later)
            (cfg["data_dir"] / "renders").mkdir(parents=True, exist_ok=True)
            (cfg["data_dir"] / "renders" / f"{cid}.transcript.json").write_text(json.dumps(tr), encoding="utf-8")
        except OSError:
            pass
        self._stage(cid, "judging")
        lib = sfxmod.library(cfg["data_dir"])
        frames = None
        if cfg.get("editing", {}).get("ai_sees_frames", True) and not cand.get("demo"):
            try:
                grid = cfg["data_dir"] / "renders" / f"{cid}.frames.jpg"
                lo = max(0.0, cand["peak_offset"] - 20)
                hi = min(cand["duration"], cand["peak_offset"] + 15)
                frames = (grid, rendermod.frame_grid(cand["source_path"], grid, lo, hi))
            except Exception as e:  # noqa: BLE001
                log(f"[clip] frame grid failed: {e}", "warn")
        plan = planmod.make_plan(cand, tr, cfg, s, lib, frames)
        if not plan.get("keep", True):
            self.db.upsert(id=cid, status="skipped", reason=plan.get("reason", ""), plan_json=plan)
            log(f"[clip] {cid} skipped: {plan.get('reason', '')}")
            bus.publish("clip.skipped", {"clip_id": cid, "streamer": cand["streamer"], "reason": plan.get("reason", "")})
            return {"status": "skipped", "clip_id": cid, "reason": plan.get("reason", "")}
        self._stage(cid, "rendering")
        out = cfg["data_dir"] / "renders" / f"{cid}.mp4"
        words = caption_words(tr, s.get("captions", "streamer"), plan.get("streamer_segments"), lang)
        out, preview = rendermod.render(cand["source_path"], out, s, plan, words, cfg, lib)
        plan["words"] = words
        plan["captions_on"] = True
        used = {e.get("name") for e in plan.get("edits") or [] if e.get("type") == "sfx"}
        plan["sfx_credits"] = sorted({lib[n]["credit"] for n in used if n in lib and lib[n].get("credit")})
        self.db.upsert(id=cid, status="pending", title=plan["title"], hashtags=" ".join(plan["hashtags"]),
                       hook=plan["hook_text"], reason=plan.get("reason", ""), output_path=str(out),
                       preview_path=str(preview), plan_json=plan)
        base = self.base_url()
        res = {"status": "rendered", "clip_id": cid, "streamer": cand["streamer"], "title": plan["title"],
               "hashtags": plan["hashtags"], "hook": plan["hook_text"], "reason": plan.get("reason", ""),
               "confidence": plan.get("confidence"), "score": cand.get("score"), "llm": plan.get("llm"),
               "edits": len(plan.get("edits") or []),
               "seconds": round(time.time() - t0, 1), "review_url": f"{base}/#/clips/{cid}",
               "video_url": f"{base}/media/{cid}/video", "preview_url": f"{base}/media/{cid}/preview"}
        log(f"[clip] {cid} ready in {res['seconds']}s ({plan.get('llm')}, {res['edits']} effects)")
        bus.publish("clip.rendered", res)
        self.notifier.event("clip.rendered", res, {"preview": str(preview)})
        return res

    # ------------------------------------------------------------ in-app editor
    def submit_edit(self, cid: str, edit: dict):
        row = self.db.get(cid)
        self.db.upsert(id=cid, status="processing")
        self.q.put({"_job": "rerender", "clip_id": cid, "streamer": row["streamer"], "edit": edit})
        bus.publish("clip.queued", {"clip_id": cid, "streamer": row["streamer"], "queue": self.q.qsize()})

    def _rerender(self, cid: str, edit: dict):
        cfg = self.cfg
        row = self.db.get(cid)
        cand = json.loads(row["candidate_json"])
        plan = json.loads(row.get("plan_json") or "{}")
        try:
            s = self.store.streamer(cand["streamer"])
        except KeyError:
            s = {"layout": "fit_blur"}
        plan.setdefault("ai", {k: plan.get(k) for k in ("start", "end", "edits", "hook_text")})
        plan.update(edit, edited=time.time())
        plan.pop("edit_error", None)
        lib = sfxmod.library(cfg["data_dir"])
        out = cfg["data_dir"] / "renders" / f"{cid}.mp4"
        t0 = time.time()
        out, preview = rendermod.render(cand["source_path"], out, s, plan,
                                        plan["words"] if plan.get("captions_on", True) else [], cfg, lib)
        used = {e.get("name") for e in plan.get("edits") or [] if e.get("type") == "sfx"}
        plan["sfx_credits"] = sorted({lib[n]["credit"] for n in used if n in lib and lib[n].get("credit")})
        self.db.upsert(id=cid, status="pending", hook=plan.get("hook_text", ""), output_path=str(out),
                       preview_path=str(preview), plan_json=plan)
        log(f"[editor] {cid} re-rendered with your edits in {time.time() - t0:.0f}s")
        bus.publish("clip.updated", {"clip_id": cid, "streamer": cand["streamer"]})

    def prepare_editor(self, cid: str, job: dict):
        """Builds the editor's preview video, and caption words for clips made before the editor existed."""
        cfg = self.cfg
        row = self.db.get(cid)
        cand = json.loads(row["candidate_json"])
        plan = json.loads(row.get("plan_json") or "{}")
        src = cand["source_path"]
        if not Path(src).exists():
            raise FileNotFoundError("the recording for this clip was deleted")
        proxy = cfg["data_dir"] / "renders" / f"{cid}.editor.mp4"
        if not proxy.exists():
            job["msg"] = "making a preview copy"
            rendermod.editor_proxy(src, proxy.with_suffix(".tmp.mp4"))
            proxy.with_suffix(".tmp.mp4").replace(proxy)
        if "words" not in plan:
            job["msg"] = "transcribing (clip made before the editor existed)"
            try:
                s = self.store.streamer(cand["streamer"])
            except KeyError:
                s = {}
            lang = s.get("language") or cfg["clipper"].get("language", "pt")
            tf = cfg["data_dir"] / "renders" / f"{cid}.transcript.json"
            tr = json.loads(tf.read_text(encoding="utf-8")) if tf.exists() else transcribe(cfg, src, lang)
            if not tf.exists():
                tf.write_text(json.dumps(tr), encoding="utf-8")
            mode = s.get("captions", "streamer")
            plan["words"] = caption_words(tr, "streamer" if mode == "off" else mode, plan.get("streamer_segments"), lang)
            plan["captions_on"] = mode != "off"
            self.db.upsert(id=cid, plan_json=plan)

    def approve(self, cid: str, title=None, hashtags=None) -> dict:
        self.db.decide(cid, "approved", title, hashtags)
        row = self.db.get(cid)
        ready = self.cfg["data_dir"] / "ready"
        dst = ready / f"{cid}.mp4"
        shutil.copy2(row["output_path"], dst)
        caption = f"{row['title']}\n\n{row['hashtags']}"
        try:
            credits = json.loads(row.get("plan_json") or "{}").get("sfx_credits") or []
        except Exception:  # noqa: BLE001
            credits = []
        if credits:
            caption += "\n\nSounds: " + " · ".join(credits)
        (ready / f"{cid}.txt").write_text(caption, encoding="utf-8")
        base = self.base_url()
        local = f"http://127.0.0.1:{self.cfg['server']['port']}"
        tok = self.token(cid)
        res = {"clip_id": cid, "streamer": row["streamer"], "title": row["title"], "hashtags": row["hashtags"],
               "caption": caption, "path": str(dst), "video_url": f"{base}/media/{cid}/video",
               "download_url": f"{base}/media/{cid}/video?token={tok}",
               "local_download_url": f"{local}/media/{cid}/video?token={tok}",
               "size_mb": round(dst.stat().st_size / 1e6, 1)}
        bus.publish("clip.approved", res)
        self.notifier.event("clip.approved", res, {"final": str(dst)})
        return res

    def reject(self, cid: str):
        self.db.decide(cid, "rejected")
        bus.publish("clip.rejected", {"clip_id": cid})
