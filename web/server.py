"""The app: HTTP API + web UI + live event stream, in one process with the watchers and worker.

Uses only the Python standard library (ThreadingHTTPServer), so it starts anywhere Python runs.
"""
import hashlib
import hmac
import json
import mimetypes
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import common.proc  # noqa: F401  (hides console windows of child processes on Windows)
from clipper import render as rendermod
from clipper.db import DB
from clipper.notify import Notifier
from clipper.pipeline import Worker
from common import system
from common.bus import bus, log, logbuf
from common.config import ConfigStore, normalize_streamer
from common.updater import Updater, install_kind, restart_self
from common.version import __version__
from watcher import media
from watcher.stream import WatcherManager

STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "gcs"
NAME_RE = re.compile(r"[^A-Za-z0-9_\-]")


# ------------------------------------------------------------------ password hashing
def hash_pw(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + "$" + h.hex()


def check_pw(pw: str, stored: str) -> bool:
    try:
        salt, h = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
        return hmac.compare_digest(got.hex(), h)
    except Exception:  # noqa: BLE001
        return False


class App:
    def __init__(self, store: ConfigStore):
        self.store = store
        cfg = store.cfg
        logbuf.open(cfg["data_dir"] / "logs" / "app.log")
        self.db = DB(cfg["data_dir"] / "clips.db")
        self.notifier = Notifier(store)
        self._ts_cache = (0, None)
        if not cfg["auth"].get("media_secret"):
            store.update_section("auth", {"media_secret": secrets.token_hex(32)})
        self.worker = Worker(store, self.db, self.notifier, self.public_url, self.media_token)
        self.manager = WatcherManager(store, self.worker.submit)
        self.updater = Updater(store, lambda: self.worker.current is None and self.worker.q.empty(), self.notifier)

    @property
    def cfg(self):
        return self.store.cfg

    def media_token(self, cid: str) -> str:
        """Per-clip download token for webhooks (n8n) that can't log in."""
        return hmac.new(self.cfg["auth"]["media_secret"].encode(), cid.encode(), "sha256").hexdigest()[:32]

    def public_url(self) -> str:
        url = (self.cfg["server"].get("public_url") or "").rstrip("/")
        if url:
            return url
        t, cached = self._ts_cache
        if time.time() - t > 300:
            info = system.tailscale_info(int(self.cfg["server"]["port"]))
            cached = info.get("url") if info.get("serving") else None
            self._ts_cache = (time.time(), cached)
        return cached or f"http://127.0.0.1:{self.cfg['server']['port']}"

    def overview(self) -> dict:
        import shutil
        du = shutil.disk_usage(self.cfg["data_dir"])
        return {"version": __version__, "streamers": self.manager.status(), "worker": self.worker.status(),
                "stats": self.db.stats_today(), "events": bus.history(80),
                "threshold": self.cfg["detector"]["threshold"], "public_url": self.public_url(),
                "disk_free_gb": round(du.free / 1e9, 1), "update": self.updater.last,
                "notify": {"telegram": self.notifier.telegram_ready(),
                           "webhooks": len(self.cfg["notify"].get("webhooks") or [])}}

    def settings_view(self) -> dict:
        c = self.cfg
        n = dict(c["notify"])
        if n.get("telegram_token"):
            n["telegram_token"] = "••••" + n["telegram_token"][-4:]
        return {"general": {"data_dir": str(c["data_dir"]), "retention_hours": c["retention_hours"],
                            "config_path": str(self.store.path)},
                "server": dict(c["server"]), "clipper": dict(c["clipper"]), "detector": dict(c["detector"]),
                "notify": n, "update": dict(c["update"]), "install_kind": install_kind()}


# ------------------------------------------------------------------ HTTP handler
ROUTES = []


def route(method, pattern, auth=True):
    rx = re.compile("^" + pattern + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn, auth))
        return fn
    return deco


