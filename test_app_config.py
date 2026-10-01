import copy
import json

import pytest

import app_config


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setattr(app_config, "CONFIG_PATH", str(p))
    return p


def test_load_missing_file_returns_defaults(cfg_path):
    assert app_config.load_config() == app_config.DEFAULTS
    # 返回的必须是副本, 改它不能污染 DEFAULTS
    got = app_config.load_config()
    got["farming_duration"] = 999
    assert app_config.DEFAULTS["farming_duration"] != 999


def test_save_then_load_roundtrips(cfg_path):
    cfg = copy.deepcopy(app_config.DEFAULTS)
    cfg["map"] = "ocean"
    cfg["location"] = [100, 120]
    cfg["farming_area"] = [[10, 10], [40, 40]]
    cfg["enemy_ai_enabled"] = False
    app_config.save_config(cfg)
    assert app_config.load_config() == cfg


def test_bad_json_falls_back_to_defaults(cfg_path):
    cfg_path.write_text("{ not json", encoding="utf-8")
    assert app_config.load_config() == app_config.DEFAULTS


def test_partial_file_fills_missing_keys(cfg_path):
    cfg_path.write_text(json.dumps({"map": "anthell"}), encoding="utf-8")
    got = app_config.load_config()
    assert got["map"] == "anthell"
    assert got["farming_duration"] == app_config.DEFAULTS["farming_duration"]


def test_garden_is_valid_map(cfg_path):
    # garden 现在是正式可选的刷图地图, config 校验必须放行.
    cfg_path.write_text(json.dumps({"map": "garden"}), encoding="utf-8")
    got = app_config.load_config()
    assert got["map"] == "garden"


def test_farming_path_valid_roundtrips(cfg_path):
    # 固定路径刷怪: 合法路径(≥2 个 int 对)原样保存.
    cfg_path.write_text(json.dumps({"farming_path": [[5, 5], [20, 30], [40, 10]]}),
                        encoding="utf-8")
    got = app_config.load_config()
    assert got["farming_path"] == [[5, 5], [20, 30], [40, 10]]


def test_farming_path_default_is_none():
    # 默认(旧 config 无此键)→ None = 区域随机模式.
    assert app_config.DEFAULTS["farming_path"] is None


@pytest.mark.parametrize("bad", [
    {"farming_path": [[5, 5]]},          # 单点没意义
    {"farming_path": [[1, 2, 3], [4, 5, 6]]},   # 不是 int 对
    {"farming_path": [["a", "b"], ["c", "d"]]},
    {"farming_path": "not-a-path"},
    {"farming_path": [[1.5, 2], [3, 4]]},
])
def test_bad_farming_path_reverts_to_none(cfg_path, bad):
    cfg_path.write_text(json.dumps(bad), encoding="utf-8")
    got = app_config.load_config()
    assert got["farming_path"] is None


@pytest.mark.parametrize("bad", [
    {"map": "nonsense"},
    {"map": 123},
    {"location": [1, 2, 3]},
    {"location": "12,32"},
    {"farming_area": [[1, 2], [3, 4], [5, 6]]},
    {"farming_area": [[1, 2], [3]]},
    {"farming_duration": -5},
    {"farming_duration": "300"},
    {"consecutive_short_round_limit": 0},
    {"enemy_ai_enabled": "yes"},
])
def test_bad_value_reverts_that_key_to_default(cfg_path, bad):
    key = next(iter(bad))
    cfg_path.write_text(json.dumps(bad), encoding="utf-8")
    got = app_config.load_config()
    assert got[key] == app_config.DEFAULTS[key]


def test_top_level_not_dict_falls_back(cfg_path):
    cfg_path.write_text("[1, 2, 3]", encoding="utf-8")
    assert app_config.load_config() == app_config.DEFAULTS


def test_unknown_keys_are_dropped(cfg_path):
    cfg_path.write_text(json.dumps({"map": "desert", "bogus": 1}), encoding="utf-8")
    assert "bogus" not in app_config.load_config()
