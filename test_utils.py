import cv2
import numpy as np
from PIL import Image

import utils
from utils import if_in_area, _ensure_grayscale_2d, _pick_server_id, calc_anti_stuck, get_player_location_on_map


def test_if_in_area_normal_corner_order():
    area = [(0, 0), (10, 10)]
    assert if_in_area([area], (5, 5)) is True
    assert if_in_area([area], (20, 20)) is False


def test_if_in_area_flipped_x_corner_order():
    # main.py的farming_area实际写的是[(20, 15), (9, 76)] —— 第一个角x比第二个角
    # x还大. 之前if_in_area直接假设area[0]是"左上角"、area[1]是"右下角", 对这种
    # 顺序会导致x区间变成 20<=x<=9 永远判不出True, 玩家哪怕站在区域正中间都会被
    # 判定"不在区域内".
    area = [(20, 15), (9, 76)]
    assert if_in_area([area], (16, 46)) is True
    assert if_in_area([area], (14, 45)) is True
    assert if_in_area([area], (0, 0)) is False
    assert if_in_area([area], (25, 50)) is False


def test_if_in_area_flipped_y_corner_order():
    area = [(0, 76), (10, 15)]
    assert if_in_area([area], (5, 45)) is True
    assert if_in_area([area], (5, 100)) is False


def test_if_in_area_checks_multiple_areas():
    areas = [[(0, 0), (5, 5)], [(20, 20), (25, 25)]]
    assert if_in_area(areas, (22, 22)) is True
    assert if_in_area(areas, (12, 12)) is False


def test_ensure_grayscale_2d_squeezes_trailing_channel_dim():
    # 复现实测撞到的场景: cv2.imread(path, cv2.IMREAD_GRAYSCALE)在某些
    # OpenCV/平台组合(Windows)下吐出(H,W,1)而不是纯(H,W), 导致下游
    # calibrate_player()的`rows, cols = map.shape`拆包报错.
    img_3d = np.zeros((10, 20, 1), dtype=np.uint8)
    img_3d[3, 5, 0] = 255
    result = _ensure_grayscale_2d(img_3d)
    assert result.shape == (10, 20)
    assert result[3, 5] == 255


def test_ensure_grayscale_2d_leaves_true_2d_untouched():
    img_2d = np.zeros((10, 20), dtype=np.uint8)
    img_2d[3, 5] = 255
    result = _ensure_grayscale_2d(img_2d)
    assert result.shape == (10, 20)
    assert result[3, 5] == 255


def test_ensure_grayscale_2d_passes_through_none():
    # cv2.imread在文件读不到时返回None(不是抛异常) —— 不能让形状归一化
    # 逻辑在这种情况下自己先炸了(None.ndim会AttributeError).
    assert _ensure_grayscale_2d(None) is None


def test_pick_server_id_excludes_ids_used_within_cooldown():
    ids = ["a", "b", "c"]
    now = 10_000
    last_used = {"a": now - 60}  # 1分钟前刚用过a, 还在30分钟冷却期内
    for _ in range(20):
        assert _pick_server_id(ids, last_used, now, cooldown_seconds=1800) != "a"


def test_pick_server_id_allows_id_once_cooldown_expires():
    ids = ["a", "b", "c"]
    now = 10_000
    last_used = {"a": now - 1801}  # 30分钟零1秒前用过, 刚好过了冷却期
    # a现在应该重新进入候选池了(不再总是被排除), 多跑几次应该能选到a.
    results = {_pick_server_id(ids, last_used, now, cooldown_seconds=1800) for _ in range(50)}
    assert "a" in results


def test_pick_server_id_falls_back_to_least_recently_used_when_all_on_cooldown():
    # 只有3台服务器, 全部都在冷却期内(换得比冷却期还频繁) —— 不能卡死不换,
    # 退化成挑最久没用过的那个(b, 5分钟前用的, 比a/c更久远).
    ids = ["a", "b", "c"]
    now = 10_000
    last_used = {"a": now - 60, "b": now - 300, "c": now - 120}
    assert _pick_server_id(ids, last_used, now, cooldown_seconds=1800) == "b"


def test_pick_server_id_never_used_before_is_always_eligible():
    # last_used里完全没有的id, 相当于"上次使用时间是很久很久以前", 天然不在
    # 冷却期内 —— 用.get(i, 0)兜底, 不能因为字典里没这个key就报KeyError.
    ids = ["a", "brand_new"]
    now = 10_000
    last_used = {"a": now - 60}
    result = _pick_server_id(ids, last_used, now, cooldown_seconds=1800)
    assert result == "brand_new"


