import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import yaml

from common.config import ConfigStore


def call(base, path, body=None, cookie=None, method=None):
    req = urllib.request.Request(base + path, method=method or ("POST" if body is not None else "GET"),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read()), r.headers.get("Set-Cookie")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()), None


def test_auth_flow_and_streamer_hotswap(tmp_path):
    from web import server
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({"data_dir": str(tmp_path / "d"), "update": {"repo": ""}}))
    app = server.App(ConfigStore(p))
    server.Handler.app = app
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        assert call(base, "/api/overview")[0] == 401
        code, _, cookie = call(base, "/api/auth/setup", {"password": "longenough1"})
        assert code == 200 and cookie
        ck = cookie.split(";")[0]
        assert call(base, "/api/auth/setup", {"password": "another123"})[0] == 403
        assert call(base, "/api/auth/login", {"password": "wrong-password"})[0] == 401
        code, body, _ = call(base, "/api/streamers", {"platform": "twitch", "channel": "twitch.tv/SomeOne", "enabled": False}, ck)
        assert code == 200 and body["streamer"]["name"] == "SomeOne"
        assert call(base, "/api/streamers", {"platform": "twitch", "channel": "SomeOne"}, ck)[0] == 400
        code, body, _ = call(base, "/api/streamers/SomeOne", {"layout": "fit_blur"}, ck, "PATCH")
        assert body["changed"] == ["layout"]
        code, body, _ = call(base, "/api/settings/detector", {"threshold": 5}, ck, "PATCH")
        assert app.cfg["detector"]["threshold"] == 5
        assert call(base, "/api/streamers/SomeOne", None, ck, "DELETE")[0] == 200
        assert app.cfg["streamers"] == []
    finally:
        httpd.shutdown()
        app.manager.shutdown()
