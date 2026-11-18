import random

from watcher.detector import Detector

DC = dict(chat_offset_s=6, baseline_s=600, warmup_s=180, threshold=3.5, cooldown_s=90, pre_s=35, post_s=20,
          w_chat=0.6, w_audio=0.4, combo_bonus=1.0, suppress_s=30)


def run(hype, raid=None, seconds=2700):
    random.seed(1)
    d, t0, events = Detector(DC), 1_000_000, []
    for t in range(t0, t0 + seconds):
        rel = t - t0
        n = random.choice([1, 2, 2, 3])
        if any(h + 6 <= rel <= h + 16 for h in hype):
            n += 15
        if raid is not None and raid <= rel <= raid + 10:
            n += 20
            if rel == raid:
                d.add_suppression(t)
        for _ in range(n):
            d.add_chat(t, 1.0)
        db = random.gauss(-20, 2) if random.random() < 0.6 else None
        if any(h <= rel <= h + 3 for h in hype):
            db = -5
        d.set_audio(t, db)
        events += d.advance(t - 9)
    return [e["peak"] - t0 for e in events]


def test_detects_hype_moments_near_their_start():
    peaks = run([900, 1500])
    assert len(peaks) == 2
    assert all(abs(p - h) <= 3 for p, h in zip(peaks, [900, 1500]))


def test_raid_flood_is_ignored():
    assert run([900], raid=2100) == run([900])


def test_no_triggers_during_warmup():
    assert run([100]) == []


def test_cooldown_merges_close_moments():
    assert len(run([900, 940])) == 1
