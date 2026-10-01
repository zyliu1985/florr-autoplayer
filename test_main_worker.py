import inspect
import io
import types

import numpy as np
import pytest

import main
import utils


def test_apply_worker_config_maps_keys(monkeypatch):
    applied = {}
    monkeypatch.setattr(main, "apply_map", lambda name: applied.setdefault("map", name))
    cfg = {
        "map": "ocean",
        "location": [11, 22],
        "farming_area": [[1, 2], [3, 4]],
        "farming_duration": 120,
        "consecutive_short_round_limit": 5,
        "enemy_ai_enabled": False,
        "auto_switch_server": False,
        "afk_enabled": True,
    }
    w = main._apply_worker_config(cfg)
    assert applied["map"] == "ocean"
    assert w["location"] == (11, 22)
    assert w["farming_area"] == [(1, 2), (3, 4)]
    assert w["farming_duration"] == 120
    assert w["short_round_limit"] == 5
    assert w["enemy_ai_enabled"] is False
    assert w["auto_switch_server"] is False
    assert w["farming_path"] is None


def test_apply_worker_config_farming_path_overrides_location_and_clamps(monkeypatch):
    # 固定路径: 目标点强制 = 路径起点(即便 config 里 location 是旧的); 越界点钳进 [0,299].
    applied = {}
    monkeypatch.setattr(main, "apply_map", lambda name: applied.setdefault("map", name))
    cfg = {
        "map": "ocean",
        "location": [99, 99],
        "farming_area": [[1, 2], [3, 4]],
        "farming_path": [[10, 20], [30, -5], [400, 55]],
        "farming_duration": 120,
        "consecutive_short_round_limit": 5,
        "enemy_ai_enabled": False,
        "auto_switch_server": False,
        "afk_enabled": True,
    }
    w = main._apply_worker_config(cfg)
    assert w["location"] == (10, 20)               # 路径起点即目标点
    assert w["farming_path"] == [(10, 20), (30, 0), (299, 55)]


def test_auto_switch_step_switches_when_limit_reached_and_switchable(monkeypatch):
    monkeypatch.setattr(utils, "MAP", "desert")
    action, count = main._auto_switch_step(False, 1, True, 2)
    assert action == "switch"
    assert count == 2


def test_auto_switch_step_blocked_on_anthell(monkeypatch):
    # ant_hell 上跑换服会卡退(用户实测) —— 攒够 limit 也必须走 "blocked",
    # 绝不能返回 "switch" 去调 switch_server().
    monkeypatch.setattr(utils, "MAP", "anthell")
    action, count = main._auto_switch_step(False, 1, True, 2)
    assert action == "blocked"
    assert count == 2


def test_auto_switch_step_none_below_limit(monkeypatch):
    monkeypatch.setattr(utils, "MAP", "desert")
    action, count = main._auto_switch_step(False, 1, True, 5)
    assert action == "none"
    assert count == 2


def test_auto_switch_step_none_when_switch_disabled(monkeypatch):
    monkeypatch.setattr(utils, "MAP", "desert")
    action, count = main._auto_switch_step(False, 2, False, 2)
    assert action == "none"
    assert count == 3


def test_auto_switch_step_full_round_resets_counter(monkeypatch):
    monkeypatch.setattr(utils, "MAP", "desert")
    action, count = main._auto_switch_step(True, 4, True, 2)
    assert action == "none"
    assert count == 0


def test_maybe_scan_enemies_disabled_never_touches_enemy_detect(monkeypatch):
    monkeypatch.setattr(main.enemy_detect, "scan_enemies",
                        lambda **k: (_ for _ in ()).throw(AssertionError("不该扫描")))
    decision, dets, last, scanned = main._maybe_scan_enemies(
        False, 1000.0, 0.0, ("chase", "x"), ["old"])
    assert decision == ("wander", None)
    assert dets == []
    assert last == 0.0
    assert scanned is False


def test_maybe_scan_enemies_throttled_returns_prev(monkeypatch):
    monkeypatch.setattr(main.enemy_detect, "scan_enemies",
                        lambda **k: (_ for _ in ()).throw(AssertionError("还没到扫描间隔")))
    prev, prev_dets = ("flee", [(1, 2)]), ["d1", "d2"]
    decision, dets, last, scanned = main._maybe_scan_enemies(True, 0.1, 0.0, prev, prev_dets)
    assert decision is prev
    assert dets is prev_dets
    assert last == 0.0
    assert scanned is False


def test_maybe_scan_enemies_scans_when_due(monkeypatch):
    monkeypatch.setattr(main.enemy_detect, "scan_enemies", lambda **k: ["det"])
    monkeypatch.setattr(main.enemy_detect, "select_action",
                        lambda dets, **k: ("chase", "target", 250, []))
    now = main.ENEMY_SCAN_INTERVAL + 1.0
    decision, dets, last, scanned = main._maybe_scan_enemies(
        True, now, 0.0, ("wander", None), [])
    assert decision == ("chase", "target", 250, [])
    assert dets == ["det"]
    assert last == now
    assert scanned is True


def test_maybe_scan_enemies_scan_error_degrades_to_wander(monkeypatch):
    monkeypatch.setattr(main.enemy_detect, "scan_enemies",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("model missing")))
    now = main.ENEMY_SCAN_INTERVAL + 1.0
    decision, dets, last, scanned = main._maybe_scan_enemies(
        True, now, 0.0, ("wander", None), ["old"])
    assert decision == ("wander", None)
    assert dets == []
    assert last == now
    assert scanned is True   # 尝试过一次观测 —— 算一次 miss


