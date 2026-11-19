"""Sound-effects library.

Sources, in order of preference:
  1. Your own sounds in <data_dir>/sfx/ (uploaded in the app, or copied there; .zip packs are unpacked).
     Name a file after a sound in CATALOG (e.g. "vine-boom.mp3") and the AI automatically knows when to use it.
  2. A few real recorded CC0 sounds bundled with the app (Kenney, public domain) - clicks, pops, glitches.
  3. Basic synthesized fallbacks (boom, whoosh, ...), used when you have nothing better.

Per-sound settings (description the AI reads, enabled, loudness gain) live in <data_dir>/sfx/library.json.
Every sound is loudness-matched once (peak to -1 dBFS) so effects sit at a consistent level in the mix.
"""
import json
import re
import shutil
import subprocess
import threading
import wave
import zipfile
from pathlib import Path

import numpy as np

SR = 48000
AUDIO_EXT = (".wav", ".mp3", ".ogg", ".m4a", ".flac", ".aac", ".opus")
PACK_DIR = Path(__file__).resolve().parent / "sfx_pack"
_lock = threading.Lock()

# The sounds most short-form editors reach for. key -> (when to use it, search words)
CATALOG = {
    "vine-boom": ("deep 'boom' on a punchline, reveal or awkward stare", "vine boom"),
    "bruh": ("deadpan 'bruh' after something dumb or disappointing", "bruh sound effect"),
    "record-scratch": ("'wait, what?' moment / plan suddenly going wrong", "record scratch"),
    "airhorn": ("hype: insane play, big win", "air horn"),
    "sad-violin": ("failure, sad or pathetic moment", "sad violin"),
    "rimshot": ("bad joke / pun landed (ba dum tss)", "rimshot drum"),
    "metal-pipe": ("loud comedic clang when something falls or hits", "metal pipe falling"),
    "bonk": ("someone gets hit or does something dumb", "bonk"),
    "boing": ("cartoon bounce, fall, jump", "cartoon boing"),
    "whoosh": ("fast transition, zoom-in, something flying past", "whoosh transition"),
    "cinematic-hit": ("big dramatic impact, crash", "cinematic impact hit"),
    "explosion": ("explosions", "explosion short"),
    "tire-screech": ("car drift, hard braking, near crash", "tire screech"),
    "police-siren": ("cops show up / crime moment", "police siren short"),
    "cash-register": ("money, heist, loot ('cha-ching')", "cash register cha ching"),
    "crowd-laugh": ("genuinely funny moment (laugh track)", "audience laughing"),
    "crowd-ooh": ("risky or painful moment ('ooooh')", "crowd oooh"),
    "crickets": ("awkward silence, nobody reacts", "crickets awkward silence"),
    "dramatic-sting": ("shocking reveal (dun dun duuun)", "dramatic sting"),
    "suspense-riser": ("tension build-up right before the payoff", "suspense riser"),
    "heartbeat": ("tense, nervous moment", "heartbeat"),
    "camera-shutter": ("freeze-frame / 'caught in 4K'", "camera shutter"),
    "wrong-buzzer": ("mistake, wrong answer", "wrong answer buzzer"),
    "correct-ding": ("smart move, correct answer", "correct answer ding"),
    "glitch": ("something bugs out / absurd", "glitch"),
    "slow-motion": ("dramatic slow-down moment", "slow motion sound effect"),
}

KENNEY = {
    "ui-bong": "short bright 'bong' - text or callout appearing",
    "ui-correct": "real 'correct' chime - smart move, success",
    "ui-wrong": "real error blip - mistake, fail",
    "ui-glitch": "short digital glitch - something bugs out",
    "ui-pop": "real soft pop - text appearing, light emphasis",
    "ui-click": "tiny click - subtle emphasis",
    "ui-glass": "glassy tick - small detail, subtle",
}

SYNTH = {
    "boom": "basic synthesized boom (fallback) - big reveals, fails, crashes",
    "impact": "basic synthesized hit (fallback) - collisions, sudden stops",
    "whoosh": "basic synthesized swoosh (fallback) - transitions, zoom-ins",
    "pop": "basic synthesized pop (fallback) - text appearing",
    "ding": "basic synthesized bell (fallback) - clever moment",
    "riser": "basic synthesized riser (fallback) - build-up",
    "glitch": "basic synthesized glitch (fallback)",
    "scratch": "basic synthesized scratch (fallback) - plan going wrong",
    "dun_dun": "basic synthesized dramatic sting (fallback)",
    "fail": "basic synthesized sad trombone (fallback) - failure",
}

