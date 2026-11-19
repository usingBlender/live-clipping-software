"""9:16 renderer: layout, edit effects (zoom punch-ins, camera takeover, shake, callout texts, sound
effects), word-by-word captions, hook card, loudness normalization.

Frames are decoded in the order they were recorded and renumbered (setpts=N/FPS), and the clip is cut
by frame number - never by seeking on container timestamps, which can be broken in live recordings.
"""
import json
import re
import unicodedata
import shutil
import subprocess
from pathlib import Path

W, H = 1080, 1920
DEFAULT_CAM_H = 0.40          # cam_top: share of the height used by the facecam band


def probe(path) -> tuple[int, int]:
    w, h, _ = probe_full(path)
    return w, h


def probe_full(path) -> tuple[int, int, float]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate,avg_frame_rate", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    fps = 30.0
    for key in ("r_frame_rate", "avg_frame_rate"):
        try:
            a, b = s.get(key, "0/1").split("/")
            v = float(a) / float(b)
            if 10 <= v <= 120:
                fps = v
                break
        except (ValueError, ZeroDivisionError):
            pass
    return int(s["width"]), int(s["height"]), fps


def _ffmpeg(cmd, what, cwd=None):
    """Run ffmpeg; on failure raise a short, readable error (last lines of ffmpeg's output)."""
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd)
    if r.returncode != 0:
        tail = " ".join((r.stderr or "").strip().splitlines()[-2:])[:240]
        code = r.returncode & 0xFFFFFFFF
        crash = {0xC0000005: "FFmpeg crashed (access violation)", 0xC0000409: "FFmpeg crashed (stack buffer overrun)",
                 0xC000001D: "FFmpeg crashed (illegal instruction)"}.get(code)
        raise RuntimeError(f"{what} failed: {tail or crash or f'ffmpeg exit code {r.returncode}'}")


