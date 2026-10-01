# Mythic-proximity Engage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a Mythic-rarity desert mob comes within ~450px of the player, latch onto it and kite it with a per-species movement policy until it's gone, then resume sandstorm farming.

**Architecture:** Three pure helpers in `enemy_detect.py` (candidate filter, target picker, movement math). A pure latch state-machine helper in `main.py`. `auto_farming()`'s loop gains a precedence slot between `flee` and `chase`/`wander` that drives the latch. `select_action()` is untouched — the Mythic path reads raw `detections`.

**Tech Stack:** Python 3.11, pytest, numpy (already deps). No new dependencies. YOLO model `models/desert.pt` (classes: scorpion, beetle, cactus, sandstorm, sand_centipede, soldier_fire_ant).

## Global Constraints

- Detection dict shape (from `enemy_detect.scan_enemies`): `{"species": str, "rarity": str, "screen_pos": (x, y), "bbox": (x1,y1,x2,y2), "confidence": float}`.
- Rarity strings come from `enemy_detect.RARITY_ORDER`: `Common, Unusual, Rare, Epic, Legendary, Mythic, Ultra, Super, Eternal, Unique`.
- Screen-space math only in this feature. `center` defaults to `enemy_detect.SCREEN_CENTER` = `(utils.SCREEN_WIDTH/2, utils.SCREEN_HEIGHT/2)`.
- `max_extend` when `None` = `500 * utils.mouse_scale()` (matches `aim_mouse_target`). Tests always pass `max_extend` explicitly to skip the scale call.
- `enemy_detect.CHASE_MIN_CONF = 0.55` is the shared phantom-box gate.
- Run tests with `./venv/bin/python -m pytest`.
- Chinese comments/messages, matching surrounding code style. Commit messages in English, conventional-commits, ending with the `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>` trailer.
- Placeholder tuning defaults — do not agonize over values: `MYTHIC_ENGAGE_PX=450`, `MYTHIC_RELEASE_PX=600`, `MYTHIC_RELEASE_MISSES=3`, `MYTHIC_STRAFE_RADIUS=180`, `MYTHIC_CACTUS_HOLD_PX=220`, `K_RADIAL=0.8`.

---

## Task 1: Mythic tables + `mythic_candidates()`

**Files:**
- Modify: `enemy_detect.py` (add after `priority_score()`, ~line 178, before the `SCREEN_CENTER` definition)
- Test: `test_enemy_detect.py` (append near the other `select_action`/`_det` tests)

**Interfaces:**
- Consumes: `CHASE_MIN_CONF` (defined later in the file at ~line 291 — module-level name, fine at call time), detection dicts.
- Produces:
  - `MYTHIC_KITE_SPECIES: dict[str, str]` — species → one of `"strafe" | "ram" | "hold"`. Keys are exactly the 5 non-sandstorm desert species.
  - `MYTHIC_TARGET_RANK: dict[str, int]` — species → priority int (higher = handled first).
  - `mythic_candidates(detections, chase_min_conf=CHASE_MIN_CONF) -> list[dict]`

- [ ] **Step 1: Write the failing test**

Append to `test_enemy_detect.py`:

```python
from enemy_detect import (
    mythic_candidates, pick_mythic_target, mythic_move_target,
    MYTHIC_KITE_SPECIES, MYTHIC_TARGET_RANK,
)


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
```

- [ ] **Step 2: Run to verify it fails**

Run: `./venv/bin/python -m pytest test_enemy_detect.py -k "mythic_kite_species or mythic_target_rank or mythic_candidates" -v`
Expected: FAIL — `ImportError: cannot import name 'mythic_candidates'`.

- [ ] **Step 3: Implement**

In `enemy_detect.py`, immediately after the `priority_score()` function (before `SCREEN_CENTER = ...`):

