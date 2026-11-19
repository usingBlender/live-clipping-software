"""Sound-effects library. Nothing is installed by default: you opt in to the packs you want.

Where sounds come from (all from inside the app, Settings -> Sounds):
  * Packs, one click each: "Classic YouTube sounds" (finds a CC0 version of each classic sound - vine boom,
    bruh, record scratch... - on Freesound / Wikimedia via Openverse), Kenney's real recorded UI sounds (CC0),
    and basic synthesized sounds.
  * Search: Openverse (Freesound + Wikimedia Commons, no account needed) and Freesound directly (free API key),
    filtered to CC0 by default; preview and add any result with one click.
  * Your own files: upload single files or .zip packs (e.g. from Pixabay or Mixkit), or copy them into
    <data_dir>/sfx/.

Packs live in <data_dir>/sfx/<pack-id>/, your files in <data_dir>/sfx/. Per-sound settings (description the AI
reads, enabled, gain, license/credit) live in <data_dir>/sfx/library.json. Every sound is leveled once
(peak to -1 dBFS) so effects sit at a consistent level in the mix.
"""
import json
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import wave
import zipfile
from pathlib import Path

import numpy as np

SR = 48000
AUDIO_EXT = (".wav", ".mp3", ".ogg", ".m4a", ".flac", ".aac", ".opus")
_lock = threading.Lock()

# The sounds most short-form editors reach for. key -> (when to use it, search words)
CATALOG = {
    "vine-boom": ("deep 'boom' on a punchline, reveal or awkward stare", "vine boom"),
    "bruh": ("deadpan 'bruh' after something dumb or disappointing", "bruh"),
    "record-scratch": ("'wait, what?' moment / plan suddenly going wrong", "record scratch"),
    "airhorn": ("hype: insane play, big win", "air horn"),
    "sad-violin": ("failure, sad or pathetic moment", "sad violin"),
    "rimshot": ("bad joke / pun landed (ba dum tss)", "rimshot"),
    "metal-pipe": ("loud comedic clang when something falls or hits", "metal pipe clang"),
    "bonk": ("someone gets hit or does something dumb", "bonk"),
    "boing": ("cartoon bounce, fall, jump", "boing"),
    "whoosh": ("fast transition, zoom-in, something flying past", "whoosh"),
    "cinematic-hit": ("big dramatic impact, crash", "cinematic impact"),
    "explosion": ("explosions", "explosion"),
    "tire-screech": ("car drift, hard braking, near crash", "tire screech"),
    "police-siren": ("cops show up / crime moment", "police siren"),
    "cash-register": ("money, heist, loot ('cha-ching')", "cash register"),
    "crowd-laugh": ("genuinely funny moment (laugh track)", "crowd laughing"),
    "crowd-ooh": ("risky or painful moment ('ooooh')", "crowd ooh"),
    "crickets": ("awkward silence, nobody reacts", "crickets"),
    "dramatic-sting": ("shocking reveal (dun dun duuun)", "dramatic sting"),
    "suspense-riser": ("tension build-up right before the payoff", "riser"),
    "heartbeat": ("tense, nervous moment", "heartbeat"),
    "camera-shutter": ("freeze-frame / 'caught in 4K'", "camera shutter"),
    "wrong-buzzer": ("mistake, wrong answer", "wrong buzzer"),
    "correct-ding": ("smart move, correct answer", "correct ding"),
    "glitch": ("something bugs out / absurd", "glitch"),
    "slow-motion": ("dramatic slow-down moment", "slow motion"),
}

SYNTH = {
    "boom": "basic synthesized boom - big reveals, fails, crashes",
    "impact": "basic synthesized hit - collisions, sudden stops",
    "whoosh": "basic synthesized swoosh - transitions, zoom-ins",
    "pop": "basic synthesized pop - text appearing",
    "ding": "basic synthesized bell - clever moment",
    "riser": "basic synthesized riser - build-up",
    "glitch": "basic synthesized glitch",
    "scratch": "basic synthesized scratch - plan going wrong",
    "dun-dun": "basic synthesized dramatic sting",
    "fail": "basic synthesized sad trombone - failure",
}