def test_check_map_border_returns_empty_for_map_without_configured_color(monkeypatch):
    # 没配墙色的图(jungle 这种还没支持的)—— 之前 anthell 就是这么崩的
    # (if/elif 直接 NameError 把脱困功能撂倒), 现在必须优雅返回空列表
    # (execute_anti_stuck 会退化成随机方向硬闯, 见它的 docstring).
    monkeypatch.setattr(utils, "MAP", "jungle")
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    assert utils.check_map_border(img) == []


def test_check_map_border_finds_anthell_wall_color(monkeypatch):
    # anthell 的墙色现在配上了(从用户实机截图提的) —— 该图脱困时真的能
    # 识别墙、朝反方向走, 而不是退化随机.
    monkeypatch.setattr(utils, "MAP", "anthell")
    bgr = tuple(int("69462e"[i:i+2], 16) for i in (4, 2, 0))
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    img[20:80, 20:80] = bgr
    points = utils.check_map_border(img)
    assert len(points) > 0


def test_check_map_border_finds_points_for_configured_color(monkeypatch):
    monkeypatch.setattr(utils, "MAP", "desert")
    # "4f3422" → BGR(34, 52, 79), 跟 check_map_border 里同一套 (4,2,0) 切片.
    bgr = tuple(int("4f3422"[i:i+2], 16) for i in (4, 2, 0))
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    img[20:80, 20:80] = bgr
    points = utils.check_map_border(img)
    assert len(points) > 0


def test_can_switch_server_false_on_blocked_map(monkeypatch):
    # ant_hell 上跑换服会卡退(用户实测), 自动换服在这张图上必须被禁.
    monkeypatch.setattr(utils, "MAP", "anthell")
    assert utils.can_switch_server() is False


def test_can_switch_server_true_on_switchable_maps(monkeypatch):
    for name in ("desert", "ocean", "garden"):
        monkeypatch.setattr(utils, "MAP", name)
        assert utils.can_switch_server() is True


def test_scale_x_and_scale_y_are_identity_at_reference_resolution(monkeypatch):
    monkeypatch.setattr(utils, "SCREEN_WIDTH", 1920)
    monkeypatch.setattr(utils, "SCREEN_HEIGHT", 1080)
    assert utils.scale_x(960) == 960
    assert utils.scale_y(540) == 540


def test_scale_point_scales_uniformly_on_larger_same_aspect_screen(monkeypatch):
    monkeypatch.setattr(utils, "SCREEN_WIDTH", 2560)
    monkeypatch.setattr(utils, "SCREEN_HEIGHT", 1440)
    # 2560/1920 == 1440/1080 == 4/3: same 16:9 aspect ratio, uniform scale-up.
    assert utils.scale_point(960, 540) == (1280, 720)


def test_scale_point_scales_axes_independently_on_non_16_9_screen(monkeypatch):
    monkeypatch.setattr(utils, "SCREEN_WIDTH", 2560)
    monkeypatch.setattr(utils, "SCREEN_HEIGHT", 1080)  # ultrawide: width changes, height doesn't
    x, y = utils.scale_point(1920, 1080)
    assert x == 2560  # scaled by width ratio (2560/1920)
    assert y == 1080  # scale_y ratio is 1, untouched


def test_scale_region_scales_position_and_size_independently(monkeypatch):
    monkeypatch.setattr(utils, "SCREEN_WIDTH", 3840)
    monkeypatch.setattr(utils, "SCREEN_HEIGHT", 2160)
    # get_map()'s reference crop region: [1600, 20, 300, 300] at 1920x1080.
    assert utils.scale_region(1600, 20, 300, 300) == [3200, 40, 600, 600]


def test_clamp_to_screen_keeps_point_inside_bounds_with_margin(monkeypatch):
    monkeypatch.setattr(utils, "SCREEN_WIDTH", 1366)
    monkeypatch.setattr(utils, "SCREEN_HEIGHT", 768)
    assert utils.clamp_to_screen(-50, 2000) == (2, 766)
    assert utils.clamp_to_screen(700, 400) == (700, 400)


def test_mouse_scale_matches_min_of_axis_ratios(monkeypatch):
    monkeypatch.setattr(utils, "SCREEN_WIDTH", 960)
    monkeypatch.setattr(utils, "SCREEN_HEIGHT", 1080)
    assert utils.mouse_scale() == 0.5  # min(960/1920, 1080/1080) == min(0.5, 1.0)
    assert round(10 * utils.mouse_scale()) == 5  # must work as a real float in arithmetic, like Task 2/4 will use it


def test_calc_anti_stuck_clips_to_actual_screen_bounds_not_1920x1080(monkeypatch):
    monkeypatch.setattr(utils, "SCREEN_WIDTH", 800)
    monkeypatch.setattr(utils, "SCREEN_HEIGHT", 600)
    monkeypatch.setattr(utils, "toggle_map", lambda: None)
    # A single "wall" pixel far to the left of screen center pushes the
    # suggested position hard to the right — enough to hit whatever the
    # x-clip upper bound is. At the old hardcoded bound (1920) this would
    # stay under it and the bug wouldn't show; at the new 800-wide bound
    # it must clip to 800.
    borders = [(-5000, 300)]
    x, y = calc_anti_stuck(borders, weight=10000.0)
    assert x == 800
    assert 0 <= y <= 600