```python
# ── Mythic 近身处理 ("先清青怪") ──────────────────────────────────────────
# 青怪 = Mythic 档 (青 = Mythic 名牌的青色). desert.pt 的 6 类里, sandstorm 是刷怪
# 目标本身, 不进这套 —— 其余 5 种每种按下面的策略走位磨死.
MYTHIC_KITE_SPECIES = {
    "beetle": "strafe",           # 直冲型, 垂直环绕让它打空
    "soldier_fire_ant": "strafe",
    "scorpion": "ram",            # 直接撞
    "sand_centipede": "ram",
    "cactus": "hold",             # 站桩带刺, 保持距离在旁边
}

# 多个 Mythic 同时在场时先处理谁 (用户给的顺序, 只用于 Mythic 锁定, 跟 SPECIES_RANK
# 那套普通追击优先级无关).
MYTHIC_TARGET_RANK = {
    "beetle": 5,
    "soldier_fire_ant": 4,
    "scorpion": 3,
    "sand_centipede": 2,
    "cactus": 1,
}


def mythic_candidates(detections, chase_min_conf=CHASE_MIN_CONF):
    """从 detections 里挑出够格进 Mythic 锁定池的: rarity 是 Mythic、species 在
    MYTHIC_KITE_SPECIES (sandstorm 排除)、置信度过 chase_min_conf (同追击的幻影框
    过滤). 返回列表, 可能为空."""
    return [
        d for d in detections
        if d.get("rarity") == "Mythic"
        and d.get("species") in MYTHIC_KITE_SPECIES
        and d.get("confidence", 1.0) >= chase_min_conf
    ]
```

- [ ] **Step 4: Run to verify it passes**

Run: `./venv/bin/python -m pytest test_enemy_detect.py -k "mythic_kite_species or mythic_target_rank or mythic_candidates" -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add enemy_detect.py test_enemy_detect.py
git commit -m "feat: mythic kite tables + mythic_candidates() filter

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: `pick_mythic_target()`

**Files:**
- Modify: `enemy_detect.py` (add after `mythic_candidates()`)
- Test: `test_enemy_detect.py`

**Interfaces:**
- Consumes: `mythic_candidates()`, `MYTHIC_TARGET_RANK`, `SCREEN_CENTER`, `CHASE_MIN_CONF`, `math` (already imported).
- Produces: `pick_mythic_target(detections, center=SCREEN_CENTER, latched=False, engage_px=450, release_px=600, chase_min_conf=CHASE_MIN_CONF) -> dict | None`
  - Search radius = `release_px` if `latched` else `engage_px`.
  - Among qualifying candidates within radius: pick `max` by `(MYTHIC_TARGET_RANK[species], -distance_to_center)` — highest rank, nearest as tiebreak.
  - `None` if nothing qualifies within radius.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `./venv/bin/python -m pytest test_enemy_detect.py -k "pick_mythic_target" -v`
Expected: FAIL — `ImportError` / `NameError: pick_mythic_target`.

- [ ] **Step 3: Implement**

Append in `enemy_detect.py` after `mythic_candidates()`:

```python
def pick_mythic_target(detections, center=SCREEN_CENTER, latched=False,
                       engage_px=450, release_px=600, chase_min_conf=CHASE_MIN_CONF):
    """挑这一 tick 要处理的那只 Mythic. 搜索半径: 已锁定用 release_px (放宽, 迟滞),
    没锁定用 engage_px. 半径内没有合格 Mythic → None. 有 → 按
    (MYTHIC_TARGET_RANK, 离屏幕中心近) 取最高."""
    radius = release_px if latched else engage_px
    cx, cy = center

    def dist(d):
        px, py = d["screen_pos"]
        return math.hypot(px - cx, py - cy)

    in_range = [d for d in mythic_candidates(detections, chase_min_conf=chase_min_conf)
                if dist(d) <= radius]
    if not in_range:
        return None
    return max(in_range, key=lambda d: (MYTHIC_TARGET_RANK[d["species"]], -dist(d)))
