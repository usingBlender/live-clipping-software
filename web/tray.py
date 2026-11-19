"""System-tray icon: Open, Pause/Resume all streamers, Quit.

Uses pystray. Works on Windows out of the box; on Linux it needs a desktop session with a tray (KDE, or
GNOME with the AppIndicator extension). If no tray is available the app just runs without one -
quit from Settings → System → Quit, or `systemctl --user stop live-clipper`."""
import os
import sys
import threading
import webbrowser
from pathlib import Path

from common.bus import log

ICON = Path(__file__).resolve().parent / "static" / "icon-192.png"


def tray_possible() -> bool:
    if os.environ.get("LIVE_CLIPPER_NO_TRAY"):
        return False
    if sys.platform == "win32" or sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def run_tray(app, url: str, quit_fn) -> bool:
    """Blocks running the tray loop (must be the main thread on some platforms). Returns False if no tray."""
    try:
        import pystray
        from PIL import Image
    except Exception as e:  # noqa: BLE001
        log(f"[tray] not available ({e}); use Settings → System → Quit to close the app")
        return False

    def open_ui(icon=None, item=None):
        webbrowser.open(url)

    def all_paused():
        return all(not s.get("enabled", True) for s in app.cfg["streamers"]) if app.cfg["streamers"] else False

    def toggle_all(icon, item):
        pause = not all_paused()
        with app.store.lock:
            for s in app.cfg["streamers"]:
                s["enabled"] = not pause
            app.store.save()
        for s in app.cfg["streamers"]:
            threading.Thread(target=(app.manager.stop if pause else app.manager.start), args=(s["name"],),
                             daemon=True).start()
        log(f"[tray] {'paused' if pause else 'resumed'} all streamers")

    def quit_app(icon, item):
        icon.stop()
        quit_fn()

    menu = pystray.Menu(
        pystray.MenuItem("Open Live Clipper", open_ui, default=True),
        pystray.MenuItem(lambda item: "Resume all streamers" if all_paused() else "Pause all streamers", toggle_all),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit Live Clipper", quit_app),
    )
    try:
        icon = pystray.Icon("live-clipper", Image.open(ICON), "Live Clipper", menu)
        log("[tray] icon shown")
        icon.run()
        return True
    except Exception as e:  # noqa: BLE001
        log(f"[tray] could not start ({e}); use Settings → System → Quit to close the app")
        return False