def _even(x):
    return max(2, int(x) // 2 * 2)


def cam_height(s: dict) -> int:
    return _even(H * min(0.6, max(0.25, float(s.get("cam_height", DEFAULT_CAM_H)))))


def cam_box(s: dict, sw: int, sh: int):
    cam = s.get("cam") or {"x": 0, "y": 0, "w": 0.25, "h": 0.25}
    return _even(cam["w"] * sw), _even(cam["h"] * sh), _even(cam["x"] * sw), _even(cam["y"] * sh)


def layout_filter(s: dict, sw: int, sh: int, src: str = "[0:v]", out: str = "[base]") -> str:
    zoom = float(s.get("game_zoom", 1.0))
    if s.get("layout", "cam_top") == "cam_top":
        ch_out = cam_height(s)
        cw, ch, cx, cy = cam_box(s, sw, sh)
        gh = _even(sh / zoom)
        gw = _even(min(sw, gh * W / (H - ch_out)))
        gx = _even(min(max(0, s.get("game_center_x", 0.5) * sw - gw / 2), sw - gw))
        gy = _even((sh - gh) / 2)
        return (f"{src}split=2[la][lb];"
                f"[la]crop={cw}:{ch}:{cx}:{cy},scale={W}:{ch_out}:force_original_aspect_ratio=increase,"
                f"crop={W}:{ch_out},setsar=1[lcam];"
                f"[lb]crop={gw}:{gh}:{gx}:{gy},scale={W}:{H - ch_out},setsar=1[lgame];"
                f"[lcam][lgame]vstack=inputs=2{out}")
    cw, ch = _even(sw / zoom), _even(sh / zoom)
    return (f"{src}crop={cw}:{ch},split=2[la][lb];"
            f"[la]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:4,setsar=1[lbg];"
            f"[lb]scale={W}:-2,setsar=1[lfg];[lbg][lfg]overlay=(W-w)/2:(H-h)/2{out}")


def focus_point(s: dict, focus) -> tuple[float, float]:
    """Where a zoom punches in, in output pixels."""
    if isinstance(focus, dict):
        return float(focus.get("x", 0.5)) * W, float(focus.get("y", 0.5)) * H
    cam_top = s.get("layout", "cam_top") == "cam_top"
    ch = cam_height(s) if cam_top else 0
    if focus == "cam" and cam_top:
        return W / 2, ch / 2
    if focus == "game" and cam_top:
        return W / 2, ch + (H - ch) / 2
    return W / 2, H / 2


# ---------------------------------------------------------------- captions, hook, callouts
def _ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _clean(word: str) -> str:
    """Plain text for the subtitle file: no override/drawing codes, no emoji or symbol glyphs (libass draws
    those as outlines from whatever fallback font it finds)."""
    w = re.sub(r"\{[^}]*\}", "", str(word)).replace("\\", "").replace("{", "").replace("}", "")
    w = "".join(ch for ch in w if unicodedata.category(ch)[0] in "LNZ" or ch in "!?.,'\"-%$#&:+/")
    return re.sub(r"\s+", " ", w).strip().strip(",.;:").upper()


def ass_color(hex_rgb: str, default="FFFFFF") -> str:
    h = (hex_rgb or default).lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        h = default
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


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


def text_positions(s: dict, layout: str) -> dict:
    cam_top = layout == "cam_top"
    ch = cam_height(s) if cam_top else 0
    cap_y = int(H * float(s.get("caption_y", 0.69)))
    return {"caption": cap_y, "hook": ch if cam_top else 470,
            "callout": (ch + 170) if cam_top else 760}


def build_ass(words, hook: str, layout: str, font: str, clip_len: float, s: dict | None = None,
              callouts: list | None = None) -> str:
    s = s or {}
    pos = text_positions(s, layout)
    base = ass_color(s.get("caption_color", "#FFFFFF"))
    hi = ass_color(s.get("highlight_color", "#FFE500"), "FFE500")
    box = ass_color(s.get("callout_color", "#E8173F"), "E8173F")
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},88,{base},{base},&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,3,5,60,60,0,1
Style: Hook,{font},70,&H00000000,&H00FFFFFF,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,16,0,5,60,60,0,1
Style: Callout,{font},64,&H00FFFFFF,&H00FFFFFF,{box},&H00000000,-1,0,0,0,100,100,0,0,3,14,0,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    if hook:
        ev.append(f"Dialogue: 3,{_ts(0)},{_ts(min(2.6, clip_len))},Hook,,0,0,0,,"
                  f"{{\\pos(540,{pos['hook']})\\fad(0,200)}}{_clean(hook)}")
    for c in callouts or []:
        t0, t1 = float(c["t"]), float(c["t"]) + float(c.get("dur", 2.0))
        ev.append(f"Dialogue: 4,{_ts(t0)},{_ts(min(t1, clip_len))},Callout,,0,0,0,,"
                  f"{{\\pos(540,{pos['callout']})\\frz-3\\fscx60\\fscy60\\t(0,110,\\fscx112\\fscy112)"
                  f"\\t(110,190,\\fscx100\\fscy100)\\fad(0,150)}}{_clean(c.get('text', ''))}")
    groups = group_words(words)
    for gi, g in enumerate(groups):
        nxt = groups[gi + 1][0]["s"] if gi + 1 < len(groups) else None
        g_end = g[-1]["e"] if nxt is None or nxt - g[-1]["e"] > 0.3 else nxt
        for i, w in enumerate(g):
            st = w["s"]
            e = g[i + 1]["s"] if i + 1 < len(g) else g_end
            parts = []
            for j, x in enumerate(g):
                txt = _clean(x["w"])
                if j == i:
                    parts.append(f"{{\\c{hi}\\fscx112\\fscy112}}{txt}{{\\c{base}\\fscx100\\fscy100}}")
                else:
                    parts.append(txt)
            pop = "\\t(0,80,\\fscx104\\fscy104)" if i == 0 else ""
            ev.append(f"Dialogue: 1,{_ts(st)},{_ts(max(e, st + 0.05))},Cap,,0,0,0,,"
                      f"{{\\pos(540,{pos['caption']}){pop}}}" + " ".join(parts))
    return head + "\n".join(ev) + "\n"


def words_in_window(words, start, end):
    return [{"w": w["w"], "s": round(w["s"] - start, 2), "e": round(w["e"] - start, 2)}
            for w in words if w["s"] >= start - 0.05 and w["e"] <= end + 0.2]


# ---------------------------------------------------------------- effects -> filtergraph
def _between(spans):
    return "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in spans) or "0"


