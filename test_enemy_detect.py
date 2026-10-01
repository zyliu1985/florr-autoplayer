import os

import numpy as np

from enemy_detect import (
    sample_rarity, _find_hp_bar, RARITY_COLORS, RARITY_ORDER, RARITY_RANK,
    _hex_to_bgr, classify_action, priority_score, aim_mouse_target, flee_mouse_target,
)

# Sandy background BGR — not green (so _find_hp_bar's green mask ignores it: its
# R=210 fails the mask's r<205), and outside every rarity color's ±40 cube.
_BG_BGR = (120, 170, 210)
# Health-bar green, measured off a real florr.io screenshot (屏幕截图 220017.png):
# BGR ≈ (48, 208, 112). Passes _find_hp_bar's green mask.
_BAR_BGR = (48, 208, 112)


def _name_tag_image(rarity_bgr, word_rows=10, word_cols=50):
    """Synthesise a florr.io mob name tag the way the game lays it out: a long
    thin green HP bar under the mob, with the rarity word (rarity-coloured)
    just below the bar, right-aligned to the bar's right end. Returns
    (image, bbox). `word_rows`/`word_cols` control how much of sample_rarity's
    word region the coloured glyphs actually fill (rest stays background)."""
    img = np.full((200, 300, 3), _BG_BGR, dtype=np.uint8)
    bbox = (60, 20, 160, 90)          # mob box, h = 70
    bar_y, bar_x0, bar_x1, thick = 110, 70, 190, 5
    img[bar_y:bar_y + thick, bar_x0:bar_x1] = _BAR_BGR
    # sample_rarity's word region for this bar: ry0 = bar_y + thick//2 + 1 = 113,
    # rx1 ≈ bar_x1, extending left. Paint the glyphs inside it.
    gy = bar_y + thick // 2 + 1 + 1
    gx1 = bar_x1 - 4
    if rarity_bgr is not None:
        img[gy:gy + word_rows, gx1 - word_cols:gx1] = rarity_bgr
    return img, bbox


def test_sample_rarity_matches_known_colors():
    # Only Common..Ultra are scanned (Super/Eternal/Unique don't spawn in this
    # zone and their colours false-match the bar green / outline black).
    for name in RARITY_ORDER[:RARITY_RANK["Ultra"] + 1]:
        img, bbox = _name_tag_image(_hex_to_bgr(RARITY_COLORS[name]))
        assert sample_rarity(img, bbox) == name, name


def test_sample_rarity_above_ultra_colors_read_as_common():
    # Super/Eternal/Unique are deliberately NOT scanned — feeding their colour
    # resolves to Common (below floor), never a spurious high-rarity read that
    # would wrongly trip AVOID on a normal mob.
    for name in ("Super", "Eternal", "Unique"):
        img, bbox = _name_tag_image(_hex_to_bgr(RARITY_COLORS[name]))
        assert sample_rarity(img, bbox) == "Common", name


def test_sample_rarity_falls_back_to_common_when_no_hp_bar():
    image = np.zeros((100, 100, 3), dtype=np.uint8)  # pure black, no green bar
    assert sample_rarity(image, (40, 60, 60, 80)) == "Common"


def test_sample_rarity_falls_back_to_common_below_coverage_floor():
    # HP bar present (so an anchor is found) but the rarity glyphs cover only a
    # sliver of the word region — under min_pixel_ratio, so not trusted.
    img, bbox = _name_tag_image(_hex_to_bgr(RARITY_COLORS["Ultra"]), word_rows=1, word_cols=2)
    assert sample_rarity(img, bbox) == "Common"


def test_find_hp_bar_locates_bar_and_rejects_blob():
    img, bbox = _name_tag_image(_hex_to_bgr(RARITY_COLORS["Rare"]))
    bar = _find_hp_bar(img, bbox)
    assert bar is not None
    bar_x0, bar_y, bar_x1, thick = bar
    assert abs(bar_y - 110) <= 1 and thick <= 8 and (bar_x1 - bar_x0) >= 90

    # A big green blob (translucent cactus/sandstorm body) is not bar-shaped:
    # fails the run >= 4*thick aspect check -> None.
    blob = np.full((200, 300, 3), _BG_BGR, dtype=np.uint8)
    blob[40:110, 70:170] = _BAR_BGR
    assert _find_hp_bar(blob, (60, 20, 160, 90)) is None


