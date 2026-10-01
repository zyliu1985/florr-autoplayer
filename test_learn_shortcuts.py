"""learn_shortcuts.py 的纯逻辑测试: 不依赖真实屏幕/游戏.

覆盖: 刷墙的几何工具(carve_line/carve_disk)、墙距离判定(_dist_to_walkable),
以及 main() 的地图名解析(任意地图 / 默认 anthell / 非法名断言).
"""
import os

import cv2
import numpy as np
import pytest

import learn_shortcuts


def _all_wall(shape=(40, 40)):
    return np.zeros(shape, dtype=np.uint8)   # 全 0 = 全是墙


def test_carve_line_connects_two_points():
    # 把两点间 Bresenham 连线刷成可走(含端点).
    b = _all_wall()
    learn_shortcuts._carve_line(b, (5, 5), (10, 10), radius=1)
    assert b[5, 5] == 255
    assert b[10, 10] == 255
    assert b[7, 7] == 255            # 中间像素也被连上
    assert b[1, 1] == 0              # 无关区域不动


def test_carve_line_horizontal():
    # 水平捷径: 一整行被刷通.
    b = _all_wall()
    learn_shortcuts._carve_line(b, (2, 7), (9, 7), radius=1)
    assert (b[7, 2:10] == 255).all()
    assert b[7, 0] == 0 and b[7, 11] == 0   # 两端之外不越界


def test_carve_disk_respects_radius():
    b = _all_wall((21, 21))
    learn_shortcuts._carve_disk(b, 10, 10, radius=2)
    # 半径 2 的圆盘: 距离≤2 的像素被刷, 距离>2 不动.
    assert b[10, 10] == 255
    assert b[12, 10] == 255          # dy=2 在圆内
    assert b[13, 10] == 0            # dy=3 超出半径
    assert b[8, 8] == 0              # dx²+dy²=8 > 4 → 圆外不刷


def test_dist_to_walkable_counts_steps_from_wall():
    # 左上 5x5 是可走区, 其余全墙.
    b = np.full((20, 20), 0, dtype=np.uint8)
    b[0:5, 0:5] = 255
    # 可走区角点在 (4,4); 墙像素到它的切比雪夫距离 = max(dx, dy).
    assert learn_shortcuts._dist_to_walkable(b, 10, 10) == 6   # max(6,6)
    assert learn_shortcuts._dist_to_walkable(b, 6, 5) == 2     # max(2,1)
    assert learn_shortcuts._dist_to_walkable(b, 5, 4) == 1     # 可走区边沿外 1px 的墙
    assert learn_shortcuts._dist_to_walkable(b, 100, 100) == 999   # 出界


def test_dist_to_walkable_zero_inside_walkable():
    # 玩家站在可走区内部 → 距离 0(不会触发"在墙里").
    b = np.full((20, 20), 0, dtype=np.uint8)
    b[0:5, 0:5] = 255
    assert learn_shortcuts._dist_to_walkable(b, 4, 4) == 0   # 可走像素自身 = 0


def test_main_uses_default_map_anthell(monkeypatch):
    # 不带参数 → 默认 anthell; 一轮轮询后退出(Ctrl+C 由 monkeypatch 模拟).
    monkeypatch.setattr(learn_shortcuts.sys, "argv", ["learn_shortcuts.py"])
    # main() 里会直接改 utils.MAP —— 用 monkeypatch 设哨兵值, teardown 自动恢复
    # 原值, 免得把全局 MAP 污染成别的图影响其他测试文件(传送逃生读 utils.MAP).
    monkeypatch.setattr(learn_shortcuts.utils, "MAP", "")
    # 第一次轮询直接抛 KeyboardInterrupt → main 捕获后 return.
    monkeypatch.setattr(
        learn_shortcuts, "_raw_player_position",
        lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    learn_shortcuts.main()
    assert learn_shortcuts.utils.MAP == "anthell"


def test_main_accepts_any_map_name(monkeypatch):
    for name in ("desert", "ocean", "garden"):
        monkeypatch.setattr(learn_shortcuts.sys, "argv", ["learn_shortcuts.py", name])
        monkeypatch.setattr(learn_shortcuts.utils, "MAP", "")   # teardown 恢复原值
        monkeypatch.setattr(
            learn_shortcuts, "_raw_player_position",
            lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
        learn_shortcuts.main()
        assert learn_shortcuts.utils.MAP == name


def test_main_rejects_unknown_map(monkeypatch):
    monkeypatch.setattr(learn_shortcuts.sys, "argv", ["learn_shortcuts.py", "nope"])
    with pytest.raises(AssertionError):
        learn_shortcuts.main()


def test_maps_dir_has_all_farmable_maps():
    # 通用化前提: 每个可刷地图都有模板文件.
    for name in ("desert", "ocean", "anthell", "garden"):
        assert os.path.isfile(f"maps/{name}.png"), name