```

- [ ] **Step 4: Run to verify it passes**

Run: `./venv/bin/python -m pytest test_enemy_detect.py -k "pick_mythic_target" -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add enemy_detect.py test_enemy_detect.py
git commit -m "feat: pick_mythic_target() with engage/release hysteresis radius

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: `mythic_move_target()`

**Files:**
- Modify: `enemy_detect.py` (add after `pick_mythic_target()`)
- Test: `test_enemy_detect.py`

**Interfaces:**
- Consumes: `MYTHIC_KITE_SPECIES`, `aim_mouse_target()`, `SCREEN_CENTER`, `utils.mouse_scale()`, `math`.
- Produces: `mythic_move_target(target, center=SCREEN_CENTER, *, strafe_radius, cactus_hold_px, max_extend=None, repel_positions=None, k_radial=0.8) -> (float, float)`
  - `target` is a detection dict; policy = `MYTHIC_KITE_SPECIES.get(target["species"], "ram")`.
  - `ram`: identical to `aim_mouse_target(target["screen_pos"], hold_px=None, center=center, max_extend=max_extend, repel_positions=repel_positions)`.
  - `hold`: `d > cactus_hold_px*1.15` → approach (delegate to `aim_mouse_target` ram); `d < cactus_hold_px*0.85` → back off along `-u` at `max_extend`; else orbit along `perp` at `max_extend`.
  - `strafe`: `dir = perp + u * ((d - strafe_radius)/strafe_radius) * k_radial`, normalised, times `max_extend`.
  - `perp = (-u_y, u_x)` (fixed side). `d == 0` → return `center`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `./venv/bin/python -m pytest test_enemy_detect.py -k "mythic_move" -v`
Expected: FAIL — `NameError: mythic_move_target`.

- [ ] **Step 3: Implement**

Append in `enemy_detect.py` after `pick_mythic_target()`:

```python
def mythic_move_target(target, center=SCREEN_CENTER, *, strafe_radius, cactus_hold_px,
                       max_extend=None, repel_positions=None, k_radial=0.8):
    """按 target 的物种策略算这一 tick 鼠标该移到哪:
      ram   (蝎子/蜈蚣)   —— 直接朝目标全速贴, 等同 aim_mouse_target(hold_px=None)
      hold  (仙人掌)       —— 远于 hold*1.15 逼近; 近于 hold*0.85 沿 -u 后撤;
                             中间沿垂直方向 perp 绕圈
      strafe(甲虫/火蚁)    —— 垂直环绕 perp + 朝 strafe_radius 的径向修正
                             (d>r 往里带, d<r 往外推), 归一化后 ×max_extend
    perp 取固定一侧 (-u_y, u_x). d==0 无方向 → 返回 center."""
    if max_extend is None:
        max_extend = 500 * utils.mouse_scale()
    policy = MYTHIC_KITE_SPECIES.get(target["species"], "ram")
    px, py = target["screen_pos"]
    cx, cy = center
    vx, vy = px - cx, py - cy
    d = math.hypot(vx, vy)
    if d == 0:
        return center
    ux, uy = vx / d, vy / d
    perp = (-uy, ux)

    if policy == "ram":
        return aim_mouse_target(target["screen_pos"], hold_px=None, center=center,
                                max_extend=max_extend, repel_positions=repel_positions)

    if policy == "hold":
        if d > cactus_hold_px * 1.15:
            return aim_mouse_target(target["screen_pos"], hold_px=None, center=center,
                                    max_extend=max_extend, repel_positions=repel_positions)
        if d < cactus_hold_px * 0.85:
            dx, dy = -ux, -uy            # 后撤
        else:
            dx, dy = perp               # 绕圈
        return (cx + dx * max_extend, cy + dy * max_extend)

    # policy == "strafe"
    radial = (d - strafe_radius) / strafe_radius * k_radial
    dx = perp[0] + ux * radial
    dy = perp[1] + uy * radial
    m = math.hypot(dx, dy)
    if m < 1e-6:
        return center
    return (cx + dx / m * max_extend, cy + dy / m * max_extend)
```