_ALL_SPECIES = [
    "scorpion", "beetle", "cactus", "sandstorm",
    "sand_centipede", "soldier_fire_ant",
]
_BELOW_ULTRA = ["Common", "Unusual", "Rare", "Epic", "Legendary", "Mythic"]
_ABOVE_ULTRA = ["Super", "Eternal", "Unique"]


def test_classify_action_engage_below_ultra_any_species():
    for species in _ALL_SPECIES:
        for rarity in _BELOW_ULTRA:
            assert classify_action(species, rarity) == "ENGAGE"


def test_classify_action_ultra_avoid_species():
    assert classify_action("scorpion", "Ultra") == "AVOID"
    assert classify_action("beetle", "Ultra") == "AVOID"


def test_classify_action_ultra_cautious_species():
    for species in ["sandstorm", "cactus", "sand_centipede", "soldier_fire_ant"]:
        assert classify_action(species, "Ultra") == "CAUTIOUS"


def test_classify_action_above_ultra_falls_back_to_avoid():
    for species in _ALL_SPECIES:
        for rarity in _ABOVE_ULTRA:
            assert classify_action(species, rarity) == "AVOID"


def test_priority_score_rarity_dominates_species():
    # Rare sand_centipede(物种优先级最低)该压过Common sandstorm(物种优先级最高) ——
    # 稀有度是第一比较项, 碾压式的.
    assert priority_score("sand_centipede", "Rare") > priority_score("sandstorm", "Common")


def test_priority_score_species_tiebreak_within_same_rarity():
    assert priority_score("sandstorm", "Common") > priority_score("cactus", "Common")
    assert priority_score("cactus", "Common") > priority_score("beetle", "Common")
    assert priority_score("beetle", "Common") > priority_score("scorpion", "Common")
    assert priority_score("scorpion", "Common") > priority_score("sand_centipede", "Common")
    assert priority_score("sand_centipede", "Common") == priority_score("soldier_fire_ant", "Common")


def test_aim_mouse_target_points_toward_target_beyond_hold():
    result = aim_mouse_target((1460, 540), hold_px=None, center=(960, 540), max_extend=500)
    assert result[0] > 960
    assert abs(result[1] - 540) < 1e-6


def test_aim_mouse_target_stops_at_hold_distance():
    result = aim_mouse_target((1200, 540), hold_px=250, center=(960, 540))
    assert result == (960, 540)


def test_aim_mouse_target_clamps_to_max_extend():
    result = aim_mouse_target((3000, 540), hold_px=None, center=(960, 540), max_extend=500)
    assert result == (1460, 540)


def test_aim_mouse_target_chases_to_actual_distance_when_within_max_extend():
    # dist=100 is well inside max_extend=500 — the mouse should land exactly
    # at the target's offset from center, not jump all the way to max_extend.
    result = aim_mouse_target((1060, 540), hold_px=None, center=(960, 540), max_extend=500)
    assert result == (1060, 540)


def test_aim_mouse_target_no_repel_positions_is_unchanged():
    # repel_positions 为 None/空 -> 跟没这个参数时结果完全一致
    a = aim_mouse_target((1060, 540), hold_px=None, center=(960, 540), max_extend=500)
    b = aim_mouse_target((1060, 540), hold_px=None, center=(960, 540), max_extend=500,
                         repel_positions=[])
    assert a == b == (1060, 540)


def test_aim_mouse_target_bends_away_from_danger_on_the_path():
    # 目标正右方, 一只危险怪在右上方近处 -> 瞄点应被往下压 (远离危险), 但整体
    # 仍朝右 (还在追)
    result = aim_mouse_target((1460, 540), hold_px=None, center=(960, 540), max_extend=500,
                              repel_positions=[(1160, 440)], repel_px=400)
    assert result[0] > 960          # 仍在朝目标方向 (右)
    assert result[1] > 540          # 被危险怪 (在上方, y 小) 往下推


