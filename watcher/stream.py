"""One StreamerWatcher per streamer, all running on a single asyncio loop owned by
WatcherManager. Streamers can be added, paused, resumed and removed while the app runs."""
import asyncio
import json
import subprocess
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from common.bus import bus, log
from watcher import media
from watcher.chat import demo_chat, kick_chat, message_weight, twitch_chat
from watcher.detector import Detector


class StreamerWatcher:
    def __init__(self, store, name: str, on_candidate):
        self.store, self.name, self.on_candidate = store, name, on_candidate
        cfg = store.cfg
        self.dc = cfg["detector"]                       # shared dict: live threshold/weight edits apply
        self.seg_dir = cfg["data_dir"] / "segments" / name
        self.feat_dir = cfg["data_dir"] / "features" / name
        self.seg_dir.mkdir(parents=True, exist_ok=True)
        self.feat_dir.mkdir(parents=True, exist_ok=True)
        self.det = Detector(self.dc)
        self.seen_users: dict[int, set] = {}
        self.chat_log = deque(maxlen=8000)
        self.chat_raw: dict[int, list] = {}
        self.analyzed: set[str] = set()
        self.audio_end = 0
        self.pending: list[dict] = []
        self.written_until = 0
        self.history = deque(maxlen=900)                # last 15 min of per-second points for the UI
        self.procs: list[subprocess.Popen] = []
        self.stopping = False
        self.rec_started = 0.0
        self.last_segment_at = 0.0
        self.live_since = None
        self.candidates_today = 0
        self.last_candidate = None
        self.day = datetime.now().date()
        self.last_state = None
        self.error = ""

    @property
    def s(self) -> dict:
        return self.store.streamer(self.name)

    # ---------------------------------------------------------------- status
    def state(self) -> str:
        if self.stopping:
            return "paused"
        running = any(p.poll() is None for p in self.procs)
        if running and time.time() - self.last_segment_at < 30:
            return "live"
        if running:
            return "connecting"
        return "offline"

    def status(self) -> dict:
        now = int(time.time())
        chat_min = sum(v[0] for k, v in self.chat_raw.items() if k > now - 60)
        last = self.history[-1] if self.history else {}
        return {"name": self.name, "state": self.state(), "live_since": self.live_since,
                "chat_per_min": chat_min, "score": last.get("score"), "voice": last.get("voice"),
                "candidates_today": self.candidates_today, "last_candidate": self.last_candidate,
                "warming_up": self.det.first_t is None or
                (self.det.last_eval or 0) - (self.det.first_t or 0) < int(self.dc["warmup_s"]),
                "error": self.error}

    # ---------------------------------------------------------------- recording
    def _record_once(self):
        src, ff = media.recorder_commands(self.s, self.seg_dir)
        self.procs = []
        if src:
            p1 = subprocess.Popen(src, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            p2 = subprocess.Popen(ff, stdin=p1.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            p1.stdout.close()
            self.procs = [p1, p2]
        else:
            p2 = subprocess.Popen(ff, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            self.procs = [p2]
        self.rec_started = time.time()
        _, err = p2.communicate()
        self.kill()
        return (err or b"").decode("utf-8", "replace").strip()[-300:]

    def kill(self):
        for p in self.procs:
            try:
                if p.poll() is None:
                    p.kill()
            except Exception:  # noqa: BLE001
                pass

    async def record_loop(self):
        while not self.stopping:
            t0 = time.time()
            try:
                err = await asyncio.to_thread(self._record_once)
                self.error = "" if time.time() - t0 > 60 else err
            except FileNotFoundError as e:
                self.error = f"{e.filename or e} not found - install it / check PATH"
                log(f"[rec] {self.name}: {self.error}", "error")
            if self.stopping:
                break
            if time.time() - t0 > 60:
                log(f"[rec] {self.name}: stream ended or dropped, polling every 60 s")
            self.live_since = None
            await asyncio.sleep(5 if self.s["platform"] == "demo" else 60)

    # ---------------------------------------------------------------- chat
    async def chat_loop(self):
        p = self.s["platform"]
        if p == "twitch":
            src = twitch_chat(self.s["channel"], log)
        elif p == "kick" and self.s.get("kick_chatroom_id"):
            src = kick_chat(int(self.s["kick_chatroom_id"]), log)
        elif p == "demo":
            src = demo_chat(lambda: self.rec_started, media.DEMO_PERIOD, log)
        else:
            log(f"[chat] {self.name}: no chat source for {p}; detection uses voice only")
            return
        async for ev in src:
            sec = int(ev["t"])
            if ev["kind"] == "event":
                self.det.add_suppression(sec)
                bus.publish("chat.suppressed", {"streamer": self.name, "type": ev["type"]})
                continue
            user, text = ev["user"], ev["text"]
            if user in self.dc["ignore_users"] or text.startswith("!"):
                continue
            self.chat_log.append((ev["t"], user, text))
            users = self.seen_users.setdefault(sec, set())
            if user in users:
                continue
            users.add(user)
            w = message_weight(text, self.dc["keywords"])
            self.det.add_chat(sec, w)
            c = self.chat_raw.setdefault(sec, [0, 0.0])
            c[0] += 1
            c[1] += w

    # ---------------------------------------------------------------- audio
    async def audio_loop(self):
        self.analyzed = {p.name for p in media.list_segments(self.seg_dir)
                         if p.stat().st_mtime < time.time() - 60}
        while not self.stopping:
            for p in media.list_segments(self.seg_dir)[:-1]:
                if p.name in self.analyzed:
                    continue
                self.analyzed.add(p.name)
                try:
                    start, end, per_sec = await asyncio.to_thread(media.analyze_segment, p)
                except Exception as e:  # noqa: BLE001
                    log(f"[audio] {self.name} {p.name}: {e}", "warn")
                    continue
                if self.audio_end and start > self.audio_end + 120:
                    log(f"[audio] {self.name}: gap in stream, restarting baseline")
                    self.det = Detector(self.dc)
                for sec, db in per_sec.items():
                    self.det.set_audio(sec, db)
                self.audio_end = max(self.audio_end, end)
                self.last_segment_at = time.time()
                if self.live_since is None:
                    self.live_since = time.time()
            await asyncio.sleep(2)

    # ---------------------------------------------------------------- detection
    async def detect_loop(self):
        while not self.stopping:
            await asyncio.sleep(2)
            st = self.state()
            if st != self.last_state:
                self.last_state = st
                bus.publish("streamer.status", self.status())
            if datetime.now().date() != self.day:
                self.day, self.candidates_today = datetime.now().date(), 0
            if not self.audio_end:
                continue
            off = int(self.det.offset)
            ready = min(self.audio_end - 2, int(time.time()) - off - 3)
            start = (self.det.last_eval or ready - 1) + 1
            for ev in self.det.advance(ready):
                log(f"[hype] {self.name} score={ev['score']:.2f} chat={ev['z_chat']:.1f} voice={ev['z_audio']:.1f}")
                bus.publish("hype", {"streamer": self.name, **ev})
                self.pending.append(ev)
            pts = []
            for t in range(max(start, ready - 30), ready + 1):
                sc = self.det.scores.get(t)
                if sc is None:
                    continue
                pt = {"t": t, "score": round(sc[0], 2), "zc": round(sc[1], 2), "za": round(sc[2], 2),
                      "chat": round(self.det.chat_w.get(t + off, 0.0), 2), "voice": self.det.audio.get(t)}
                pts.append(pt)
                self.history.append(pt)
            if pts:
                bus.publish("metrics", {"streamer": self.name, "points": pts,
                                        "threshold": self.dc["threshold"], "status": self.status()}, keep=False)
            self._write_features(ready)
            for ev in list(self.pending):
                if self.audio_end >= ev["peak"] + int(self.dc["post_s"]) + 1:
                    self.pending.remove(ev)
                    asyncio.create_task(self.emit(ev))
            self._prune(ready)

    def _write_features(self, ready):
        if ready <= self.written_until:
            return
        start = max(self.written_until + 1, ready - 120)
        day = datetime.now().strftime("%Y%m%d")
        with open(self.feat_dir / f"{day}.jsonl", "a", encoding="utf-8") as f:
            for t in range(start, ready + 1):
                cnt, w = self.chat_raw.get(t, [0, 0.0])
                f.write(json.dumps({"t": t, "chat_n": cnt, "chat_w": round(w, 2),
                                    "db": self.det.audio.get(t), "supp": t in self.det.supp}) + "\n")
        self.written_until = ready

    def _prune(self, now_t):
        cut = now_t - 900
        for d in (self.seen_users, self.chat_raw):
            for k in [k for k in d if k < cut]:
                del d[k]

    async def emit(self, ev):
        peak = ev["peak"]
        t0, t1 = peak - int(self.dc["pre_s"]), peak + int(self.dc["post_s"])
        clip_id = f"{self.name}-{datetime.fromtimestamp(peak).strftime('%Y%m%d-%H%M%S')}"
        cand_dir = self.store.cfg["data_dir"] / "candidates"
        out = cand_dir / f"{clip_id}.mkv"
        try:
            file_start, dur = await asyncio.to_thread(media.cut_window, self.seg_dir, t0, t1, out)
        except Exception as e:  # noqa: BLE001
            log(f"[cut] {clip_id}: {e}", "error")
            return
        off = int(self.det.offset)
        sample = [{"t": round(t - off - file_start, 1), "user": u, "text": x[:120]}
                  for (t, u, x) in self.chat_log if t0 + off <= t <= t1 + off][-60:]
        payload = {"clip_id": clip_id, "streamer": self.name, "platform": self.s["platform"],
                   "channel": self.s["channel"], "source_path": str(out), "file_start": file_start,
                   "duration": dur, "peak_offset": peak - file_start, "score": round(ev["score"], 2),
                   "z_chat": round(ev["z_chat"], 2), "z_audio": round(ev["z_audio"], 2),
                   "chat_sample": sample, "created": time.time(), "demo": self.s["platform"] == "demo"}
        (cand_dir / f"{clip_id}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                                  encoding="utf-8")
        self.candidates_today += 1
        self.last_candidate = time.time()
        self.on_candidate(payload)

    async def cleanup_loop(self):
        while not self.stopping:
            await asyncio.to_thread(media.cleanup, self.seg_dir, float(self.store.cfg["retention_hours"]))
            self.analyzed = {n for n in self.analyzed if (self.seg_dir / n).exists()}
            await asyncio.sleep(600)

    async def run(self):
        log(f"[watch] {self.name} started ({self.s['platform']}/{self.s['channel']})")
        tasks = [asyncio.create_task(c) for c in
                 (self.record_loop(), self.chat_loop(), self.audio_loop(), self.detect_loop(), self.cleanup_loop())]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            self.stopping = True
            self.kill()
            for t in tasks:
                t.cancel()
            log(f"[watch] {self.name} stopped")


class WatcherManager:
    def __init__(self, store, on_candidate):
        self.store, self.on_candidate = store, on_candidate
        self.loop = asyncio.new_event_loop()
        self.watchers: dict[str, StreamerWatcher] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.thread = threading.Thread(target=self.loop.run_forever, name="watchers", daemon=True)
        self.thread.start()

    def _call(self, coro, timeout=15):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def start_all(self):
        for s in self.store.cfg["streamers"]:
            if s.get("enabled", True):
                self.start(s["name"])

    def start(self, name):
        return self._call(self._start(name))

    def stop(self, name):
        return self._call(self._stop(name))

    def restart(self, name):
        self.stop(name)
        return self.start(name)

    async def _start(self, name):
        if name in self.tasks and not self.tasks[name].done():
            return False
        w = StreamerWatcher(self.store, name, self.on_candidate)
        self.watchers[name] = w
        self.tasks[name] = self.loop.create_task(w.run())
        bus.publish("streamer.status", w.status())
        return True

    async def _stop(self, name):
        w, task = self.watchers.get(name), self.tasks.get(name)
        if not w:
            return False
        w.stopping = True
        w.kill()
        if task:
            task.cancel()
            await asyncio.wait([task], timeout=8)
        self.tasks.pop(name, None)
        bus.publish("streamer.status", {"name": name, "state": "paused"})
        return True

    def forget(self, name):
        self.stop(name)
        self.watchers.pop(name, None)

    def reset_detectors(self):
        def _reset():
            for w in self.watchers.values():
                w.det = Detector(w.dc)
        self.loop.call_soon_threadsafe(_reset)

    def status(self) -> list[dict]:
        out = []
        for s in self.store.cfg["streamers"]:
            w = self.watchers.get(s["name"])
            running = s["name"] in self.tasks and not self.tasks[s["name"]].done()
            st = w.status() if (w and running) else {"name": s["name"], "state": "paused",
                                                     "candidates_today": w.candidates_today if w else 0}
            st.update(platform=s["platform"], channel=s["channel"], enabled=s.get("enabled", True),
                      layout=s.get("layout"))
            out.append(st)
        return out

    def history(self, name) -> list:
        w = self.watchers.get(name)
        return list(w.history) if w else []

    def shutdown(self):
        for name in list(self.tasks):
            try:
                self.stop(name)
            except Exception:  # noqa: BLE001
                pass
