"""9:16 render: layout + word-by-word captions + hook + loudness normalization (NVENC)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

W, H = 1080, 1920
CAM_H = 768                 # cam_top: facecam band height; gameplay gets the remaining 1152 px
SAFE_CAPTION_Y = {"cam_top": 1330, "fit_blur": 1400}   # above TikTok/Reels bottom UI
HOOK_Y = {"cam_top": CAM_H, "fit_blur": 470}


def probe(path) -> tuple[int, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    return int(s["width"]), int(s["height"])


def _ffmpeg(cmd, what, cwd=None):
    """Run ffmpeg; on failure raise a short, readable error (last lines of ffmpeg's output)."""
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd)
    if r.returncode != 0:
        tail = " ".join((r.stderr or "").strip().splitlines()[-2:])[:240]
        raise RuntimeError(f"{what} failed: {tail or 'ffmpeg exit code ' + str(r.returncode)}")


def _even(x):
    return int(x) // 2 * 2


def layout_filter(s: dict, sw: int, sh: int) -> str:
    zoom = float(s.get("game_zoom", 1.0))
    if s.get("layout", "cam_top") == "cam_top":
        cam = s["cam"]
        cx, cy = _even(cam["x"] * sw), _even(cam["y"] * sh)
        cw, ch = _even(cam["w"] * sw), _even(cam["h"] * sh)
        gh = _even(sh / zoom)
        gw = _even(min(sw, gh * W / (H - CAM_H)))
        gx = _even(min(max(0, s.get("game_center_x", 0.5) * sw - gw / 2), sw - gw))
        gy = _even((sh - gh) / 2)
        return (f"[0:v]split=2[a][b];"
                f"[a]crop={cw}:{ch}:{cx}:{cy},scale={W}:{CAM_H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{CAM_H},setsar=1[cam];"
                f"[b]crop={gw}:{gh}:{gx}:{gy},scale={W}:{H - CAM_H},setsar=1[game];"
                f"[cam][game]vstack=inputs=2[base]")
    # fit_blur
    cw, ch = _even(sw / zoom), _even(sh / zoom)
    return (f"[0:v]crop={cw}:{ch},split=2[a][b];"
            f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:4,setsar=1[bg];"
            f"[b]scale={W}:-2,setsar=1[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[base]")


# ---------------------------------------------------------------- captions
def _ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _clean(word: str) -> str:
    w = re.sub(r"[{}\\]", "", word).strip()
    return w.strip(",.;:").upper()


def group_words(words, max_words=3, max_chars=16, gap=0.5):
    groups, cur = [], []
    for w in words:
        if not w["w"]:
            continue
        if cur and (len(cur) >= max_words or sum(len(x["w"]) for x in cur) + len(w["w"]) > max_chars
                    or w["s"] - cur[-1]["e"] > gap):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def build_ass(words, hook: str, layout: str, font: str, clip_len: float) -> str:
    cy, hy = SAFE_CAPTION_Y[layout], HOOK_Y[layout]
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},88,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,3,5,60,60,0,1
Style: Hook,{font},70,&H00000000,&H00FFFFFF,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,16,0,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    if hook:
        ev.append(f"Dialogue: 2,{_ts(0)},{_ts(min(2.6, clip_len))},Hook,,0,0,0,,"
                  f"{{\\pos(540,{hy})\\fad(0,200)}}{_clean(hook)}")
    groups = group_words(words)
    for gi, g in enumerate(groups):
        nxt = groups[gi + 1][0]["s"] if gi + 1 < len(groups) else None
        g_end = g[-1]["e"] if nxt is None or nxt - g[-1]["e"] > 0.3 else nxt
        for i, w in enumerate(g):
            s = w["s"]
            e = g[i + 1]["s"] if i + 1 < len(g) else g_end
            parts = []
            for j, x in enumerate(g):
                txt = _clean(x["w"])
                if j == i:
                    parts.append(f"{{\\c&H00E5FF&\\fscx112\\fscy112}}{txt}{{\\c&HFFFFFF&\\fscx100\\fscy100}}")
                else:
                    parts.append(txt)
            pop = "\\t(0,80,\\fscx104\\fscy104)" if i == 0 else ""
            ev.append(f"Dialogue: 1,{_ts(s)},{_ts(max(e, s + 0.05))},Cap,,0,0,0,,"
                      f"{{\\pos(540,{cy}){pop}}}" + " ".join(parts))
    return head + "\n".join(ev) + "\n"


def words_in_window(words, start, end):
    return [{"w": w["w"], "s": round(w["s"] - start, 2), "e": round(w["e"] - start, 2)}
            for w in words if w["s"] >= start - 0.05 and w["e"] <= end + 0.2]


# ---------------------------------------------------------------- render
def _stage_fonts(c: dict, render_dir: Path) -> bool:
    """Copy fonts from clipper.fonts_dir next to the renders so libass finds them on any OS."""
    src = c.get("fonts_dir")
    if not src:
        return False
    src = Path(src).expanduser()
    if not src.is_dir():
        src = Path(__file__).resolve().parent.parent / src
    files = [p for p in src.glob("*") if p.suffix.lower() in (".ttf", ".otf")] if src.is_dir() else []
    if not files:
        return False
    dst = render_dir / "fonts"
    dst.mkdir(exist_ok=True)
    for f in files:
        if not (dst / f.name).exists():
            shutil.copy2(f, dst / f.name)
    return True


def _venc(encoder: str, preview=False) -> list[str]:
    if encoder == "h264_nvenc":
        if preview:
            return ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "30", "-b:v", "0",
                    "-maxrate", "2M", "-bufsize", "4M"]
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "21", "-b:v", "0",
                "-maxrate", "8M", "-bufsize", "16M", "-profile:v", "high"]
    return ["-c:v", "libx264", "-preset", "veryfast" if preview else "medium",
            "-crf", "28" if preview else "20", "-maxrate", "2M" if preview else "8M",
            "-bufsize", "4M" if preview else "16M"]


def render(src: str, out: Path, streamer: dict, plan: dict, words: list, cfg: dict) -> tuple[Path, Path]:
    c = cfg["clipper"]
    start, end = float(plan["start"]), float(plan["end"])
    layout = streamer.get("layout", "cam_top")
    sw, sh = probe(src)
    ass_path = out.with_suffix(".ass")
    ass_path.write_text(build_ass(words_in_window(words, start, end), plan.get("hook_text", ""),
                                  layout, c.get("font", "Sans"), end - start), encoding="utf-8")
    fonts = _stage_fonts(c, out.parent)
    # relative names only: Windows drive letters ("C:") break filtergraph parsing
    vf = layout_filter(streamer, sw, sh) + f";[base]ass=filename={ass_path.name}" + (":fontsdir=fonts" if fonts else "") + "[v]"
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}", "-i", src,
           "-filter_complex", vf, "-map", "[v]", "-map", "0:a:0",
           "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", "48000",
           *_venc(c.get("encoder", "h264_nvenc")), "-pix_fmt", "yuv420p", "-fps_mode", "cfr",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
    _ffmpeg(cmd, "Render", cwd=out.parent)
    preview = out.with_name(out.stem + ".preview.mp4")
    _ffmpeg(["ffmpeg", "-v", "error", "-y", "-i", str(out), "-vf", "scale=540:-2",
             *_venc(c.get("encoder", "h264_nvenc"), preview=True), "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(preview)], "Preview")
    return out, preview
