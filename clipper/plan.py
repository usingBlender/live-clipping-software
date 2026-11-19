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
import shutil
import subprocess
import tempfile
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
and design a fast, punchy edit in the style of popular Brazilian streamer-clip channels: punch-in zooms on
the key moment and on the streamer's reaction, the facecam taking over the screen when the reaction IS the
content, short on-screen texts that tell the viewer what is happening / what the streamer did wrong / what
caused it, and meme-style sound effects on impacts and reactions.

Streamer: {streamer}. Clip language: {lang_name}.
Source duration {duration:.1f}s. Detected peak at {peak:.1f}s (score {score}, chat z {z_chat}, voice z {z_audio}).
Layout: {layout_desc}
{frames_note}
Transcript segments (#index [start-end] (detected language) text). Times are seconds from the file start.
It contains BOTH the streamer and the game's own dialogue (NPCs, radio, cutscenes, phone calls):
{transcript}

Chat around the moment (already aligned to the video):
{chat}

Available sound effects (name: when to use). Prefer real recorded sounds over ones marked "basic synthesized":
{sfx_list}

Rules:
- keep=false if nothing really happens: raid/sub/giveaway spam, ads, AFK, just reading chat, or a payoff that
  needs too much context.
- Clip 12-45 s (15-30 ideal). Start at most ~3 s before the action that leads to the payoff; end 1-3 s after
  the reaction peaks. Never trail off.
- hook_text: 2-6 words, {lang_name}, UPPERCASE, curiosity without spoiling the payoff (shown for the first 2.6 s).
- title: max 70 chars, {lang_name}, include the streamer name. hashtags: 3-5 incl. #gta6.
- streamer_segments: indexes of the transcript segments spoken by the STREAMER (not game characters, radio,
  cutscenes or other players' voice lines). Only these get captions.
- edits: at most {max_edits} effects, times in FILE seconds inside [start, end]. Types:
  {{"type":"zoom","t":..,"dur":0.5-3,"focus":"cam"|"game"|"center","amount":1.15-1.6}}  punch-in on the
     streamer's face during a reaction (focus cam) or on the action at the key moment (focus game)
  {{"type":"cam_focus","t":..,"dur":1-4.5}}  facecam fills the screen: big reactions, or when he talks seriously
  {{"type":"text","t":..,"dur":1-3.5,"text":"2-5 WORDS"}}  callout in {lang_name} explaining what's happening / what
     went wrong / what caused it, e.g. "ESQUECEU DO FREIO", "O AVIÃO TAVA ATRÁS DELE". Never repeat the spoken
     captions, no emoji.
  {{"type":"sfx","t":..,"name":"<one of the available names>"}}  on impacts, fails, reveals, zoom-ins, callouts
  {{"type":"shake","t":..,"dur":0.2-0.8}}  screen shake on a big impact/crash
  Allowed types for this streamer: {allowed}. Keep the first 2.6 s clean except an optional sfx (hook on screen).
  Effects must land exactly on the moment they refer to; fewer, well-timed effects beat many random ones.
- confidence: 0-1, how likely this performs well.

Reply with ONLY a JSON object:
{{"keep": true, "start": 0.0, "end": 0.0, "hook_text": "", "title": "", "hashtags": [], "streamer_segments": [],
"edits": [], "reason": "", "confidence": 0.0}}
"""


def _extract_json(text: str) -> dict:
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b < 0:
        raise ValueError(f"no JSON in LLM output: {text[:200]}")
    return json.loads(text[a: b + 1])


def _claude(prompt: str, model: str, image: Path | None = None) -> dict:
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
        if image:
            shutil.copy2(image, Path(d) / "frames.jpg")
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=300,
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


def sanitize_edits(edits, start: float, end: float, streamer: dict, sfx_names: set) -> list:
    """Validate the AI's edit list and convert times to clip time (seconds from the clip start)."""
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
            ne = {"type": typ, "t": round(t, 2)}
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
                ne["text"] = str(e.get("text", "")).strip()[:40]
                if not ne["text"]:
                    continue
            if typ == "sfx":
                name = str(e.get("name", "")).lower()
                fallback = next((n for n in ("vine-boom", "cinematic-hit", "boom") if n in sfx_names), None)
                ne["name"] = name if name in sfx_names else fallback
                if not ne["name"]:
                    continue
            out.append(ne)
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["t"])
    return out[:max_n]


def sanitize(plan: dict, cand: dict, cfg: dict, streamer: dict | None = None, sfx_names: set | None = None) -> dict:
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
    plan["hook_text"] = (plan.get("hook_text") or "").upper()[:40]
    plan["title"] = (plan.get("title") or f"{cand['streamer']} GTA 6")[:100]
    ss = plan.get("streamer_segments")
    plan["streamer_segments"] = [int(i) for i in ss if str(i).lstrip("-").isdigit()] if isinstance(ss, list) else None
    plan["edits"] = sanitize_edits(plan.get("edits"), plan["start"], plan["end"], streamer, sfx_names or set())
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
        times = ", ".join(f"{t:.1f}s" for t in frames[1])
        frames_note = (f"IMPORTANT: read the image file frames.jpg in your current folder first. It is a grid of "
                       f"frames from the source (left to right, top to bottom) at these file times: {times}. "
                       f"Use it to see what actually happens on screen.\n")
    prompt = PROMPT.format(
        lang_name=LANG.get(lang, lang), streamer=streamer.get("display_name") or cand["streamer"],
        duration=cand["duration"], peak=cand["peak_offset"], score=cand["score"], z_chat=cand["z_chat"],
        z_audio=cand["z_audio"], transcript=transcript, chat=chat, layout_desc=layout_desc,
        frames_note=frames_note, max_edits=max_n, allowed=", ".join(allowed) or "none",
        sfx_list="\n".join(f"- {k}: {v['desc']}" for k, v in sfx_lib.items()) or "none")
    backend = "none" if cand.get("demo") else c.get("llm", "claude")  # demo clips never spend Claude usage
    try:
        if backend == "claude":
            try:
                plan = _claude(prompt, c.get("claude_model", ""), frames[0] if frames else None)
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
    return sanitize(plan, cand, cfg, streamer, set(sfx_lib))