GH_ZIP = "https://codeload.github.com/Calinou/{repo}/zip/refs/heads/master"
PACKS = {
    "classic-youtube": {
        "name": "Classic YouTube sounds", "kind": "auto", "license": "CC0 / public domain",
        "desc": "Finds a free CC0 version of each of the 26 classic sounds (vine boom, bruh, record scratch, "
                "airhorn, sad violin...) on Freesound / Wikimedia Commons. Listen to the picks and replace any "
                "you don't like with Search."},
    "kenney-interface": {
        "name": "Kenney Interface Sounds", "kind": "zip", "license": "CC0",
        "url": GH_ZIP.format(repo="kenney-interface-sounds"),
        "desc": "Real recorded clicks, pops, bongs, glitches, confirmations and error blips (one of each kind)."},
    "kenney-ui-audio": {
        "name": "Kenney UI Audio", "kind": "zip", "license": "CC0",
        "url": GH_ZIP.format(repo="kenney-ui-audio"),
        "desc": "Real recorded switches, clicks and rollovers - subtle emphasis for texts and zooms."},
    "basic-synth": {
        "name": "Basic synthesized", "kind": "synth", "license": "made by the app",
        "desc": "Simple generated boom, whoosh, pop, ding... They sound synthetic - only if you have nothing else."},
}
KENNEY_DESC = {
    "click": "tiny real click - subtle emphasis", "confirmation": "real success chime - smart move, it worked",
    "error": "real error blip - mistake, fail", "glitch": "short digital glitch - something bugs out",
    "bong": "bright 'bong' - text or callout appearing", "pluck": "soft pluck - light emphasis",
    "drop": "small drop/pop - something lands", "scratch": "short scratch - plan going wrong",
    "question": "curious blip - 'huh?' moment", "tick": "tick - small detail", "toggle": "toggle click",
    "switch": "switch click - subtle emphasis", "rollover": "soft rollover blip", "select": "select blip",
    "maximize": "rising blip - zoom-in", "minimize": "falling blip - zoom-out", "open": "opening blip",
    "close": "closing blip", "back": "back blip", "scroll": "scroll tick", "mouseclick": "mouse click",
    "mouserelease": "mouse release click", "glass": "glassy tick - small detail",
}

PIXABAY = "https://pixabay.com/sound-effects/search/{q}/"
# Libraries without a download API: open them in the browser, then upload the files (or a .zip) here.
SOURCES = [
    {"name": "Pixabay sound effects", "url": "https://pixabay.com/sound-effects/",
     "note": "huge library incl. meme-style remakes; free, no credit. Download, then Upload here"},
    {"name": "Mixkit sound effects", "url": "https://mixkit.co/free-sound-effects/",
     "note": "curated whooshes, impacts, game sounds; free, no credit. Download, then Upload here"},
    {"name": "Sonniss GDC bundles", "url": "https://gdc.sonniss.com/",
     "note": "gigabytes of professional cinematic SFX; royalty-free. Upload the folders you want as .zip"},
    {"name": "YouTube Audio Library", "url": "https://studio.youtube.com/",
     "note": "Studio -> Audio library -> Sound effects; safe on YouTube (check terms before other apps)"},
]

# ---------------------------------------------------------------- network (allow-listed hosts only)
UA = "LiveClipper (+https://github.com/usingBlender/live-clipping-software)"
ALLOWED_HOSTS = ("openverse.org", "freesound.org", "wikimedia.org", "github.com", "githubusercontent.com",
                 "ccmixter.org", "jamendo.com")


