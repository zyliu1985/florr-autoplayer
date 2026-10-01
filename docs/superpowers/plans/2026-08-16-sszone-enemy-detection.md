# Sandstorm Zone Enemy Detection (YOLO Targeting) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `auto_farming()` a YOLO-based enemy-awareness layer — detect the 6 `desert.pt` mob classes, read each one's rarity from its name-tag color, and either chase/aim at the best target, back off from a dangerous one, or fall back to the existing random-wander behavior.

**Architecture:** A new self-contained module, `enemy_detect.py`, does all detection/classification/decision math in pure, unit-testable functions (no game/screen dependency except at the one `scan_enemies()` boundary, which accepts an injectable image for tests). `main.py`'s `auto_farming()` loop calls into it once per throttled scan interval and branches on the result — everything else about the existing farming loop (death/menu/AFK checks, area-return pathing, stuck handling) is untouched.

**Tech Stack:** Python 3.11, `ultralytics` (YOLOv8 inference, safe-loading path), `opencv-python`, `numpy`, `pyautogui`, `pytest`.

## Global Constraints

- Model file: `models/desert.pt` (classes: `scorpion, beetle, cactus, sandstorm, sand_centipede, soldier_fire_ant`), loaded **only** through `ultralytics.YOLO(path)` — never a raw unrestricted `torch.load(..., weights_only=False)`. See the spec's "Model provenance note".
- The model file itself is **not** downloaded or committed by any task here — it's user-provided and gitignored (5.7MB third-party binary). Any test that needs it on disk must skip gracefully when it's absent.
- Two coordinate systems must never be mixed: **screen-space** (YOLO detections, mouse-aim math — origin is the 1920×1080 screen, player renders at/near center `(960, 540)`) vs **minimap-space** (existing pathing — the 300×300 `maps/*.png` grid). Every function in this plan is explicitly one or the other; none accept a mix.
- `classify_action` for any (species, rarity) combination not explicitly given a rule (i.e. rarity above `Ultra`) falls back to `AVOID` — fail-closed, not `CAUTIOUS`.
- Enemy detection is strictly additive: any exception anywhere in the scan/decide path must degrade to `("wander", None)`, never crash or block `auto_farming()`.
- `venv/` is Python 3.11 with `torch==2.2.2`, `ultralytics==8.4.120`, `numpy<2`, `opencv-python<5` already installed (rebuilt from a prior 3.14 venv — no torch wheels exist for 3.14). Don't upgrade `numpy`/`opencv-python` independently of `torch` without re-checking `pip check`.

## File Structure