def test_aim_mouse_target_repels_even_while_holding_distance():
    # 已进 CAUTIOUS 保持距离内, 平时返回 center; 但半路有危险怪 -> 仍往远离方向挪
    held = aim_mouse_target((1100, 540), hold_px=250, center=(960, 540))
    assert held == (960, 540)
    with_danger = aim_mouse_target((1100, 540), hold_px=250, center=(960, 540),
                                   repel_positions=[(960, 440)], repel_px=400)
    assert with_danger != (960, 540)
    assert with_danger[1] > 540     # 危险怪在上方 -> 往下挪


def test_flee_mouse_target_points_away_from_single_threat():
    result = flee_mouse_target([(1460, 540)], center=(960, 540), extend=400)
    assert result[0] < 960
    assert abs(result[1] - 540) < 1e-6


def test_flee_mouse_target_returns_center_when_forces_cancel():
    result = flee_mouse_target([(1460, 540), (460, 540)], center=(960, 540))
    assert result == (960, 540)


from enemy_detect import (
    mythic_candidates, pick_mythic_target, mythic_move_target,
    MYTHIC_KITE_SPECIES, MYTHIC_TARGET_RANK,
)
from enemy_detect import measure_hp_bar_thickness, scan_bar_thickness

from enemy_detect import select_action, chase_is_stalled


def _det(species, rarity, screen_pos, conf=0.9):
    return {
        "species": species, "rarity": rarity, "screen_pos": screen_pos,
        "bbox": (0, 0, 0, 0), "confidence": conf,
    }


def test_mythic_kite_species_table_is_the_five_non_sandstorm_species():
    assert set(MYTHIC_KITE_SPECIES) == {
        "beetle", "soldier_fire_ant", "scorpion", "sand_centipede", "cactus",
    }
    assert set(MYTHIC_KITE_SPECIES.values()) <= {"strafe", "ram", "hold"}
    assert MYTHIC_KITE_SPECIES["beetle"] == "strafe"
    assert MYTHIC_KITE_SPECIES["soldier_fire_ant"] == "strafe"
    assert MYTHIC_KITE_SPECIES["scorpion"] == "ram"
    assert MYTHIC_KITE_SPECIES["sand_centipede"] == "ram"
    assert MYTHIC_KITE_SPECIES["cactus"] == "hold"


def test_mythic_target_rank_order():
    order = ["beetle", "soldier_fire_ant", "scorpion", "sand_centipede", "cactus"]
    ranks = [MYTHIC_TARGET_RANK[s] for s in order]
    assert ranks == sorted(ranks, reverse=True)
    assert len(set(ranks)) == 5


def test_mythic_candidates_filters_rarity_species_and_conf():
    dets = [
        _det("beetle", "Mythic", (100, 100), conf=0.9),        # keep
        _det("cactus", "Mythic", (200, 200), conf=0.9),        # keep
        _det("beetle", "Ultra", (300, 300), conf=0.9),         # wrong rarity
        _det("sandstorm", "Mythic", (400, 400), conf=0.9),     # sandstorm excluded
        _det("scorpion", "Mythic", (500, 500), conf=0.4),      # below conf gate
    ]
    got = mythic_candidates(dets, chase_min_conf=0.55)
    assert [d["species"] for d in got] == ["beetle", "cactus"]


def test_mythic_candidates_empty_when_nothing_qualifies():
    assert mythic_candidates([]) == []
    assert mythic_candidates([_det("sandstorm", "Mythic", (10, 10))]) == []


def test_pick_mythic_target_none_when_empty_or_out_of_radius():
    assert pick_mythic_target([], center=(960, 540)) is None
    far = [_det("beetle", "Mythic", (960 + 500, 540), conf=0.9)]  # 500px > 450 engage
    assert pick_mythic_target(far, center=(960, 540), latched=False) is None