- [ ] **Step 4: Run to verify it passes**

Run: `./venv/bin/python -m pytest test_enemy_detect.py -k "mythic" -v`
Expected: PASS (all Task 1–3 mythic tests).

- [ ] **Step 5: Run the whole enemy_detect suite**

Run: `./venv/bin/python -m pytest test_enemy_detect.py -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add enemy_detect.py test_enemy_detect.py
git commit -m "feat: mythic_move_target() per-species kite (ram/hold/strafe)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: `main.py` — expose detections + `_update_mythic_latch()`

**Files:**
- Modify: `main.py` — `_maybe_scan_enemies()` (~line 364) and add `_update_mythic_latch()` next to it; the one caller (~line 459).
- Test: `test_main_worker.py` — update the 5 existing `_maybe_scan_enemies` tests, add latch tests.

**Interfaces:**
- Consumes: `enemy_detect.select_action`, `enemy_detect.scan_enemies`, `ENEMY_SCAN_INTERVAL`.
- Produces:
  - `_maybe_scan_enemies(enemy_ai_enabled, now, last_enemy_scan, prev_decision, prev_detections) -> (decision, detections, last_enemy_scan)` — now 5 args, 3 return values. Disabled → `(("wander", None), [], last)`. Throttled → `(prev_decision, prev_detections, last)`. Scan error → `(("wander", None), [], now)`.
  - `_update_mythic_latch(latched, misses, has_target, release_misses) -> (latched, misses)` — pure. `has_target` truthy → `(True, 0)`. Not latched, no target → `(False, 0)`. Latched, no target → increment `misses`; `>= release_misses` → `(False, 0)`, else `(True, misses)`.

- [ ] **Step 1: Update the 5 existing `_maybe_scan_enemies` tests to the new arity**

In `test_main_worker.py`, replace the five `test_maybe_scan_enemies_*` functions with:

```python
def test_maybe_scan_enemies_disabled_never_touches_enemy_detect(monkeypatch):
    monkeypatch.setattr(main.enemy_detect, "scan_enemies",
                        lambda **k: (_ for _ in ()).throw(AssertionError("不该扫描")))
    decision, dets, last = main._maybe_scan_enemies(False, 1000.0, 0.0, ("chase", "x"), ["old"])
    assert decision == ("wander", None)
    assert dets == []
    assert last == 0.0


def test_maybe_scan_enemies_throttled_returns_prev(monkeypatch):
    monkeypatch.setattr(main.enemy_detect, "scan_enemies",
                        lambda **k: (_ for _ in ()).throw(AssertionError("还没到扫描间隔")))
    prev, prev_dets = ("flee", [(1, 2)]), ["d1", "d2"]
    decision, dets, last = main._maybe_scan_enemies(True, 0.1, 0.0, prev, prev_dets)
    assert decision is prev
    assert dets is prev_dets
    assert last == 0.0


def test_maybe_scan_enemies_scans_when_due(monkeypatch):
    monkeypatch.setattr(main.enemy_detect, "scan_enemies", lambda **k: ["det"])
    monkeypatch.setattr(main.enemy_detect, "select_action",
                        lambda dets, **k: ("chase", "target", 250, []))
    now = main.ENEMY_SCAN_INTERVAL + 1.0
    decision, dets, last = main._maybe_scan_enemies(True, now, 0.0, ("wander", None), [])
    assert decision == ("chase", "target", 250, [])
    assert dets == ["det"]
    assert last == now


def test_maybe_scan_enemies_scan_error_degrades_to_wander(monkeypatch):
    monkeypatch.setattr(main.enemy_detect, "scan_enemies",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("model missing")))
    now = main.ENEMY_SCAN_INTERVAL + 1.0
    decision, dets, last = main._maybe_scan_enemies(True, now, 0.0, ("wander", None), ["old"])
    assert decision == ("wander", None)
    assert dets == []
    assert last == now
