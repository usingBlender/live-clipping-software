from clipper import plan, render


def test_caption_groups_and_ass():
    words = [{"w": w, "s": i * 0.4, "e": i * 0.4 + 0.3} for i, w in enumerate("mano olha isso não acredito".split())]
    groups = render.group_words(words)
    assert sum(len(g) for g in groups) == 5 and all(len(g) <= 3 for g in groups)
    ass = render.build_ass(words, "olha isso", "cam_top", "Sans", 10)
    assert "Style: Cap" in ass and "OLHA ISSO" in ass and ass.count("Dialogue:") == 6


def test_layout_filters_have_base_output():
    s = {"layout": "cam_top", "cam": {"x": 0.7, "y": 0.7, "w": 0.25, "h": 0.25}}
    assert "[base]" in render.layout_filter(s, 1920, 1080)
    assert "[base]" in render.layout_filter({"layout": "fit_blur"}, 1920, 1080)


def test_plan_sanitize_clamps_bad_llm_output():
    cand = {"streamer": "x", "duration": 55.0, "peak_offset": 35.0}
    cfg = {"clipper": {"language": "pt", "default_hashtags": ["#gta6"]}}
    p = plan.sanitize({"start": -5, "end": 500, "hashtags": ["gtavi"], "hook_text": "olha"}, cand, cfg)
    assert 0 <= p["start"] < p["end"] <= 55 and p["end"] - p["start"] <= 59
    assert p["hashtags"][0] == "#gta6" and "#gtavi" in p["hashtags"] and p["hook_text"] == "OLHA"