def test_pick_mythic_target_uses_release_radius_when_latched():
    d = [_det("beetle", "Mythic", (960 + 500, 540), conf=0.9)]     # 500px
    assert pick_mythic_target(d, center=(960, 540), latched=False) is None       # >450
    got = pick_mythic_target(d, center=(960, 540), latched=True)                 # <600
    assert got is not None and got["species"] == "beetle"


def test_pick_mythic_target_prefers_higher_rank():
    dets = [
        _det("cactus", "Mythic", (1000, 540), conf=0.9),   # rank 1, closer
        _det("beetle", "Mythic", (1100, 540), conf=0.9),   # rank 5, farther
    ]
    got = pick_mythic_target(dets, center=(960, 540))
    assert got["species"] == "beetle"


def test_pick_mythic_target_nearest_breaks_a_rank_tie():
    dets = [
        _det("beetle", "Mythic", (960 + 300, 540), conf=0.9),  # 300px
        _det("beetle", "Mythic", (960 + 120, 540), conf=0.9),  # 120px — nearer
    ]
    got = pick_mythic_target(dets, center=(960, 540))
    assert got["screen_pos"] == (960 + 120, 540)


def test_pick_mythic_target_latched_holds_continuity_across_jitter():
    """两只同 rank 甲虫分居中心两侧, 已锁定 + prev_pos 贴右边那只 —— 每 tick 都该
    锁右边那只, 就算两只都有 ±2px 抖动也不翻 180° (纯 -dist_to_center tiebreak
    会因亚像素抖动来回跳)."""
    prev = (960 + 150, 540)
    for dl, dr in [(0, 0), (2, -2), (-2, 2), (2, 2), (-2, -2)]:
        left = _det("beetle", "Mythic", (960 - 150 + dl, 540), conf=0.9)
        right = _det("beetle", "Mythic", (960 + 150 + dr, 540), conf=0.9)
        got = pick_mythic_target([left, right], center=(960, 540),
                                 latched=True, prev_pos=prev)
        assert got["screen_pos"][0] > 960          # 始终是右边那只


def test_pick_mythic_target_latched_switches_only_for_strictly_higher_rank():
    """已锁定 + prev_pos 贴着一只仙人掌, 但范围内还有一只甲虫 (rank 更高) ——
    真来了更值得打的目标, 切过去."""
    cactus = _det("cactus", "Mythic", (960 + 100, 540), conf=0.9)   # 贴 prev_pos
    beetle = _det("beetle", "Mythic", (960 - 220, 540), conf=0.9)   # rank 更高, 离 prev 远
    prev = (960 + 100, 540)
    got = pick_mythic_target([cactus, beetle], center=(960, 540),
                             latched=True, prev_pos=prev)
    assert got["species"] == "beetle"


def test_pick_mythic_target_not_latched_ignores_prev_pos():
    """没锁定时 prev_pos 不参与 —— 仍是 rank 优先 + 离中心近 tiebreak."""
    dets = [
        _det("cactus", "Mythic", (1000, 540), conf=0.9),   # rank 1, 更近, 贴 prev
        _det("beetle", "Mythic", (1150, 540), conf=0.9),   # rank 5, 更远
    ]
    got = pick_mythic_target(dets, center=(960, 540), latched=False,
                             prev_pos=(1000, 540))
    assert got["species"] == "beetle"


def test_select_action_flees_when_avoid_mob_in_range():
    detections = [
        _det("scorpion", "Ultra", (1100, 540)),   # 160px from center, 在触发半径内
        _det("sandstorm", "Common", (960, 700)),  # 优先级再高也不该盖过flee
    ]
    action, payload = select_action(detections, avoid_trigger_px=400, center=(960, 540))
    assert action == "flee"
    assert (1100, 540) in payload


def test_select_action_ignores_avoid_mob_outside_trigger_radius():
    detections = [
        _det("scorpion", "Ultra", (2000, 540)),      # 1040px, 远超触发半径
        _det("sandstorm", "Mythic", (1000, 560)),    # Mythic 才够格当追击目标
    ]
    action, target, hold_px, repel = select_action(detections, avoid_trigger_px=400, center=(960, 540))
    assert action == "chase"
    assert target["species"] == "sandstorm"
    assert hold_px is None
    # AVOID怪没触发flee, 但还是进repel列表让追击路径绕开它
    assert (2000, 540) in repel


