"""The AI editor: judges a candidate and writes the edit.

It returns trim points, hook, title, hashtags, which transcript lines are the streamer (for captions),
and an edit list (punch-in zooms, camera takeovers, callout texts, sound effects, screen shake).

Default backend: Claude Code in print mode (`claude -p`), which runs on your Claude Pro subscription
(shares its usage limits, no API bill). With "AI looks at frames" on, Claude also reads a grid of frames
from the clip so it can see what happens, not just hear it. ANTHROPIC_API_KEY is removed from its
environment so it never bills an API account.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import urllib.request
from pathlib import Path

from common.bus import log

LANG = {"pt": "Brazilian Portuguese", "en": "English"}

INTENSITY = {  # max effects per clip and which types are allowed at that level
    "off": (0, set()),
    "light": (3, {"zoom", "cam_focus", "text", "sfx"}),
    "normal": (7, {"zoom", "cam_focus", "text", "sfx", "shake"}),
    "heavy": (12, {"zoom", "cam_focus", "text", "sfx", "shake"}),
}
DUR = {"zoom": (0.5, 3.0, 1.4), "cam_focus": (1.0, 4.5, 2.5), "text": (1.0, 3.5, 2.2), "shake": (0.2, 0.8, 0.4)}

PROMPT = """You are the editor of a {lang_name} short-form channel posting clips of GTA 6 livestreams
(TikTok / YouTube Shorts / Reels). A hype detector flagged a moment. Decide if it's worth posting, cut it,
and design a fast, punchy edit: punch-in zooms on the key moment and on the streamer's reaction, the facecam
taking over the screen when the reaction IS the content, short on-screen texts that tell the viewer what is
happening / what the streamer did wrong / what caused it, and sound effects on impacts and reactions.