- **Create** `enemy_detect.py` — rarity/species tables, `sample_rarity`, `classify_action`, `priority_score`, `aim_mouse_target`, `flee_mouse_target`, `select_action`, `load_enemy_model`, `scan_enemies`. One cohesive module for the whole enemy-awareness layer (mirrors `utils.py`'s existing "related helpers in one file" convention).
- **Create** `test_enemy_detect.py` — unit tests for every function above, following `test_utils.py`'s plain-pytest-function style (no classes/fixtures).
- **Modify** `main.py` — add `import enemy_detect`, a new `===== 索敌配置 =====` constants block, and the scan/decide branch inside `auto_farming()`'s loop.
- **Modify** `.gitignore` — add `models/*.pt` (binary model weights aren't tracked, same reasoning as `venv/` not being tracked).
- **Modify** `README.md` — one short paragraph telling a future reader where `desert.pt` needs to go and why it isn't in the repo.

---

### Task 1: Rarity color table + `sample_rarity()`

**Files:**
- Create: `enemy_detect.py`
- Test: `test_enemy_detect.py`

**Interfaces:**
- Produces: `RARITY_COLORS: dict[str, str]` (rarity name → 6-hex-digit `RRGGBB` string), `RARITY_ORDER: list[str]` (low → high), `RARITY_RANK: dict[str, int]` (name → index in `RARITY_ORDER`), `_hex_to_bgr(hex_color: str) -> tuple[int, int, int]`, `sample_rarity(image: np.ndarray, bbox: tuple[float, float, float, float], tolerance: int = 40) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# test_enemy_detect.py
import numpy as np

from enemy_detect import sample_rarity, RARITY_COLORS, _hex_to_bgr


def test_sample_rarity_matches_known_colors():
    for name, hexcode in RARITY_COLORS.items():
        bgr = _hex_to_bgr(hexcode)
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        bbox = (40, 60, 60, 80)
        # sample_rarity looks in a patch above the bbox's top-center; paint
        # that exact patch so the test doesn't depend on sample_rarity's
        # internal offsets matching some other guess.
        image[40:52, 30:70] = bgr
        assert sample_rarity(image, bbox) == name


def test_sample_rarity_falls_back_to_common_when_no_match():
    image = np.zeros((100, 100, 3), dtype=np.uint8)  # pure black
    bbox = (40, 60, 60, 80)
    assert sample_rarity(image, bbox) == "Common"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest test_enemy_detect.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'enemy_detect'`

- [ ] **Step 3: Write the implementation**

```python
# enemy_detect.py
import math

import cv2
import numpy as np
import pyautogui
from ultralytics import YOLO

# florr.io稀有度色表(低到高). 来源: 一个公开的florr.io稀有度检测油猴脚本, 缺
# Eternal档(脚本没更新到那个档, 借用Super当占位) —— 跟这个项目其它靠实测校准出来
# 的颜色(比如玩家标记的f8de60)一样, 这张表投入实机使用前需要拿真实截图校准一遍,
# 别直接信.
RARITY_COLORS = {
    "Common": "7EEF6D",
    "Unusual": "FFE65D",
    "Rare": "4D52E3",
    "Epic": "861FDE",
    "Legendary": "DE1F1F",
    "Mythic": "1FDBDE",
    "Ultra": "FF2B75",
    "Super": "2BFFA3",
    "Eternal": "2BFFA3",  # 占位, 未校准 —— 借用Super的颜色, 见上面注释
    "Unique": "555555",
}

RARITY_ORDER = [
    "Common", "Unusual", "Rare", "Epic", "Legendary",
    "Mythic", "Ultra", "Super", "Eternal", "Unique",
]
RARITY_RANK = {name: i for i, name in enumerate(RARITY_ORDER)}


def _hex_to_bgr(hex_color):
    """'RRGGBB' → (B, G, R), 跟cv2的通道顺序一致. 复用utils.py里同样的
    (4, 2, 0)切片写法(那边给玩家标记色用的同一个手法)."""
    return tuple(int(hex_color[i:i + 2], 16) for i in (4, 2, 0))


def sample_rarity(image, bbox, tolerance=40):
    """在检测框上方采样一小块区域(florr.io怪物名牌悬浮在头顶), 按最近色距匹配
    RARITY_COLORS. 采样区域越界(空)或容差外没匹配上 → 默认Common —— 这是最
    宽松/正常接战的那一档, 颜色采样失败不会误触发规避行为."""
    x1, y1, x2, y2 = bbox
    cx = int((x1 + x2) / 2)
    tag_cy = max(0, int(y1) - 14)
    half_w, half_h = 20, 6
    y0, y1s = max(0, tag_cy - half_h), tag_cy + half_h
    x0, x1s = max(0, cx - half_w), cx + half_w
    region = image[y0:y1s, x0:x1s]
    if region.size == 0:
        return "Common"
    mean_bgr = region.reshape(-1, 3).mean(axis=0).tolist()

    best_name, best_dist = "Common", tolerance + 1
    for name in RARITY_ORDER:
        dist = math.dist(mean_bgr, _hex_to_bgr(RARITY_COLORS[name]))
        if dist < best_dist:
            best_name, best_dist = name, dist
    return best_name if best_dist <= tolerance else "Common"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest test_enemy_detect.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add enemy_detect.py test_enemy_detect.py
git commit -m "feat: add rarity color table + sample_rarity()

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `classify_action()` + `priority_score()`

**Files:**
- Modify: `enemy_detect.py`
- Test: `test_enemy_detect.py`

**Interfaces:**
- Consumes: `RARITY_RANK` (Task 1).
- Produces: `SPECIES_RANK: dict[str, int]` (higher = higher chase priority), `classify_action(species: str, rarity: str) -> str` (one of `"ENGAGE"`, `"CAUTIOUS"`, `"AVOID"`), `priority_score(species: str, rarity: str) -> tuple[int, int]`.

- [ ] **Step 1: Write the failing tests**

```python
# test_enemy_detect.py (append)
from enemy_detect import classify_action, priority_score

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest test_enemy_detect.py -v`
Expected: FAIL with `ImportError: cannot import name 'classify_action'`

- [ ] **Step 3: Write the implementation**

```python
# enemy_detect.py (append)

# 数值越大优先级越高(故意跟RARITY_RANK同方向, 好用max()一起挑目标).
# sandstorm > cactus > beetle > scorpion > {sand_centipede, soldier_fire_ant}(并列最低)
SPECIES_RANK = {
    "sandstorm": 5,
    "cactus": 4,
    "beetle": 3,
    "scorpion": 2,
    "sand_centipede": 1,
    "soldier_fire_ant": 1,
}

_AVOID_PAIRS = {("scorpion", "Ultra"), ("beetle", "Ultra")}
_CAUTIOUS_PAIRS = {
    ("sandstorm", "Ultra"), ("cactus", "Ultra"),
    ("sand_centipede", "Ultra"), ("soldier_fire_ant", "Ultra"),
}


def classify_action(species, rarity):
    """按(物种, 稀有度)分档: ENGAGE(正常接战)/CAUTIOUS(可打但保持距离)/
    AVOID(不打, 触发规避). Mythic及以下全ENGAGE; Ultra档蝎子/甲虫AVOID,
    沙尘暴/仙人掌/沙蜈蚣/火蚁CAUTIOUS; 比Ultra还稀有(Super/Eternal/Unique, 实测
    这个刷怪区不会刷新这个档位)没规则覆盖时兜底AVOID —— 失败方向选"别惹", 不选
    "谨慎打": 比已经判AVOID的Ultra蝎子/甲虫还稀有的东西没理由更弱。"""
    if RARITY_RANK[rarity] < RARITY_RANK["Ultra"]:
        return "ENGAGE"
    if (species, rarity) in _AVOID_PAIRS:
        return "AVOID"
    if (species, rarity) in _CAUTIOUS_PAIRS:
        return "CAUTIOUS"
    return "AVOID"


def priority_score(species, rarity):
    """排序键, 数值越大优先级越高. 稀有度档位是第一比较项(碾压式), 物种优先级
    只在同稀有度档位时当平手规则."""
    return (RARITY_RANK[rarity], SPECIES_RANK[species])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest test_enemy_detect.py -v`
Expected: PASS (all tests so far)

- [ ] **Step 5: Commit**

```bash
git add enemy_detect.py test_enemy_detect.py
git commit -m "feat: add classify_action() + priority_score()

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `aim_mouse_target()` + `flee_mouse_target()`

**Files:**
- Modify: `enemy_detect.py`
- Test: `test_enemy_detect.py`

**Interfaces:**
- Produces: `aim_mouse_target(target_pos: tuple[float, float], hold_px: float | None = None, center: tuple[float, float] = (960, 540), max_extend: float = 500) -> tuple[float, float]`, `flee_mouse_target(avoid_positions: list[tuple[float, float]], center: tuple[float, float] = (960, 540), extend: float = 400) -> tuple[float, float]`.

- [ ] **Step 1: Write the failing tests**

```python
# test_enemy_detect.py (append)
from enemy_detect import aim_mouse_target, flee_mouse_target


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


def test_flee_mouse_target_points_away_from_single_threat():
    result = flee_mouse_target([(1460, 540)], center=(960, 540), extend=400)
    assert result[0] < 960
    assert abs(result[1] - 540) < 1e-6


def test_flee_mouse_target_returns_center_when_forces_cancel():
    result = flee_mouse_target([(1460, 540), (460, 540)], center=(960, 540))
    assert result == (960, 540)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest test_enemy_detect.py -v`
Expected: FAIL with `ImportError: cannot import name 'aim_mouse_target'`

- [ ] **Step 3: Write the implementation**

```python
# enemy_detect.py (append)

def aim_mouse_target(target_pos, hold_px=None, center=(960, 540), max_extend=500):
    """把目标的屏幕坐标换算成鼠标该移到的位置 —— 纯屏幕坐标系计算, 跟
    move_to_position()那套小地图坐标系是两套独立空间, 不能互相传参数。
    hold_px设了值时, 一旦已经进到这个距离内就不再继续靠近(退回屏幕中心, 停止
    输出"继续接近"的方向), 给CAUTIOUS档的怪用; hold_px=None时无视距离上限一直
    往目标方向贴(只按max_extend限速度), 给ENGAGE档用。"""
    tx, ty = target_pos
    cx, cy = center
    dx, dy = tx - cx, ty - cy
    dist = math.hypot(dx, dy)
    if dist == 0:
        return center
    if hold_px is not None and dist <= hold_px:
        return center
    extend = min(dist, max_extend)
    return (cx + dx / dist * extend, cy + dy / dist * extend)


def flee_mouse_target(avoid_positions, center=(960, 540), extend=400):
    """算所有AVOID怪的排斥力合向量, 换算成鼠标该移到的位置(往远离它们的方向)。
    合力互相抵消成约0向量(比如两个AVOID怪分别在玩家两侧)时没有明确逃离方向,
    退回屏幕中心 —— 等同于"停止移动", 跟utils.keyup()把鼠标收回中心停止移动是
    同一个约定。"""
    cx, cy = center
    fx, fy = 0.0, 0.0
    for px, py in avoid_positions:
        dx, dy = cx - px, cy - py
        dist = math.hypot(dx, dy)
        if dist == 0:
            continue
        fx += dx / dist
        fy += dy / dist
    mag = math.hypot(fx, fy)
    if mag == 0:
        return center
    return (cx + fx / mag * extend, cy + fy / mag * extend)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest test_enemy_detect.py -v`
Expected: PASS (all tests so far)

- [ ] **Step 5: Commit**

```bash
git add enemy_detect.py test_enemy_detect.py
git commit -m "feat: add aim_mouse_target() + flee_mouse_target()

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: `select_action()`

**Files:**
- Modify: `enemy_detect.py`
- Test: `test_enemy_detect.py`

**Interfaces:**
- Consumes: `classify_action`, `priority_score` (Task 2).
- Produces: `select_action(detections: list[dict], avoid_trigger_px: float = 400, cautious_hold_px: float = 250, center: tuple[float, float] = (960, 540)) -> tuple`. Each item of `detections` is a dict with keys `species: str`, `rarity: str`, `screen_pos: tuple[float, float]`, `bbox: tuple[float, float, float, float]`, `confidence: float` — this exact schema is also what Task 5's `scan_enemies()` produces. Return shape is one of:
  - `("flee", avoid_positions: list[tuple[float, float]])`
  - `("chase", target: dict, hold_px: float | None)`
  - `("wander", None)`

- [ ] **Step 1: Write the failing tests**

```python
# test_enemy_detect.py (append)
from enemy_detect import select_action


def _det(species, rarity, screen_pos):
    return {
        "species": species, "rarity": rarity, "screen_pos": screen_pos,
        "bbox": (0, 0, 0, 0), "confidence": 0.9,
    }


def test_select_action_flees_when_avoid_mob_in_range():
    detections = [
        _det("scorpion", "Ultra", (1100, 540)),   # 160px from center, 在触发半径内
        _det("sandstorm", "Common", (960, 700)),  # 优先级再高也不该盖过flee
    ]
    action, payload = select_action(detections, avoid_trigger_px=400)
    assert action == "flee"
    assert (1100, 540) in payload


def test_select_action_ignores_avoid_mob_outside_trigger_radius():
    detections = [
        _det("scorpion", "Ultra", (2000, 540)),   # 1040px, 远超触发半径
        _det("sandstorm", "Common", (1000, 560)),
    ]
    action, target, hold_px = select_action(detections, avoid_trigger_px=400)
    assert action == "chase"
    assert target["species"] == "sandstorm"
    assert hold_px is None


def test_select_action_chases_best_priority_candidate():
    detections = [
        _det("scorpion", "Common", (1000, 540)),
        _det("sand_centipede", "Rare", (1010, 540)),  # 稀有度更高, 该选它
    ]
    action, target, hold_px = select_action(detections)
    assert action == "chase"
    assert target["species"] == "sand_centipede"


def test_select_action_holds_distance_for_cautious_target():
    detections = [_det("cactus", "Ultra", (1000, 540))]
    action, target, hold_px = select_action(detections, cautious_hold_px=250)
    assert action == "chase"
    assert hold_px == 250


def test_select_action_wanders_with_no_relevant_detections():
    action, payload = select_action([])
    assert action == "wander"
    assert payload is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest test_enemy_detect.py -v`
Expected: FAIL with `ImportError: cannot import name 'select_action'`

- [ ] **Step 3: Write the implementation**

```python
# enemy_detect.py (append)

def select_action(detections, avoid_trigger_px=400, cautious_hold_px=250, center=(960, 540)):
    """每tick的索敌决策入口. detections是scan_enemies()给的检测列表(或测试里
    手搭的同结构字典列表). 返回三选一:
      ("flee", avoid_positions)   —— 触发半径内有AVOID怪, 优先规避
      ("chase", target, hold_px)  —— 没有近身危险, 但有可打目标(ENGAGE/CAUTIOUS)
      ("wander", None)            —— 啥有效目标都没有, 交回原来的随机漫游
    AVOID怪永远进不了"chase"候选池, 哪怕它稀有度算下来优先级最高。"""
    avoid_positions = []
    candidates = []
    for d in detections:
        bucket = classify_action(d["species"], d["rarity"])
        if bucket == "AVOID":
            avoid_positions.append(d["screen_pos"])
        else:
            candidates.append((d, bucket))

    if avoid_positions:
        cx, cy = center
        nearest = min(math.hypot(px - cx, py - cy) for px, py in avoid_positions)
        if nearest <= avoid_trigger_px:
            return ("flee", avoid_positions)

    if candidates:
        best, best_bucket = max(
            candidates,
            key=lambda pair: priority_score(pair[0]["species"], pair[0]["rarity"]))
        hold_px = cautious_hold_px if best_bucket == "CAUTIOUS" else None
        return ("chase", best, hold_px)

    return ("wander", None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest test_enemy_detect.py -v`
Expected: PASS (all tests so far)

- [ ] **Step 5: Commit**

```bash
git add enemy_detect.py test_enemy_detect.py
git commit -m "feat: add select_action() enemy decision function

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: `load_enemy_model()` + `scan_enemies()`

**Files:**
- Modify: `enemy_detect.py`
- Test: `test_enemy_detect.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `sample_rarity` (Task 1).
- Produces: `load_enemy_model(path: str = "models/desert.pt")` (returns a cached `ultralytics.YOLO` instance), `scan_enemies(image: np.ndarray | None = None, conf: float = 0.4, model_path: str = "models/desert.pt") -> list[dict]` — each dict matches the schema Task 4 consumes.

Requires `models/desert.pt` to already exist on disk at the repo root (the user placed it there during brainstorming — confirm with `ls models/desert.pt` before starting; if missing, stop and ask the user to put it back rather than guessing a path).

- [ ] **Step 1: Add the model file to `.gitignore`**

```bash
echo '' >> .gitignore
echo '# YOLO model weights - third-party binaries, user-provided, not tracked' >> .gitignore
echo 'models/*.pt' >> .gitignore
```

- [ ] **Step 2: Write the failing tests**

```python
# test_enemy_detect.py (append)
import os

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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `venv/bin/pytest test_enemy_detect.py -v`
Expected: FAIL with `ImportError: cannot import name 'load_enemy_model'` (both new tests; if `models/desert.pt` is missing on this machine they'll instead show as `SKIPPED` — in that case, stop and get the model file placed before continuing, since Step 4's implementation can't be verified without it)

- [ ] **Step 4: Write the implementation**

```python
# enemy_detect.py (append)

_model = None


def load_enemy_model(path="models/desert.pt"):
    """加载一次desert.pt, 模块级单例缓存. 只走ultralytics.YOLO()的安全加载
    路径(底层是torch的weights_only安全反序列化), 不直接用不设限的
    torch.load(..., weights_only=False) —— 见
    docs/superpowers/specs/2026-08-16-sszone-enemy-detection-design.md的
    "模型来源"说明。"""
    global _model
    if _model is None:
        _model = YOLO(path)
    return _model


def scan_enemies(image=None, conf=0.4, model_path="models/desert.pt"):
    """跑一次YOLO检测, 返回屏幕坐标系(不是小地图坐标系!)下的检测列表.
    image=None时截一次全屏游戏画面; 传image是为了测试时能喂合成图片, 不用依赖
    真实截屏(pyautogui.screenshot()在没有真实显示器的环境里跑不了)。model_path
    转手传给load_enemy_model() —— 不在这里写死, 让调用方(main.py)的配置常量
    真正管用, 不是摆设。"""
    if image is None:
        screenshot = pyautogui.screenshot(region=[0, 0, 1920, 1080])
        image = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

    model = load_enemy_model(model_path)
    results = model.predict(image, conf=conf, verbose=False)
    if not results:
        return []

    result = results[0]
    names = result.names
    detections = []
    for box in result.boxes:
        species = names[int(box.cls[0])]
        confidence = float(box.conf[0])
        x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
        bbox = (x1, y1, x2, y2)
        screen_pos = ((x1 + x2) / 2, (y1 + y2) / 2)
        detections.append({
            "species": species,
            "rarity": sample_rarity(image, bbox),
            "screen_pos": screen_pos,
            "bbox": bbox,
            "confidence": confidence,
        })
    return detections
```

Add `import os` to the top of `test_enemy_detect.py` if not already present (used by `_HAS_MODEL`). `numpy as np` should already be imported there from Task 1's tests.

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/bin/pytest test_enemy_detect.py -v`
Expected: PASS (all tests; the two new ones actually exercise the real model against a blank image, which should take a few seconds but produce zero detections)

- [ ] **Step 6: Commit**

```bash
git add enemy_detect.py test_enemy_detect.py .gitignore
git commit -m "feat: add load_enemy_model() + scan_enemies()

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Wire into `main.py`'s `auto_farming()` loop

**Files:**
- Modify: `main.py`
- Test: `test_main_smoke.py` (new — import-time smoke check; the loop logic itself needs a real game to verify, same as the rest of `auto_farming()`/`move_to_position()`, which have no existing unit tests either)
- Modify: `README.md`

**Interfaces:**
- Consumes: `enemy_detect.scan_enemies`, `enemy_detect.select_action`, `enemy_detect.aim_mouse_target`, `enemy_detect.flee_mouse_target` (Tasks 3-5).

- [ ] **Step 1: Write the failing smoke test**

```python
# test_main_smoke.py
def test_main_module_imports_and_exposes_enemy_config():
    import main
    assert hasattr(main, "auto_farming")
    assert hasattr(main, "ENEMY_SCAN_INTERVAL")
    assert hasattr(main, "AVOID_TRIGGER_PX")
    assert hasattr(main, "CAUTIOUS_HOLD_PX")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest test_main_smoke.py -v`
Expected: FAIL with `AttributeError: module 'main' has no attribute 'ENEMY_SCAN_INTERVAL'`

- [ ] **Step 3: Add the import and config block to `main.py`**

Find this near the top of `main.py`:

```python
from utils import *
from overlay import create_overlay
import time
import random
import afk_watch

overlay = create_overlay()
```

Replace with:

```python
from utils import *
from overlay import create_overlay
import time
import random
import afk_watch
import enemy_detect

overlay = create_overlay()

# ===== 索敌配置 (sszone敌怪检测/追击/规避) =====
ENEMY_MODEL_PATH = "models/desert.pt"
ENEMY_SCAN_INTERVAL = 0.3   # 秒, YOLO扫描节流间隔(不是每tick都跑, 推理有开销)
AVOID_TRIGGER_PX = 400      # 屏幕像素半径, AVOID怪进入此半径触发逃离
CAUTIOUS_HOLD_PX = 250      # 屏幕像素, CAUTIOUS怪保持的最小距离(不继续贴近)
# 以上数值是没实机测过的占位默认值, 实机跑一遍后再按观察到的效果调.
# ================================================
```

- [ ] **Step 4: Insert the scan/decide branch into `auto_farming()`**

Find this block inside `auto_farming()` (right after the "离开刷怪区域" `if not if_in_area(...)` block, before "在区域内随机选择一个可走的目标点"):

```python
        # 在区域内随机选择一个可走的目标点(不是瞎猜矩形里的坐标, 避开墙)
        random_x, random_y = random_walkable_point(farming_area, binary_map)

        # 移动到目标点 —— 到了立刻挑下一个点接着走, 不暂停.
        print(f"🚶 移动到 ({random_x}, {random_y})")
```

Replace with:

```python
        # 索敌: 按ENEMY_SCAN_INTERVAL节流跑YOLO(不是每tick都跑, 推理有开销).
        # 索敌是附加功能, 任何异常都退化成"漫游", 不能让它打断刷怪主循环.
        now = time.time()
        if now - last_enemy_scan >= ENEMY_SCAN_INTERVAL:
            last_enemy_scan = now
            try:
                detections = enemy_detect.scan_enemies(model_path=ENEMY_MODEL_PATH)
                enemy_decision = enemy_detect.select_action(
                    detections,
                    avoid_trigger_px=AVOID_TRIGGER_PX,
                    cautious_hold_px=CAUTIOUS_HOLD_PX,
                )
            except Exception as e:
                print(f"⚠️ 索敌出错, 本轮当漫游处理: {e}")
                enemy_decision = ("wander", None)

        enemy_action = enemy_decision[0]

        if enemy_action == "flee":
            avoid_positions = enemy_decision[1]
            mouse_target = enemy_detect.flee_mouse_target(avoid_positions)
            overlay.update(state="规避中", pos=current_pos, message="附近有危险稀有怪, 拉开距离")
            pyautogui.moveTo(mouse_target)
            time.sleep(0.05)
            continue

        if enemy_action == "chase":
            target, hold_px = enemy_decision[1], enemy_decision[2]
            mouse_target = enemy_detect.aim_mouse_target(target["screen_pos"], hold_px=hold_px)
            overlay.update(state="索敌中", pos=current_pos,
                            message=f"追击 {target['species']}({target['rarity']})")
            pyautogui.moveTo(mouse_target)
            time.sleep(0.05)
            continue

        # enemy_action == "wander": 没有可打/需规避的目标, 跟原来一样随机漫游.
        random_x, random_y = random_walkable_point(farming_area, binary_map)

        # 移动到目标点 —— 到了立刻挑下一个点接着走, 不暂停.
        print(f"🚶 移动到 ({random_x}, {random_y})")
```

- [ ] **Step 5: Initialize the two new loop-state variables**

Find this near the top of `auto_farming()`:

```python
    start_time = time.time()
    move_count = 0
    exit_reason = "timeout"
```

Replace with:

```python
    start_time = time.time()
    move_count = 0
    exit_reason = "timeout"
    last_enemy_scan = 0.0
    enemy_decision = ("wander", None)
```

- [ ] **Step 6: Run the smoke test to verify it passes**

Run: `venv/bin/pytest test_main_smoke.py -v`
Expected: PASS

- [ ] **Step 7: Run the full existing test suite to confirm nothing broke**

Run: `venv/bin/pytest test_utils.py test_overlay.py test_afk_watch.py test_enemy_detect.py test_main_smoke.py -v`
Expected: PASS (all — the original 29 plus everything added in Tasks 1-6)

- [ ] **Step 8: Add a short README note on the model file**

Find this in `README.md`:

```markdown
## Implements
```

Insert this new section directly above it:

```markdown
## Enemy Detection (Sandstorm Zone)

`auto_farming()` can chase/avoid mobs by rarity using a YOLO model. This needs
`models/desert.pt` (6 classes: scorpion, beetle, cactus, sandstorm,
sand_centipede, soldier_fire_ant) placed at that exact path — it's not
included in this repo (third-party binary weights, gitignored). Get it from
[Shiny-Ladybug/assets](https://github.com/Shiny-Ladybug/assets) yourself and
verify its source before use; whoever/whatever wires this up should not be
downloading and loading arbitrary `.pt` files from the internet without a
human confirming that step (`.pt` files are pickle-based and can execute
code on load). See
`docs/superpowers/specs/2026-08-16-sszone-enemy-detection-design.md` for the
full design and the rarity-color-table caveats.

## Implements
```

- [ ] **Step 9: Commit**

```bash
git add main.py test_main_smoke.py README.md
git commit -m "feat: wire enemy detection into auto_farming()

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

- [ ] **Step 10: Manual real-game verification (not automatable — same posture as the rest of this repo's game-loop code)**

1. Confirm `models/desert.pt` is present, florr.io is fullscreen at 1920×1080, player is in the Sandstorm Zone farming area.
2. Run `main.py`. Watch the overlay: it should show `"刷怪中"` while wandering normally, then switch to `"索敌中"` when a mob is on screen, with a message naming the species/rarity it picked.
3. Manually place/wait for an Ultra scorpion or beetle nearby (or the closest safe approximation) and confirm the overlay switches to `"规避中"` and the mouse steers away rather than toward it.
4. Confirm a CAUTIOUS-tier detection (e.g. Ultra sandstorm) gets chased but the character visibly stops closing in once near it, rather than walking into melee range.
5. If the default `AVOID_TRIGGER_PX` (400) or `CAUTIOUS_HOLD_PX` (250) feel wrong (triggers too early/late, holds too far/close), tune those two constants from what's actually observed rather than guessing further — flag the change to the user instead of silently picking new numbers.
6. If the rarity color table misreads a known-rarity mob (compare against the name tag by eye), that's the `RARITY_COLORS` table needing real calibration (same category of fix as the player-marker color bug) — capture a debug screenshot of the mismatch before changing values, don't guess-and-check blind.