PIXABAY = "https://pixabay.com/sound-effects/search/{q}/"
FREESOUND = 'https://freesound.org/search/?q={q}&f=license:"Creative+Commons+0"'
# Whole libraries / packs worth browsing (free for monetized videos, no credit needed - check each license)
SOURCES = [
    {"name": "Pixabay sound effects", "url": "https://pixabay.com/sound-effects/",
     "note": "huge library incl. many meme-style sounds; free, no credit"},
    {"name": "Mixkit sound effects", "url": "https://mixkit.co/free-sound-effects/",
     "note": "curated transitions, whooshes, impacts, game sounds; free, no credit"},
    {"name": "Freesound (CC0 filter)", "url": 'https://freesound.org/search/?f=license:"Creative+Commons+0"',
     "note": "anything you can think of; use the CC0 filter so no credit is needed"},
    {"name": "Sonniss GDC bundles", "url": "https://gdc.sonniss.com/",
     "note": "gigabytes of professional cinematic SFX (impacts, whooshes, explosions, cars); royalty-free"},
    {"name": "YouTube Audio Library", "url": "https://studio.youtube.com/",
     "note": "Studio → Audio library → Sound effects; Content ID-safe on YouTube (check before using on other apps)"},
]


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
    elif name == "dun_dun":
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


def library(data_dir: Path, include_disabled=False) -> dict[str, dict]:
    """name -> {path, desc, source, enabled, gain_db}. User sounds override bundled ones with the same name."""
    with _lock:
        sfx_dir = data_dir / "sfx"
        synth_dir = sfx_dir / "_builtin"
        synth_dir.mkdir(parents=True, exist_ok=True)
        meta = load_meta(data_dir)
        lib = {}
        for name, desc in SYNTH.items():
            p = synth_dir / f"{name}.wav"
            if not p.exists():
                _write_wav(p, synth(name))
            lib[name] = {"path": p, "desc": desc, "source": "synth"}
        for name, desc in KENNEY.items():
            p = PACK_DIR / "kenney" / f"{name}.wav"
            if p.exists():
                lib[name] = {"path": p, "desc": desc, "source": "bundled (Kenney, CC0)"}
        for p in sorted(sfx_dir.glob("*")):
            if p.is_file() and p.suffix.lower() in AUDIO_EXT:
                name = slug(p.stem)
                lib[name] = {"path": p, "desc": CATALOG.get(name, ("custom sound - judge by its name", ""))[0],
                             "source": "yours"}
        changed = False
        for name, item in lib.items():
            m = meta.setdefault(name, {})
            if "gain_db" not in m or m.get("_file") != item["path"].name:
                m["gain_db"] = _peak_gain_db(item["path"])
                m["_file"] = item["path"].name
                changed = True
            if m.get("desc"):
                item["desc"] = m["desc"]
            # synthesized fallbacks switch themselves off once you have 5+ real sounds of your own
            default_on = item["source"] != "synth" or sum(1 for v in lib.values() if v["source"] == "yours") < 5
            item["enabled"] = m.get("enabled", default_on)
            item["gain_db"] = m["gain_db"]
        if changed:
            save_meta(data_dir, meta)
        return lib if include_disabled else {k: v for k, v in lib.items() if v["enabled"]}


def update_meta(data_dir: Path, name: str, **fields):
    with _lock:
        meta = load_meta(data_dir)
        meta.setdefault(name, {}).update({k: v for k, v in fields.items() if v is not None})
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
    """Delete one of your own sounds (bundled ones can only be disabled)."""
    for p in (data_dir / "sfx").glob("*"):
        if p.is_file() and p.suffix.lower() in AUDIO_EXT and slug(p.stem) == name:
            p.unlink()
            return True
    return False


def starter_list(data_dir: Path) -> list[dict]:
    have = {k for k, v in library(data_dir, include_disabled=True).items() if v["source"] != "synth"}
    out = []
    for key, (use, q) in CATALOG.items():
        qq = q.replace(" ", "%20")
        out.append({"name": key, "use": use, "have": key in have,
                    "pixabay": PIXABAY.format(q=qq), "freesound": FREESOUND.format(q=q.replace(" ", "+"))})
    return out
