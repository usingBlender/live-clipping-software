"""Send any local video through the clip pipeline (skips detection). The running app picks it up.

  python -m tools.test_candidate <streamer_name> my_clip.mkv --peak 30
"""
import argparse
import json
import shutil
import subprocess
import time

from common.config import load_config


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("streamer")
    ap.add_argument("video")
    ap.add_argument("--peak", type=float, default=30.0, help="seconds into the video where the moment peaks")
    a = ap.parse_args()
    cfg = load_config()
    cid = f"{a.streamer}-test-{int(time.time())}"
    dst = cfg["data_dir"] / "candidates" / f"{cid}.mkv"
    shutil.copy2(a.video, dst)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(dst)], capture_output=True, text=True).stdout.strip())
    cand = {"clip_id": cid, "streamer": a.streamer, "platform": "test", "channel": a.streamer,
            "source_path": str(dst), "file_start": 0, "duration": dur, "peak_offset": a.peak,
            "score": 0, "z_chat": 0, "z_audio": 0, "chat_sample": [], "created": time.time()}
    inbox = cfg["data_dir"] / "candidates" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / f"{cid}.json").write_text(json.dumps(cand, ensure_ascii=False), encoding="utf-8")
    print(f"queued {cid} - watch it in the app (Clips / Activity)")


if __name__ == "__main__":
    main()
