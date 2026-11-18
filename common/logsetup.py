import os
import sys


def redirect_output():
    """When GTA_CLIPPER_LOG is set (run.ps1 does this for background tasks), send stdout/stderr
    to that file as UTF-8, appending. Avoids PowerShell redirection quirks on Windows."""
    path = os.environ.get("GTA_CLIPPER_LOG")
    if path:
        f = open(path, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = f