```

(That is 4 functions — the original set had `test_maybe_scan_enemies_disabled...`, `_throttled_...`, `_scans_when_due`, `_scan_error_...`. If a 5th exists in the file, update it to unpack `decision, dets, last` the same way.)

- [ ] **Step 2: Add the latch-helper tests**

Append to `test_main_worker.py`:

```python
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
```

- [ ] **Step 3: Run to verify the new/updated tests fail**

Run: `./venv/bin/python -m pytest test_main_worker.py -k "maybe_scan_enemies or mythic_latch" -v`
Expected: FAIL — `_maybe_scan_enemies` still returns 2 values (ValueError unpacking) / `_update_mythic_latch` missing.

- [ ] **Step 4: Implement in `main.py`**

Replace `_maybe_scan_enemies()` body and add the helper below it:

```python
def _maybe_scan_enemies(enemy_ai_enabled, now, last_enemy_scan, prev_decision, prev_detections):
    """索敌节流 + 总开关. 返回 (decision, detections, last_enemy_scan).

    - enemy_ai_enabled=False: 永远返回漫游决策 + 空检测列表, 一次都不碰 enemy_detect.
    - 距上次扫描不到 ENEMY_SCAN_INTERVAL: 沿用上一轮的 decision 和 detections.
    - 到点了: 跑一次 scan_enemies + select_action; 任何异常 → 漫游 + 空列表.
    detections 单独回传是给 Mythic 近身锁定用的 (select_action 不看这个).
    """
    if not enemy_ai_enabled:
        return ("wander", None), [], last_enemy_scan
    if now - last_enemy_scan < ENEMY_SCAN_INTERVAL:
        return prev_decision, prev_detections, last_enemy_scan
    last_enemy_scan = now
    try:
        detections = enemy_detect.scan_enemies(model_path=ENEMY_MODEL_PATH)
        decision = enemy_detect.select_action(
            detections,
            avoid_trigger_px=AVOID_TRIGGER_PX,
            cautious_hold_px=CAUTIOUS_HOLD_PX,
            chase_min_conf=CHASE_MIN_CONF,
        )
    except Exception as e:
        print(f"⚠️ 索敌出错, 本轮当漫游处理: {e}")
        decision, detections = ("wander", None), []
    return decision, detections, last_enemy_scan


def _update_mythic_latch(latched, misses, has_target, release_misses):
    """Mythic 近身锁定的状态机 (纯函数). has_target = 这次扫描有没有合格的近身
    Mythic. 有 → 锁定, misses 清零. 没有且已锁定 → misses+1, 攒够 release_misses
    就解锁 (迟滞, 扛检测闪烁). 返回 (latched, misses)."""
    if has_target:
        return True, 0
    if not latched:
        return False, 0
    misses += 1
    if misses >= release_misses:
        return False, 0
    return True, misses
```

- [ ] **Step 5: Update the caller in `auto_farming()` (~line 459)**

Find:

```python
        enemy_decision, last_enemy_scan = _maybe_scan_enemies(
            enemy_ai_enabled, now, last_enemy_scan, enemy_decision)
```

Replace with:

```python
        enemy_decision, detections, last_enemy_scan = _maybe_scan_enemies(
            enemy_ai_enabled, now, last_enemy_scan, enemy_decision, detections)
```

And in the loop-state init block (~line 417, right after `enemy_decision = ("wander", None)`), add:

```python
    detections = []
```

- [ ] **Step 6: Run tests**

Run: `./venv/bin/python -m pytest test_main_worker.py test_main_smoke.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add main.py test_main_worker.py
git commit -m "feat: _maybe_scan_enemies returns detections; add _update_mythic_latch

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: Wire the Mythic latch into `auto_farming()`

**Files:**
- Modify: `main.py` — tuning block (~line 24), `auto_farming()` loop-state init (~line 417) and the enemy branch (~line 461–503). Extract a `_drive_and_check_stall()` helper (module level, near `_update_mythic_latch`).
- Test: `test_main_worker.py` (one structural test); rely on the full suite + smoke for the loop wiring (same posture as the rest of the farm loop, per spec).

