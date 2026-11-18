"""In-process event bus. Anything can publish(); every open dashboard (SSE connection)
gets its own queue. Recent non-metric events are kept so a freshly opened page has history."""
import itertools
import queue
import threading
import time
from collections import deque


class Bus:
    def __init__(self, keep=300):
        self.lock = threading.Lock()
        self.subs: set[queue.Queue] = set()
        self.recent = deque(maxlen=keep)
        self.ids = itertools.count(1)

    def publish(self, type_: str, data: dict | None = None, keep: bool = True):
        ev = {"id": next(self.ids), "type": type_, "ts": time.time(), "data": data or {}}
        with self.lock:
            if keep:
                self.recent.append(ev)
            for q in list(self.subs):
                try:
                    q.put_nowait(ev)
                except queue.Full:
                    pass  # slow client: drop, it will resync from /api/overview
        return ev

    def subscribe(self) -> queue.Queue:
        q = queue.Queue(maxsize=2000)
        with self.lock:
            self.subs.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.subs.discard(q)

    def history(self, n=100):
        with self.lock:
            return list(self.recent)[-n:]


bus = Bus()


class LogBuffer:
    """Keeps the last lines of the app log in memory for the Logs view."""

    def __init__(self, keep=1000):
        self.lines = deque(maxlen=keep)
        self.lock = threading.Lock()
        self.file = None

    def open(self, path):
        self.file = open(path, "a", encoding="utf-8", buffering=1)

    def log(self, msg: str, level: str = "info"):
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        with self.lock:
            self.lines.append(line)
            try:
                print(line, flush=True)
            except Exception:  # noqa: BLE001  (no console when run windowless)
                pass
            if self.file:
                self.file.write(line + "\n")
        bus.publish("log", {"line": line, "level": level}, keep=False)

    def tail(self, n=300):
        with self.lock:
            return list(self.lines)[-n:]


logbuf = LogBuffer()
log = logbuf.log
