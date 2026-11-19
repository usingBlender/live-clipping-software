from pathlib import Path

from clipper import plan, render, sfx
from clipper.transcribe import caption_words
from watcher.detector import StreamerDetectorSettings

S = {"layout": "cam_top", "cam": {"x": 0.7, "y": 0.7, "w": 0.25, "h": 0.25}, "edit_intensity": "normal"}


def test_sanitize_edits_converts_times_and_filters():
    edits = [{"type": "zoom", "t": 12, "dur": 9, "focus": "cam", "amount": 3},
             {"type": "text", "t": 11, "text": "ESQUECEU O FREIO"},
             {"type": "sfx", "t": 12, "name": "not-a-sound"},
             {"type": "shake", "t": 5},              # before the clip -> dropped
             {"type": "bogus", "t": 12}]
    out = plan.sanitize_edits(edits, 10, 30, S, {"boom", "pop"})
    types = [e["type"] for e in out]
    assert types == ["text", "zoom", "sfx"]
    z = out[1]
    assert z["t"] == 2.0 and z["dur"] == 3.0 and z["amount"] == 1.6
    assert out[2]["name"] == "boom"


def test_intensity_and_switches_limit_effects():
    many = [{"type": "sfx", "t": 10 + i, "name": "boom"} for i in range(20)]
    assert len(plan.sanitize_edits(many, 10, 40, {**S, "edit_intensity": "light"}, {"boom"})) == 3
    assert plan.sanitize_edits(many, 10, 40, {**S, "edit_sfx": False}, {"boom"}) == []
    cam = [{"type": "cam_focus", "t": 12}]
    assert plan.sanitize_edits(cam, 10, 40, {**S, "layout": "fit_blur"}, set()) == []


def test_caption_words_streamer_only():
    tr = {"segments": [{"i": 0, "lang": "pt", "lang_p": 0.9}, {"i": 1, "lang": "en", "lang_p": 0.95}],
          "words": [{"w": "mano", "s": 0, "e": 0.3, "seg": 0}, {"w": "mayday", "s": 1, "e": 1.3, "seg": 1}]}
    assert [w["w"] for w in caption_words(tr, "streamer", None, "pt")] == ["mano"]     # language fallback
    assert [w["w"] for w in caption_words(tr, "streamer", [1], "pt")] == ["mayday"]    # AI decision wins
    assert len(caption_words(tr, "all", None, "pt")) == 2 and caption_words(tr, "off", None, "pt") == []


def test_effects_graph_builds_every_effect():
    edits = [{"type": "zoom", "t": 1, "dur": 1, "focus": "game", "amount": 1.3},
             {"type": "cam_focus", "t": 3, "dur": 2}, {"type": "shake", "t": 4, "dur": 0.3}]
    g = render.effects_graph(S, 1920, 1080, 60.0, 600, 1800, edits, "x.ass", True)
    assert "setpts=N/(60.000000*TB)" in g and "trim=start_frame=600:end_frame=1800" in g
    assert "[camfull]" in g and "crop=" in g and g.endswith("[v]") and "fontsdir=fonts" in g
    a = render.audio_graph(10, 30, [(1, 2.5, 0.8)])
    assert "adelay=2500|2500" in a and "amix=inputs=2" in a


def test_streamer_detection_overrides():
    g = {"threshold": 3.0, "cooldown_s": 90, "keywords": ["kkkk"]}
    st = {"sensitivity": 1.5, "cooldown_s": None, "keywords_extra": ["LUL"]}
    v = StreamerDetectorSettings(g, lambda: st)
    assert v["threshold"] == 2.0 and v["cooldown_s"] == 90 and v["keywords"] == ["kkkk", "lul"]


def test_sfx_library_sources_and_import(tmp_path: Path):
    lib = sfx.library(tmp_path)
    assert set(sfx.SYNTH) <= set(lib) and "ui-pop" in lib            # synthesized + bundled CC0
    assert all(v["path"].exists() and "gain_db" in v for v in lib.values())
    wav = (sfx.PACK_DIR / "kenney" / "ui-pop.wav").read_bytes()
    assert sfx.import_file(tmp_path, "Vine Boom.WAV", wav) == ["vine-boom"]
    lib = sfx.library(tmp_path)
    assert lib["vine-boom"]["source"] == "yours" and "punchline" in lib["vine-boom"]["desc"]  # catalog tag
    sfx.update_meta(tmp_path, "vine-boom", enabled=False)
    assert "vine-boom" not in sfx.library(tmp_path) and sfx.remove(tmp_path, "vine-boom")