**Interfaces:**
- Consumes: `enemy_detect.pick_mythic_target`, `enemy_detect.mythic_move_target`, `enemy_detect.MYTHIC_KITE_SPECIES`, `enemy_detect.SCREEN_CENTER`, `enemy_detect.CHASE_STALL_WINDOW`, `enemy_detect.chase_is_stalled`, `_update_mythic_latch`, `execute_anti_stuck`, `clamp_to_screen`, `overlay`.
- Produces: no new public API. New module constants: `MYTHIC_LATCH_ENABLED`, `MYTHIC_ENGAGE_PX`, `MYTHIC_RELEASE_PX`, `MYTHIC_RELEASE_MISSES`, `MYTHIC_STRAFE_RADIUS`, `MYTHIC_CACTUS_HOLD_PX`. New helper `_drive_and_check_stall(mouse_target, current_pos, chase_pos_history, state, message) -> "stuck" | "moved"`.

- [ ] **Step 1: Add tuning constants**

In `main.py`, in the `# ===== 索敌配置 =====` block, after `CHASE_MIN_CONF = 0.55` and before the `# 以上数值...` comment line:

```python
MYTHIC_LATCH_ENABLED  = True   # 贴脸有 Mythic 怪 → 锁定优先清掉再继续刷 (总开关)
MYTHIC_ENGAGE_PX      = 450    # Mythic 怪进此半径 → 锁定
MYTHIC_RELEASE_PX     = 600    # 已锁定后, Mythic 出此半径才算脱离 (迟滞)
MYTHIC_RELEASE_MISSES = 3      # 连续多少次扫描没有合格 Mythic 才解锁
MYTHIC_STRAFE_RADIUS  = 180    # 甲虫/火蚁: 环绕它转圈的目标半径 (px)
MYTHIC_CACTUS_HOLD_PX = 220    # 仙人掌: 保持的距离 (px)
```

- [ ] **Step 2: Write the structural test**

Append to `test_main_worker.py`:

```python
def test_main_exposes_mythic_wiring():
    assert hasattr(main, "_drive_and_check_stall")
    assert isinstance(main.MYTHIC_LATCH_ENABLED, bool)
    for name in ("MYTHIC_ENGAGE_PX", "MYTHIC_RELEASE_PX", "MYTHIC_RELEASE_MISSES",
                 "MYTHIC_STRAFE_RADIUS", "MYTHIC_CACTUS_HOLD_PX"):
        assert isinstance(getattr(main, name), (int, float))
```

- [ ] **Step 3: Run to verify it fails**

Run: `./venv/bin/python -m pytest test_main_worker.py::test_main_exposes_mythic_wiring -v`
Expected: FAIL — `_drive_and_check_stall` missing.

- [ ] **Step 4: Extract `_drive_and_check_stall()`**

Add at module level in `main.py`, right after `_update_mythic_latch()`:

```python
def _drive_and_check_stall(mouse_target, current_pos, chase_pos_history, state, message):
    """chase / flee / 清青怪 三条分支共用的"卡住检测 + 出手"收尾.

    mouse_target == enemy_detect.SCREEN_CENTER 是"刻意停在这" (保持距离 / 合力抵消),
    不算移动 —— 这种 tick 不往 history 塞样本 (留给下一个真在动的 tick). 其余情况:
    攒近期 minimap 坐标, 时间窗内净位移不足 → execute_anti_stuck() 接管这一 tick、
    返回 "stuck"; 否则 overlay 更新 + moveTo + sleep, 返回 "moved"."""
    if mouse_target != enemy_detect.SCREEN_CENTER:
        chase_pos_history.append(current_pos)
        if len(chase_pos_history) > enemy_detect.CHASE_STALL_WINDOW:
            chase_pos_history.pop(0)
        if enemy_detect.chase_is_stalled(chase_pos_history):
            overlay.update(state="卡住", message=f"{message}途中卡住, 脱困中")
            execute_anti_stuck()
            chase_pos_history.clear()
            return "stuck"
    overlay.update(state=state, pos=current_pos, message=message)
    pyautogui.moveTo(clamp_to_screen(*mouse_target))
    time.sleep(0.05)
    return "moved"
```