def test_auto_farming_accepts_enemy_ai_enabled_kwarg():
    sig = inspect.signature(main.auto_farming)
    assert "enemy_ai_enabled" in sig.parameters
    assert sig.parameters["enemy_ai_enabled"].kind == inspect.Parameter.KEYWORD_ONLY
    assert "farming_path" in sig.parameters   # 固定路径刷怪的新参数(默认 None = 区域模式)


def _stub_auto_farming_env(monkeypatch, death_calls_to_false=2):
    """把 auto_farming 主循环的实机依赖打桩掉. on_death_screen 前 N 次返回 False,
    再下一次抛 KeyboardInterrupt 让循环退出(不依赖刷满 duration)."""
    import types as _t
    calls = {"n": 0}

    def fake_death():
        calls["n"] += 1
        if calls["n"] <= death_calls_to_false:
            return False
        raise KeyboardInterrupt()

    monkeypatch.setattr(main.afk_watch, "poll_afk_pause", lambda: False)
    monkeypatch.setattr(main, "_garden_escape_needed", lambda: False)
    monkeypatch.setattr(main, "on_death_screen", fake_death)
    monkeypatch.setattr(main, "on_start_screen", lambda: False)
    monkeypatch.setattr(main, "get_player_position", lambda: (5, 5))
    monkeypatch.setattr(main, "if_in_area", lambda *a, **k: True, raising=False)
    monkeypatch.setattr(main, "_maybe_scan_enemies",
                        lambda *a, **k: (("wander", None), [], 0.0, False))
    monkeypatch.setattr(main, "overlay",
                        _t.SimpleNamespace(update=lambda **k: None), raising=False)
    return calls


def test_auto_farming_fixed_path_walks_in_order_then_loops(monkeypatch):
    # 固定路径: 依次走 path 点; 到终点(index=len-1)后循环回起点(index=0).
    targets = []
    monkeypatch.setattr(main, "move_to_position",
                        lambda cur, tgt, **k: (targets.append(tgt), True)[1])
    # 每轮迭代 on_death_screen 被调 2 次(循环顶 + 循环底), 走 4 次需要 8 次 False.
    _stub_auto_farming_env(monkeypatch, death_calls_to_false=8)
    path = [(10, 10), (20, 20), (30, 30)]
    with pytest.raises(KeyboardInterrupt):
        main.auto_farming([(0, 0), (9, 9)], duration=300,
                          enemy_ai_enabled=False, farming_path=path)
    # 3 个点依次走, 第 4 次循环回第 0 个(10,10).
    assert targets == [(10, 10), (20, 20), (30, 30), (10, 10)]


def test_auto_farming_fixed_path_does_not_advance_on_stuck(monkeypatch):
    # 卡住时不推进 index —— 脱困后重试当前点, 不会跳过它.
    targets = []
    results = iter(["stuck", True, True])
    monkeypatch.setattr(main, "move_to_position",
                        lambda cur, tgt, **k: (targets.append(tgt), next(results))[1])
    escapes = []
    monkeypatch.setattr(main, "_run_escape", lambda *a, **k: escapes.append(1))
    _stub_auto_farming_env(monkeypatch, death_calls_to_false=6)
    path = [(10, 10), (20, 20)]
    with pytest.raises(KeyboardInterrupt):
        main.auto_farming([(0, 0), (9, 9)], duration=300,
                          enemy_ai_enabled=False, farming_path=path)
    # 第一个点卡住(不推进)→ 重试同一个点走成功 → 再走到第二个点.
    assert targets == [(10, 10), (10, 10), (20, 20)]
    assert escapes == [1]


def test_worker_graceful_exit_resets_keyboard_then_exits(monkeypatch):
    called = []
    monkeypatch.setattr(main, "reset_keyboard", lambda: called.append("reset"))
    with pytest.raises(SystemExit):
        main._worker_graceful_exit(15, None)
    assert called == ["reset"]


class _StubOverlay:
    def update(self, **kw):
        pass

    def show_warning(self, *a, **k):
        pass

    def hide_warning(self):
        pass


def test_run_worker_does_not_start_florr_auto_afk(monkeypatch):
    """florr-auto-afk 的生命周期归 GUI. worker 一旦自己调
    ensure_florr_auto_afk_running(), 在 exe 缺失时它会走到 input() —— 而
    console=False 打包出来的 worker stdin 是死的, 那一下直接把 worker 撂倒
    (RuntimeError: lost sys.stdin), 第一轮都跑不到. 而且用户刚在界面上关掉
    AFK 开关, worker 又会把它拉回来.
    """
    monkeypatch.setattr(main.cdp_bridge, "is_dedicated_chrome_ready", lambda: True)
    monkeypatch.setattr(
        main.afk_watch, "ensure_florr_auto_afk_running",
        lambda *a, **k: pytest.fail("worker 不该自己去拉起 florr-auto-afk"))
    monkeypatch.setattr(main, "create_overlay", lambda *a, **k: _StubOverlay())
    monkeypatch.setattr(main, "overlay", None, raising=False)
    monkeypatch.setattr(main, "_apply_worker_config", lambda cfg: {
        "location": (1, 2),
        "farming_area": [(0, 0), (9, 9)],
        "farming_path": None,
        "farming_duration": 300,
        "short_round_limit": 2,
        "enemy_ai_enabled": False,
        "auto_switch_server": False,
    })
    # 主循环体的第一个调用 —— 在这里掐断, 前面的 setup 已经全跑完了.
    monkeypatch.setattr(main, "on_death_screen",
                        lambda: (_ for _ in ()).throw(KeyboardInterrupt))

    with pytest.raises(KeyboardInterrupt):
        main.run_worker({})


