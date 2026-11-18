"""Recording, segment bookkeeping, audio features and cutting."""
import subprocess
import time
from pathlib import Path

import numpy as np

SR = 16000
HOP = SR // 2  # 0.5 s analysis windows

try:  # Silero VAD bundled with faster-whisper (onnx, CPU)
    from faster_whisper.vad import VadOptions, get_speech_timestamps
except Exception:  # noqa: BLE001
    get_speech_timestamps = None


def stream_url(s: dict) -> str:
    if s["platform"] == "twitch":
        return f"https://twitch.tv/{s['channel']}"
    if s["platform"] == "kick":
        return f"https://kick.com/{s['channel']}"
    if s["platform"] == "youtube":
        return f"https://www.youtube.com/@{s['channel']}/live"
    raise ValueError(s["platform"])


def seg_start(path: Path) -> int:
    """Segment files are named %Y%m%d-%H%M%S.ts in local time (Brazil has no DST, so this is unambiguous)."""
    return int(time.mktime(time.strptime(path.stem, "%Y%m%d-%H%M%S")))


def list_segments(seg_dir: Path) -> list[Path]:
    return sorted(seg_dir.glob("*.ts"))


DEMO_PERIOD = 90  # demo source: one "hype" burst every 90 s


def recorder_commands(s: dict, seg_dir: Path) -> tuple[list | None, list]:
    """Returns (source_cmd or None, ffmpeg_cmd). The source pipes MPEG-TS into ffmpeg's stdin.
    platform 'demo' needs no internet: ffmpeg generates a test picture + audio with periodic
    loud bursts, so you can watch the whole pipeline work without a live stream."""
    seg = ["-f", "segment", "-segment_time", "10", "-reset_timestamps", "1", "-strftime", "1",
           (seg_dir / "%Y%m%d-%H%M%S.ts").as_posix()]
    if s["platform"] == "demo":
        voice = (f"0.05*sin(2*PI*180*t)*(0.6+0.4*sin(2*PI*2*t))"
                 f"+if(lt(mod(t,{DEMO_PERIOD}),4),0.7*sin(2*PI*700*t)*(0.7+0.3*sin(2*PI*5*t)),0)")
        ff = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-re",
              "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
              "-f", "lavfi", "-i", f"aevalsrc='{voice}':s=48000",
              "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-g", "60",
              "-c:a", "aac", "-b:a", "128k", *seg]
        return None, ff
    sl = ["streamlink", "--stdout", *s.get("streamlink_args", []), stream_url(s), s.get("quality", "best")]
    ff = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-map", "0:v:0", "-map", "0:a:0",
          "-c", "copy", *seg]
    return sl, ff


def _decode_audio(path: Path) -> np.ndarray:
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "pipe:1"],
        capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.float32)


def analyze_segment(path: Path) -> tuple[int, int, dict]:
    """Returns (start_epoch, end_epoch, {second: voice_band_db or None})."""
    start = seg_start(path)
    x = _decode_audio(path)
    n = len(x) // HOP
    if n == 0:
        return start, start, {}
    frames = x[: n * HOP].reshape(n, HOP)
    spec = np.abs(np.fft.rfft(frames * np.hanning(HOP), axis=1)) ** 2
    freqs = np.fft.rfftfreq(HOP, 1 / SR)
    band = (freqs >= 300) & (freqs <= 3000)
    db = 10 * np.log10(spec[:, band].sum(axis=1) + 1e-9)

    speech = np.ones(n, dtype=bool)
    if get_speech_timestamps is not None:
        try:
            speech[:] = False
            for ts in get_speech_timestamps(x, VadOptions(threshold=0.5, min_silence_duration_ms=300)):
                speech[ts["start"] // HOP: min(n, ts["end"] // HOP + 1)] = True
        except Exception:  # noqa: BLE001
            speech[:] = True

    per_sec = {}
    for i in range(0, n, 2):
        vals = [db[j] for j in (i, i + 1) if j < n and speech[j]]
        per_sec[start + i // 2] = float(max(vals)) if vals else None
    return start, start + n // 2, per_sec


def cut_window(seg_dir: Path, t0: int, t1: int, out: Path) -> tuple[int, float]:
    """Stream-copy the segments overlapping [t0, t1] into one file.
    Returns (file_start_epoch, duration_s)."""
    segs = list_segments(seg_dir)
    starts = [seg_start(p) for p in segs]
    chosen = []
    for i, (p, st) in enumerate(zip(segs, starts)):
        end = starts[i + 1] if i + 1 < len(segs) else st + 12
        if st < t1 and end > t0:
            chosen.append((p, st))
    if not chosen:
        raise RuntimeError("no segments for window")
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p, _ in chosen), encoding="utf-8")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", str(out)], check=True)
    lst.unlink(missing_ok=True)
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True).stdout.strip() or 0)
    return chosen[0][1], dur


def cleanup(seg_dir: Path, retention_h: float):
    cutoff = time.time() - retention_h * 3600
    for p in seg_dir.glob("*.ts"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except (FileNotFoundError, PermissionError):  # Windows: file still open by ffmpeg
            pass
