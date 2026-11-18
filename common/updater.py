"""Self-update from GitHub Releases.

- Windows (installed with the setup .exe): downloads the new setup .exe from the latest release and
  runs it silently; the installer stops the app, replaces files and starts it again.
- Linux (installed from the dnf/apt repository): the package manager updates it
  (`dnf upgrade` / `apt upgrade`, or automatically with dnf-automatic / unattended-upgrades).
  The app shows the command.
- Running from a git checkout: `git pull` + dependency sync, then the app restarts itself.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from common.bus import bus, log
from common.config import ROOT
from common.version import __version__, parse


def install_kind() -> str:
    if (ROOT / ".git").exists():
        return "git"
    if sys.platform == "win32":
        return "windows"
    if Path("/etc/debian_version").exists():
        return "apt"
    return "dnf"


def fetch_latest(repo: str) -> dict:
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json",
                                          "User-Agent": f"gta-clipper/{__version__}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


class Updater:
    def __init__(self, store, can_restart_fn, notifier=None):
        self.store, self.can_restart, self.notifier = store, can_restart_fn, notifier
        self.last = {"current": __version__, "kind": install_kind(), "checked": None, "available": False}
        self._notified = None
        threading.Thread(target=self._loop, name="updater", daemon=True).start()
        watch_installed_version(can_restart_fn)

    @property
    def u(self):
        return self.store.cfg["update"]

    def check(self) -> dict:
        repo = (self.u.get("repo") or "").strip()
        info = {"current": __version__, "kind": install_kind(), "checked": time.time(), "repo": repo}
        if not repo:
            info.update(available=False, error="Set your GitHub repo (owner/name) in Settings → Updates")
            self.last = info
            return info
        try:
            rel = fetch_latest(repo)
            latest = rel.get("tag_name", "")
            info.update(latest=latest, notes=(rel.get("body") or "")[:2000], url=rel.get("html_url"),
                        available=parse(latest) > parse(__version__),
                        assets=[{"name": a["name"], "url": a["browser_download_url"]} for a in rel.get("assets", [])])
        except Exception as e:  # noqa: BLE001
            info.update(available=False, error=str(e)[:200])
        if info.get("available"):
            info["command"] = {"apt": "sudo apt update && sudo apt install --only-upgrade gta-clipper",
                               "dnf": "sudo dnf upgrade --refresh gta-clipper"}.get(info["kind"])
            if self._notified != info["latest"]:
                self._notified = info["latest"]
                bus.publish("update.available", {"latest": info["latest"], "current": __version__})
                if self.notifier:
                    self.notifier.event("update.available", {"latest": info["latest"], "current": __version__})
        self.last = info
        return info

    def install(self) -> dict:
        info = self.last if self.last.get("available") else self.check()
        if not info.get("available"):
            return {"ok": False, "detail": info.get("error") or "Already up to date"}
        kind = info["kind"]
        if kind == "windows":
            asset = next((a for a in info.get("assets", []) if re.search(r"setup.*\.exe$", a["name"], re.I)), None)
            if not asset:
                return {"ok": False, "detail": "Release has no *setup*.exe asset"}
            dst = Path(tempfile.gettempdir()) / asset["name"]
            log(f"[update] downloading {asset['name']}")
            urllib.request.urlretrieve(asset["url"], dst)
            log("[update] running installer; the app will restart")
            bus.publish("update.installing", {"latest": info["latest"]})
            subprocess.Popen([str(dst), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                             creationflags=0x00000008 | 0x00000200, close_fds=True)  # detached, new group
            threading.Timer(3, lambda: os._exit(0)).start()
            return {"ok": True, "detail": "Installer started. The app restarts in ~1 minute."}
        if kind == "git":
            rc = subprocess.run(["git", "-C", str(ROOT), "pull", "--ff-only"], capture_output=True, text=True)
            if rc.returncode != 0:
                return {"ok": False, "detail": rc.stderr[-300:]}
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")])
            restart_self()
            return {"ok": True, "detail": "Updated from git; restarting"}
        return {"ok": False, "detail": f"Run: {info.get('command')}", "command": info.get("command")}

    def _loop(self):
        time.sleep(30)
        while True:
            try:
                info = self.check()
                if info.get("available") and self.u.get("auto_install") and info["kind"] in ("windows", "git"):
                    while not self.can_restart():
                        time.sleep(60)
                    self.install()
            except Exception as e:  # noqa: BLE001
                log(f"[update] {e}", "warn")
            time.sleep(max(1, float(self.u.get("check_hours", 6))) * 3600)


def restart_self():
    """Under a supervisor (--supervise, systemd, installer) exit with code 3 and get restarted;
    otherwise start a fresh copy and exit."""
    def _go():
        time.sleep(1)
        if os.environ.get("GTA_CLIPPER_SUPERVISED"):
            os._exit(3)
        subprocess.Popen([sys.executable, "-m", "web", "--no-browser"], cwd=str(ROOT), close_fds=True)
        os._exit(0)
    threading.Thread(target=_go, daemon=True).start()


def watch_installed_version(can_restart_fn):
    """When a package manager (dnf/apt) or the installer replaces the files on disk, the running app
    is still the old version. Detect that and restart into the new one when idle."""
    vfile = ROOT / "common" / "version.py"

    def _loop():
        while True:
            time.sleep(60)
            try:
                txt = vfile.read_text(encoding="utf-8")
                on_disk = re.search(r'__version__\s*=\s*"([^"]+)"', txt).group(1)
            except Exception:  # noqa: BLE001
                continue
            if on_disk != __version__ and can_restart_fn():
                log(f"[update] version {on_disk} installed on disk; restarting")
                bus.publish("update.installing", {"latest": on_disk})
                restart_self()
                return
    threading.Thread(target=_loop, name="version-watch", daemon=True).start()
