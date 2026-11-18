"""Environment checks shown in onboarding and Settings, plus Tailscale helpers."""
import json
import shutil
import sys

from common.proc import run_text, which


def check(cfg: dict) -> list[dict]:
    items = []

    def add(key, label, ok, detail, fix=""):
        items.append({"key": key, "label": label, "ok": ok, "detail": detail, "fix": fix})

    win = sys.platform == "win32"
    ff = which("ffmpeg")
    add("ffmpeg", "FFmpeg", bool(ff), ff or "not found",
        "winget install Gyan.FFmpeg" if win else "sudo dnf install ffmpeg (RPM Fusion) / sudo apt install ffmpeg")
    if ff:
        _, enc = run_text(["ffmpeg", "-hide_banner", "-encoders"])
        _, flt = run_text(["ffmpeg", "-hide_banner", "-filters"])
        nv = "h264_nvenc" in enc
        add("nvenc", "NVIDIA encoder (NVENC)", nv, "available" if nv else "missing - renders will use the CPU",
            "Update the NVIDIA driver; use an ffmpeg build with NVENC")
        add("libass", "Caption renderer (libass)", " ass " in flt, "available" if " ass " in flt else "missing",
            "Use a full ffmpeg build")
    sl = which("streamlink")
    add("streamlink", "Streamlink", bool(sl), sl or "not found", "pip install streamlink (included in requirements)")
    gpu = which("nvidia-smi")
    if gpu:
        rc, out = run_text(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"])
        add("gpu", "NVIDIA GPU", rc == 0, out.strip().splitlines()[0] if rc == 0 and out.strip() else out[:120])
    else:
        add("gpu", "NVIDIA GPU", False, "nvidia-smi not found", "Install the NVIDIA driver")
    try:
        import faster_whisper  # noqa: F401
        add("whisper", "Speech-to-text (faster-whisper)", True, "installed")
    except Exception as e:  # noqa: BLE001
        add("whisper", "Speech-to-text (faster-whisper)", False, str(e)[:120], "pip install faster-whisper")
    cl = which("claude")
    add("claude", "Claude Code CLI", bool(cl), cl or "not found",
        "irm https://claude.ai/install.ps1 | iex" if win else "curl -fsSL https://claude.ai/install.sh | bash")
    ts = which("tailscale")
    add("tailscale", "Tailscale (phone access)", bool(ts), ts or "not found (optional)",
        "winget install Tailscale.Tailscale" if win else "see tailscale.com/download/linux")
    du = shutil.disk_usage(cfg["data_dir"])
    free = du.free / 1e9
    add("disk", "Free disk space", free > 100, f"{free:.0f} GB free at {cfg['data_dir']}",
        "Point data_dir at a drive with 150+ GB free")
    return items


def test_claude(model: str = "") -> dict:
    exe = which("claude")
    if not exe:
        return {"ok": False, "detail": "claude not found on PATH"}
    cmd = [exe, "-p", "--output-format", "json"] + (["--model", model] if model else [])
    import os
    import subprocess
    import tempfile
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    try:
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run(cmd, input="Reply with exactly the word: ok", capture_output=True, text=True,
                               encoding="utf-8", timeout=120, cwd=d, env=env)
        data = json.loads(r.stdout or "{}")
        ok = r.returncode == 0 and not data.get("is_error") and "ok" in str(data.get("result", "")).lower()
        return {"ok": ok, "detail": str(data.get("result", r.stderr))[:200]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": str(e)[:200]}


def tailscale_info(port: int) -> dict:
    if not which("tailscale"):
        return {"installed": False}
    info = {"installed": True}
    rc, out = run_text(["tailscale", "status", "--json"], timeout=10)
    if rc == 0:
        try:
            st = json.loads(out[out.index("{"):])
            info["dns_name"] = (st.get("Self", {}).get("DNSName") or "").rstrip(".")
            info["online"] = st.get("BackendState") == "Running"
        except Exception:  # noqa: BLE001
            pass
    rc, out = run_text(["tailscale", "serve", "status"], timeout=10)
    info["serving"] = f"127.0.0.1:{port}" in out
    info["public"] = "Funnel on" in out
    if info.get("dns_name"):
        info["url"] = f"https://{info['dns_name']}"
    return info


def tailscale_serve(port: int) -> dict:
    rc, out = run_text(["tailscale", "serve", "--bg", str(port)], timeout=30)
    return {"ok": rc == 0, "detail": out.strip()[-400:]}
