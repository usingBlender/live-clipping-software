"""Ask an LLM to judge the candidate and choose trim points, hook, title, hashtags.

Default backend: Claude Code in print mode (`claude -p`), which runs on your Claude Pro
subscription (shares its 5-hour / weekly limits, no API bill). Make sure ANTHROPIC_API_KEY
is NOT set in the clipper's environment, or it will bill the API instead.
"""
import json
import os
import shutil
import subprocess
import tempfile
import urllib.request

from common.bus import log

PROMPT = """You are the editor of a {lang_name} short-form channel that posts GTA 6 livestream clips
(TikTok, YouTube Shorts, Instagram Reels). A hype detector flagged a moment in a stream.

Streamer: {streamer}
Source file duration: {duration:.1f}s. Detected peak at {peak:.1f}s.
Signals: combined score {score}, chat z-score {z_chat}, streamer-voice z-score {z_audio}.

Transcript (seconds from file start; includes the streamer AND game dialogue):
{transcript}

Chat around the moment (seconds from file start, already aligned to the video):
{chat}

Decide whether this is worth posting and how to cut it. Rules:
- keep=false if nothing actually happens: raid/sub/giveaway spam, ads, BRB/AFK, the streamer just reading chat,
  or the payoff is not understandable without long context.
- Clip length 12-45 s (15-30 ideal). Start as late as possible: at most ~3 s of setup before the action
  that leads to the payoff. End 1-3 s after the reaction peaks; never trail off.
- hook_text: 2-6 words in {lang_name}, UPPERCASE, creates curiosity WITHOUT spoiling the payoff.
- title: max 70 chars in {lang_name}, natural, include the streamer name, no clickbait lies.
- hashtags: 3-5, always include #gta6.
- confidence: 0-1, how likely this performs well as a short.

Reply with ONLY a JSON object, no prose:
{{"keep": true, "start": 0.0, "end": 0.0, "hook_text": "", "title": "", "hashtags": [], "reason": "", "confidence": 0.0}}
"""

LANG = {"pt": "Brazilian Portuguese", "en": "English"}


def _extract_json(text: str) -> dict:
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b < 0:
        raise ValueError(f"no JSON in LLM output: {text[:200]}")
    return json.loads(text[a: b + 1])


def _claude(prompt: str, model: str) -> dict:
    exe = shutil.which("claude")  # finds claude.exe or claude.cmd on Windows
    if not exe:
        raise RuntimeError("claude not found on PATH")
    cmd = [exe, "-p", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    with tempfile.TemporaryDirectory() as d:  # empty cwd: no project files for it to read
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=240, cwd=d, env=env)
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


def heuristic(cand: dict, cfg: dict) -> dict:
    p, d = cand["peak_offset"], cand["duration"]
    return {"keep": True, "start": max(0.0, p - 15), "end": min(d, p + 10),
            "hook_text": "OLHA ISSO" if cfg["clipper"].get("language") == "pt" else "WAIT FOR IT",
            "title": f"{cand['streamer']} no GTA 6", "hashtags": cfg["clipper"]["default_hashtags"],
            "reason": "heuristic (no LLM)", "confidence": 0.3, "llm": "none"}


def sanitize(plan: dict, cand: dict, cfg: dict) -> dict:
    d, p = cand["duration"], cand["peak_offset"]
    s = float(plan.get("start", p - 15))
    e = float(plan.get("end", p + 10))
    s, e = max(0.0, min(s, d - 5)), max(0.0, min(e, d))
    if e - s < 8 or e - s > 59:
        s, e = max(0.0, p - 15), min(d, p + 10)
    plan["start"], plan["end"] = round(s, 2), round(e, 2)
    tags = plan.get("hashtags") or []
    tags = [t if t.startswith("#") else f"#{t}" for t in tags][:5]
    if "#gta6" not in [t.lower() for t in tags]:
        tags.insert(0, "#gta6")
    plan["hashtags"] = tags
    plan["hook_text"] = (plan.get("hook_text") or "").upper()[:40]
    plan["title"] = (plan.get("title") or f"{cand['streamer']} GTA 6")[:100]
    return plan


def make_plan(cand: dict, tr: dict, cfg: dict) -> dict:
    c = cfg["clipper"]
    lang = c.get("language", "pt")
    transcript = "\n".join(f"[{s['s']:.1f}-{s['e']:.1f}] {s['text']}" for s in tr["segments"]) or "(no speech)"
    chat = "\n".join(f"[{m['t']:.1f}] {m['user']}: {m['text']}" for m in cand.get("chat_sample", [])) or "(none)"
    prompt = PROMPT.format(lang_name=LANG.get(lang, lang), streamer=cand["streamer"], duration=cand["duration"],
                           peak=cand["peak_offset"], score=cand["score"], z_chat=cand["z_chat"],
                           z_audio=cand["z_audio"], transcript=transcript, chat=chat)
    backend = "none" if cand.get("demo") else c.get("llm", "claude")  # demo clips never spend Claude usage
    try:
        if backend == "claude":
            plan = _claude(prompt, c.get("claude_model", ""))
        elif backend == "ollama":
            plan = _ollama(prompt, c["ollama_url"], c["ollama_model"])
        else:
            plan = heuristic(cand, cfg)
        plan["llm"] = backend
    except Exception as e:  # noqa: BLE001
        log(f"[plan] {backend} failed: {e}", "warn")
        if backend == "claude" and c.get("ollama_model"):
            try:
                plan = _ollama(prompt, c["ollama_url"], c["ollama_model"])
                plan["llm"] = "ollama-fallback"
            except Exception as e2:  # noqa: BLE001
                log(f"[plan] ollama fallback failed: {e2}", "warn")
                plan = heuristic(cand, cfg)
        else:
            plan = heuristic(cand, cfg)
    return sanitize(plan, cand, cfg)
