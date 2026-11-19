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


def test_sfx_nothing_by_default_then_packs_and_import(tmp_path: Path, monkeypatch):
    import io, json, zipfile
    assert sfx.library(tmp_path) == {}                                   # no default sounds
    sfx.install_pack(tmp_path, "basic-synth")
    lib = sfx.library(tmp_path)
    assert set(sfx.SYNTH) <= set(lib) and lib["boom"]["license"] == "synth"
    wav = lib["pop"]["path"].read_bytes()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:                                 # fake Kenney repo zip
        for n in ("click_001", "click_002", "error_003", "error_001", "glitch_004"):
            z.writestr(f"kenney-interface-sounds-master/Audio/{n}.ogg", wav)
    monkeypatch.setattr(sfx, "fetch", lambda url, *a, **k: buf.getvalue())
    sfx.install_pack(tmp_path, "kenney-interface")
    assert sfx.JOBS["kenney-interface"]["state"] == "done"
    lib = sfx.library(tmp_path)
    assert {"ui-click", "ui-error", "ui-glitch"} <= set(lib) and "error blip" in lib["ui-error"]["desc"]
    assert sfx.import_file(tmp_path, "Vine Boom.WAV", wav) == ["vine-boom"]
    lib = sfx.library(tmp_path)
    assert lib["vine-boom"]["source"] == "yours" and "punchline" in lib["vine-boom"]["desc"]  # catalog tag
    sfx.update_meta(tmp_path, "vine-boom", enabled=False)
    assert "vine-boom" not in sfx.library(tmp_path) and sfx.remove(tmp_path, "vine-boom")
    sfx.uninstall_pack(tmp_path, "kenney-interface")
    assert "ui-click" not in sfx.library(tmp_path)
    # search results + add with credit
    ov = {"results": [{"title": "Bruh", "url": "https://cdn.freesound.org/previews/1/bruh.mp3", "creator": "x",
                       "license": "by", "duration": 900, "source": "freesound", "foreign_landing_url": "https://freesound.org/s/1"},
                      {"title": "NC", "url": "https://cdn.freesound.org/p/2.mp3", "license": "by-nc", "duration": 500}]}
    monkeypatch.setattr(sfx, "fetch", lambda url, *a, **k: json.dumps(ov).encode() if "openverse" in url else wav)
    res = sfx.search("bruh", allow_attribution=True)["results"]
    assert len(res) == 1 and res[0]["license"] == "by" and res[0]["duration"] == 0.9
    name = sfx.add_from_web(tmp_path, res[0], name="bruh")
    assert "Bruh" in sfx.library(tmp_path)[name]["credit"]
    assert sfx.host_ok("https://cdn.freesound.org/a.mp3") and not sfx.host_ok("http://127.0.0.1/x")
    assert not sfx.host_ok("https://evil-freesound.org.example.com/x")


CTX_TR = {"segments": [{"i": 0, "s": 10, "e": 14, "text": "mano eu esqueci o freio"},
                       {"i": 1, "s": 20, "e": 22, "text": "caí do prédio"}]}
CAND = {"streamer": "cellbit", "duration": 60, "peak_offset": 20, "chat_sample": [{"t": 21, "user": "a", "text": "KKKKK caiu"}]}


def test_edits_without_evidence_are_dropped():
    ctx = plan._ctx(CTX_TR, CAND, [11.0, 15.0, 19.0, 23.0], "Cellbit")
    edits = [{"type": "text", "t": 12, "text": "ESQUECEU O FREIO", "why": {"src": "speech", "seg": 0}},
             {"type": "text", "t": 21, "text": "BIGFOOT APARECEU", "why": {"src": "frame", "n": 4, "seen": "a bigfoot"}},
             {"type": "text", "t": 30, "text": "PÉ GRANDE"},                                  # no evidence
             {"type": "zoom", "t": 21, "why": {"src": "chat", "quote": "kkkkk caiu"}},
             {"type": "shake", "t": 40, "why": {"src": "speech", "seg": 0}},                 # evidence elsewhere
             {"type": "sfx", "t": 22, "name": "boom", "why": {"src": "frame", "n": 9, "seen": "x"}}]  # bad frame
    out = plan.sanitize_edits(edits, 10, 45, S, {"boom"}, ctx)
    assert [(e["type"], e.get("text")) for e in out] == [("text", "ESQUECEU O FREIO"), ("text", "BIGFOOT APARECEU"),
                                                         ("zoom", None)]
    assert out[0]["grounded"] and not out[1]["grounded"] and out[0]["why"] == "speech #0"


def test_fact_check_without_claude_keeps_only_grounded():
    ctx = plan._ctx(CTX_TR, CAND, [11.0, 15.0, 19.0, 23.0], "Cellbit")
    p = {"start": 10, "end": 40, "hook_text": "ELE VIU O BIGFOOT", "title": "Cellbit caiu do PRÉDIO no GTA 6 😭",
         "edits": plan.sanitize_edits([
             {"type": "text", "t": 12, "text": "ESQUECEU O FREIO", "why": {"src": "speech", "seg": 0}},
             {"type": "text", "t": 21, "text": "BIGFOOT APARECEU", "why": {"src": "frame", "n": 4, "seen": "bigfoot"}}],
             10, 40, S, set(), ctx)}
    out = plan.verify(p, CAND, CTX_TR, ctx, {"display_name": "Cellbit"}, "pt", None, None)
    assert [e["text"] for e in out["edits"]] == ["ESQUECEU O FREIO"]
    assert out["hook_text"] == "" and out["title"].startswith("Cellbit caiu do PR")
    assert len(out["fact_check"]["removed"]) == 2 and "verify" not in out["edits"][0]


def test_fact_check_with_claude_answers(monkeypatch):
    ctx = plan._ctx(CTX_TR, CAND, None, "Cellbit")
    p = {"start": 10, "end": 40, "hook_text": "OLHA ISSO", "title": "Cellbit no GTA 6",
         "edits": [{"type": "text", "t": 2, "text": "ESQUECEU O FREIO", "why": "speech #0", "verify": True}]}
    monkeypatch.setattr(plan, "_claude", lambda *a, **k: {"ok": [False, True, True]})
    out = plan.verify(p, CAND, CTX_TR, ctx, {}, "pt", "", None)
    assert out["edits"] == [] and out["hook_text"] == "OLHA ISSO" and out["fact_check"]["by"] == "claude"


def test_clean_text_strips_codes_and_emoji():
    assert plan.clean_text("{\\p1}m 0 0 l 5 5{\\p0} CAIU 🦶!") == "m 0 0 l 5 5 CAIU !"
    assert plan.clean_text("Título 😭", allow_emoji=True) == "Título 😭"