def effects_graph(s: dict, sw: int, sh: int, fps: float, f0: int, f1: int, edits: list, captions_ass: str | None,
                  fonts: bool) -> str:
    """Video filtergraph: [0:v] -> renumbered, cut, laid out, effects, captions -> [v]."""
    cam_top = s.get("layout", "cam_top") == "cam_top"
    focus_spans = [(e["t"], e["t"] + e["dur"]) for e in edits if e["type"] == "cam_focus" and cam_top]
    g = [f"[0:v]setpts=N/({fps:.6f}*TB),trim=start_frame={f0}:end_frame={f1},setpts=PTS-STARTPTS[src]"]
    if focus_spans:
        g.append("[src]split=2[src1][src2]")
        g.append(layout_filter(s, sw, sh, "[src1]", "[v0]"))
        cw, ch, cx, cy = cam_box(s, sw, sh)
        g.append(f"[src2]crop={cw}:{ch}:{cx}:{cy},scale={W}:{H}:force_original_aspect_ratio=increase,"
                 f"crop={W}:{H},setsar=1[camfull]")
        g.append(f"[v0][camfull]overlay=0:0:enable='{_between(focus_spans)}'[v1]")
        cur = "[v1]"
    else:
        g.append(layout_filter(s, sw, sh, "[src]", "[v1]"))
        cur = "[v1]"
    k = 0
    for e in edits:
        if e["type"] == "zoom":
            k += 1
            z = min(1.8, max(1.08, float(e.get("amount", 1.3))))
            cw, ch = _even(W / z), _even(H / z)
            fx, fy = focus_point(s, e.get("focus", "center"))
            x = int(min(max(0, fx - cw / 2), W - cw))
            y = int(min(max(0, fy - ch / 2), H - ch))
            g.append(f"{cur}split=2[zk{k}][zs{k}]")
            g.append(f"[zs{k}]crop={cw}:{ch}:{x}:{y},scale={W}:{H},setsar=1[zz{k}]")
            g.append(f"[zk{k}][zz{k}]overlay=0:0:enable='{_between([(e['t'], e['t'] + e['dur'])])}'[ze{k}]")
            cur = f"[ze{k}]"
        elif e["type"] == "shake":
            k += 1
            a, b = e["t"], e["t"] + e["dur"]
            g.append(f"{cur}split=2[sk{k}][ss{k}]")
            g.append(f"[ss{k}]scale={_even(W * 1.06)}:{_even(H * 1.06)},"
                     f"crop={W}:{H}:x='(iw-ow)/2+22*sin(t*61)':y='(ih-oh)/2+18*cos(t*53)'[sh{k}]")
            g.append(f"[sk{k}][sh{k}]overlay=0:0:enable='{_between([(a, b)])}'[se{k}]")
            cur = f"[se{k}]"
    if captions_ass:
        g.append(f"{cur}ass=filename={captions_ass}" + (":fontsdir=fonts" if fonts else "") + "[v]")
    else:
        g.append(f"{cur}null[v]")
    return ";".join(g)


