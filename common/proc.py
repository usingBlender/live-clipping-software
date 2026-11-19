"""Process helpers.

On Windows, when the app runs without a console (installer / background task), every ffmpeg,
streamlink or claude call would flash a console window. Importing this module makes all
subprocesses start with CREATE_NO_WINDOW."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

# streamlink/uv live next to the venv's python; winget puts ffmpeg in WinGet\Links, which a freshly
# installed app may not have on PATH until the next login.
# Background services (systemd, log-in shortcuts) get a minimal PATH, so also look where the Claude Code,
# npm and pip installers put their commands.
_home = Path.home()
_extra = [str(Path(sys.executable).parent), str(_home / ".local" / "bin"), str(_home / ".claude" / "local"),
          str(_home / ".npm-global" / "bin"), str(_home / "bin")]
if sys.platform == "win32":
    if os.environ.get("LOCALAPPDATA"):
        _extra.append(str(Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "WinGet" / "Links"))
    if os.environ.get("APPDATA"):
        _extra.append(str(Path(os.environ["APPDATA"]) / "npm"))
else:
    _extra += ["/usr/local/bin", "/usr/bin", "/bin", "/usr/local/sbin", "/usr/sbin"]
os.environ["PATH"] = os.pathsep.join([p for p in _extra if Path(p).is_dir()] + [os.environ.get("PATH", "")])

if sys.platform == "win32" and not getattr(subprocess.Popen, "_gc_patched", False):
    _orig_init = subprocess.Popen.__init__

    def _init(self, *args, **kwargs):
        flags = kwargs.get("creationflags", 0)
        if not flags & 0x00000008:  # leave DETACHED_PROCESS launches alone
            flags |= subprocess.CREATE_NO_WINDOW
        kwargs["creationflags"] = flags
        _orig_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _init
    subprocess.Popen._gc_patched = True


def which(name: str) -> str | None:
    return shutil.which(name)


def run_text(cmd: list[str], timeout=30) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError:
        return 127, f"{cmd[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "timed out"