def test_get_map_resizes_scaled_capture_back_to_300x300(monkeypatch):
    monkeypatch.setattr(utils, "SCREEN_WIDTH", 3840)
    monkeypatch.setattr(utils, "SCREEN_HEIGHT", 2160)
    captured = {}

    def fake_screenshot(region):
        captured["region"] = region
        w, h = region[2], region[3]
        return Image.new("RGB", (w, h), color=(10, 20, 30))

    monkeypatch.setattr(utils.pyautogui, "screenshot", fake_screenshot)

    image = utils.get_map()

    # At 4K (2x the 1920x1080 reference), the scaled minimap region should
    # be captured at 600x600 (2x the reference 300x300)...
    assert captured["region"] == [3200, 40, 600, 600]
    # ...but get_map() must hand back exactly 300x300 regardless, since
    # maps/*.png templates and every downstream map-space consumer assume
    # that fixed pixel space.
    assert image.shape[:2] == (300, 300)


def test_get_map_is_a_no_op_resize_at_reference_resolution(monkeypatch):
    monkeypatch.setattr(utils, "SCREEN_WIDTH", 1920)
    monkeypatch.setattr(utils, "SCREEN_HEIGHT", 1080)
    captured = {}

    def fake_screenshot(region):
        captured["region"] = region
        w, h = region[2], region[3]
        return Image.new("RGB", (w, h), color=(10, 20, 30))

    monkeypatch.setattr(utils.pyautogui, "screenshot", fake_screenshot)

    image = utils.get_map()

    # Unchanged from the pre-this-plan behavior: region is already 300x300.
    assert captured["region"] == [1600, 20, 300, 300]
    assert image.shape[:2] == (300, 300)


def test_get_map_uses_height_based_uniform_scale_for_non_16_9(monkeypatch):
    """实机验证(见2026-08-26的调试会话): 1024x768下小地图真实边界实测约
    左上(797,20)~右下(1009,227) —— 用debug_screen_pos.py量鼠标点 + 用
    debug_position_diag.py对着get_map()的截图跑f8de60颜色匹配确认过, 独立轴
    scale_region()那套公式(算出来是[853,14,160,214])截歪了: 宽度偏窄、外边框
    整个漏在截图外, 匹配像素数是0. 换成"整体按SCREEN_HEIGHT/1080统一缩放、
    保持正方形、贴右上角"的公式跟实测数据吻合(右/下边几乎分毫不差, 左边基本对上)。
    """
    monkeypatch.setattr(utils, "SCREEN_WIDTH", 1024)
    monkeypatch.setattr(utils, "SCREEN_HEIGHT", 768)
    captured = {}

    def fake_screenshot(region):
        captured["region"] = region
        w, h = region[2], region[3]
        return Image.new("RGB", (w, h), color=(10, 20, 30))

    monkeypatch.setattr(utils.pyautogui, "screenshot", fake_screenshot)

    image = utils.get_map()

    assert captured["region"] == [797, 14, 213, 213]
    assert image.shape[:2] == (300, 300)


def test_get_player_location_on_map_accepts_a_shrunk_marker_blob():
    """实机(1024x768)调试确认过: 非参照分辨率下get_map()截到的原始区域比300x300小,
    resize放大回300x300后, 真实玩家标记的像素footprint会缩水 —— 抓到的失败瞬间现场
    量出来radius=1.90(debug_position_diag.py+debug_position_diag_marked.png肉眼确认
    过是真标记不是噪声), 被原来radius>2的阈值当噪声滤掉. 这里在300x300画布上画一个
    对角十字形的7像素小blob(minEnclosingCircle算出来radius≈1.41, 复现"比参照分辨率
    下的标记小, 但不是孤立噪声"这个情况), 用precise=True跳过calibrate_player(不需要
    真实binary_map), 直接验证这个尺寸的标记能被找到.
    """
    image = np.zeros((300, 300, 3), dtype=np.uint8)
    bgr = (0x60, 0xde, 0xf8)  # f8de60的BGR顺序 (cv2图像是BGR, 不是RGB)
    for (px, py) in [(149, 150), (150, 150), (151, 150), (150, 149), (150, 151), (149, 149), (151, 151)]:
        image[py, px] = bgr

    position = get_player_location_on_map(image, "f8de60", map=None, precise=True)

    assert position is not None
    x, y = position
    assert abs(x - 150) < 1.5
    assert abs(y - 150) < 1.5