def test_select_action_chases_best_priority_candidate():
    detections = [
        _det("scorpion", "Common", (1000, 540)),          # Common: 不到 Mythic 档, 不追
        _det("sand_centipede", "Mythic", (1010, 540)),    # Mythic: 唯一够格的目标
    ]
    action, target, hold_px, repel = select_action(detections, center=(960, 540))
    assert action == "chase"
    assert target["species"] == "sand_centipede"
    assert repel == []   # 没有AVOID/别的CAUTIOUS, 没什么要绕的


def test_select_action_wanders_when_best_candidate_below_mythic():
    # 密集刷怪区的实况: 一堆 Common/传奇沙尘暴, 一个 Mythic 都没有 -> 交回 wander,
    # 别对着乱跳的沙尘暴原地打转 (旧行为 move_count=0 的根因).
    detections = [
        _det("sandstorm", "Common", (1000, 540), conf=0.95),
        _det("sandstorm", "Legendary", (900, 600), conf=0.95),
        _det("beetle", "Epic", (1100, 500), conf=0.95),
    ]
    action, payload = select_action(detections, center=(960, 540))
    assert action == "wander" and payload is None


def test_select_action_holds_distance_for_cautious_target():
    detections = [_det("cactus", "Ultra", (1000, 540))]
    action, target, hold_px, repel = select_action(detections, cautious_hold_px=250, center=(960, 540))
    assert action == "chase"
    assert hold_px == 250
    assert repel == []   # 目标本身是CAUTIOUS, 不该把自己放进repel


def test_select_action_chase_repels_around_other_danger_mobs():
    detections = [
        _det("sandstorm", "Ultra", (1100, 540)),   # CAUTIOUS, 物种优先级最高 -> 目标
        _det("cactus", "Ultra", (900, 400)),        # CAUTIOUS, 不是目标 -> 要绕开
        _det("scorpion", "Ultra", (300, 540)),      # AVOID, 660px>400 不触发flee -> 也要绕开
    ]
    action, target, hold_px, repel = select_action(detections, avoid_trigger_px=400, center=(960, 540))
    assert action == "chase"
    assert target["species"] == "sandstorm"
    assert hold_px == 250                           # 目标是 CAUTIOUS
    assert (900, 400) in repel and (300, 540) in repel
    assert (1100, 540) not in repel                 # 目标本身不进 repel


def test_select_action_wanders_with_no_relevant_detections():
    action, payload = select_action([])
    assert action == "wander"
    assert payload is None


def test_select_action_skips_low_confidence_chase_target():
    # 唯一的候选是个 0.45 的幻影框 -> 不追, 回漫游
    action, payload = select_action([_det("sandstorm", "Common", (1100, 540), conf=0.45)],
                                    chase_min_conf=0.55, center=(960, 540))
    assert action == "wander" and payload is None


def test_select_action_prefers_confident_target_over_higher_priority_ghost():
    detections = [
        _det("sand_centipede", "Mythic", (1200, 540), conf=0.45),  # 优先级更高但是幻影
        _det("scorpion", "Mythic", (1000, 540), conf=0.92),        # 优先级低但确实存在
    ]
    action, target, hold_px, repel = select_action(detections, chase_min_conf=0.55, center=(960, 540))
    assert action == "chase"
    assert target["species"] == "scorpion"


def test_select_action_low_conf_avoid_still_flees():
    # 危险怪不吃置信度关: 0.42 的 Ultra 蝎子进半径照样触发规避
    action, payload = select_action([_det("scorpion", "Ultra", (1100, 540), conf=0.42)],
                                    avoid_trigger_px=400, chase_min_conf=0.55, center=(960, 540))
    assert action == "flee"
    assert (1100, 540) in payload


