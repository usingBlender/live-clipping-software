import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

_lock = threading.Lock()

SCHEMA = [
    """create table if not exists clips (
      id text primary key, streamer text, created real, status text,
      score real, title text, hashtags text, hook text, reason text,
      source_path text, output_path text, preview_path text,
      plan_json text, candidate_json text, decided real)""",
    """create table if not exists sessions (token text primary key, created real, expires real, agent text)""",
]


class DB:
    def __init__(self, path: Path):
        self.path = str(path)
        with self._c() as c:
            for s in SCHEMA:
                c.execute(s)

    def _c(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    # ---- clips -------------------------------------------------------------
    def upsert(self, **row):
        with _lock, self._c() as c:
            cols = ",".join(row)
            qs = ",".join("?" for _ in row)
            upd = ",".join(f"{k}=excluded.{k}" for k in row if k != "id")
            c.execute(f"insert into clips ({cols}) values ({qs}) on conflict(id) do update set {upd}",
                      [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                       for v in row.values()])

    def get(self, cid):
        with self._c() as c:
            r = c.execute("select * from clips where id=?", (cid,)).fetchone()
            return dict(r) if r else None

    def list(self, status=None, limit=100, streamer=None):
        q, args = "select * from clips where 1=1", []
        if status:
            q += " and status=?"
            args.append(status)
        if streamer:
            q += " and streamer=?"
            args.append(streamer)
        q += " order by created desc limit ?"
        args.append(limit)
        with self._c() as c:
            return [dict(r) for r in c.execute(q, args)]

    by_status = list

    def decide(self, cid, status, title=None, hashtags=None):
        with _lock, self._c() as c:
            c.execute("update clips set status=?, decided=?, title=coalesce(?,title), "
                      "hashtags=coalesce(?,hashtags) where id=?",
                      (status, time.time(), title, hashtags, cid))

    def stats_today(self) -> dict:
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        with self._c() as c:
            rows = c.execute("select status, count(*) n from clips where created>=? group by status",
                             (start,)).fetchall()
            pending = c.execute("select count(*) from clips where status='pending'").fetchone()[0]
        by = {r["status"]: r["n"] for r in rows}
        return {"candidates": sum(by.values()), "pending_total": pending, **{f"today_{k}": v for k, v in by.items()}}

    # ---- sessions ------------------------------------------------------------
    def add_session(self, token, days=30, agent=""):
        now = time.time()
        with _lock, self._c() as c:
            c.execute("insert into sessions values (?,?,?,?)", (token, now, now + days * 86400, agent[:200]))

    def valid_session(self, token) -> bool:
        if not token:
            return False
        with self._c() as c:
            r = c.execute("select expires from sessions where token=?", (token,)).fetchone()
        return bool(r and r[0] > time.time())

    def drop_session(self, token=None, all_=False):
        with _lock, self._c() as c:
            if all_:
                c.execute("delete from sessions")
            else:
                c.execute("delete from sessions where token=?", (token,))