class Handler(BaseHTTPRequestHandler):
    app: App = None
    server_version = "live-clipper"

    def log_message(self, fmt, *args):  # quiet access log
        pass

    # ---- helpers ------------------------------------------------------------
    def _cookie(self):
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return c[COOKIE].value if COOKIE in c else None

    def authed(self):
        return self.app.db.valid_session(self._cookie())

    def body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        raw = self.rfile.read(n)
        return json.loads(raw or b"{}")

    def send_json(self, obj, code=200, headers=None):
        data = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def set_session(self) -> dict:
        token = secrets.token_urlsafe(32)
        self.app.db.add_session(token, agent=self.headers.get("User-Agent", ""))
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        return {"Set-Cookie": f"{COOKIE}={token}; Path=/; Max-Age={30 * 86400}; HttpOnly; SameSite=Lax{secure}"}

    def send_file(self, path: Path, ctype=None, cache="no-cache", download: str | None = None):
        if not path or not path.exists():
            return self.send_json({"error": "not found"}, 404)
        size = path.stat().st_size
        ctype = ctype or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            if a:
                start = int(a)
                end = int(b) if b else size - 1
            else:
                start = size - int(b)
            end = min(end, size - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", cache)
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(256 * 1024, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)

    def send_bytes(self, data: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---- dispatch -------------------------------------------------------------
    def dispatch(self, method):
        url = urlparse(self.path)
        path = unquote(url.path)
        self.query = {k: v[-1] for k, v in parse_qs(url.query).items()}
        try:
            for m, rx, fn, auth in ROUTES:
                if m != method:
                    continue
                mt = rx.match(path)
                if not mt:
                    continue
                if auth and not self.authed():
                    return self.send_json({"error": "login required"}, 401)
                return fn(self, *mt.groups())
            if method == "GET" and not path.startswith(("/api/", "/media/")):
                return self.static(path)
            self.send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except KeyError as e:
            self.send_json({"error": f"not found: {e}"}, 404)
        except Exception as e:  # noqa: BLE001
            log(f"[http] {method} {path}: {e!r}", "error")
            try:
                self.send_json({"error": str(e)}, 500)
            except Exception:  # noqa: BLE001
                pass

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def static(self, path):
        rel = path.lstrip("/") or "index.html"
        f = (STATIC / rel).resolve()
        if not str(f).startswith(str(STATIC)) or not f.is_file():
            f = STATIC / "index.html"
        self.send_file(f, cache="no-cache")


A = lambda h: h.app  # noqa: E731


# ------------------------------------------------------------------ auth
@route("GET", "/api/state", auth=False)
def r_state(h):
    c = A(h).cfg
    h.send_json({"version": __version__, "has_password": bool(c["auth"]["password_hash"]),
                 "onboarded": c["onboarded"], "authed": h.authed(), "platform": sys.platform})


@route("POST", "/api/auth/setup", auth=False)
def r_setup(h):
    c = A(h).cfg
    if c["auth"]["password_hash"]:
        return h.send_json({"error": "password already set"}, 403)
    pw = h.body().get("password", "")
    if len(pw) < 8:
        return h.send_json({"error": "use at least 8 characters"}, 400)
    A(h).store.update_section("auth", {"password_hash": hash_pw(pw)})
    h.send_json({"ok": True}, headers=h.set_session())


@route("POST", "/api/auth/login", auth=False)
def r_login(h):
    pw = h.body().get("password", "")
    if not check_pw(pw, A(h).cfg["auth"]["password_hash"]):
        time.sleep(1.5)
        log("[auth] failed login", "warn")
        return h.send_json({"error": "wrong password"}, 401)
    h.send_json({"ok": True}, headers=h.set_session())


@route("POST", "/api/auth/logout")
def r_logout(h):
    A(h).db.drop_session(h._cookie())
    h.send_json({"ok": True}, headers={"Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0"})


@route("POST", "/api/auth/password")
def r_password(h):
    b = h.body()
    if not check_pw(b.get("current", ""), A(h).cfg["auth"]["password_hash"]):
        return h.send_json({"error": "current password is wrong"}, 400)
    if len(b.get("new", "")) < 8:
        return h.send_json({"error": "use at least 8 characters"}, 400)
    A(h).store.update_section("auth", {"password_hash": hash_pw(b["new"])})
    A(h).db.drop_session(all_=True)
    h.send_json({"ok": True}, headers=h.set_session())


# ------------------------------------------------------------------ overview + live events
@route("GET", "/api/overview")
def r_overview(h):
    h.send_json(A(h).overview())


@route("GET", "/api/events")
def r_events(h):
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream")
    h.send_header("Cache-Control", "no-store")
    h.send_header("X-Accel-Buffering", "no")
    h.end_headers()
    q = bus.subscribe()
    try:
        h.wfile.write(b"retry: 3000\n\n")
        h.wfile.flush()
        while True:
            try:
                ev = q.get(timeout=15)
                h.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n".encode())
            except queue.Empty:
                h.wfile.write(b": ping\n\n")
            h.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    finally:
        bus.unsubscribe(q)


@route("GET", "/api/logs")
def r_logs(h):
    h.send_json({"lines": logbuf.tail(int(h.query.get("n", 300)))})


# ------------------------------------------------------------------ streamers (hot-swap)
STREAMER_FIELDS = {"platform", "channel", "kick_chatroom_id", "quality", "streamlink_args", "layout", "cam",
                   "game_center_x", "game_zoom", "enabled"}
RESTART_FIELDS = {"platform", "channel", "kick_chatroom_id", "quality", "streamlink_args"}


@route("GET", "/api/streamers")
def r_streamers(h):
    h.send_json({"streamers": A(h).cfg["streamers"], "status": A(h).manager.status()})


@route("POST", "/api/streamers")
def r_add_streamer(h):
    b = h.body()
    app = A(h)
    channel = str(b.get("channel", "")).strip().lstrip("@#").split("/")[-1]
    if not channel and b.get("platform") != "demo":
        return h.send_json({"error": "channel is required"}, 400)
    name = NAME_RE.sub("_", str(b.get("name") or channel or "demo").strip())[:40] or "streamer"
    with app.store.lock:
        if any(s["name"] == name for s in app.cfg["streamers"]):
            return h.send_json({"error": f"'{name}' already exists"}, 400)
        s = normalize_streamer({"name": name, "channel": channel or name,
                                **{k: v for k, v in b.items() if k in STREAMER_FIELDS and k != "channel"}})
        app.cfg["streamers"].append(s)
        app.store.save()
    if s.get("enabled", True):
        app.manager.start(name)
    log(f"[ui] added streamer {name}")
    h.send_json({"ok": True, "streamer": s})


@route("PATCH", "/api/streamers/([^/]+)")
def r_edit_streamer(h, name):
    b = h.body()
    app = A(h)
    with app.store.lock:
        s = app.store.streamer(name)
        changed = {k for k, v in b.items() if k in STREAMER_FIELDS and s.get(k) != v}
        for k in changed:
            s[k] = b[k]
        app.store.save()
    running = name in app.manager.tasks and not app.manager.tasks[name].done()
    if "enabled" in changed:
        (app.manager.start if s["enabled"] else app.manager.stop)(name)
    elif running and changed & RESTART_FIELDS:
        app.manager.restart(name)
    h.send_json({"ok": True, "streamer": s, "changed": sorted(changed)})


@route("DELETE", "/api/streamers/([^/]+)")
def r_del_streamer(h, name):
    app = A(h)
    app.manager.forget(name)
    with app.store.lock:
        app.cfg["streamers"] = [s for s in app.cfg["streamers"] if s["name"] != name]
        app.store.save()
    log(f"[ui] removed streamer {name}")
    bus.publish("streamer.removed", {"name": name})
    h.send_json({"ok": True})


@route("POST", "/api/streamers/([^/]+)/(pause|resume|restart)")
def r_streamer_ctl(h, name, action):
    app = A(h)
    with app.store.lock:
        s = app.store.streamer(name)
        s["enabled"] = action != "pause"
        app.store.save()
    {"pause": app.manager.stop, "resume": app.manager.start, "restart": app.manager.restart}[action](name)
    h.send_json({"ok": True})


@route("GET", "/api/streamers/([^/]+)/history")
def r_history(h, name):
    h.send_json({"points": A(h).manager.history(name), "threshold": A(h).cfg["detector"]["threshold"]})


def _latest_segment(app, name):
    segs = media.list_segments(app.cfg["data_dir"] / "segments" / name)
    return segs[-2] if len(segs) >= 2 else None


@route("GET", "/api/streamers/([^/]+)/(frame|layout)\\.jpg")
def r_frame(h, name, kind):
    app = A(h)
    seg = _latest_segment(app, name)
    if not seg:
        return h.send_json({"error": "No recording yet. Wait until this streamer is live."}, 404)
    if kind == "frame":
        cmd = ["ffmpeg", "-v", "error", "-ss", "1", "-i", str(seg), "-frames:v", "1", "-vf", "scale=960:-2",
               "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
    else:
        s = app.store.streamer(name)
        w, hh = rendermod.probe(seg)
        fg = rendermod.layout_filter(s, w, hh) + ";[base]scale=360:-2[o]"
        cmd = ["ffmpeg", "-v", "error", "-ss", "1", "-i", str(seg), "-filter_complex", fg, "-map", "[o]",
               "-frames:v", "1", "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
    r = subprocess.run(cmd, capture_output=True, timeout=30)
    if r.returncode != 0 or not r.stdout:
        return h.send_json({"error": r.stderr.decode(errors="replace")[-300:]}, 500)
    h.send_bytes(r.stdout, "image/jpeg")


# ------------------------------------------------------------------ clips
def _clip_view(row):
    if not row:
        return None
    out = {k: row[k] for k in ("id", "streamer", "created", "status", "score", "title", "hashtags", "hook",
                               "reason", "decided")}
    try:
        plan = json.loads(row.get("plan_json") or "{}")
        out.update(confidence=plan.get("confidence"), llm=plan.get("llm"),
                   duration=round(float(plan.get("end", 0)) - float(plan.get("start", 0)), 1) if plan else None)
    except Exception:  # noqa: BLE001
        pass
    out["has_video"] = bool(row.get("output_path")) and Path(row["output_path"]).exists()
    return out


@route("GET", "/api/clips")
def r_clips(h):
    rows = A(h).db.list(status=h.query.get("status") or None, limit=int(h.query.get("limit", 60)),
                        streamer=h.query.get("streamer") or None)
    h.send_json({"clips": [_clip_view(r) for r in rows]})


@route("GET", "/api/clips/([^/]+)")
def r_clip(h, cid):
    row = A(h).db.get(cid)
    if not row:
        return h.send_json({"error": "not found"}, 404)
    v = _clip_view(row)
    try:
        v["chat_sample"] = json.loads(row["candidate_json"] or "{}").get("chat_sample", [])[-20:]
    except Exception:  # noqa: BLE001
        v["chat_sample"] = []
    h.send_json(v)


@route("POST", "/api/clips/([^/]+)/(approve|reject|retry)")
def r_clip_action(h, cid, action):
    app = A(h)
    b = h.body()
    if action == "approve":
        return h.send_json(app.worker.approve(cid, b.get("title"), b.get("hashtags")))
    if action == "reject":
        app.worker.reject(cid)
        return h.send_json({"ok": True})
    row = app.db.get(cid)
    app.worker.submit(json.loads(row["candidate_json"]))
    h.send_json({"ok": True})


@route("GET", "/media/([^/]+)/(video|preview|source)", auth=False)
def r_media(h, cid, kind):
    tok = h.query.get("token", "")
    if not (h.authed() or (tok and hmac.compare_digest(tok, A(h).media_token(cid)))):
        return h.send_json({"error": "login required"}, 401)
    row = A(h).db.get(cid)
    if not row:
        return h.send_json({"error": "not found"}, 404)
    key = {"video": "output_path", "preview": "preview_path", "source": "source_path"}[kind]
    p = Path(row[key]) if row.get(key) else None
    h.send_file(p, "video/mp4" if kind != "source" else "video/x-matroska", cache="private, max-age=3600",
                download=f"{cid}.mp4" if h.query.get("download") else None)


@route("POST", "/api/process")
def r_process(h):
    A(h).worker.submit(h.body())
    h.send_json({"ok": True})


# ------------------------------------------------------------------ settings
EDITABLE = {
    "detector": {"chat_offset_s", "baseline_s", "warmup_s", "threshold", "cooldown_s", "pre_s", "post_s", "w_chat",
                 "w_audio", "combo_bonus", "suppress_s", "keywords", "ignore_users"},
    "clipper": {"language", "whisper_model", "whisper_compute_type", "llm", "claude_model", "ollama_url",
                "ollama_model", "encoder", "font", "fonts_dir", "default_hashtags"},
    "notify": {"telegram_token", "telegram_chat_id", "telegram_preview", "telegram_final", "webhooks"},
    "update": {"repo", "auto_install", "check_hours"},
    "server": {"public_url"},
}


@route("GET", "/api/settings")
def r_settings(h):
    h.send_json(A(h).settings_view())


@route("PATCH", "/api/settings/([a-z]+)")
def r_settings_patch(h, section):
    app = A(h)
    b = h.body()
    if section == "general":
        with app.store.lock:
            if "retention_hours" in b:
                app.cfg["retention_hours"] = float(b["retention_hours"])
            app.store.save()
        return h.send_json({"ok": True})
    if section not in EDITABLE:
        return h.send_json({"error": "unknown section"}, 400)
    if section == "notify" and str(b.get("telegram_token", "")).startswith("••••"):
        b.pop("telegram_token")
    for k in ("keywords", "ignore_users", "default_hashtags", "webhooks"):
        if isinstance(b.get(k), str):
            b[k] = [x.strip() for x in re.split(r"[,\n]", b[k]) if x.strip()]
    old_offset = app.cfg["detector"]["chat_offset_s"]
    app.store.update_section(section, b, EDITABLE[section])
    if section == "detector" and app.cfg["detector"]["chat_offset_s"] != old_offset:
        app.manager.reset_detectors()
    if section == "server":
        app._ts_cache = (0, None)
    log(f"[ui] settings saved: {section}")
    h.send_json({"ok": True, "settings": app.settings_view()})


@route("POST", "/api/onboarding/complete")
def r_onboarded(h):
    with A(h).store.lock:
        A(h).cfg["onboarded"] = True
        A(h).store.save()
    h.send_json({"ok": True})


@route("GET", "/api/system/check")
def r_check(h):
    h.send_json({"items": system.check(A(h).cfg)})


@route("POST", "/api/system/claude-test")
def r_claude_test(h):
    h.send_json(system.test_claude(A(h).cfg["clipper"].get("claude_model", "")))


@route("GET", "/api/system/tailscale")
def r_ts(h):
    h.send_json(system.tailscale_info(int(A(h).cfg["server"]["port"])))


@route("POST", "/api/system/tailscale-serve")
def r_ts_serve(h):
    res = system.tailscale_serve(int(A(h).cfg["server"]["port"]))
    A(h)._ts_cache = (0, None)
    h.send_json(res)


@route("POST", "/api/notify/test")
def r_notify_test(h):
    h.send_json(A(h).notifier.test())


@route("GET", "/api/update")
def r_update(h):
    h.send_json(A(h).updater.last)


@route("POST", "/api/update/(check|install)")
def r_update_do(h, action):
    u = A(h).updater
    h.send_json(u.check() if action == "check" else u.install())


@route("POST", "/api/restart")
def r_restart(h):
    h.send_json({"ok": True})
    A(h).manager.shutdown()
    restart_self()


# ------------------------------------------------------------------ main
def _lock_data_dir(data_dir: Path):
    """Only one running copy may use a data folder (two would process the same clips)."""
    f = open(data_dir / "app.lock", "a+")
    try:
        if sys.platform == "win32":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit(f"Another copy of Live Clipper is already using {data_dir}")
    return f  # keep the handle open for the life of the process


def serve(store: ConfigStore, open_browser=True):
    _lock = _lock_data_dir(store.cfg["data_dir"])  # noqa: F841
    app = App(store)
    Handler.app = app
    host, port = store.cfg["server"]["host"], int(store.cfg["server"]["port"])
    httpd = None
    for _ in range(20):  # the previous instance may still be releasing the port after a restart
        try:
            httpd = ThreadingHTTPServer((host, port), Handler)
            break
        except OSError:
            time.sleep(1)
    if httpd is None:
        raise SystemExit(f"port {port} is busy - is Live Clipper already running?")
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{port}"
    log(f"Live Clipper {__version__} on {url}  (config: {store.path})")
    app.manager.start_all()
    app.worker.requeue_unfinished()
    if open_browser and not store.cfg["onboarded"]:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        log("shutting down")
        app.manager.shutdown()
        httpd.server_close()
        os._exit(0)