- [ ] **Step 5: Rewrite the enemy branch of `auto_farming()`'s loop**

Replace the block that currently runs from `enemy_action = enemy_decision[0]` down to just before `# enemy_action == "wander"` / `chase_pos_history.clear()` with:

```python
        enemy_action = enemy_decision[0]

        # 1) flee 最优先 —— 且立刻放掉 Mythic 锁定 (躲优先, 不为打 Mythic 送死).
        if enemy_action == "flee":
            mythic_latch, mythic_misses = False, 0
            mouse_target = enemy_detect.flee_mouse_target(enemy_decision[1])
            _drive_and_check_stall(mouse_target, current_pos, chase_pos_history,
                                   "规避中", "附近有危险稀有怪, 拉开距离")
            continue

        # 2) Mythic 近身锁定 —— flee 之外, 贴脸有合格 Mythic 就锁定按物种走位磨掉.
        if MYTHIC_LATCH_ENABLED and enemy_ai_enabled:
            mtarget = enemy_detect.pick_mythic_target(
                detections, enemy_detect.SCREEN_CENTER, mythic_latch,
                MYTHIC_ENGAGE_PX, MYTHIC_RELEASE_PX, CHASE_MIN_CONF)
            mythic_latch, mythic_misses = _update_mythic_latch(
                mythic_latch, mythic_misses, mtarget is not None, MYTHIC_RELEASE_MISSES)
            if mythic_latch and mtarget is not None:
                repel = enemy_decision[3] if enemy_action == "chase" else []
                mouse_target = enemy_detect.mythic_move_target(
                    mtarget, enemy_detect.SCREEN_CENTER,
                    strafe_radius=MYTHIC_STRAFE_RADIUS,
                    cactus_hold_px=MYTHIC_CACTUS_HOLD_PX,
                    repel_positions=repel)
                policy = enemy_detect.MYTHIC_KITE_SPECIES[mtarget["species"]]
                _drive_and_check_stall(mouse_target, current_pos, chase_pos_history,
                                       "清青怪", f"遛 {mtarget['species']}({policy})")
                continue

        # 3) 普通追击 —— 不 fleeing 也没锁定 Mythic.
        if enemy_action == "chase":
            target, hold_px, repel = enemy_decision[1], enemy_decision[2], enemy_decision[3]
            mouse_target = enemy_detect.aim_mouse_target(
                target["screen_pos"], hold_px=hold_px, repel_positions=repel)
            _drive_and_check_stall(mouse_target, current_pos, chase_pos_history,
                                   "索敌中", f"追击 {target['species']}({target['rarity']})")
            continue

        # 4) enemy_action == "wander": 没有可打/需规避的目标, 随机漫游.
        chase_pos_history.clear()
```

Then the existing `random_x, random_y = random_walkable_point(...)` line and everything after it stays unchanged.

- [ ] **Step 6: Add loop-state init**

In the loop-state block (~line 417), alongside `enemy_decision = ("wander", None)` / `detections = []` / `chase_pos_history = []`, add:

```python
    mythic_latch = False
    mythic_misses = 0
```

- [ ] **Step 7: Compile-check + full suite + smoke**

Run: `./venv/bin/python -m py_compile main.py enemy_detect.py`
Run: `./venv/bin/python -m pytest -q`
Expected: PASS — all tests, no regressions.

- [ ] **Step 8: Diagnostic smoke on a real frame**

