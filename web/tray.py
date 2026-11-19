"""System-tray icon: Open, Pause/Resume all streamers, Quit.

Windows/macOS: pystray. Linux: our own StatusNotifierItem over D-Bus (web/sni.py) - KDE, Cinnamon, XFCE,
Budgie, waybar, and GNOME with the "AppIndicator and KStatusNotifierItem Support" extension. If there is no
tray the app runs without one - quit from Settings → System → Quit, the app menu's "Quit Live Clipper"
action, `live-clipper --quit`, or `systemctl --user stop live-clipper`."""
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
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        bus = Path(f"/run/user/{os.getuid()}/bus")
        if bus.exists():   # services started by systemd sometimes lack the variable
            os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"
    return bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS") or os.environ.get("DISPLAY")
                or os.environ.get("WAYLAND_DISPLAY"))


def _run_sni(app, url, quit_fn, all_paused, toggle_all) -> bool:
    try:
        from web.sni import Tray
    except Exception as e:  # noqa: BLE001
        log(f"[tray] D-Bus tray unavailable ({e})", "warn")
        return False
    holder = {}

    def toggle():
        toggle_all(None, None)
        holder["t"].refresh_menu()

    def quit_app():
        holder["t"].stop()
        quit_fn()

    t = holder["t"] = Tray("Live Clipper", ICON, [
        ("Open Live Clipper", lambda: webbrowser.open(url)),
        (lambda: "Resume all streamers" if all_paused() else "Pause all streamers", toggle),
        None,
        ("Quit Live Clipper", quit_app),
    ])
    import time
    for attempt in range(13):   # at login the panel may start after us: retry for ~2 minutes
        try:
            if t.start():
                break
        except Exception as e:  # noqa: BLE001
            if attempt == 12:
                log(f"[tray] D-Bus session not available ({e})", "warn")
                return False
        if attempt == 0:
            log("[tray] waiting for a tray host... (on GNOME install the 'AppIndicator and KStatusNotifierItem "
                "Support' extension)")
        if attempt == 12:
            log("[tray] no tray host on this desktop; quit from Settings → System → Quit", "warn")
            return False
        time.sleep(10)
    log("[tray] icon shown (StatusNotifierItem)")
    t.run()
    return True


def run_tray(app, url: str, quit_fn) -> bool:
    """Blocks running the tray loop (must be the main thread on some platforms). Returns False if no tray."""
    if sys.platform.startswith("linux"):
        def _all_paused():
            return all(not s.get("enabled", True) for s in app.cfg["streamers"]) if app.cfg["streamers"] else False

        def _toggle(icon, item):
            pause = not _all_paused()
            with app.store.lock:
                for s in app.cfg["streamers"]:
                    s["enabled"] = not pause
                app.store.save()
            for s in app.cfg["streamers"]:
                threading.Thread(target=(app.manager.stop if pause else app.manager.start), args=(s["name"],),
                                 daemon=True).start()
            log(f"[tray] {'paused' if pause else 'resumed'} all streamers")
        return _run_sni(app, url, quit_fn, _all_paused, _toggle)
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
