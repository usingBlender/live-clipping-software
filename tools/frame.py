"""Grab a frame from a streamer's latest segment (or any video) to measure the facecam box,
and optionally render a 9:16 layout test image with the current config.

  python -m tools.frame streamer_one            -> frame.png + layout_test.png
  python -m tools.frame streamer_one some.mkv
"""
import subprocess
import sys

from clipper.render import layout_filter, probe
from common.config import load_config, streamer_by_name
from watcher.media import list_segments


def main():
    cfg = load_config()
    s = streamer_by_name(cfg, sys.argv[1])
    src = sys.argv[2] if len(sys.argv) > 2 else str(list_segments(cfg["data_dir"] / "segments" / s["name"])[-2])
    w, h = probe(src)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-frames:v", "1", "frame.png"], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-filter_complex", layout_filter(s, w, h),
                    "-map", "[base]", "-frames:v", "1", "layout_test.png"], check=True)
    print(f"source {w}x{h}. Open frame.png, measure the facecam box in pixels, then set in config:")
    print(f"  cam: {{x: X/{w}, y: Y/{h}, w: W/{w}, h: H/{h}}}   (fractions). Check layout_test.png.")


if __name__ == "__main__":
    main()
