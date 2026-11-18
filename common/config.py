"""Settings live in one YAML file that the UI reads and writes.

Location (first match):  $LIVE_CLIPPER_CONFIG  ->  ./config.yaml in the app folder (dev)  ->
  Windows: %APPDATA%/live-clipper/config.yaml     Linux: ~/.config/live-clipper/config.yaml
Missing keys are filled from DEFAULTS, so old config files keep working after updates.
"""
import copy
import os
import sys
import threading
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "data_dir": "~/live-clipper-data",
    "retention_hours": 24,
    "onboarded": False,
    "auth": {"password_hash": ""},
    "server": {"host": "127.0.0.1", "port": 8787, "public_url": ""},
    "clipper": {
        "language": "pt",
        "whisper_model": "large-v3-turbo",
        "whisper_compute_type": "int8_float16",
        "llm": "claude",
        "claude_model": "sonnet",
        "ollama_url": "http://127.0.0.1:11434",
        "ollama_model": "",
        "encoder": "h264_nvenc",
        "font": "Montserrat ExtraBold",
        "fonts_dir": "fonts",
        "default_hashtags": ["#gta6", "#gtavi", "#gta"],
    },
    "detector": {
        "chat_offset_s": 6,
        "baseline_s": 600,
        "warmup_s": 180,
        "threshold": 3.5,
        "cooldown_s": 90,
        "pre_s": 35,
        "post_s": 20,
        "w_chat": 0.6,
        "w_audio": 0.4,
        "combo_bonus": 1.0,
        "suppress_s": 30,
        "keywords": ["kkkk", "clipa", "clip it", "mds", "kekw", "lul", "omegalul", "lmao", "???", "pog",
                     "caralho", "nossa", "wtf"],
        "ignore_users": ["nightbot", "streamelements", "streamlabs", "moobot", "fossabot", "wizebot",
                         "botrix", "kickbot"],
    },
    "notify": {
        "telegram_token": "",
        "telegram_chat_id": "",
        "telegram_preview": True,        # send a small preview video when a clip is ready to review
        "telegram_final": True,          # send the final file + caption when you approve
        "webhooks": [],                  # URLs that receive every event as JSON (e.g. n8n webhook)
    },
    "update": {"repo": "", "auto_install": False, "check_hours": 6},
    "streamers": [],
}

STREAMER_DEFAULTS = {
    "platform": "twitch",
    "enabled": True,
    "quality": "1080p60,1080p,best",
    "streamlink_args": [],
    "layout": "cam_top",
    "cam": {"x": 0.0, "y": 0.0, "w": 0.25, "h": 0.25},
    "game_center_x": 0.5,
    "game_zoom": 1.0,
}


def config_path() -> Path:
    if os.environ.get("LIVE_CLIPPER_CONFIG"):
        return Path(os.environ["LIVE_CLIPPER_CONFIG"])
    if (ROOT / "config.yaml").exists():
        return ROOT / "config.yaml"
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    new = base / "live-clipper" / "config.yaml"
    old = base / "gta-clipper" / "config.yaml"  # name used before v0.3
    if not new.exists() and old.exists():
        new.parent.mkdir(parents=True, exist_ok=True)
        new.write_bytes(old.read_bytes())
    return new


def _merge(dst: dict, src: dict) -> dict:
    for k, v in src.items():
        if k not in dst:
            dst[k] = copy.deepcopy(v)
        elif isinstance(v, dict) and isinstance(dst[k], dict):
            _merge(dst[k], v)
    return dst


def normalize_streamer(s: dict) -> dict:
    s = _merge(dict(s), STREAMER_DEFAULTS)
    s["name"] = str(s.get("name") or s.get("channel") or "streamer").strip()
    s["channel"] = str(s.get("channel") or s["name"]).strip()
    return s


class ConfigStore:
    """Thread-safe holder of the live config. `cfg` is the runtime view (data_dir as Path,
    keyword list lower-cased). Mutate through the helpers so changes are saved to disk."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else config_path()
        self.lock = threading.RLock()
        raw = {}
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        _merge(raw, DEFAULTS)
        # migrate v0.1 layout (clipper.host/port/public_url, n8n.*)
        old = raw.get("clipper", {})
        for k in ("host", "port", "public_url"):
            if k in old:
                raw["server"][k] = old.pop(k)
        if "n8n" in raw:
            hook = (raw.pop("n8n") or {}).get("publish_webhook")
            if hook and hook not in raw["notify"]["webhooks"]:
                raw["notify"]["webhooks"].append(hook)
        raw["streamers"] = [normalize_streamer(s) for s in raw.get("streamers") or []]
        self.cfg = raw
        self._apply_runtime()
        if not self.path.exists():
            self.save()

    def _apply_runtime(self):
        c = self.cfg
        c["data_dir"] = Path(os.path.expanduser(str(c["data_dir"])))
        for sub in ("segments", "candidates", "renders", "ready", "features", "logs"):
            (c["data_dir"] / sub).mkdir(parents=True, exist_ok=True)
        d = c["detector"]
        d["keywords"] = [str(k).lower() for k in d.get("keywords", [])]
        d["ignore_users"] = sorted({str(u).lower() for u in d.get("ignore_users", [])})

    def save(self):
        with self.lock:
            out = copy.deepcopy({k: v for k, v in self.cfg.items() if k != "data_dir"})
            out["data_dir"] = str(self.cfg["data_dir"])
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                yaml.safe_dump(out, f, allow_unicode=True, sort_keys=False)
            os.replace(tmp, self.path)

    # ---- helpers -------------------------------------------------------------
    def streamer(self, name: str) -> dict:
        for s in self.cfg["streamers"]:
            if s["name"] == name:
                return s
        raise KeyError(name)

    def update_section(self, section: str, values: dict, allowed: set | None = None):
        """Update a settings section *in place* so running components see the change."""
        with self.lock:
            target = self.cfg[section]
            for k, v in values.items():
                if allowed is not None and k not in allowed:
                    continue
                cur = target.get(k)
                if isinstance(cur, bool):
                    v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
                elif isinstance(cur, (int, float)) and v not in (None, ""):
                    f = float(v)
                    v = int(f) if isinstance(cur, int) and f.is_integer() else f
                target[k] = v
            self._apply_runtime()
            self.save()


# ---- compatibility for the CLI tools -------------------------------------------
_store: ConfigStore | None = None


def get_store() -> ConfigStore:
    global _store
    if _store is None:
        _store = ConfigStore()
    return _store


def load_config(path: str | None = None) -> dict:
    return ConfigStore(Path(path) if path else None).cfg


def streamer_by_name(cfg: dict, name: str) -> dict:
    for s in cfg["streamers"]:
        if s["name"] == name:
            return s
    raise KeyError(f"streamer {name!r} not in config")