def test_select_action_low_conf_cautious_repels_but_is_not_chased():
    detections = [
        _det("scorpion", "Mythic", (1000, 540), conf=0.9),      # 确实存在的 Mythic -> 目标
        _det("cactus", "Ultra", (900, 400), conf=0.4),          # 低置信 CAUTIOUS -> 只当危险源
    ]
    action, target, hold_px, repel = select_action(detections, chase_min_conf=0.55, center=(960, 540))
    assert action == "chase"
    assert target["species"] == "scorpion"
    assert (900, 400) in repel          # 仍要绕开
    assert hold_px is None              # 目标是 ENGAGE(Mythic), 不是那只低置信 CAUTIOUS


def test_select_action_flee_excludes_out_of_range_avoid_mobs():
    detections = [
        _det("scorpion", "Ultra", (1060, 540)),  # 100px, in range
        _det("beetle", "Ultra", (60, 540)),       # 900px, out of range — must not dilute the flee vector
    ]
    action, payload = select_action(detections, avoid_trigger_px=400, center=(960, 540))
    assert action == "flee"
    assert payload == [(1060, 540)]


def test_chase_is_stalled_false_until_window_full():
    # 样本还没攒满一个 window -> 不判 (返回 False)
    hist = [(0.0, 0.0)] * 10
    assert chase_is_stalled(hist, window=25) is False


def test_chase_is_stalled_true_when_net_displacement_below_threshold():
    # 攒满 window, 首尾净位移几乎为 0 (贴墙被顶住) -> 卡住
    hist = [(5.0 + 0.1 * (i % 2), 5.0) for i in range(25)]   # 只在 0.1 之间抖
    assert chase_is_stalled(hist, min_progress=4.0, window=25) is True


def test_chase_is_stalled_false_when_circling_but_making_progress():
    # 追一个走位的目标: 每 tick 挪一点点 (相邻差 < 1.5, 旧写法会误判卡住),
    # 但一个 window 下来净位移累积过阈值 -> 不算卡住
    hist = [(i * 0.5, 0.0) for i in range(25)]   # 24*0.5 = 12 净位移 > 4.0
    assert chase_is_stalled(hist, min_progress=4.0, window=25) is False


def test_chase_is_stalled_uses_last_window_of_a_longer_history():
    # history 比 window 长时只看最后 window 个样本
    hist = [(i * 5.0, 0.0) for i in range(20)]           # 早期大位移
    hist += [(95.0 + 0.1 * (i % 2), 0.0) for i in range(25)]  # 最近 25 tick 停住
    assert chase_is_stalled(hist, min_progress=4.0, window=25) is True


def test_chase_is_stalled_handles_none_and_empty():
    assert chase_is_stalled(None) is False
    assert chase_is_stalled([]) is False


import pytest

from enemy_detect import load_enemy_model, scan_enemies

_HAS_MODEL = os.path.exists("models/desert.pt")
_SKIP_REASON = "models/desert.pt not present locally (gitignored, user-provided)"


@pytest.mark.skipif(not _HAS_MODEL, reason=_SKIP_REASON)
def test_load_enemy_model_exposes_expected_classes():
    model = load_enemy_model()
    expected = {
        "scorpion", "beetle", "cactus",
        "sandstorm", "sand_centipede", "soldier_fire_ant",
    }
    assert set(model.names.values()) == expected


@pytest.mark.skipif(not _HAS_MODEL, reason=_SKIP_REASON)
def test_scan_enemies_returns_empty_list_for_blank_image():
    blank = np.zeros((640, 640, 3), dtype=np.uint8)
    assert scan_enemies(image=blank) == []


import math as _math


def _mdet(species, screen_pos):
    return {"species": species, "rarity": "Mythic", "screen_pos": screen_pos,
            "bbox": (0, 0, 0, 0), "confidence": 0.9}


def test_mythic_move_ram_matches_aim_mouse_target():
    from enemy_detect import aim_mouse_target
    tgt = _mdet("scorpion", (1460, 540))
    got = mythic_move_target(tgt, center=(960, 540), strafe_radius=180,
                             cactus_hold_px=220, max_extend=500)
    assert got == aim_mouse_target((1460, 540), hold_px=None, center=(960, 540),
                                   max_extend=500)
    assert got == (1460, 540)