def host_ok(url: str) -> bool:
    p = urllib.parse.urlparse(url)
    h = (p.hostname or "").lower()
    return p.scheme == "https" and any(h == a or h.endswith("." + a) for a in ALLOWED_HOSTS)


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not host_ok(newurl):
            raise ValueError(f"refusing redirect to {urllib.parse.urlparse(newurl).hostname}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_Redirect)


def fetch(url: str, limit: int = 25_000_000, timeout: int = 30) -> bytes:
    if not host_ok(url):
        raise ValueError(f"host not allowed: {urllib.parse.urlparse(url).hostname}")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with _opener.open(req, timeout=timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("file too large")
    return data


def _license(code: str) -> str:
    c = (code or "").lower()
    if "zero" in c or c in ("cc0", "pdm") or "publicdomain" in c:
        return "cc0"
    if "nc" in c.replace("license", "") or "sampling" in c:
        return "nc"
    if "by-sa" in c or "by_sa" in c:
        return "by-sa"
    if "by" in c or "attribution" in c:
        return "by"
    return "unknown"


def _openverse(q: str, allow_attribution: bool) -> list[dict]:
    params = {"q": q, "category": "sound_effect", "page_size": "20", "filter_dead": "true"}
    if allow_attribution:
        params["license_type"] = "commercial"
    else:
        params["license"] = "cc0,pdm"
    data = json.loads(fetch("https://api.openverse.org/v1/audio/?" + urllib.parse.urlencode(params), 5_000_000))
    out = []
    for r in data.get("results", []):
        lic = _license(r.get("license"))
        if lic == "nc" or not r.get("url"):
            continue
        out.append({"provider": f"Openverse / {r.get('source') or r.get('provider') or '?'}",
                    "title": r.get("title") or "untitled", "creator": r.get("creator") or "",
                    "license": lic, "duration": round((r.get("duration") or 0) / 1000, 1),
                    "preview": r["url"], "page": r.get("foreign_landing_url") or ""})
    return out


def _freesound(q: str, key: str, allow_attribution: bool) -> list[dict]:
    lic = '("Creative Commons 0" OR "Attribution")' if allow_attribution else '"Creative Commons 0"'
    params = {"query": q, "filter": f"license:{lic} duration:[0 TO 30]", "page_size": "20", "token": key,
              "fields": "id,name,username,license,duration,previews,url", "sort": "score"}
    data = json.loads(fetch("https://freesound.org/apiv2/search/text/?" + urllib.parse.urlencode(params), 5_000_000))
    out = []
    for r in data.get("results", []):
        l = _license(r.get("license"))
        prev = (r.get("previews") or {}).get("preview-hq-mp3")
        if l == "nc" or not prev:
            continue
        out.append({"provider": "Freesound", "title": r.get("name") or "untitled", "creator": r.get("username") or "",
                    "license": l, "duration": round(float(r.get("duration") or 0), 1), "preview": prev,
                    "page": r.get("url") or ""})
    return out


def search(q: str, freesound_key: str = "", allow_attribution: bool = False) -> dict:
    """Search every source that has an API. Returns {results: [...], errors: [...]}."""
    results, errors = [], []
    if freesound_key:
        try:
            results += _freesound(q, freesound_key, allow_attribution)
        except Exception as e:  # noqa: BLE001
            errors.append(f"Freesound: {e}")
    try:
        results += _openverse(q, allow_attribution)
    except Exception as e:  # noqa: BLE001
        errors.append(f"Openverse: {e}")
    seen, out = set(), []
    for r in results:
        k = (r["title"].lower(), r["creator"].lower())
        if k in seen or r["duration"] > 30:
            continue
        seen.add(k)
        out.append(r)
    return {"results": out[:40], "errors": errors}


def credit_line(m: dict) -> str:
    if m.get("license") in ("by", "by-sa"):
        return f'"{m.get("title", "?")}" by {m.get("creator") or "unknown"} ({m["license"].upper()}) {m.get("page", "")}'.strip()
    return ""


def _ext_from_url(url: str) -> str:
    ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
    return ext if ext in AUDIO_EXT else ".mp3"


def add_from_web(data_dir: Path, item: dict, name: str = "", desc: str = "", folder: str = "") -> str:
    """Download a search result into the library. Returns the sound's name."""
    if item.get("license") not in ("cc0", "by", "by-sa"):
        raise ValueError("only CC0 / CC-BY sounds can be added")
    data = fetch(item["preview"])
    name = slug(name or item.get("title") or "sound")
    d = data_dir / "sfx" / folder if folder else data_dir / "sfx"
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob(f"{name}.*"):
        old.unlink()
    (d / f"{name}{_ext_from_url(item['preview'])}").write_bytes(data)
    update_meta(data_dir, name, desc_default=desc or CATALOG.get(name, (f"{item.get('title', name)}",))[0],
                license=item["license"], creator=item.get("creator", ""), page=item.get("page", ""),
                title=item.get("title", ""), provider=item.get("provider", ""), pack=folder or None, _file=None)
    return name


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:48] or "sound"


# ---------------------------------------------------------------- synthesized fallbacks
def _env(n, attack=0.005, release=None):
    t = np.arange(n) / SR
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    r = np.exp(-t / release) if release else 1.0
    return a * r


def _noise(n, seed=1):
    return np.random.default_rng(seed).standard_normal(n)


def _bandpass(x, lo, hi):
    f = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    f[(freqs < lo) | (freqs > hi)] = 0
    return np.fft.irfft(f, len(x))


def _sweep(f0, f1, dur):
    n = int(SR * dur)
    t = np.arange(n) / SR
    f = f0 * (f1 / f0) ** (t / dur)
    return np.sin(2 * np.pi * np.cumsum(f) / SR)


def _tone(freq, dur, decay, harmonics=(1.0, 0.5, 0.25)):
    n = int(SR * dur)
    t = np.arange(n) / SR
    x = sum(a * np.sin(2 * np.pi * freq * (k + 1) * t) for k, a in enumerate(harmonics))
    return x * _env(n, 0.003, decay)


def synth(name: str) -> np.ndarray:
    if name == "boom":
        n = int(SR * 1.6)
        x = _sweep(110, 32, 1.6) * _env(n, 0.004, 0.45) + 0.35 * _bandpass(_noise(n), 30, 300) * _env(n, 0.002, 0.12)
    elif name == "impact":
        n = int(SR * 0.7)
        x = 0.8 * _bandpass(_noise(n, 2), 60, 4000) * _env(n, 0.001, 0.06) + _sweep(90, 40, 0.7) * _env(n, 0.002, 0.18)
    elif name == "whoosh":
        n = int(SR * 0.7)
        t = np.arange(n) / n
        x = _bandpass(_noise(n, 3), 300, 5000) * np.sin(np.pi * t) ** 2
    elif name == "pop":
        x = _sweep(900, 300, 0.09) * _env(int(SR * 0.09), 0.001, 0.025)
    elif name == "ding":
        x = _tone(1568, 1.4, 0.45, (1, 0.3, 0.15)) + 0.6 * _tone(2093, 1.4, 0.35, (1, 0.2))
    elif name == "riser":
        n = int(SR * 1.6)
        t = np.arange(n) / n
        x = (0.6 * _sweep(180, 1400, 1.6) + 0.5 * _bandpass(_noise(n, 4), 800, 8000)) * t ** 2
    elif name == "glitch":
        rng = np.random.default_rng(5)
        parts = []
        for _ in range(9):
            k = int(SR * rng.uniform(0.02, 0.06))
            f = rng.choice([220, 440, 880, 1760, 3520])
            parts.append(np.sign(np.sin(2 * np.pi * f * np.arange(k) / SR)) * rng.uniform(0.3, 0.8))
            parts.append(np.zeros(int(SR * rng.uniform(0.0, 0.02))))
        x = np.concatenate(parts)
    elif name == "scratch":
        a = _sweep(1200, 250, 0.18) * 0.7 + 0.3 * _bandpass(_noise(int(SR * 0.18), 6), 500, 6000)
        b = _sweep(300, 1500, 0.12) * 0.7 + 0.3 * _bandpass(_noise(int(SR * 0.12), 7), 500, 6000)
        x = np.concatenate([a, b]) * 0.8
    elif name == "dun-dun":
        notes = [(196.0, 0.32), (185.0, 0.32), (155.6, 1.3)]
        x = np.concatenate([_tone(f, d, d * 0.8, (1, 0.6, 0.4, 0.2)) for f, d in notes])
    elif name == "fail":
        parts = []
        for f, d in [(392.0, 0.35), (370.0, 0.35), (349.2, 0.35), (329.6, 1.0)]:
            n = int(SR * d)
            t = np.arange(n) / SR
            vib = 1 + (0.012 * np.sin(2 * np.pi * 6 * t) if d > 0.5 else 0)
            parts.append(np.sign(np.sin(2 * np.pi * f * vib * t)) * 0.25 * _env(n, 0.02, d))
        x = _bandpass(np.concatenate(parts), 80, 3000)
    else:
        raise KeyError(name)
    x = x / (np.max(np.abs(x)) + 1e-9) * 0.9
    return x.astype(np.float32)


def _write_wav(path: Path, x: np.ndarray):
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())