def test_worker_stdin_watcher_resets_keyboard_on_eof(monkeypatch):
    """GUI 关掉 worker 的 stdin 管道 = 停止请求. 打包成 console=False 之后
    CTRL_BREAK / SIGTERM 都不一定送得到, 这条 EOF 路径是唯一保证"停止"时
    space+WASD 会被松开的机制 —— 它坏了, 每次停止都把角色卡在按住状态.
    """
    order = []
    monkeypatch.setattr(main, "reset_keyboard", lambda: order.append("reset"))
    monkeypatch.setattr(main.os, "_exit",
                        lambda code: (order.append(("exit", code)),
                                      (_ for _ in ()).throw(SystemExit(code))))
    monkeypatch.setattr(main.sys, "stdin", io.StringIO(""))   # 立刻 EOF

    with pytest.raises(SystemExit):
        main._worker_stdin_watch()

    assert order == ["reset", ("exit", 0)]


def test_install_worker_stdin_watcher_noop_without_stdin(monkeypatch):
    """打包后的 GUI 直接双击 exe 跑 worker 调试时 sys.stdin 可能是 None ——
    那种情况下别起线程(读 None 会直接抛)."""
    started = []

    class _FakeThreading:
        @staticmethod
        def Thread(*a, **k):
            started.append(1)
            raise AssertionError("stdin 是 None 时不该起看门线程")

    monkeypatch.setattr(main.sys, "stdin", None)
    monkeypatch.setattr(main, "threading", _FakeThreading)
    main._install_worker_stdin_watcher()
    assert started == []


def test_update_mythic_latch_locks_on_target():
    assert main._update_mythic_latch(False, 0, True, 3) == (True, 0)
    assert main._update_mythic_latch(True, 2, True, 3) == (True, 0)   # miss counter resets


def test_update_mythic_latch_stays_off_without_target():
    assert main._update_mythic_latch(False, 0, False, 3) == (False, 0)


def test_update_mythic_latch_counts_misses_then_releases():
    latched, misses = True, 0
    latched, misses = main._update_mythic_latch(latched, misses, False, 3)
    assert (latched, misses) == (True, 1)
    latched, misses = main._update_mythic_latch(latched, misses, False, 3)
    assert (latched, misses) == (True, 2)
    latched, misses = main._update_mythic_latch(latched, misses, False, 3)
    assert (latched, misses) == (False, 0)


def test_main_exposes_mythic_wiring():
    assert hasattr(main, "_drive_and_check_stall")
    assert isinstance(main.MYTHIC_LATCH_ENABLED, bool)
    for name in ("MYTHIC_ENGAGE_PX", "MYTHIC_RELEASE_PX", "MYTHIC_RELEASE_MISSES",
                 "MYTHIC_STRAFE_RADIUS", "MYTHIC_CACTUS_HOLD_PX",
                 "MYTHIC_STRAFE_K_RADIAL"):
        assert isinstance(getattr(main, name), (int, float))


def test_mythic_miss_counter_only_advances_on_fresh_scan():
    """节流 tick (scanned=False) 不能推进 miss 计数 —— 循环里 mythic 分支每 tick
    都跑, 但只有真扫描过的 tick 才是一次新观测. 少了这道门, 3-miss 释放在快机器上
    会缩成 ~2 (节流 tick 拿同一份缓存检测重复扣数)."""
    latched, misses = True, 0

    def tick(scanned, has_target):
        nonlocal latched, misses
        if scanned:
            latched, misses = main._update_mythic_latch(latched, misses, has_target, 3)

    tick(scanned=True, has_target=False)      # 真扫描 miss 1
    assert (latched, misses) == (True, 1)
    tick(scanned=False, has_target=False)     # 节流 tick —— 不推进
    assert (latched, misses) == (True, 1)
    tick(scanned=True, has_target=False)      # 真扫描 miss 2
    assert (latched, misses) == (True, 2)
    tick(scanned=False, has_target=False)     # 节流 tick —— 不推进
    assert (latched, misses) == (True, 2)
    tick(scanned=True, has_target=False)      # 真扫描 miss 3 —— 解锁
    assert (latched, misses) == (False, 0)


# ── move_to_position 的 on_tick 钩子 (wander 腿途中让外层索敌) ──────────────