GROUNDING - the most important rule. You only know what is in the inputs below: the transcript, the chat and
(if provided) the frames. Every text, sound effect, hook and title must describe something that is actually
said in the transcript, written in chat, or clearly visible in the frames at that time. NEVER invent people,
creatures, objects, places, events, causes, jokes or lore that are not in the inputs, and never guess what a
blurry/ambiguous frame shows. Plain descriptions of what visibly happens are good (he fell, crashed, got run
over, fell from very high, cops arrived, the car exploded). If you are not sure, leave the effect out:
fewer, correct effects are always better than a wrong one. Every edit must say what it is based on ("why").
{style}
Streamer: {streamer}. Clip language: {lang_name}.
Source duration {duration:.1f}s. Detected peak at {peak:.1f}s (score {score}, chat z {z_chat}, voice z {z_audio}).
Layout: {layout_desc}
{frames_note}
Transcript segments (#index [start-end] (detected language) text). Times are seconds from the file start.
It contains BOTH the streamer and the game's own dialogue (NPCs, radio, cutscenes, phone calls):
{transcript}

Chat around the moment (already aligned to the video):
{chat}

Available sound effects (name: when to use):
{sfx_list}

Rules:
- keep=false if nothing really happens: raid/sub/giveaway spam, ads, AFK, just reading chat, or a payoff that
  needs too much context.
- Clip 12-45 s (15-30 ideal). Start at most ~3 s before the action that leads to the payoff; end 1-3 s after
  the reaction peaks. Never trail off.
- hook_text: 2-6 words, {lang_name}, UPPERCASE, curiosity without spoiling the payoff (shown for the first
  2.6 s). It must be true to the clip (grounding rule).
- title: max 70 chars, {lang_name}: the streamer's name + what actually happened, with the key words in
  CAPS, e.g. "{streamer} caiu do PRÉDIO MAIS ALTO no GTA 6"; you may end with one emoji. hashtags: 3-5 incl.
  #gta6 and the streamer's name.
- streamer_segments: indexes of the transcript segments spoken by the STREAMER (not game characters, radio,
  cutscenes or other players' voice lines). Only these get captions.
- edits: at most {max_edits} effects, times in FILE seconds inside [start, end]. Every edit needs "why":
  {{"src":"speech","seg":<segment index>}} or {{"src":"chat","quote":"<exact chat text>"}} or
  {{"src":"frame","n":<frame number>,"seen":"<what is visible>"}} - the evidence must be at that time.
  Types:
  {{"type":"zoom","t":..,"dur":0.5-3,"focus":"cam"|"game"|"center","amount":1.15-1.6,"why":..}}  punch-in on
     the streamer's face during a reaction (focus cam) or on the action at the key moment (focus game)
  {{"type":"cam_focus","t":..,"dur":1-4.5,"why":..}}  facecam fills the screen: big reactions, serious talk
  {{"type":"text","t":..,"dur":1-3.5,"text":"2-5 WORDS","why":..}}  callout in {lang_name} saying what is
     happening / what went wrong / what caused it, e.g. "ESQUECEU DO FREIO", "CAIU DE 50 METROS". Never repeat
     the spoken captions. Plain words only: no emoji, symbols, formatting codes or drawings.
  {{"type":"sfx","t":..,"name":"<one of the available names>","why":..}}  on impacts, fails, reveals, zoom-ins,
     callouts. A literal sound (siren, explosion, cash register, tires...) only if that thing really happens.
  {{"type":"shake","t":..,"dur":0.2-0.8,"why":..}}  screen shake on a big impact/crash
  Allowed types for this streamer: {allowed}. Keep the first 2.6 s clean except an optional sfx (hook on screen).
  Effects must land exactly on the moment they refer to.
- confidence: 0-1, how likely this performs well.

Reply with ONLY a JSON object:
{{"keep": true, "start": 0.0, "end": 0.0, "hook_text": "", "title": "", "hashtags": [], "streamer_segments": [],
"edits": [], "reason": "", "confidence": 0.0}}
"""

VERIFY_PROMPT = """You are a strict fact-checker for a short video edit. Below is everything known about the
clip: the transcript and the chat{frames_hint}. An editor proposed on-screen texts, sound effects, a hook and a
title. For EACH item answer true only if everything it states or implies is directly supported by the
transcript, the chat or what is clearly visible in the frames at that time. Answer false if it mentions any
person, creature, object, place, event or cause that is not in these inputs, or if you are not sure.
Pure style/hype words that claim no facts ("OLHA ISSO", "INSANO", "NÃO ACREDITO") are fine.
{frames_note}
Transcript:
{transcript}

Chat:
{chat}

Items:
{items}

Reply with ONLY JSON: {{"ok": [true/false for each item, in order], "notes": ["very short reason", ...]}}
"""

STOP = set("""o a os as um uma uns umas de do da dos das e em no na nos nas que pra pro para por com sem se ele
ela eles elas isso esse essa este esta aqui ai la mas mais muito muita tudo nada ja nao sim vai foi tava ta era
meu minha seu sua mano cara olha isso quando como onde porque entao tipo agora the an of to in on at is it he
she they this that and or but with for was are be his her him them what why how just like into from your you
gta""".split())


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", str(text).lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def content_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[^\W_]+", _norm(text)) if len(w) >= 4 and w not in STOP]


def grounded(text: str, corpus: set) -> bool:
    """True if most content words of `text` appear (loosely, by prefix) in the clip's transcript/chat."""
    words = content_words(text)
    if not words:
        return True
    hits = sum(1 for w in words if any(c[:5] == w[:5] or c.startswith(w) or w.startswith(c) for c in corpus))
    return hits / len(words) >= 0.5


def clean_text(s, limit=40, allow_emoji=False) -> str:
    """Plain text only: strips subtitle override/drawing codes ({...}, backslashes) and, by default, emoji."""
    s = re.sub(r"\{[^}]*\}", " ", str(s or "")).replace("\\", " ").replace("{", "").replace("}", "")
    if not allow_emoji:
        s = "".join(ch for ch in s if unicodedata.category(ch)[0] in "LNZ" or ch in "!?.,'\"-%$#&:+/")
    return re.sub(r"\s+", " ", s).strip()[:limit]


def _ctx(tr: dict, cand: dict, frame_times: list | None, streamer_name: str) -> dict:
    segs = {int(s.get("i", k)): s for k, s in enumerate(tr.get("segments") or [])}
    chat = [m.get("text", "") for m in cand.get("chat_sample", [])]
    corpus = set()
    for t in [s["text"] for s in segs.values()] + chat + [streamer_name]:
        corpus.update(content_words(t))
    return {"segs": segs, "chat": [_norm(c) for c in chat], "frames": list(frame_times or []), "corpus": corpus}


def evidence(e: dict, t_file: float, ctx: dict) -> str | None:
    """Checks the edit's "why" against the inputs. Returns "speech"/"chat"/"frame" or None."""
    w = e.get("why")
    if not isinstance(w, dict):
        return None
    src = w.get("src")
    try:
        if src == "speech":
            seg = ctx["segs"].get(int(w.get("seg")))
            if seg and seg["s"] - 4 <= t_file <= seg["e"] + 4:
                return "speech"
        elif src == "chat":
            q = _norm(w.get("quote", "")).strip()
            if len(q) >= 2 and any(q in c or c in q for c in ctx["chat"] if c):
                return "chat"
        elif src == "frame":
            n = int(w.get("n"))
            fr = ctx["frames"]
            if 1 <= n <= len(fr) and abs(fr[n - 1] - t_file) <= 5 and str(w.get("seen", "")).strip():
                return "frame"
    except (TypeError, ValueError):
        return None
    return None


def _extract_json(text: str) -> dict:
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b < 0:
        raise ValueError(f"no JSON in LLM output: {text[:200]}")
    return json.loads(text[a: b + 1])


def _claude(prompt: str, model: str, image=None, timeout: int = 300) -> dict:
    """image: a Path (copied as frames.jpg) or a list of Paths (copied as ref1.jpg, ref2.jpg, ...)."""
    exe = shutil.which("claude")  # finds claude.exe or claude.cmd on Windows
    if not exe:
        raise RuntimeError("claude not found on PATH")
    cmd = [exe, "-p", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    if image:
        cmd += ["--allowedTools", "Read"]
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    with tempfile.TemporaryDirectory() as d:  # its own folder: only the frame grid is there
        if isinstance(image, (list, tuple)):
            for k, im in enumerate(image, 1):
                shutil.copy2(im, Path(d) / f"ref{k}.jpg")
        elif image:
            shutil.copy2(image, Path(d) / "frames.jpg")
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                           cwd=d, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"claude -p failed: {r.stderr[:300] or r.stdout[:300]}")
    outer = json.loads(r.stdout)
    if outer.get("is_error"):
        raise RuntimeError(f"claude -p error: {outer.get('result', '')[:300]}")
    return _extract_json(outer.get("result", ""))


def _ollama(prompt: str, url: str, model: str) -> dict:
    req = urllib.request.Request(
        url.rstrip("/") + "/api/generate",
        data=json.dumps({"model": model, "prompt": prompt, "format": "json", "stream": False}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return _extract_json(json.loads(r.read())["response"])


def heuristic(cand: dict, cfg: dict, streamer: dict) -> dict:
    """No-AI fallback: cut around the peak, punch in on the reaction with a boom."""
    p, d = cand["peak_offset"], cand["duration"]
    lang = streamer.get("language") or cfg["clipper"].get("language", "pt")
    edits = [{"type": "zoom", "t": p - 0.2, "dur": 1.4, "focus": "cam", "amount": 1.3},
             {"type": "sfx", "t": p - 0.2, "name": "vine-boom"},
             {"type": "shake", "t": p - 0.2, "dur": 0.35}]
    name = streamer.get("display_name") or cand["streamer"]
    return {"keep": True, "start": max(0.0, p - 15), "end": min(d, p + 10),
            "hook_text": "OLHA ISSO" if lang == "pt" else "WAIT FOR IT",
            "title": f"{name} no GTA 6" if lang == "pt" else f"{name} in GTA 6",
            "hashtags": list(cfg["clipper"]["default_hashtags"]) + list(streamer.get("hashtags") or []),
            "edits": edits, "streamer_segments": None,
            "reason": "simple cut (AI not used)", "confidence": 0.3, "llm": "none"}


def _why_text(e: dict, src: str) -> str:
    w = e.get("why") or {}
    if src == "speech":
        return f"speech #{w.get('seg')}"
    if src == "chat":
        return f"chat: {clean_text(w.get('quote', ''), 60)}"
    return f"frame {w.get('n')}: {clean_text(w.get('seen', ''), 80)}"


def sanitize_edits(edits, start: float, end: float, streamer: dict, sfx_names: set, ctx: dict | None = None) -> list:
    """Validate the AI's edit list and convert times to clip time (seconds from the clip start).

    With ctx (an AI plan), every edit must cite evidence that exists in the inputs at that time; texts that
    aren't backed by the transcript/chat, and literal sounds backed only by a frame, are marked for the
    fact-check pass (verify=True)."""
    max_n, allowed = INTENSITY.get(streamer.get("edit_intensity", "normal"), INTENSITY["normal"])
    flags = {"zoom": "edit_zoom", "cam_focus": "edit_cam_focus", "text": "edit_text", "sfx": "edit_sfx",
             "shake": "edit_shake"}
    allowed = {t for t in allowed if streamer.get(flags[t], True)}
    if streamer.get("layout", "cam_top") != "cam_top":
        allowed.discard("cam_focus")
    out = []
    for e in edits or []:
        try:
            typ = e.get("type")
            if typ not in allowed:
                continue
            t = float(e.get("t", 0)) - start
            if t < 0 or t > end - start - 0.2:
                continue
            src = None
            if ctx is not None:
                src = evidence(e, t + start, ctx)
                if not src:
                    continue
            ne = {"type": typ, "t": round(t, 2)}
            if src:
                ne["why"] = _why_text(e, src)
            if typ in DUR:
                lo, hi, default = DUR[typ]
                ne["dur"] = round(min(hi, max(lo, float(e.get("dur", default)))), 2)
                ne["dur"] = round(min(ne["dur"], end - start - t), 2)
            if typ == "zoom":
                ne["amount"] = min(1.6, max(1.15, float(e.get("amount", 1.3))))
                ne["focus"] = e.get("focus") if e.get("focus") in ("cam", "game", "center") else "center"
                if ne["focus"] == "cam" and streamer.get("layout", "cam_top") != "cam_top":
                    ne["focus"] = "center"
            if typ == "text":
                ne["text"] = clean_text(e.get("text", ""))
                if len(ne["text"]) < 2:
                    continue
                if ctx is not None:
                    ne["verify"] = True   # every callout is fact-checked
                    ne["grounded"] = src in ("speech", "chat") and grounded(ne["text"], ctx["corpus"])
            if typ == "sfx":
                name = str(e.get("name", "")).lower()
                fallback = next((n for n in ("vine-boom", "cinematic-hit", "boom") if n in sfx_names), None)
                ne["name"] = name if name in sfx_names else fallback
                if not ne["name"]:
                    continue
                if ctx is not None and src == "frame":
                    ne["verify"] = True
            out.append(ne)
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["t"])
    return out[:max_n]


def sanitize(plan: dict, cand: dict, cfg: dict, streamer: dict | None = None, sfx_names: set | None = None,
             ctx: dict | None = None) -> dict:
    streamer = streamer or {}
    d, p = cand["duration"], cand["peak_offset"]
    s = float(plan.get("start", p - 15))
    e = float(plan.get("end", p + 10))
    s, e = max(0.0, min(s, d - 5)), max(0.0, min(e, d))
    if e - s < 8 or e - s > 59:
        s, e = max(0.0, p - 15), min(d, p + 10)
    plan["start"], plan["end"] = round(s, 2), round(e, 2)
    tags = [t if t.startswith("#") else f"#{t}" for t in (plan.get("hashtags") or [])]
    for extra in streamer.get("hashtags") or []:
        extra = extra if extra.startswith("#") else f"#{extra}"
        if extra.lower() not in [t.lower() for t in tags]:
            tags.append(extra)
    if "#gta6" not in [t.lower() for t in tags]:
        tags.insert(0, "#gta6")
    plan["hashtags"] = tags[:6]
    plan["hook_text"] = clean_text(plan.get("hook_text") or "").upper()
    plan["title"] = clean_text(plan.get("title") or "", 100, allow_emoji=True) or f"{cand['streamer']} GTA 6"
    ss = plan.get("streamer_segments")
    plan["streamer_segments"] = [int(i) for i in ss if str(i).lstrip("-").isdigit()] if isinstance(ss, list) else None
    plan["edits"] = sanitize_edits(plan.get("edits"), plan["start"], plan["end"], streamer, sfx_names or set(), ctx)
    return plan


def _fallback_title(cand: dict, streamer: dict, lang: str) -> str:
    name = streamer.get("display_name") or cand["streamer"]
    return f"{name} no GTA 6" if lang == "pt" else f"{name} in GTA 6"


def verify(plan: dict, cand: dict, tr: dict, ctx: dict, streamer: dict, lang: str, model: str | None,
           frames: tuple | None) -> dict:
    """Fact-check pass: a second Claude call removes every text/sound/hook/title not backed by the clip.
    model=None (no Claude) or a failed check -> keep only what is lexically backed by the transcript/chat."""
    items = []  # (kind, ref, description)
    for k, e in enumerate(plan["edits"]):
        if e.get("verify"):
            what = f'text "{e["text"]}"' if e["type"] == "text" else f'sound effect "{e["name"]}"'
            items.append(("edit", k, f"[{e['t'] + plan['start']:.1f}s] on-screen {what} (editor's evidence: {e.get('why', '')})"))
    if plan.get("hook_text"):
        items.append(("hook", None, f'hook text shown at the start: "{plan["hook_text"]}"'))
    items.append(("title", None, f'title: "{plan["title"]}"'))
    ok = None
    if model is not None and items:
        try:
            frames_note = ""
            if frames:
                times = ", ".join(f"{k}={t:.1f}s" for k, t in enumerate(frames[1], 1))
                frames_note = (f"Read the image frames.jpg in your current folder: frames numbered left to right, "
                               f"top to bottom, 4 per row, at file times {times}.\n")
            transcript = "\n".join(f"#{i} [{s['s']:.1f}-{s['e']:.1f}] {s['text']}" for i, s in ctx["segs"].items()) or "(no speech)"
            chat = "\n".join(f"[{m['t']:.1f}] {m['text']}" for m in cand.get("chat_sample", [])) or "(none)"
            r = _claude(VERIFY_PROMPT.format(frames_hint=" and frames" if frames else "", frames_note=frames_note,
                                             transcript=transcript, chat=chat,
                                             items="\n".join(f"{n}. {d}" for n, (_, _, d) in enumerate(items, 1))),
                        model, frames[0] if frames else None, timeout=180)
            ok = [bool(x) for x in r.get("ok", [])]
            if len(ok) != len(items):
                raise ValueError(f"expected {len(items)} answers, got {len(ok)}")
        except Exception as e:  # noqa: BLE001
            log(f"[plan] fact-check failed ({e}); keeping only effects backed by the transcript/chat", "warn")
            ok = None
    removed, drop = [], set()
    for n, (kind, ref, desc) in enumerate(items):
        if ok is not None:
            good = ok[n]
        elif kind == "edit":
            e = plan["edits"][ref]
            good = e["type"] == "text" and e.get("grounded")
        else:
            good = grounded(plan["hook_text"] if kind == "hook" else plan["title"], ctx["corpus"])
        if good:
            continue
        removed.append(desc)
        if kind == "edit":
            drop.add(ref)
        elif kind == "hook":
            plan["hook_text"] = ""
        else:
            plan["title"] = _fallback_title(cand, streamer, lang)
    plan["edits"] = [e for k, e in enumerate(plan["edits"]) if k not in drop]
    for e in plan["edits"]:
        e.pop("verify", None)
        e.pop("grounded", None)
    plan["fact_check"] = {"checked": len(items), "removed": removed, "by": "claude" if ok is not None else "words"}
    if removed:
        log(f"[plan] fact-check removed {len(removed)}: " + "; ".join(removed)[:400])
    return plan


def make_plan(cand: dict, tr: dict, cfg: dict, streamer: dict | None = None, sfx_lib: dict | None = None,
              frames: tuple[Path, list] | None = None) -> dict:
    streamer = streamer or {}
    sfx_lib = sfx_lib or {}
    c = cfg["clipper"]
    lang = streamer.get("language") or c.get("language", "pt")
    max_n, allowed = INTENSITY.get(streamer.get("edit_intensity", "normal"), INTENSITY["normal"])
    flags = {"zoom": "edit_zoom", "cam_focus": "edit_cam_focus", "text": "edit_text", "sfx": "edit_sfx",
             "shake": "edit_shake"}
    allowed = sorted(t for t in allowed if streamer.get(flags[t], True))
    if streamer.get("layout", "cam_top") != "cam_top" and "cam_focus" in allowed:
        allowed.remove("cam_focus")
    if not sfx_lib and "sfx" in allowed:
        allowed.remove("sfx")   # no sounds installed
    transcript = "\n".join(
        f"#{s.get('i', k)} [{s['s']:.1f}-{s['e']:.1f}] ({s.get('lang') or '?'}) {s['text']}"
        for k, s in enumerate(tr["segments"])) or "(no speech)"
    chat = "\n".join(f"[{m['t']:.1f}] {m['user']}: {m['text']}" for m in cand.get("chat_sample", [])) or "(none)"
    if streamer.get("layout", "cam_top") == "cam_top":
        layout_desc = "facecam on top of the screen, gameplay below."
    else:
        layout_desc = "no facecam; gameplay only (do not use cam_focus or focus 'cam')."
    frames_note = ""
    if frames:
        times = ", ".join(f"{k}={t:.1f}s" for k, t in enumerate(frames[1], 1))
        frames_note = (f"IMPORTANT: read the image file frames.jpg in your current folder first. It is a grid of "
                       f"frames from the source, numbered left to right, top to bottom, 4 per row: {times}. "
                       f"Use it to see what actually happens on screen (frame numbers go in \"why\").\n")
    guide = (cfg.get("editing", {}).get("style_guide") or "").strip()
    style = ("\nHOUSE STYLE, learned from reference videos. Follow it for HOW to edit (pacing, kinds of texts, "
             "zoom and sound habits). It is NOT information about this clip: never take content from it.\n"
             + guide[:3000] + "\n") if guide else ""
    prompt = PROMPT.format(
        lang_name=LANG.get(lang, lang), streamer=streamer.get("display_name") or cand["streamer"],
        duration=cand["duration"], peak=cand["peak_offset"], score=cand["score"], z_chat=cand["z_chat"],
        z_audio=cand["z_audio"], transcript=transcript, chat=chat, layout_desc=layout_desc,
        frames_note=frames_note, style=style, max_edits=max_n, allowed=", ".join(allowed) or "none",
        sfx_list="\n".join(f"- {k}: {v['desc']}" for k, v in sfx_lib.items()) or "none")
    backend = "none" if cand.get("demo") else c.get("llm", "claude")  # demo clips never spend Claude usage
    try:
        if backend == "claude":
            try:
                plan = _claude(prompt, c.get("claude_model", ""), frames[0] if frames else None)
                plan["_saw_frames"] = bool(frames)
            except Exception as e:  # noqa: BLE001
                if not frames:
                    raise
                log(f"[plan] Claude with frames failed ({e}); retrying without frames", "warn")
                plan = _claude(prompt.replace(frames_note, ""), c.get("claude_model", ""))
        elif backend == "ollama":
            plan = _ollama(prompt.replace(frames_note, ""), c["ollama_url"], c["ollama_model"])
        else:
            plan = heuristic(cand, cfg, streamer)
        plan["llm"] = backend
    except Exception as e:  # noqa: BLE001
        log(f"[plan] {backend} failed: {e}", "warn")
        plan = None
        if backend == "claude" and c.get("ollama_model"):
            try:
                plan = _ollama(prompt.replace(frames_note, ""), c["ollama_url"], c["ollama_model"])
                plan["llm"] = "ollama-fallback"
            except Exception as e2:  # noqa: BLE001
                log(f"[plan] ollama fallback failed: {e2}", "warn")
        plan = plan or heuristic(cand, cfg, streamer)
    if plan.get("llm") == "none":
        return sanitize(plan, cand, cfg, streamer, set(sfx_lib))
    ctx = _ctx(tr, cand, frames[1] if frames and plan.get("_saw_frames") else None, streamer.get("display_name") or cand["streamer"])
    plan = sanitize(plan, cand, cfg, streamer, set(sfx_lib), ctx)
    if plan.get("keep", True):
        use_claude = plan["llm"] == "claude" and cfg.get("editing", {}).get("verify_edits", True)
        plan = verify(plan, cand, tr, ctx, streamer, lang, c.get("claude_model", "") if use_claude else None,
                      frames if plan.get("_saw_frames") else None)
    plan.pop("_saw_frames", None)
    return plan
