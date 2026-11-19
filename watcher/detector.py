"""Hype detector: combines chat activity and voice loudness into a per-second score.

Time base: integer UTC epoch seconds of the *video* (when we received that part of the stream).
Chat arrives `chat_offset_s` later than the moment it reacts to, so chat at second t+offset
is attributed to video second t.
"""
from collections import deque

import numpy as np


def _robust_z(value, history, floor):
    if value is None or len(history) < 30:
        return 0.0
    arr = np.fromiter(history, dtype=float)
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med))) * 1.4826
    return (value - med) / max(mad, floor)


class Detector:
    def __init__(self, dcfg: dict):
        self.c = dcfg
        self.offset = int(dcfg["chat_offset_s"])
        self.chat_w: dict[int, float] = {}      # chat-arrival second -> weighted unique chatters
        self.supp: set[int] = set()             # chat-arrival seconds under raid/gift suppression
        self.audio: dict[int, float | None] = {}  # video second -> voice-band dB (None = no speech)
        self.chat_hist = deque(maxlen=int(dcfg["baseline_s"]))
        self.audio_hist = deque(maxlen=int(dcfg["baseline_s"]))
        self.pending_chat = deque()             # (t, value) waiting 30s before entering baseline
        self.pending_audio = deque()
        self.first_t = None
        self.last_eval = None
        self.event = None
        self.last_trigger = -10**12
        self.scores: dict[int, tuple] = {}

    # ---- inputs -------------------------------------------------------------
    def add_chat(self, sec: int, weight: float):
        self.chat_w[sec] = self.chat_w.get(sec, 0.0) + weight

    def add_suppression(self, sec: int):
        for s in range(sec - 3, sec + int(self.c.get("suppress_s", 30))):
            self.supp.add(s)

    def set_audio(self, sec: int, db):
        self.audio[sec] = db

    # ---- series -------------------------------------------------------------
    def _chat_val(self, t):
        o = t + self.offset
        return sum(self.chat_w.get(o + d, 0.0) for d in range(-2, 3))

    def _audio_val(self, t):
        vals = [self.audio.get(t + d) for d in (-1, 0, 1)]
        vals = [v for v in vals if v is not None]
        return max(vals) if vals else None

    # ---- main step ----------------------------------------------------------
    def advance(self, ready_t: int):
        """Evaluate every second up to ready_t. Returns finalized events (dicts)."""
        out = []
        if self.last_eval is None:
            known = [k for k in self.audio] + [k - self.offset for k in self.chat_w]
            if not known:
                return out
            self.last_eval = min(known) - 1
            self.first_t = self.last_eval + 1
        thr = float(self.c["threshold"])
        for t in range(self.last_eval + 1, ready_t + 1):
            cv, av = self._chat_val(t), self._audio_val(t)
            zc = _robust_z(cv, self.chat_hist, 1.0)
            za = _robust_z(av, [x for x in self.audio_hist if x is not None], 2.0) if av is not None else 0.0
            zc, za = float(np.clip(zc, -2, 8)), float(np.clip(za, -2, 8))
            if any((t + self.offset + d) in self.supp for d in range(-2, 3)):
                zc = min(zc, 1.0)  # raid / gifted-sub floods are not hype
            score = self.c["w_chat"] * zc + self.c["w_audio"] * za
            recent = [self.scores.get(t - d, (0.0, 0.0, 0.0)) for d in range(1, 11)]
            chat_recent = zc > 1.5 or any(r[1] > 1.5 for r in recent)
            voice_recent = za > 1.5 or any(r[2] > 1.5 for r in recent)
            if (zc > 1.5 or za > 1.5) and chat_recent and voice_recent:
                score += self.c["combo_bonus"]
            self.scores[t] = (score, zc, za)

            # baseline updates lag 30s so a hype moment doesn't raise its own baseline instantly
            self.pending_chat.append((t, cv))
            self.pending_audio.append((t, av))
            while self.pending_chat and self.pending_chat[0][0] <= t - 30:
                self.chat_hist.append(self.pending_chat.popleft()[1])
                self.audio_hist.append(self.pending_audio.popleft()[1])

            warm = (t - self.first_t) >= int(self.c["warmup_s"])
            if not warm:
                continue
            if score >= thr:
                if self.event is None:
                    self.event = {"start": t, "peak": t, "score": score, "z_chat": zc, "z_audio": za}
                elif score > self.event["score"]:
                    self.event.update(peak=t, score=score, z_chat=zc, z_audio=za)
            if self.event and (score < thr * 0.6 or t - self.event["peak"] > 20):
                ev, self.event = self.event, None
                if ev["peak"] - self.last_trigger >= int(self.c["cooldown_s"]):
                    self.last_trigger = ev["peak"]
                    out.append(ev)
        self.last_eval = max(self.last_eval, ready_t)
        self._prune(ready_t)
        return out

    def _prune(self, now_t):
        cut = now_t - int(self.c["baseline_s"]) - 120
        for d in (self.chat_w, self.audio, self.scores):
            for k in [k for k in d if k < cut]:
                del d[k]
        self.supp = {s for s in self.supp if s >= cut}


class StreamerDetectorSettings:
    """Detection settings for one streamer: the global values with that streamer's overrides on top.
    Read live on every access, so edits in the UI apply immediately."""

    def __init__(self, global_dc: dict, streamer_fn):
        self.g, self.s = global_dc, streamer_fn

    def _streamer(self):
        try:
            return self.s() or {}
        except KeyError:
            return {}

    def __getitem__(self, k):
        s = self._streamer()
        if k == "threshold":
            sens = float(s.get("sensitivity") or 1.0)
            return float(self.g["threshold"]) / max(0.2, sens)
        if k in ("cooldown_s", "chat_offset_s") and s.get(k) not in (None, ""):
            return s[k]
        if k == "keywords":
            return list(self.g["keywords"]) + [str(x).lower() for x in s.get("keywords_extra") or []]
        return self.g[k]

    def get(self, k, default=None):
        try:
            return self[k]
        except KeyError:
            return default