def _stub_move_env(monkeypatch, pos=(10, 10), dead=False, menu=False):
    """把 move_to_position 的所有实机依赖打桩掉, 只留纯逻辑."""
    import types
    monkeypatch.setattr(main, "get_player_position", lambda *a, **k: pos, raising=False)
    monkeypatch.setattr(main, "on_death_screen", lambda: dead, raising=False)
    monkeypatch.setattr(main, "on_start_screen", lambda: menu, raising=False)
    monkeypatch.setattr(main, "reset_keyboard", lambda: None, raising=False)
    monkeypatch.setattr(main.afk_watch, "poll_afk_pause", lambda: False)
    monkeypatch.setattr(main.pyautogui, "moveTo", lambda *a, **k: None)
    monkeypatch.setattr(main.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(main, "overlay",
                        types.SimpleNamespace(update=lambda **k: None), raising=False)


def test_move_to_position_on_tick_aborts_leg_with_its_signal(monkeypatch):
    # 玩家位置恒定 (永远到不了目标), on_tick 第 3 次返回 "enemy" —— 应在那一 tick
    # 立刻收手, 返回该信号, 早于 max_attempts 和 stall 判定.
    _stub_move_env(monkeypatch, pos=(10, 10))
    calls = []

    def on_tick(pos):
        calls.append(pos)
        return "enemy" if len(calls) >= 3 else None

    result = main.move_to_position((10, 10), (999, 999), max_attempts=50, on_tick=on_tick)
    assert result == "enemy"
    assert len(calls) == 3


def test_move_to_position_on_tick_falsy_does_not_abort(monkeypatch):
    # on_tick 从不返回信号 —— 腿正常按老逻辑走完 (位置恒定 → stall → "stuck"),
    # 钩子每 tick 都被调到.
    _stub_move_env(monkeypatch, pos=(10, 10))
    ticks = []
    result = main.move_to_position((10, 10), (999, 999), max_attempts=50,
                                   on_tick=lambda p: ticks.append(p))
    assert result == "stuck"
    assert len(ticks) >= 5


def test_move_to_position_without_on_tick_unchanged(monkeypatch):
    # 不传 on_tick (默认 None) —— 起点在到达半径(ARRIVE_RADIUS)内 → 立刻到达.
    _stub_move_env(monkeypatch, pos=(500, 500))
    assert main.move_to_position((500, 500), (501, 500), max_attempts=5) is True


# ── ensure_zoom_for_rarity (开刷前滚轮拉近相机, 让 sample_rarity 读得出稀有度) ──

def _stub_zoom_env(monkeypatch, thick_seq):
    """thick_seq: list of lists — successive scan_bar_thickness() return values
    (last entry repeats once exhausted). Returns a dict recording calls."""
    import types
    calls = {"scan": 0, "scroll": [], "sleep": 0.0, "moveto": []}
    seq = list(thick_seq)

    def fake_scan(**k):
        i = min(calls["scan"], len(seq) - 1)
        calls["scan"] += 1
        return list(seq[i])

    monkeypatch.setattr(main.enemy_detect, "scan_bar_thickness", fake_scan)
    monkeypatch.setattr(main, "overlay",
                        types.SimpleNamespace(update=lambda **k: None), raising=False)
    monkeypatch.setattr(main.afk_watch, "poll_afk_pause", lambda: False)
    monkeypatch.setattr(main.pyautogui, "moveTo", lambda *a, **k: calls["moveto"].append(a))
    # zoom 滚轮走 CDP (main.cdp_bridge.scroll_wheel), 不是 pyautogui.scroll
    monkeypatch.setattr(main.cdp_bridge, "scroll_wheel",
                        lambda amt, *a, **k: calls["scroll"].append(amt))
    clock = {"t": 0.0}
    monkeypatch.setattr(main.time, "time", lambda: clock["t"])

    def fake_sleep(s):
        clock["t"] += s
        calls["sleep"] += s

    monkeypatch.setattr(main.time, "sleep", fake_sleep)
    return calls


def test_ensure_zoom_disabled_returns_immediately(monkeypatch):
    calls = _stub_zoom_env(monkeypatch, [[9, 9]])
    assert main.ensure_zoom_for_rarity(False) is False
    assert calls["scan"] == 0
    assert calls["scroll"] == []


def test_ensure_zoom_reaches_target(monkeypatch):
    calls = _stub_zoom_env(monkeypatch, [[2, 2], [3, 3], [4, 4]])
    assert main.ensure_zoom_for_rarity(True) is True
    assert len(calls["scroll"]) == 2          # 2 scrolls, 3rd scan median hits 4


def test_ensure_zoom_already_ok_no_scroll(monkeypatch):
    calls = _stub_zoom_env(monkeypatch, [[5, 6]])   # median 5.5 >= 4 first scan
    assert main.ensure_zoom_for_rarity(True) is True
    assert calls["scroll"] == []


def test_ensure_zoom_scroll_cap(monkeypatch):
    calls = _stub_zoom_env(monkeypatch, [[2, 2]])   # never improves
    assert main.ensure_zoom_for_rarity(True) is False
    # FIX 1: cap give-up now restores the zoom, so it's ZOOM_MAX_SCROLLS
    # forward scrolls + one restore scroll.
    assert len(calls["scroll"]) == main.ZOOM_MAX_SCROLLS + 1


def test_ensure_zoom_waits_for_mobs_then_succeeds(monkeypatch):
    calls = _stub_zoom_env(monkeypatch, [[], [], [4, 4]])
    assert main.ensure_zoom_for_rarity(True) is True
    assert calls["scroll"] == []              # never scrolled during empty rounds
    assert calls["sleep"] >= 4.0


def test_ensure_zoom_wait_cap(monkeypatch):
    calls = _stub_zoom_env(monkeypatch, [[]])       # always empty
    assert main.ensure_zoom_for_rarity(True) is False
    assert calls["scroll"] == []
    assert calls["sleep"] >= main.ZOOM_WAIT_CAP


def test_ensure_zoom_flips_scroll_direction(monkeypatch):
    calls = _stub_zoom_env(monkeypatch, [[3, 3], [2, 2], [4, 4]])
    assert main.ensure_zoom_for_rarity(True) is True
    assert calls["scroll"][0] == main.ZOOM_SCROLL_AMOUNT
    assert calls["scroll"][1] == -main.ZOOM_SCROLL_AMOUNT


def test_ensure_zoom_bails_and_restores_when_both_directions_regress(monkeypatch):
    # regress -> flip -> STILL regress: give up, and undo every scroll applied.
    calls = _stub_zoom_env(monkeypatch, [[3, 3], [2, 2], [1, 1]])
    assert main.ensure_zoom_for_rarity(True) is False
    assert sum(calls["scroll"]) == 0                       # net zoom restored
    assert calls["scroll"][0] == main.ZOOM_SCROLL_AMOUNT   # forward...
    assert -main.ZOOM_SCROLL_AMOUNT in calls["scroll"]     # ...then a flip


def test_ensure_zoom_restores_on_scroll_cap(monkeypatch):
    calls = _stub_zoom_env(monkeypatch, [[2, 2]])          # never improves
    assert main.ensure_zoom_for_rarity(True) is False
    assert len(calls["scroll"]) == main.ZOOM_MAX_SCROLLS + 1   # + restore
    assert sum(calls["scroll"]) == 0                           # net zoom restored


def test_ensure_zoom_wait_cap_bounds_afk(monkeypatch):
    calls = _stub_zoom_env(monkeypatch, [[]])
    monkeypatch.setattr(main.afk_watch, "poll_afk_pause", lambda: True)
    assert main.ensure_zoom_for_rarity(True) is False
    assert calls["sleep"] >= main.ZOOM_WAIT_CAP     # top-of-loop cap fires
    assert calls["scroll"] == []


def test_ensure_zoom_recenters_mouse_on_entry(monkeypatch):
    calls = _stub_zoom_env(monkeypatch, [[]])       # no mob -> wait-cap out, no scroll
    assert main.ensure_zoom_for_rarity(True) is False
    assert calls["scroll"] == []
    assert (main.SCREEN_WIDTH // 2, main.SCREEN_HEIGHT // 2) in calls["moveto"]


def test_ensure_zoom_success_does_not_restore(monkeypatch):
    calls = _stub_zoom_env(monkeypatch, [[2, 2], [4, 4]])
    assert main.ensure_zoom_for_rarity(True) is True
    assert sum(calls["scroll"]) == main.ZOOM_SCROLL_AMOUNT   # one forward, kept


# ── 高精度 A* 寻路 (移植自 my-auto-pathing-system/pathfinder.py) ────────────

def _s_corridor_map():
    """1px 宽的蛇形走廊图(墙 0 / 可走 255). 三道横向墙各留 1px 口, 水平错开."""
    m = np.full((9, 9), 255, dtype=np.uint8)
    m[0, :] = 0
    m[8, :] = 0
    m[:, 0] = 0
    m[:, 8] = 0
    for y in (2, 4, 6):
        m[y, 1:8] = 0
    m[2, 3] = 255
    m[4, 5] = 255
    m[6, 3] = 255
    return m


def test_find_walkable_path_no_corner_cutting_in_narrow_corridor():
    # 1px 走廊里不可能合法走对角(两个正交邻格必有一格是墙). 若路径出现对角步,
    # 就是切墙角 —— 那正是旧 lazy_theta_star 在窄道里反复卡住的根因.
    m = _s_corridor_map()
    path = main.find_walkable_path(m, (4, 1), (4, 7))
    assert path is not None
    assert path[0] == (4, 1)
    assert path[-1] == (4, 7)
    # 窄道里抽稀全部失败 → 航点逐像素密集, 相邻航点必须 4-连通(无对角切角).
    for a, b in zip(path, path[1:]):
        assert abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1


def test_find_path_routes_through_interior_not_hugging_walls():
    # keepout 代价的本意: 贴墙格子贵, A* 自动走房间中部而不是贴着墙走直线.
    # 稠密路径(未抽稀)应明显下探到中部再回来 —— 抽稀会把它拉成"安全但贴墙"的
    # 直线, 所以这里测的是稠密输出.
    m = np.full((30, 40), 255, dtype=np.uint8)
    m[0, :] = 0
    m[29, :] = 0
    m[:, 0] = 0
    m[:, 39] = 0
    cost = np.where(m == 255, 1.0, np.inf).astype(np.float32)
    d_wall = main._bfs_dist_to_wall(~np.isfinite(cost))
    dense = main.find_path(cost, d_wall, (2, 2), (37, 2))
    assert dense is not None
    assert any(p[1] >= 8 for p in dense)   # 至少下探到房间中部
    assert max(p[1] for p in dense) <= 27  # 也不至于一路顶到对墙


def test_find_walkable_path_snaps_goal_off_wall():
    # 目标点落在墙上(比如用户在地图选择器里点到墙) → 吸附到最近可走点, 不再
    # "路径规划失败".
    m = np.full((10, 10), 255, dtype=np.uint8)
    m[0, :] = 0
    m[9, :] = 0
    m[:, 0] = 0
    m[:, 9] = 0
    path = main.find_walkable_path(m, (5, 5), (0, 0))   # (0,0) 是墙
    assert path is not None
    assert path[-1] == (1, 1)                            # (0,0) 最近的墙外可走点


def test_find_walkable_path_sparse_in_open_dense_in_narrow():
    open_map = np.full((30, 40), 255, dtype=np.uint8)
    open_map[0, :] = 0
    open_map[29, :] = 0
    open_map[:, 0] = 0
    open_map[:, 39] = 0
    open_path = main.find_walkable_path(open_map, (2, 2), (37, 2))
    assert open_path is not None
    assert len(open_path) <= 3   # 开阔区 LOS 全通 → 直接拉成直线

    narrow_path = main.find_walkable_path(_s_corridor_map(), (4, 1), (4, 7))
    assert narrow_path is not None
    assert len(narrow_path) > 10   # 窄道 LOS 全失败 → 保持逐像素密集


def test_lazy_theta_pathing_stuck_calls_run_escape(monkeypatch):
    """卡住分支不再白读一次位置 —— 每轮只读一次(寻路起点), 卡住直接调 _run_escape."""
    pos_reads = []
    escapes = []
    monkeypatch.setattr(main, "get_player_position",
                        lambda: (pos_reads.append(1), (4, 4))[1])
    monkeypatch.setattr(main, "load_binary_map",
                        lambda: np.full((9, 9), 255, dtype=np.uint8))
    monkeypatch.setattr(main, "execute_path", lambda path: "stuck")
    monkeypatch.setattr(main, "_run_escape",
                        lambda pos, target, binary: escapes.append(1))
    monkeypatch.setattr(main, "overlay",
                        types.SimpleNamespace(update=lambda **k: None), raising=False)
    monkeypatch.setattr(main.afk_watch, "poll_afk_pause", lambda: False)
    death_calls = {"n": 0}

    def fake_death():
        death_calls["n"] += 1
        if death_calls["n"] == 1:
            return False
        raise KeyboardInterrupt()

    monkeypatch.setattr(main, "on_death_screen", fake_death)
    monkeypatch.setattr(main, "on_start_screen", lambda: False)

    with pytest.raises(KeyboardInterrupt):
        main.lazy_theta_pathing((8, 8), [])
    # 第一轮: 读一次位置(寻路起点) → execute_path 卡住 → _run_escape 一次, 不再白读位置.
    assert len(pos_reads) == 1
    assert escapes == [1]


def test_execute_path_groups_collinear_waypoints(monkeypatch):
    # 稠密航点(1px 一格)在直走廊里逐格停顿很慢; 同向直线段应合并成一条腿.
    targets = []

    def fake_move(cur, tgt, *a, **k):
        targets.append((cur, tgt))
        return True

    monkeypatch.setattr(main, "move_to_position", fake_move)
    path = [(0, 0), (1, 0), (2, 0), (3, 0), (3, 1), (4, 1), (5, 1)]
    assert main.execute_path(path) is True
    # 水平直线 (0,0)->(3,0) 合成一条; (3,0)->(3,1) 转弯单独; (3,1)->(5,1) 直段合成.
    assert targets == [((0, 0), (3, 0)), ((3, 0), (3, 1)), ((3, 1), (5, 1))]


def test_execute_path_propagates_stuck(monkeypatch):
    monkeypatch.setattr(main, "move_to_position",
                        lambda cur, tgt, *a, **k: "stuck")
    assert main.execute_path([(0, 0), (1, 0), (2, 0)]) == "stuck"


def test_nearest_wall_direction_points_to_closest_wall():
    # 玩家右侧一列墙 → 最近墙方向指向右(脱困会朝左远离).
    m = np.full((15, 15), 255, dtype=np.uint8)
    m[5:10, 10] = 0   # x=10 一列墙
    d = main._nearest_wall_direction((7, 7), m)
    assert d is not None
    assert d[0] > 0.5   # 指向右


def test_nearest_wall_direction_none_when_no_wall():
    # 周围全是可走(没墙) → None, 退墙色兜底.
    m = np.full((9, 9), 255, dtype=np.uint8)
    assert main._nearest_wall_direction((4, 4), m) is None


def test_run_escape_v1_determines_direction_once_and_holds(monkeypatch):
    # 只确定一次方向: 沿最近墙的斥力方向顶 ESCAPE_HOLD_SECONDS 秒, 不循环重算.
    called = []
    monkeypatch.setattr(main, "execute_anti_stuck",
                        lambda **k: called.append(k))
    binary = np.full((15, 15), 255, dtype=np.uint8)
    binary[5:10, 10] = 0   # 右侧有墙 → 脱困应朝左(远离)
    main._run_escape_v1((7, 7), (999, 999), binary)
    assert len(called) == 1                       # 只调一次
    assert called[0]["duration"] == main.ESCAPE_HOLD_SECONDS
    assert called[0]["mouse_target"][0] < main.SCREEN_WIDTH / 2   # 朝左


def test_run_escape_v1_falls_back_without_wall(monkeypatch):
    # 附近没墙 → 退墙色随机脱困(不带 mouse_target).
    called = []
    monkeypatch.setattr(main, "execute_anti_stuck",
                        lambda **k: called.append(k))
    binary = np.full((15, 15), 255, dtype=np.uint8)
    main._run_escape_v1((7, 7), (999, 999), binary)
    assert called == [{}]


def test_escape_candidates_prefers_corridor_axis():
    # 竖直 1px 窄道: 净空最长方向应是通道轴向(上/下), 而不是横向(左右就是墙).
    # 目标在正上方时, 首选应是 (0, -1)(通道轴向且顺目标).
    m = np.full((9, 9), 0, dtype=np.uint8)
    m[:, 4] = 255                 # x=4 的竖直 1px 通道
    cands = main._escape_candidates((4, 4), m, target=(4, 0))
    assert cands, "四周应能找到净空方向"
    assert cands[0] == (0.0, -1.0)


def test_escape_candidates_empty_when_boxed_in():
    # 四周全是墙 → 空列表, 交由 v2 退回 execute_anti_stuck 兜底.
    m = np.zeros((3, 3), dtype=np.uint8)
    assert main._escape_candidates((1, 1), m) == []


def test_run_escape_v2_rotates_direction_on_no_progress(monkeypatch):
    # 首选方向没挪窝 → 换下一个候选, 轮到能挪的才停.
    tries = []
    monkeypatch.setattr(main, "_escape_candidates",
                        lambda pos, bm, target=None: [(0.0, -1.0), (1.0, 0.0)])
    monkeypatch.setattr(main, "_push_and_verify",
                        lambda pos, d: (tries.append(d), d == (1.0, 0.0))[1])
    main._run_escape_v2((4, 4), (9, 9), np.full((9, 9), 255, dtype=np.uint8))
    assert tries == [(0.0, -1.0), (1.0, 0.0)]


def test_run_escape_v2_falls_back_when_all_directions_fail(monkeypatch):
    # 所有候选方向都挪不动 → 退 execute_anti_stuck 兜底.
    monkeypatch.setattr(main, "_escape_candidates",
                        lambda pos, bm, target=None: [(0.0, -1.0)])
    monkeypatch.setattr(main, "_push_and_verify", lambda pos, d: False)
    fallback = []
    monkeypatch.setattr(main, "execute_anti_stuck", lambda **k: fallback.append(1))
    main._run_escape_v2((4, 4), (9, 9), np.full((9, 9), 255, dtype=np.uint8))
    assert fallback == [1]


def test_stuck_cost_marks_radius_and_expires(monkeypatch):
    # 卡点封堵: 记录后 _build_stuck_cost 返回以卡点为中心的涨价网格; 过期后返回 None.
    main._stuck_penalty.clear()
    monkeypatch.setattr(main, "ESCAPE_V2_ENABLED", True)
    monkeypatch.setattr(main, "time", types.SimpleNamespace(time=lambda: 0.0))
    main._mark_stuck((5, 5))
    grid = main._build_stuck_cost((11, 11))
    assert grid is not None
    assert grid[5, 5] == main.STUCK_PENALTY_COST          # 中心满价
    assert grid[5, 4] > 0 and grid[4, 5] > 0             # 半径内也涨价
    assert grid[0, 0] == 0.0                              # 远处不受影响
    # 过期(now 前进超过 TTL) → 清理并返回 None.
    monkeypatch.setattr(main, "time", types.SimpleNamespace(time=lambda: 999.0))
    assert main._build_stuck_cost((11, 11)) is None
    assert main._stuck_penalty == {}


def test_run_escape_dispatches_to_v2_when_enabled(monkeypatch):
    # 回退开关: ESCAPE_V2_ENABLED=True 走 v2, False 走 v1(一键回退通道).
    calls = []
    monkeypatch.setattr(main, "_run_escape_v2", lambda p, t, b: calls.append("v2"))
    monkeypatch.setattr(main, "_run_escape_v1", lambda p, t, b: calls.append("v1"))
    monkeypatch.setattr(main, "ESCAPE_V2_ENABLED", True)
    main._run_escape((1, 1), (2, 2), None)
    monkeypatch.setattr(main, "ESCAPE_V2_ENABLED", False)
    main._run_escape((1, 1), (2, 2), None)
    assert calls == ["v2", "v1"]


def test_recover_from_teleport_success(monkeypatch):
    # 检测到 garden → 切 garden 寻路到传送门 → 进门黑屏 → 恢复 → 回 anthell.
    applied = []
    monkeypatch.setattr(main, "apply_map", lambda name: applied.append(name))
    monkeypatch.setattr(main, "lazy_theta_pathing", lambda loc, area: True)
    monkeypatch.setattr(main, "overlay",
                        types.SimpleNamespace(update=lambda **k: None), raising=False)
    black_calls = {"n": 0}

    def fake_black():
        black_calls["n"] += 1
        return black_calls["n"] >= 2   # 第2次调用 = 进门那一刻黑屏

    monkeypatch.setattr(main, "screen_is_black", fake_black)
    monkeypatch.setattr(main, "_wait_for_screen_recovery", lambda timeout=15: True)
    monkeypatch.setattr(main, "get_player_position", lambda: (135, 233))
    monkeypatch.setattr(main.pyautogui, "moveTo", lambda *a, **k: None)
    monkeypatch.setattr(main.time, "sleep", lambda s: None)

    assert main.recover_from_teleport() is True
    # 先切 garden 导航, 再恢复原地图(anthell)
    assert applied == ["garden", "anthell"]


def test_recover_from_teleport_fails_when_cannot_reach_portal(monkeypatch):
    # 走到传送门失败(且仍在 garden) → 返回 False, 且恢复原地图(外层会自愈重试).
    applied = []
    monkeypatch.setattr(main, "apply_map", lambda name: applied.append(name))
    monkeypatch.setattr(main, "lazy_theta_pathing", lambda loc, area: False)
    monkeypatch.setattr(main, "is_on_garden", lambda: True)   # 还在 garden
    monkeypatch.setattr(main, "overlay",
                        types.SimpleNamespace(update=lambda **k: None), raising=False)

    assert main.recover_from_teleport() is False
    assert applied == ["garden", "anthell"]


def test_recover_from_teleport_true_when_left_garden_midway(monkeypatch):
    # 寻路中途小地图不再是 garden(黑屏传送/已回 anthell) → 按成功收尾并切回 anthell.
    applied = []
    monkeypatch.setattr(main, "apply_map", lambda name: applied.append(name))
    monkeypatch.setattr(main, "lazy_theta_pathing", lambda loc, area: False)
    monkeypatch.setattr(main, "is_on_garden", lambda: False)   # 已离开 garden
    monkeypatch.setattr(main, "overlay",
                        types.SimpleNamespace(update=lambda **k: None), raising=False)

    assert main.recover_from_teleport() is True
    assert applied == ["garden", "anthell"]   # 黑屏之后必须切回 anthell


def test_move_to_position_recovers_on_garden(monkeypatch):
    # 小地图是 garden 时 move_to_position 会触发传送恢复, 不会原地干等.
    recovered = []
    monkeypatch.setattr(main, "is_on_garden", lambda: True)
    monkeypatch.setattr(main, "_teleport_recovery_active", False)
    monkeypatch.setattr(main, "recover_from_teleport",
                        lambda: (recovered.append(1), True)[1])
    _stub_move_env(monkeypatch, pos=(10, 10))
    main.move_to_position((10, 10), (999, 999), max_attempts=50)
    assert recovered


def test_teleport_recovery_flag_prevents_recursion(monkeypatch):
    # 恢复期间 _teleport_recovery_active=True, garden 检测不再触发(防递归:
    # 恢复流程里的 lazy_theta_pathing 循环顶部也有 garden 检测).
    recover_calls = []
    monkeypatch.setattr(main, "_teleport_recovery_active", True)
    monkeypatch.setattr(main, "is_on_garden", lambda: True)
    monkeypatch.setattr(main, "recover_from_teleport",
                        lambda: (recover_calls.append(1), True)[1])
    _stub_move_env(monkeypatch, pos=(10, 10))
    main.move_to_position((10, 10), (999, 999), max_attempts=5)
    assert recover_calls == []


def test_garden_escape_needed_false_when_farming_garden(monkeypatch):
    # 刷图目标本身是 garden: is_on_garden() 恒真, 但绝不能触发逃生.
    monkeypatch.setattr(utils, "MAP", "garden")
    monkeypatch.setattr(main, "_teleport_recovery_active", False)
    monkeypatch.setattr(main, "is_on_garden", lambda: True)
    assert main._garden_escape_needed() is False


def test_garden_escape_needed_true_on_other_maps(monkeypatch):
    # 在 anthell/desert/ocean 刷图, 小地图匹配上 garden(被传送) → 需要逃生.
    monkeypatch.setattr(main, "_teleport_recovery_active", False)
    for name in ("anthell", "desert", "ocean"):
        monkeypatch.setattr(utils, "MAP", name)
        monkeypatch.setattr(main, "is_on_garden", lambda: True)
        assert main._garden_escape_needed() is True


def test_garden_escape_needed_false_during_recovery(monkeypatch):
    # 逃生恢复期间: 门卫必须关掉, 防止恢复流程里的 garden 检测再次触发.
    monkeypatch.setattr(utils, "MAP", "anthell")
    monkeypatch.setattr(main, "_teleport_recovery_active", True)
    monkeypatch.setattr(main, "is_on_garden", lambda: True)
    assert main._garden_escape_needed() is False


def test_garden_escape_needed_false_not_on_garden(monkeypatch):
    # 不在 garden 上 → 不需要逃生.
    monkeypatch.setattr(utils, "MAP", "anthell")
    monkeypatch.setattr(main, "_teleport_recovery_active", False)
    monkeypatch.setattr(main, "is_on_garden", lambda: False)
    assert main._garden_escape_needed() is False


def test_precise_approach_reaches_exact_target(monkeypatch):
    # 位置从 (132,233) 被引导到目标 (133,233) → 返回 True(真正踩到目标格).
    pos = {"v": (132, 233)}
    monkeypatch.setattr(main, "get_player_position", lambda: pos["v"])
    monkeypatch.setattr(main.pyautogui, "moveTo",
                        lambda *a, **k: pos.__setitem__("v", (133, 233)))
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    assert main._precise_approach((133, 233)) is True


def test_precise_approach_gives_up_when_unreachable(monkeypatch):
    # 位置始终到不了目标 → 尝试满次数返回 False.
    monkeypatch.setattr(main, "get_player_position", lambda: (130, 233))
    monkeypatch.setattr(main.pyautogui, "moveTo", lambda *a, **k: None)
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    assert main._precise_approach((133, 233)) is False