# ---------------------------------------------------------------- metadata + loudness
def _meta_path(data_dir: Path) -> Path:
    return data_dir / "sfx" / "library.json"


def load_meta(data_dir: Path) -> dict:
    p = _meta_path(data_dir)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_meta(data_dir: Path, meta: dict):
    p = _meta_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _peak_gain_db(path: Path) -> float:
    """Gain that brings the file's peak to -1 dBFS (so quiet and loud downloads mix at the same level)."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"max_volume:\s*(-?[\d.]+) dB", r.stderr or "")
    if not m:
        return 0.0
    return round(max(-12.0, min(24.0, -1.0 - float(m.group(1)))), 1)



def _sources(data_dir: Path):
    """(name, path, source label, pack id) in priority order: your files, then packs."""
    sfx_dir = data_dir / "sfx"
    for p in sorted(sfx_dir.iterdir()):
        if p.is_file() and p.suffix.lower() in AUDIO_EXT:
            yield slug(p.stem), p, "yours", None
    for pid, pack in PACKS.items():
        d = sfx_dir / pid
        if d.is_dir():
            for p in sorted(d.iterdir()):
                if p.is_file() and p.suffix.lower() in AUDIO_EXT:
                    yield slug(p.stem), p, pack["name"], pid


def library(data_dir: Path, include_disabled=False) -> dict[str, dict]:
    """name -> {path, desc, source, pack, enabled, gain_db, license, credit}."""
    with _lock:
        (data_dir / "sfx").mkdir(parents=True, exist_ok=True)
        meta = load_meta(data_dir)
        lib, changed = {}, False
        for name, p, source, pid in _sources(data_dir):
            if name in lib:
                continue
            m = meta.setdefault(name, {})
            if "gain_db" not in m or m.get("_file") != p.name:
                m["gain_db"] = _peak_gain_db(p)
                m["_file"] = p.name
                changed = True
            desc = m.get("desc") or m.get("desc_default") or CATALOG.get(name, ("",))[0] or SYNTH.get(name) \
                or "custom sound - judge by its name"
            lic = m.get("license") or ("synth" if pid == "basic-synth" else "cc0" if pid else "yours")
            lib[name] = {"path": p, "desc": desc, "source": source, "pack": pid, "enabled": m.get("enabled", True),
                         "gain_db": m["gain_db"], "license": lic, "credit": credit_line(m),
                         "page": m.get("page", "")}
        if changed:
            save_meta(data_dir, meta)
        return lib if include_disabled else {k: v for k, v in lib.items() if v["enabled"]}


def update_meta(data_dir: Path, name: str, **fields):
    with _lock:
        meta = load_meta(data_dir)
        m = meta.setdefault(name, {})
        for k, v in fields.items():
            if v is None and k == "_file":
                m.pop("_file", None)     # force re-leveling
            elif v is not None:
                m[k] = v
        save_meta(data_dir, meta)


def import_file(data_dir: Path, filename: str, data: bytes) -> list[str]:
    """Save an uploaded sound (or every sound inside an uploaded .zip). Returns the names added."""
    sfx_dir = data_dir / "sfx"
    sfx_dir.mkdir(parents=True, exist_ok=True)
    added = []
    if filename.lower().endswith(".zip"):
        tmp = sfx_dir / "_upload.zip"
        tmp.write_bytes(data)
        try:
            with zipfile.ZipFile(tmp) as z:
                for info in z.infolist():
                    base = Path(info.filename).name
                    if info.is_dir() or not base.lower().endswith(AUDIO_EXT) or base.startswith("._"):
                        continue
                    if info.file_size > 25_000_000:
                        continue
                    name = slug(Path(base).stem)
                    dst = sfx_dir / f"{name}{Path(base).suffix.lower()}"
                    with z.open(info) as src, open(dst, "wb") as out:
                        shutil.copyfileobj(src, out)
                    added.append(name)
        finally:
            tmp.unlink(missing_ok=True)
    else:
        ext = Path(filename).suffix.lower()
        if ext not in AUDIO_EXT:
            raise ValueError(f"not an audio file: {filename}")
        name = slug(Path(filename).stem)
        (sfx_dir / f"{name}{ext}").write_bytes(data)
        added.append(name)
    return added


def remove(data_dir: Path, name: str) -> bool:
    """Delete one sound (your file or a pack's file)."""
    for n, p, _src, _pid in list(_sources(data_dir)):
        if n == name:
            p.unlink()
            with _lock:
                meta = load_meta(data_dir)
                meta.pop(name, None)
                save_meta(data_dir, meta)
            return True
    return False


# ---------------------------------------------------------------- packs
JOBS: dict[str, dict] = {}   # pack id -> {state, done, total, msg}


def _kenney_install(data_dir: Path, pid: str, data: bytes, job: dict):
    """Keep one file per sound family (click_001..click_005 -> ui-click) so the AI's list stays short."""
    d = data_dir / "sfx" / pid
    best = {}
    tmp = data_dir / "sfx" / f"_{pid}.zip"
    tmp.write_bytes(data)
    try:
        with zipfile.ZipFile(tmp) as z:
            for info in z.infolist():
                base = Path(info.filename).name
                if info.is_dir() or not base.lower().endswith(AUDIO_EXT) or base.startswith("._"):
                    continue
                m = re.match(r"^(.*?)[-_ ]?(\d+)$", Path(base).stem)
                fam, num = (m.group(1), int(m.group(2))) if m else (Path(base).stem, 0)
                fam = slug(fam)
                if fam not in best or num < best[fam][0]:
                    best[fam] = (num, info)
            job["total"] = len(best)
            d.mkdir(parents=True, exist_ok=True)
            for fam, (_n, info) in best.items():
                name = slug(f"ui-{fam}")
                with z.open(info) as src, open(d / f"{name}{Path(info.filename).suffix.lower()}", "wb") as out:
                    shutil.copyfileobj(src, out)
                key = next((k for k in KENNEY_DESC if k in fam), None)
                update_meta(data_dir, name, desc_default=KENNEY_DESC[key] if key else f"short interface sound ({fam})",
                            license="cc0", creator="Kenney", page="https://kenney.nl", pack=pid, _file=None)
                job["done"] += 1
    finally:
        tmp.unlink(missing_ok=True)


def _auto_install(data_dir: Path, pid: str, freesound_key: str, job: dict):
    job["total"] = len(CATALOG)
    missing = []
    for key, (use, q) in CATALOG.items():
        try:
            res = search(q, freesound_key)["results"]
            pick = next((r for r in res if r["license"] == "cc0" and 0.2 <= r["duration"] <= 12), None)
            if pick:
                add_from_web(data_dir, pick, name=key, desc=use, folder=pid)
            else:
                missing.append(key)
        except Exception:  # noqa: BLE001
            missing.append(key)
        job["done"] += 1
        time.sleep(0.4)  # be gentle with the free APIs
    if missing:
        job["msg"] = "not found: " + ", ".join(missing) + " (use Search or Upload)"


def install_pack(data_dir: Path, pid: str, freesound_key: str = ""):
    """Runs in a background thread; progress in JOBS[pid]."""
    pack = PACKS[pid]
    job = JOBS[pid] = {"state": "running", "done": 0, "total": 0, "msg": ""}
    (data_dir / "sfx").mkdir(parents=True, exist_ok=True)
    try:
        if pack["kind"] == "synth":
            d = data_dir / "sfx" / pid
            d.mkdir(parents=True, exist_ok=True)
            job["total"] = len(SYNTH)
            for name, desc in SYNTH.items():
                _write_wav(d / f"{name}.wav", synth(name))
                update_meta(data_dir, name, desc_default=desc, license="synth", pack=pid, _file=None)
                job["done"] += 1
        elif pack["kind"] == "zip":
            job["msg"] = "downloading..."
            data = fetch(pack["url"], 200_000_000, timeout=120)
            job["msg"] = ""
            _kenney_install(data_dir, pid, data, job)
        else:
            _auto_install(data_dir, pid, freesound_key, job)
        job["state"] = "done"
    except Exception as e:  # noqa: BLE001
        job.update(state="error", msg=str(e)[:200])


def uninstall_pack(data_dir: Path, pid: str):
    d = data_dir / "sfx" / pid
    if pid in PACKS and d.is_dir():
        shutil.rmtree(d)
    with _lock:
        meta = load_meta(data_dir)
        for k in [k for k, v in meta.items() if isinstance(v, dict) and v.get("pack") == pid]:
            meta.pop(k)
        save_meta(data_dir, meta)
    JOBS.pop(pid, None)


def packs_view(data_dir: Path) -> list[dict]:
    out = []
    for pid, p in PACKS.items():
        d = data_dir / "sfx" / pid
        n = sum(1 for f in d.iterdir() if f.suffix.lower() in AUDIO_EXT) if d.is_dir() else 0
        out.append({"id": pid, "name": p["name"], "desc": p["desc"], "license": p["license"], "installed": n > 0,
                    "count": n, "job": JOBS.get(pid)})
    return out


def starter_list(data_dir: Path) -> list[dict]:
    have = {k for k, v in library(data_dir, include_disabled=True).items() if v["license"] != "synth"}
    return [{"name": key, "use": use, "query": q, "have": key in have,
             "pixabay": PIXABAY.format(q=q.replace(" ", "%20"))} for key, (use, q) in CATALOG.items()]
