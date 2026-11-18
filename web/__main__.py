"""Start Live Clipper.

  python -m web                 run the app (opens the browser on first run)
  python -m web --supervise     keep it running: restart after crashes or self-updates
  python -m web --no-browser    don't open a browser window
If the app is already running, this just opens it in the browser.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.config import ConfigStore  # noqa: E402
from common.logsetup import redirect_output  # noqa: E402

RESTART_CODE = 3


def already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=2) as r:
            return "version" in json.loads(r.read())
    except Exception:  # noqa: BLE001
        return False


def supervise(args: list[str]):
    """Parent loop: restarts the app if it crashes (after 5 s) or asks for a restart (exit code 3)."""
    os.environ["LIVE_CLIPPER_SUPERVISED"] = "1"
    while True:
        rc = subprocess.call([sys.executable, "-m", "web", *args], cwd=str(Path(__file__).resolve().parent.parent))
        if rc == 0:
            return
        time.sleep(0 if rc == RESTART_CODE else 5)


def main():
    ap = argparse.ArgumentParser(prog="live-clipper")
    ap.add_argument("--config")
    ap.add_argument("--port", type=int)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--supervise", action="store_true")
    a = ap.parse_args()
    if a.config:
        os.environ["LIVE_CLIPPER_CONFIG"] = a.config
    if a.supervise:
        rest = [x for x in sys.argv[1:] if x != "--supervise"]
        return supervise(rest)
    redirect_output()
    store = ConfigStore()
    if a.port:
        store.cfg["server"]["port"] = a.port
    port = int(store.cfg["server"]["port"])
    if already_running(port):
        if not a.no_browser:
            webbrowser.open(f"http://127.0.0.1:{port}")
        print(f"Live Clipper is already running on http://127.0.0.1:{port}")
        return
    from web.server import serve
    serve(store, open_browser=not a.no_browser)


if __name__ == "__main__":
    main()