class _ClickSpy:
    def __init__(self):
        self.clicks = 0

    def moveTo(self, *_a, **_kw):
        pass

    def click(self, *_a, **_kw):
        self.clicks += 1


def _patch_click(monkeypatch, on_screen_sequence):
    """on_screen_sequence: 每次复查画面时依次返回的值; 用完后保持最后一个值."""
    spy = _ClickSpy()
    monkeypatch.setattr(utils, "pyautogui", spy)
    monkeypatch.setattr(utils.time, "sleep", lambda *_a, **_kw: None)
    seq = list(on_screen_sequence)
    calls = {"n": 0}

    def fake_on_start_screen():
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return seq[i]

    monkeypatch.setattr(utils, "on_start_screen", fake_on_start_screen)
    return spy


def test_click_start_game_stops_after_menu_gone_on_first_try(monkeypatch):
    # 点一下菜单就消失了 —— 只该点这一轮(两下connect click), 返回True.
    spy = _patch_click(monkeypatch, [False])
    assert utils.click_start_game() is True
    assert spy.clicks == 2


def test_click_start_game_retries_until_menu_gone(monkeypatch):
    # 前两轮点了菜单还在, 第三轮才进去 —— 该重试到进去为止.
    spy = _patch_click(monkeypatch, [True, True, False])
    assert utils.click_start_game() is True
    assert spy.clicks == 2 * 3


def test_click_start_game_gives_up_after_max_attempts_without_crashing(monkeypatch):
    # 菜单怎么点都不消失(标签页卡死之类) —— 试满次数后返回False, 交给主循环下轮再试,
    # 不无限卡在这里, 也不抛异常.
    spy = _patch_click(monkeypatch, [True])
    assert utils.click_start_game() is False
    assert spy.clicks == 2 * utils._CONFIRM_CLICK_MAX_ATTEMPTS


def test_execute_anti_stuck_default_duration_lengthened_to_2s():
    # 加长到 2.0s 让脱困有更充分的时间真正把角色顶出卡点(用户实测 1.0s 太短,
    # 容易脱困后立刻又卡回去).
    import inspect
    assert inspect.signature(utils.execute_anti_stuck).parameters["duration"].default == 2.0


def test_execute_anti_stuck_uses_mouse_target_directly(monkeypatch):
    # 给定 mouse_target 时应直接朝它顶 duration 秒并停回中心, 且完全不碰墙色截图路径.
    moves = []
    sleeps = []
    monkeypatch.setattr(utils.pyautogui, "moveTo", lambda *a: moves.append(a))
    monkeypatch.setattr(utils.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(
        utils.pyautogui, "screenshot",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该走墙色截图路径")))

    utils.execute_anti_stuck(mouse_target=(100, 200))

    assert moves[0] == ((100, 200),)   # 第一次 moveTo(脱困目标) 是单个元组参数
    assert moves[-1] == (utils.SCREEN_WIDTH // 2, utils.SCREEN_HEIGHT // 2)  # 最后是两参数收中心
    assert sleeps == [2.0]


def test_screen_is_black_detects_teleport_black(monkeypatch):
    from PIL import Image
    black = Image.new("RGB", (200, 200), (0, 0, 0))
    monkeypatch.setattr(utils.pyautogui, "screenshot", lambda *a, **k: black)
    assert utils.screen_is_black() is True


def test_screen_is_black_false_on_normal_scene(monkeypatch):
    from PIL import Image
    bright = Image.new("RGB", (200, 200), (100, 100, 100))
    monkeypatch.setattr(utils.pyautogui, "screenshot", lambda *a, **k: bright)
    assert utils.screen_is_black() is False


def test_is_on_garden_matches_same_layout():
    # 实时小地图布局和 garden 模板一致 → 判定是 garden.
    tpl = np.zeros((60, 60), dtype=np.uint8)
    tpl[10:50, 10:50] = 255
    live = np.zeros((60, 60, 3), dtype=np.uint8)
    live[10:50, 10:50] = (255, 255, 255)
    assert utils.is_on_garden(threshold=0.7, map_img=live, template=tpl) is True


def test_is_on_garden_false_on_different_layout():
    tpl = np.zeros((60, 60), dtype=np.uint8)
    tpl[10:50, 10:50] = 255
    live = np.zeros((60, 60, 3), dtype=np.uint8)
    live[20:40, 20:40] = (255, 255, 255)   # 布局明显不同
    assert utils.is_on_garden(threshold=0.7, map_img=live, template=tpl) is False


def test_is_on_garden_false_without_template(monkeypatch):
    monkeypatch.setattr(utils, "_load_garden_template", lambda: None)
    live = np.zeros((60, 60, 3), dtype=np.uint8)
    assert utils.is_on_garden(map_img=live) is False
