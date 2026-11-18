"""Re-run the detector over recorded features to tune thresholds/offset without a live stream.

  python -m tools.replay streamer_one 20261119 [--threshold 3.0] [--offset 8] [--top 30]

Prints detected peaks (local time) so you can compare against the VOD / your own picks.
"""
import argparse
import json
from datetime import datetime

from common.config import load_config
from watcher.detector import Detector


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("streamer")
    ap.add_argument("day", help="local day, YYYYMMDD")
    ap.add_argument("--config")
    ap.add_argument("--threshold", type=float)
    ap.add_argument("--offset", type=int)
    ap.add_argument("--cooldown", type=int)
    ap.add_argument("--top", type=int, default=50)
    a = ap.parse_args()
    cfg = load_config(a.config)
    dc = dict(cfg["detector"])
    for k, v in (("threshold", a.threshold), ("chat_offset_s", a.offset), ("cooldown_s", a.cooldown)):
        if v is not None:
            dc[k] = v
    path = cfg["data_dir"] / "features" / a.streamer / f"{a.day}.jsonl"
    rows = [json.loads(line) for line in open(path, encoding="utf-8")]
    rows.sort(key=lambda r: r["t"])
    det = Detector(dc)
    events = []
    for r in rows:
        t = r["t"]
        if r["chat_w"]:
            det.add_chat(t, r["chat_w"])
        if r.get("supp"):
            det.supp.add(t)
        det.set_audio(t, r["db"])
        events += det.advance(t - dc["chat_offset_s"] - 3)
    events.sort(key=lambda e: -e["score"])
    print(f"{len(events)} events (threshold {dc['threshold']}, offset {dc['chat_offset_s']}s)")
    for e in sorted(events[: a.top], key=lambda e: e["peak"]):
        ts = datetime.fromtimestamp(e["peak"]).strftime("%H:%M:%S")
        print(f"{ts}  score {e['score']:5.2f}  chat {e['z_chat']:4.1f}  voice {e['z_audio']:4.1f}")


if __name__ == "__main__":
    main()
