"""Outbound notifications: Telegram (built in, optional) and generic webhooks (e.g. n8n)."""
import json
import mimetypes
import threading
import uuid
import urllib.request
from pathlib import Path

from common.bus import log

TG = "https://api.telegram.org/bot{token}/{method}"


def _post_json(url, payload, timeout=20):
    req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def _post_multipart(url, fields: dict, file_field: str, path: Path, timeout=120):
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
                 f"filename=\"{path.name}\"\r\nContent-Type: {ctype}\r\n\r\n".encode())
    body = b"".join(parts) + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


class Notifier:
    def __init__(self, store):
        self.store = store

    @property
    def n(self):
        return self.store.cfg["notify"]

    def telegram_ready(self) -> bool:
        return bool(self.n.get("telegram_token") and self.n.get("telegram_chat_id"))

    def tg_message(self, text):
        return _post_json(TG.format(token=self.n["telegram_token"], method="sendMessage"),
                          {"chat_id": self.n["telegram_chat_id"], "text": text, "disable_web_page_preview": True})

    def tg_file(self, path: Path, caption: str, as_video=True):
        method, field = ("sendVideo", "video") if as_video else ("sendDocument", "document")
        if path.stat().st_size > 49_000_000:
            return self.tg_message(caption + "\n\n(file too big for Telegram - open it in the app)")
        return _post_multipart(TG.format(token=self.n["telegram_token"], method=method),
                               {"chat_id": self.n["telegram_chat_id"], "caption": caption[:1000],
                                "supports_streaming": "true"}, field, path)

    def test(self) -> dict:
        out = {}
        if self.telegram_ready():
            try:
                self.tg_message("✅ Live Clipper is connected. You'll get clips here.")
                out["telegram"] = "ok"
            except Exception as e:  # noqa: BLE001
                out["telegram"] = f"failed: {e}"
        else:
            out["telegram"] = "not configured"
        for url in self.n.get("webhooks") or []:
            try:
                _post_json(url, {"type": "test", "message": "Live Clipper webhook test"})
                out[url] = "ok"
            except Exception as e:  # noqa: BLE001
                out[url] = f"failed: {e}"
        return out

    # ---- events --------------------------------------------------------------
    def event(self, type_: str, data: dict, files: dict | None = None):
        threading.Thread(target=self._event, args=(type_, data, files or {}), daemon=True).start()

    def _event(self, type_, data, files):
        for url in self.n.get("webhooks") or []:
            try:
                _post_json(url, {"type": type_, **data})
            except Exception as e:  # noqa: BLE001
                log(f"[notify] webhook {url}: {e}", "warn")
        if not self.telegram_ready():
            return
        try:
            if type_ == "clip.rendered" and self.n.get("telegram_preview", True):
                cap = (f"🎬 {data['streamer']} · score {data.get('score')}\n{data['title']}\n"
                       f"Hook: {data.get('hook', '')}\nWhy: {data.get('reason', '')}\n\nReview: {data['review_url']}")
                self.tg_file(Path(files["preview"]), cap, as_video=True)
            elif type_ == "clip.approved" and self.n.get("telegram_final", True):
                self.tg_file(Path(files["final"]), "✅ Ready to post\n\n" + data["caption"], as_video=False)
            elif type_ == "update.available":
                self.tg_message(f"⬆️ Live Clipper {data['latest']} is available (you have {data['current']}).")
        except Exception as e:  # noqa: BLE001
            log(f"[notify] telegram: {e}", "warn")