def test_mythic_move_hold_approaches_when_far():
    tgt = _mdet("cactus", (1360, 540))          # d = 400 > 220*1.15
    got = mythic_move_target(tgt, center=(960, 540), strafe_radius=180,
                             cactus_hold_px=220, max_extend=500)
    assert got == (1360, 540)                   # straight-in, dist within max_extend


def test_mythic_move_hold_backs_off_when_too_close():
    tgt = _mdet("cactus", (1110, 540))          # d = 150 < 220*0.85 = 187
    x, y = mythic_move_target(tgt, center=(960, 540), strafe_radius=180,
                              cactus_hold_px=220, max_extend=500)
    assert x < 960 and abs(y - 540) < 1e-6      # moved away along -u


def test_mythic_move_hold_orbits_in_the_band():
    tgt = _mdet("cactus", (1180, 540))          # d = 220, inside [187, 253]
    x, y = mythic_move_target(tgt, center=(960, 540), strafe_radius=180,
                              cactus_hold_px=220, max_extend=500)
    assert abs(x - 960) < 1e-6 and abs(abs(y - 540) - 500) < 1e-6   # pure perpendicular


def test_mythic_move_strafe_is_perpendicular_when_at_radius():
    tgt = _mdet("beetle", (1140, 540))          # d = 180 == strafe_radius
    x, y = mythic_move_target(tgt, center=(960, 540), strafe_radius=180,
                              cactus_hold_px=220, max_extend=500)
    assert abs(x - 960) < 1e-6 and abs(abs(y - 540) - 500) < 1e-6


def test_mythic_move_strafe_pulls_inward_when_far():
    tgt = _mdet("beetle", (1440, 540))          # d = 480 > radius -> inward (+u) component
    x, y = mythic_move_target(tgt, center=(960, 540), strafe_radius=180,
                              cactus_hold_px=220, max_extend=500)
    assert x > 960 and y > 540                  # perp (down) + inward (toward mob, right)


def test_mythic_move_strafe_pushes_outward_when_too_close():
    tgt = _mdet("soldier_fire_ant", (1040, 540))  # d = 80 < radius -> outward (-u)
    x, y = mythic_move_target(tgt, center=(960, 540), strafe_radius=180,
                              cactus_hold_px=220, max_extend=500)
    assert x < 960 and y > 540


def test_mythic_move_zero_distance_returns_center():
    tgt = _mdet("beetle", (960, 540))
    assert mythic_move_target(tgt, center=(960, 540), strafe_radius=180,
                              cactus_hold_px=220, max_extend=500) == (960, 540)


def test_measure_hp_bar_thickness_one_bar():
    img, bbox = _name_tag_image(_hex_to_bgr(RARITY_COLORS["Rare"]))
    # _name_tag_image draws its HP bar 5 rows thick (thick=5 in that helper)
    assert measure_hp_bar_thickness([{"bbox": bbox}], img) == [5]


def test_measure_hp_bar_thickness_two_bars_known_thickness():
    img = np.full((200, 400, 3), _BG_BGR, dtype=np.uint8)
    img[100:104, 40:160] = _BAR_BGR     # 4 rows thick, under bbox A
    img[100:107, 240:360] = _BAR_BGR    # 7 rows thick, under bbox B
    dets = [{"bbox": (60, 20, 140, 90)}, {"bbox": (260, 20, 340, 90)}]
    assert measure_hp_bar_thickness(dets, img) == [4, 7]


def test_measure_hp_bar_thickness_skips_no_bar_and_empty():
    img = np.full((200, 300, 3), _BG_BGR, dtype=np.uint8)   # no bar anywhere
    assert measure_hp_bar_thickness([{"bbox": (10, 10, 60, 60)}], img) == []
    assert measure_hp_bar_thickness([], img) == []


@pytest.mark.skipif(not _HAS_MODEL, reason=_SKIP_REASON)
def test_scan_bar_thickness_empty_for_blank_image():
    blank = np.zeros((480, 640, 3), dtype=np.uint8)
    assert scan_bar_thickness(image=blank) == []