Run:
```bash
./venv/bin/python -c "
import cv2, enemy_detect as ed
img = cv2.imread('debug_enemy_live_raw.png')
dets = ed.scan_enemies(image=img)
print('dets:', len(dets), 'mythic candidates:', len(ed.mythic_candidates(dets)))
print('pick:', ed.pick_mythic_target(dets, ed.SCREEN_CENTER))
"
```
Expected: runs without error; prints counts (likely 0 Mythic candidates on that frame — that's fine, it confirms the path is wired and non-crashing).

- [ ] **Step 9: Commit**

```bash
git add main.py test_main_worker.py
git commit -m "feat: latched Mythic-proximity engage in auto_farming loop

Slots between flee and chase: a Mythic mob within MYTHIC_ENGAGE_PX latches
the bot onto it (hysteresis release via MYTHIC_RELEASE_PX / _MISSES) and
kites per species (strafe beetle/fire_ant, ram scorpion/centipede, hold
cactus) until it's gone. flee still wins and clears the latch. Shared
drive+stall tail extracted to _drive_and_check_stall().

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

- [ ] **Step 10: Push**

```bash
git push origin main
```

---

## Self-Review

**1. Spec coverage:**

| Spec item | Task |
|---|---|
| Trigger 450 / release 600 / 3-miss hysteresis | Task 2 (`pick_mythic_target` radius) + Task 4 (`_update_mythic_latch`) + Task 5 (wiring) |
| Qualifying = Mythic + 5 species + conf≥0.55 | Task 1 (`mythic_candidates`) |
| Which Mythic: beetle>fire_ant>scorpion>centipede>cactus, nearest tiebreak | Task 1 (`MYTHIC_TARGET_RANK`) + Task 2 (`pick_mythic_target` key) |
| Per-species policy strafe/ram/hold | Task 1 (`MYTHIC_KITE_SPECIES`) + Task 3 (`mythic_move_target`) |
| Perpendicular circle-strafe, no heading estimation | Task 3 (strafe branch) |
| Precedence flee → mythic → chase/wander; flee clears latch | Task 5 (branch order + `mythic_latch = False` on flee) |
| Mythic sandstorm excluded → normal ram | Task 1 (`MYTHIC_KITE_SPECIES` has no `sandstorm`) — `pick_mythic_target` never returns it, falls through to `select_action` |
| Reuse `chase_pos_history` + `chase_is_stalled` | Task 5 (`_drive_and_check_stall`) |
| `MYTHIC_LATCH_ENABLED` constant kill switch | Task 5 (constant + `if MYTHIC_LATCH_ENABLED`) |
| No config.json toggle | (not implemented — correct) |
| repel threaded into ram / hold-approach; strafe skips repel v1 | Task 3 (delegates to `aim_mouse_target` for ram + hold-approach; strafe branch has no repel) |
| Detections exposed from `_maybe_scan_enemies`, reused on throttled tick | Task 4 |
| Overlay state for the new mode | Task 5 (`state="清青怪"`) |

No gaps.

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N". All steps have real code. `d == 0` handled explicitly in Task 3. Empty detections handled in Tasks 1–2.

**3. Type consistency:**
- `pick_mythic_target` signature identical in Task 2 interface, Task 2 impl, Task 5 call site (`detections, SCREEN_CENTER, mythic_latch, MYTHIC_ENGAGE_PX, MYTHIC_RELEASE_PX, CHASE_MIN_CONF`).
- `mythic_move_target` — keyword-only `strafe_radius` / `cactus_hold_px` in Task 3 interface, impl, tests, and Task 5 call. `repel_positions` name matches `aim_mouse_target`.
- `_maybe_scan_enemies` — 5 params / 3 returns in Task 4 impl, Task 4 tests, Task 5 caller.
- `_update_mythic_latch(latched, misses, has_target, release_misses)` — same in Task 4 impl, Task 4 tests, Task 5 call.
- `_drive_and_check_stall(mouse_target, current_pos, chase_pos_history, state, message)` — same in Task 5 impl and all three call sites.
- `MYTHIC_KITE_SPECIES` values `{"strafe","ram","hold"}` — consumed by `mythic_move_target` dispatch and the Task 5 overlay message. Consistent.

No inconsistencies found.