def audio_graph(t0: float, t1: float, sfx_inputs: list[tuple[int, float, float]]) -> str:
    """[0:a] renumbered by sample count, cut, plus sound effects mixed in -> [a].
    sfx_inputs: (input_index, time_s, volume)."""
    g = [f"[0:a]asetpts=N/SR/TB,atrim=start={t0:.3f}:end={t1:.3f},asetpts=PTS-STARTPTS,"
         f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a0]"]
    if not sfx_inputs:
        g.append("[a0]loudnorm=I=-14:TP=-1.5:LRA=11[a]")
        return ";".join(g)
    labels = ["[a0]"]
    for i, (idx, t, vol) in enumerate(sfx_inputs):
        ms = max(0, int(t * 1000))
        g.append(f"[{idx}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                 f"adelay={ms}|{ms},volume={vol:.2f}[fx{i}]")
        labels.append(f"[fx{i}]")
    g.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0,"
             f"loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    return ";".join(g)


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


def _attempts(encoder: str, fonts: bool):
    """Settings to try in order. If FFmpeg crashes or fails, the next safer variant is used:
    GPU encoder -> CPU encoder, bundled caption font -> system fonts, no effects, no captions."""
    out = [(encoder, fonts, True, True)]
    if encoder != "libx264":
        out.append(("libx264", fonts, True, True))
    if fonts:
        out.append(("libx264", False, True, True))
    out.append(("libx264", False, True, False))
    out.append(("libx264", False, False, False))
    return out


def render(src: str, out: Path, streamer: dict, plan: dict, words: list, cfg: dict,
           sfx_lib: dict | None = None) -> tuple[Path, Path]:
    """words: caption words in file time (already filtered to what should be captioned).
    plan["edits"]: effects in clip time (seconds from the clip start), already sanitized."""
    from common.bus import log
    c = cfg["clipper"]
    start, end = float(plan["start"]), float(plan["end"])
    clip_len = end - start
    layout = streamer.get("layout", "cam_top")
    sw, sh, fps = probe_full(src)
    f0, f1 = int(round(start * fps)), int(round(end * fps))
    edits = plan.get("edits") or []
    callouts = [e for e in edits if e["type"] == "text"]
    ass_path = out.with_suffix(".ass")
    hook = plan.get("hook_text", "") if streamer.get("hook", True) else ""
    ass_path.write_text(build_ass(words_in_window(words, start, end), hook, layout, c.get("font", "Sans"),
                                  clip_len, streamer, callouts), encoding="utf-8")
    fonts = _stage_fonts(c, out.parent)
    sfx_events = [e for e in edits if e["type"] == "sfx" and sfx_lib and e.get("name") in sfx_lib]
    vol = float(streamer.get("sfx_volume", 0.8))
    errors = []
    used_enc = c.get("encoder", "h264_nvenc")
    for enc, use_fonts, captions, effects in _attempts(used_enc, fonts):
        ed = edits if effects else []
        sfx_in, extra_inputs = [], []
        for i, e in enumerate(sfx_events if effects else []):
            extra_inputs += ["-i", str(sfx_lib[e["name"]]["path"])]
            gain = 10 ** (float(sfx_lib[e["name"]].get("gain_db", 0.0)) / 20)  # loudness-matched once per file
            sfx_in.append((i + 1, float(e["t"]), vol * gain * float(e.get("volume", 1.0))))
        vf = effects_graph(streamer, sw, sh, fps, f0, f1, ed, ass_path.name if captions else None, use_fonts)
        af = audio_graph(start, end, sfx_in)
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src), *extra_inputs,
               "-filter_complex", vf + ";" + af, "-map", "[v]", "-map", "[a]",
               *_venc(enc), "-pix_fmt", "yuv420p", "-r", f"{min(fps, 60):.3f}", "-fps_mode", "cfr",
               "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(out)]
        try:
            _ffmpeg(cmd, "Render", cwd=out.parent)
            used_enc = enc
            if errors:
                log(f"[render] worked with encoder={enc} bundled_font={use_fonts} effects={effects} "
                    f"captions={captions} after: {' | '.join(errors)}", "warn")
            break
        except RuntimeError as e:
            label = f"{enc}/font={use_fonts}/effects={effects}/captions={captions}: {e}"
            errors.append(label)
            log(f"[render] attempt failed - {label}", "warn")
            (out.parent / "last-failed-render.txt").write_text(
                "cwd: " + str(out.parent) + "\n" + subprocess.list2cmdline(cmd) + "\n", encoding="utf-8")
    else:
        raise RuntimeError("Render failed in every mode: " + " | ".join(errors)[-400:])
    preview = out.with_name(out.stem + ".preview.mp4")
    _ffmpeg(["ffmpeg", "-v", "error", "-y", "-i", str(out), "-vf", "scale=540:-2",
             *_venc(used_enc, preview=True), "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(preview)], "Preview")
    return out, preview


def frame_grid(src: str, out_jpg: Path, start: float, end: float, n: int = 12) -> list[float]:
    """Contact sheet (4 columns) of n frames between start and end, for the AI editor to look at.
    Returns the frame times (file seconds) in reading order."""
    _, _, fps = probe_full(src)
    times = [start + (end - start) * (i + 0.5) / n for i in range(n)]
    sel = "+".join(f"eq(n\\,{int(t * fps)})" for t in times)
    rows = (n + 3) // 4
    _ffmpeg(["ffmpeg", "-v", "error", "-y", "-i", src, "-vf",
             f"setpts=N/({fps:.6f}*TB),select={sel},scale=480:-2,tile=4x{rows}:padding=6:color=black",
             "-frames:v", "1", "-q:v", "4", str(out_jpg)], "Frame grid")
    return times
