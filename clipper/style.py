"""Learn an editing style from reference videos (YouTube/TikTok links or uploaded .mp4 files).

For each reference the app downloads it (yt-dlp), measures the pacing (hard cuts per minute), and makes two
contact sheets of 8 frames. Claude then looks at all the sheets and writes a short, concrete style guide
(hook, captions, callout texts, zooms, facecam use, sound habits, pacing, titles). The guide is saved in
Settings -> Clips & AI -> Style and given to the AI editor for every clip - as HOW to edit, never as content.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

from clipper import plan as planmod
from clipper import render as rendermod
from common.bus import log

PROMPT = """You are studying reference short-form videos to learn their EDITING STYLE, so an automatic editor
can imitate it on other clips. Read every image in your current folder ({files}). Each reference has two
contact sheets of 8 frames (first half and second half of the video, reading order, 4 per row), listed below.

References:
{refs}

Describe the editing style concretely, as rules another editor can follow. Cover:
- hook: is there a text at the start, how long, what kind of wording, where on screen
- captions: position, size, colours, highlight, words per line, which speech gets captions
- callout texts: how often, what they say (what happened / what went wrong / the cause / a reaction), length,
  position, colours, give 3-5 example phrasings of the TYPE of text (not copied from these videos)
- zooms and punch-ins: how often, on what (face, action), how strong
- facecam / layout: cam on top or not, does the cam take over the screen, when
- sound effects: how often and on which moments (you can't hear them - infer from visual cues only if obvious,
  otherwise say "unknown")
- pacing: cuts per minute (measured below), clip length
- titles: patterns in the titles above
Only describe what you can actually see or what is measured. Never describe the videos' content (people,
places, story) - only the editing. Max 250 words, bullet points, in English.
Reply with ONLY JSON: {{"style_guide": "<the bullet list as one string with \\n line breaks>"}}
"""


def _yt_dlp() -> list[str] | None:
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    try:
        import yt_dlp  # noqa: F401
        return [sys.executable, "-m", "yt_dlp"]
    except ImportError:
        return None


def download(url: str, out_dir: Path, k: int) -> tuple[Path, str]:
    cmd = _yt_dlp()
    if not cmd:
        raise RuntimeError("yt-dlp is not installed (pip install yt-dlp), or upload the videos as .mp4 files")
    out = out_dir / f"ref{k}.%(ext)s"
    r = subprocess.run(cmd + ["--no-playlist", "--max-filesize", "300M", "-f",
                              "b[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b", "--merge-output-format", "mp4",
                              "--print", "after_move:title", "--no-simulate", "-o", str(out), url],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    files = sorted(out_dir.glob(f"ref{k}.*"))
    if r.returncode != 0 or not files:
        raise RuntimeError((r.stderr or r.stdout or "download failed").strip().splitlines()[-1][:200])
    return files[0], (r.stdout.strip().splitlines() or [""])[-1]


def cuts_per_minute(path: Path, duration: float) -> float:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-an", "-vf",
                        "scale=320:-2,select='gt(scene,0.32)',metadata=print", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    n = len(re.findall(r"pts_time", r.stderr or ""))
    return round(n / max(duration, 1) * 60, 1)


def duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def learn(cfg: dict, urls: list[str], files: list[Path], job: dict) -> str:
    """Returns the style guide; progress goes into job (dict shown in the UI)."""
    work = cfg["data_dir"] / "style_refs"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    sources = [("url", u) for u in urls if u.strip()] + [("file", f) for f in files]
    if not sources:
        raise ValueError("add at least one link or video file")
    job.update(state="running", done=0, total=len(sources), msg="")
    images, refs, errors = [], [], []
    for k, (kind, src) in enumerate(sources, 1):
        try:
            job["msg"] = f"reference {k}: downloading" if kind == "url" else f"reference {k}: reading"
            if kind == "url":
                path, title = download(src, work, k)
            else:
                path, title = Path(src), Path(src).stem
            d = duration(path)
            if d <= 0:
                raise RuntimeError("could not read the video")
            job["msg"] = f"reference {k}: analysing"
            cpm = cuts_per_minute(path, d)
            for half in (0, 1):
                img = work / f"ref{len(images) + 1}.jpg"
                rendermod.frame_grid(str(path), img, d * half / 2, d * (half + 1) / 2, n=8)
                images.append(img)
            refs.append(f"{k}. title: \"{title}\" | length {d:.0f}s | {cpm} hard cuts per minute | "
                        f"sheets ref{len(images) - 1}.jpg + ref{len(images)}.jpg")
        except Exception as e:  # noqa: BLE001
            errors.append(f"reference {k}: {e}")
            log(f"[style] reference {k} failed: {e}", "warn")
        job["done"] = k
    if not images:
        raise RuntimeError("; ".join(errors) or "no usable reference")
    job["msg"] = "Claude is studying the references"
    out = planmod._claude(PROMPT.format(files=", ".join(p.name for p in images), refs="\n".join(refs)),
                          cfg["clipper"].get("claude_model", ""), images, timeout=600)
    guide = str(out.get("style_guide", "")).strip()
    if not guide:
        raise RuntimeError("Claude returned no style guide")
    if errors:
        job["msg"] = "done, but " + "; ".join(errors)
    return guide[:3000]
