import yaml

from common.config import ConfigStore


def test_defaults_and_migration(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({"data_dir": str(tmp_path / "d"), "clipper": {"port": 9999, "language": "en"},
                                 "n8n": {"publish_webhook": "http://x/hook"},
                                 "streamers": [{"name": "a", "channel": "a"}]}))
    s = ConfigStore(p)
    assert s.cfg["server"]["port"] == 9999
    assert s.cfg["clipper"]["language"] == "en"
    assert s.cfg["notify"]["webhooks"] == ["http://x/hook"]
    assert s.cfg["streamers"][0]["layout"] == "cam_top"
    assert (tmp_path / "d" / "segments").is_dir()


def test_update_section_keeps_types_and_saves(tmp_path):
    p = tmp_path / "config.yaml"
    s = ConfigStore(p)
    s.update_section("detector", {"threshold": "4.25", "cooldown_s": "120", "nope": 1}, {"threshold", "cooldown_s"})
    assert s.cfg["detector"]["threshold"] == 4.25 and s.cfg["detector"]["cooldown_s"] == 120
    assert "nope" not in s.cfg["detector"]
    again = ConfigStore(p)
    assert again.cfg["detector"]["threshold"] == 4.25
