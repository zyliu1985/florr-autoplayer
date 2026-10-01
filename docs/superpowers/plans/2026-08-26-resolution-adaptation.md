# Resolution Adaptation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the bot work at any fullscreen browser resolution/aspect ratio (not just 1920×1080) by detecting the real screen size once at startup and deriving every currently-hardcoded screen coordinate from it.

**Architecture:** Add screen-size detection + a small set of pure scaling helpers (`scale_x`/`scale_y`/`scale_point`/`scale_region`/`clamp_to_screen`) to `utils.py`. Every function in `utils.py`, `main.py`, and `enemy_detect.py` that currently hardcodes `1920`/`1080`/`960`/`540` is rewritten to go through these helpers instead. The one non-mechanical piece: `get_map()`'s captured minimap region must be resized back to exactly 300×300 pixels after capture, because `maps/*.png` are fixed-size templates and every downstream map-space consumer (`lazy_theta_star`, `calibrate_player`, etc.) assumes that exact pixel space.

**Tech Stack:** Python, `pyautogui` (screen size/screenshot/mouse), `opencv-python` (`cv2.resize`), `pytest` + `monkeypatch` for hermetic tests (no real display needed — screen size and `pyautogui.screenshot` are monkeypatched, matching this repo's existing test style in `test_enemy_detect.py`).

## Global Constraints

- Full design/rationale/known-risk lives in `docs/superpowers/specs/2026-08-26-resolution-adaptation-design.md` — read it before starting if anything below is unclear on *why*.
- Reference resolution for every existing hardcoded constant is 1920×1080 (`_REF_WIDTH`/`_REF_HEIGHT` in `utils.py`) — do not change any of the literal numbers being scaled (e.g. `1059, 527` for the start button); only change *how* they're turned into a final coordinate.
- `SCREEN_WIDTH`/`SCREEN_HEIGHT` detection must not crash module import on a display-less machine (existing test suite runs on a real display today, but don't regress that safety) — wrap `pyautogui.size()` in `try/except`, falling back to `(1920, 1080)`.
- Run the full existing test suite after every task and keep it green: `source venv/bin/activate && python3 -m pytest -q` (55 tests pass on the baseline before this plan).
- Every new automated test must be hermetic — no dependency on the actual resolution of the machine running the test (monkeypatch `utils.SCREEN_WIDTH`/`utils.SCREEN_HEIGHT`, never assert against whatever `pyautogui.size()` happens to return on the box running CI).

---

### Task 1: Screen-size detection + scaling helpers in `utils.py`

**Files:**
- Modify: `utils.py` (insert after the existing Windows DPI-awareness block, currently lines 17–31, before `MAP = ""` at line 33)
- Test: `test_utils.py`

**Interfaces:**
- Produces (used by every later task in this plan):
  - `utils.SCREEN_WIDTH: int`, `utils.SCREEN_HEIGHT: int` — module-level, detected once at import
  - `utils.scale_x(value: float) -> int`
  - `utils.scale_y(value: float) -> int`
  - `utils.scale_point(x: float, y: float) -> tuple[int, int]`
  - `utils.scale_region(x: float, y: float, w: float, h: float) -> list[int]` (4-element `[left, top, width, height]`, matching the `region=[...]` shape `pyautogui.screenshot` already takes elsewhere in this file)
  - `utils.mouse_scale() -> float` — `min(SCREEN_WIDTH/1920, SCREEN_HEIGHT/1080)`, for scaling mouse-steering *distances* (not absolute positions). **Amended during Task 1's fix round** (see ledger): originally specified as a plain `MOUSE_SCALE` module-level value, but a plain assignment can't be both (a) hermetically re-testable via `monkeypatch.setattr(utils, "SCREEN_WIDTH", ...)` and (b) usable via bare-name lookup inside `utils.py` itself and via `main.py`'s `from utils import *`. A `def mouse_scale():` function (recomputed on every call, same pattern as `scale_x`/`scale_y`) satisfies all three; a PEP 562 module `__getattr__` property does not — it only fires on external dotted access, not on internal bare-name lookups or on `import *`.
  - `utils.clamp_to_screen(x: float, y: float, margin: int = 2) -> tuple[float, float]`

- [ ] **Step 1: Write the failing tests**

Add to `test_utils.py` (new imports at top: add `import utils` alongside the existing `from utils import ...` line):

```python
import utils


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
    assert round(10 * utils.mouse_scale()) == 5  # must work as a real float in arithmetic, like Task 2/4 use it
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source venv/bin/activate && python3 -m pytest test_utils.py -v`
Expected: FAIL — `AttributeError: module 'utils' has no attribute 'scale_x'` (and similarly for the other new names).

- [ ] **Step 3: Implement the helpers**

Insert into `utils.py` right after the DPI-awareness `try/except` block (before `MAP = ""`):

```python
try:
    SCREEN_WIDTH, SCREEN_HEIGHT = pyautogui.size()
except Exception:
    # 没有真实显示器(比如headless CI)时pyautogui.size()可能取不到 —— 退化成
    # 原来硬编码的1920x1080, 跟这个改动之前的行为完全一致, 不让import本身崩掉.
    SCREEN_WIDTH, SCREEN_HEIGHT = 1920, 1080

_REF_WIDTH, _REF_HEIGHT = 1920, 1080  # 下面这些函数里所有写死的坐标常量都是照这个分辨率量出来的


def scale_x(value):
    """把一个照1920宽量出来的x坐标/宽度, 换算成实际屏幕宽度下的等效值."""
    return round(value * SCREEN_WIDTH / _REF_WIDTH)


def scale_y(value):
    """同scale_x, 换算y坐标/高度(照1080高量出来的)."""
    return round(value * SCREEN_HEIGHT / _REF_HEIGHT)


def scale_point(x, y):
    return (scale_x(x), scale_y(y))


def scale_region(x, y, w, h):
    """把pyautogui.screenshot(region=[x,y,w,h])用的截图区域(照1920x1080量出来的)
    换算成实际分辨率下的区域. 宽高各自按对应轴单独换算(不是简单乘同一个比例),
    这样非16:9分辨率(宽高比跟1920x1080不一样)也不用另外分支处理 —— 跟
    scale_x/scale_y是同一套"每根轴独立缩放"逻辑.

    位置和宽高分开算两次scale_x/scale_y再相减(而不是直接scale_x(w)),是为了让
    四舍五入的误差不累积: right-left的差值比"起点+独立换算的宽度"更贴近实际
    截到的物理像素范围.
    """
    left = scale_x(x)
    top = scale_y(y)
    right = scale_x(x + w)
    bottom = scale_y(y + h)
    return [left, top, right - left, bottom - top]


def mouse_scale():
    """鼠标转向距离(不是绝对坐标)该乘的缩放系数. 写成函数(不是模块级常量), 跟
    scale_x/scale_y同一套模式 —— 每次调用都从当前SCREEN_WIDTH/SCREEN_HEIGHT
    重新算, 这样测试里monkeypatch这两个全局变量后, 调用方(不管是utils.py内部裸
    调用还是外部utils.mouse_scale())拿到的都是按monkeypatch后的值算出来的结果.
    """
    return min(SCREEN_WIDTH / _REF_WIDTH, SCREEN_HEIGHT / _REF_HEIGHT)


def clamp_to_screen(x, y, margin=2):
    """把一个鼠标目标位置钳制在屏幕范围内(留一点margin) —— 防止小分辨率屏幕上
    算出来的转向偏移量把pyautogui.moveTo()的目标坐标推到屏幕外报错."""
    return (
        min(max(x, margin), SCREEN_WIDTH - margin),
        min(max(y, margin), SCREEN_HEIGHT - margin),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source venv/bin/activate && python3 -m pytest test_utils.py -v`
Expected: all tests PASS, including the 12 pre-existing ones in this file.

- [ ] **Step 5: Run the full suite to confirm no regressions elsewhere**

Run: `source venv/bin/activate && python3 -m pytest -q`
Expected: `55 passed` → now `55 + 6 passed` (the 6 new tests from this task).

- [ ] **Step 6: Commit**

```bash
git add utils.py test_utils.py
git commit -m "feat: add resolution-independent scaling helpers to utils.py

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Wire the scaling helpers into `utils.py`'s screen-space functions

**Files:**
- Modify: `utils.py` — `calc_anti_stuck`, `execute_anti_stuck`, `keydown`, `keyup`, `abandon_game`, `_START_BUTTON_POS`/`_CONTINUE_BUTTON_POS`, `_green_button_ratio`, `check_stage`
- Test: `test_utils.py`

**Interfaces:**
- Consumes: `SCREEN_WIDTH`, `SCREEN_HEIGHT`, `mouse_scale()`, `scale_x`, `scale_y`, `scale_point`, `clamp_to_screen` (all from Task 1, same module — no import needed, just used directly).
- No new interfaces produced — this task only changes internals of existing functions. Signatures are unchanged.

`calc_anti_stuck` is the only function here with no `pyautogui` call (pure `numpy` math), so it's the only one that gets a real TDD cycle. The rest (`execute_anti_stuck`, `keydown`, `keyup`, `abandon_game`, `_green_button_ratio`, `check_stage`) touch real mouse/screenshot APIs and — matching this repo's existing test coverage (none of them are unit-tested today either) — get mechanical edits + a full-suite regression run, not new tests. Real on-machine behavior is covered by the spec's "Verification" section, not by this plan.

- [ ] **Step 1: Write the failing test for `calc_anti_stuck`**

Add to `test_utils.py`:

```python
from utils import calc_anti_stuck


def test_calc_anti_stuck_clips_to_actual_screen_bounds_not_1920x1080(monkeypatch):
    monkeypatch.setattr(utils, "SCREEN_WIDTH", 800)
    monkeypatch.setattr(utils, "SCREEN_HEIGHT", 600)
    # A single "wall" pixel far to the left of screen center pushes the
    # suggested position hard to the right — enough to hit whatever the
    # x-clip upper bound is. At the old hardcoded bound (1920) this would
    # stay under it and the bug wouldn't show; at the new 800-wide bound
    # it must clip to 800.
    borders = [(-5000, 300)]
    x, y = calc_anti_stuck(borders, weight=10000.0)
    assert x == 800
    assert 0 <= y <= 600
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source venv/bin/activate && python3 -m pytest test_utils.py::test_calc_anti_stuck_clips_to_actual_screen_bounds_not_1920x1080 -v`
Expected: FAIL — `assert 1920 == 800` (old code still clips to the hardcoded `1920`).

- [ ] **Step 3: Edit `calc_anti_stuck`**

Current (`utils.py`, function `calc_anti_stuck`):

```python
def calc_anti_stuck(borders, weight=1.0):
    screen_center = np.array([960, 540])
    total_force = np.array([0.0, 0.0])

    for point in borders:
        point_vector = np.array(point)
        distance = np.linalg.norm(screen_center - point_vector)
        if distance == 0:
            continue
        force_vector = (screen_center - point_vector) / distance
        total_force += force_vector

    final_position = screen_center + total_force * weight
    final_position[0] = np.clip(final_position[0], 0, 1920)
    final_position[1] = np.clip(final_position[1], 0, 1080)
    toggle_map()
    return final_position[0], final_position[1]
```

Replace with:

```python
def calc_anti_stuck(borders, weight=1.0):
    screen_center = np.array([SCREEN_WIDTH / 2, SCREEN_HEIGHT / 2])
    total_force = np.array([0.0, 0.0])

    for point in borders:
        point_vector = np.array(point)
        distance = np.linalg.norm(screen_center - point_vector)
        if distance == 0:
            continue
        force_vector = (screen_center - point_vector) / distance
        total_force += force_vector

    final_position = screen_center + total_force * weight
    final_position[0] = np.clip(final_position[0], 0, SCREEN_WIDTH)
    final_position[1] = np.clip(final_position[1], 0, SCREEN_HEIGHT)
    toggle_map()
    return final_position[0], final_position[1]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source venv/bin/activate && python3 -m pytest test_utils.py::test_calc_anti_stuck_clips_to_actual_screen_bounds_not_1920x1080 -v`
Expected: PASS

- [ ] **Step 5: Edit `execute_anti_stuck`**

Current:

```python
    pyautogui_img = pyautogui.screenshot(region=[0, 0, 1920, 1080])
    opencv_img = cv2.cvtColor(np.array(pyautogui_img), cv2.COLOR_RGB2BGR)
    borders = check_map_border(opencv_img)
    suggested_position = calc_anti_stuck(borders)
    print(f"🧭 脱困: 朝 {suggested_position} 移动...")
    screen_center = np.array([960, 540])
```

Replace with:

```python
    pyautogui_img = pyautogui.screenshot(region=[0, 0, SCREEN_WIDTH, SCREEN_HEIGHT])
    opencv_img = cv2.cvtColor(np.array(pyautogui_img), cv2.COLOR_RGB2BGR)
    borders = check_map_border(opencv_img)
    suggested_position = calc_anti_stuck(borders)
    print(f"🧭 脱困: 朝 {suggested_position} 移动...")
    screen_center = np.array([SCREEN_WIDTH / 2, SCREEN_HEIGHT / 2])
```

(The rest of `execute_anti_stuck` — the `delta`/`max_delta` steering logic below this — is untouched; it already works purely in terms of `screen_center`, which is now correct.)

- [ ] **Step 6: Edit `keydown`/`keyup`**

Current:

```python
def keydown(direction, delta=500):
    if direction == "w":
        pyautogui.moveTo(1920//2, 1080//2-delta)
    if direction == "s":
        pyautogui.moveTo(1920//2, 1080//2+delta)
    if direction == "a":
        pyautogui.moveTo(1920//2-delta, 1080//2)
    if direction == "d":
        pyautogui.moveTo(1920//2+delta, 1080//2)
    if direction == "wa":
        pyautogui.moveTo(1920//2-delta, 1080//2-delta)
    if direction == "wd":
        pyautogui.moveTo(1920//2+delta, 1080//2-delta)
    if direction == "sa":
        pyautogui.moveTo(1920//2-delta, 1080//2+delta)
    if direction == "sd":
        pyautogui.moveTo(1920//2+delta, 1080//2+delta)


def keyup(direction):
    pyautogui.moveTo(1920//2, 1080//2)
```

Replace with:

```python
def keydown(direction, delta=500):
    delta = round(delta * mouse_scale())
    cx, cy = SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2
    if direction == "w":
        pyautogui.moveTo(*clamp_to_screen(cx, cy - delta))
    if direction == "s":
        pyautogui.moveTo(*clamp_to_screen(cx, cy + delta))
    if direction == "a":
        pyautogui.moveTo(*clamp_to_screen(cx - delta, cy))
    if direction == "d":
        pyautogui.moveTo(*clamp_to_screen(cx + delta, cy))
    if direction == "wa":
        pyautogui.moveTo(*clamp_to_screen(cx - delta, cy - delta))
    if direction == "wd":
        pyautogui.moveTo(*clamp_to_screen(cx + delta, cy - delta))
    if direction == "sa":
        pyautogui.moveTo(*clamp_to_screen(cx - delta, cy + delta))
    if direction == "sd":
        pyautogui.moveTo(*clamp_to_screen(cx + delta, cy + delta))


def keyup(direction):
    pyautogui.moveTo(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)
```

- [ ] **Step 7: Edit `abandon_game`**

Current:

```python
def abandon_game():
    pyautogui.moveTo(307, 32)
    pyautogui.doubleClick()
    pyautogui.doubleClick()
    pyautogui.doubleClick()
```

Replace with:

```python
def abandon_game():
    pyautogui.moveTo(*scale_point(307, 32))
    pyautogui.doubleClick()
    pyautogui.doubleClick()
    pyautogui.doubleClick()
```

- [ ] **Step 8: Edit the button-position constants and `_green_button_ratio`**

Current:

```python
_BUTTON_GREEN_RGB = (27, 203, 37)  # florr.io确认类按钮统一用这个绿色底(开始/继续都是)
_START_BUTTON_POS = (1059, 527)     # 开局菜单"开始"按钮(还没进过局/或已经回到开局菜单)
_CONTINUE_BUTTON_POS = (959, 634)   # 死亡结算画面"继续"按钮(注意: 跟开局菜单是两个完全不同的界面!)


def _green_button_ratio(pos, half_w=15, half_h=10):
    """采样按钮周围一小块区域, 算绿色像素占比 —— 不能只采一个点.

    按钮上的文字/图标带黑色描边, 单点坐标很容易正好落在描边或图标上而不是纯色
    背景上, 只有采样一整块区域看绿色占比才稳. 实测按钮区域里文字+图标占比不小,
    纯绿色背景经常只剩10%~20%, 别把阈值定太高.
    """
    x, y = pos
    region = pyautogui.screenshot(region=[x - half_w, y - half_h, half_w * 2, half_h * 2])
    arr = np.array(region)[:, :, :3]
    match = np.all(np.abs(arr.astype(int) - np.array(_BUTTON_GREEN_RGB)) <= 25, axis=-1)
    return match.sum() / match.size
```

Replace with:

```python
_BUTTON_GREEN_RGB = (27, 203, 37)  # florr.io确认类按钮统一用这个绿色底(开始/继续都是)
_START_BUTTON_POS = scale_point(1059, 527)     # 开局菜单"开始"按钮(还没进过局/或已经回到开局菜单), 1920x1080下量出来的
_CONTINUE_BUTTON_POS = scale_point(959, 634)   # 死亡结算画面"继续"按钮(注意: 跟开局菜单是两个完全不同的界面!), 同样是1920x1080下量出来的


def _green_button_ratio(pos, half_w=15, half_h=10):
    """采样按钮周围一小块区域, 算绿色像素占比 —— 不能只采一个点.

    按钮上的文字/图标带黑色描边, 单点坐标很容易正好落在描边或图标上而不是纯色
    背景上, 只有采样一整块区域看绿色占比才稳. 实测按钮区域里文字+图标占比不小,
    纯绿色背景经常只剩10%~20%, 别把阈值定太高.

    half_w/half_h默认值是1920x1080下量出来的采样半径, 换算到实际分辨率(至少
    留1px, 否则超小分辨率下可能四舍五入成0导致采样区域是空的).
    """
    x, y = pos
    half_w = max(1, scale_x(half_w))
    half_h = max(1, scale_y(half_h))
    region = pyautogui.screenshot(region=[x - half_w, y - half_h, half_w * 2, half_h * 2])
    arr = np.array(region)[:, :, :3]
    match = np.all(np.abs(arr.astype(int) - np.array(_BUTTON_GREEN_RGB)) <= 25, axis=-1)
    return match.sum() / match.size
```

Note: `on_death_screen()` calls `_green_button_ratio(_CONTINUE_BUTTON_POS, half_w=_DEATH_SCREEN_SAMPLE_HALF_W, half_h=_DEATH_SCREEN_SAMPLE_HALF_H)` with `_DEATH_SCREEN_SAMPLE_HALF_W = 30`/`_DEATH_SCREEN_SAMPLE_HALF_H = 16` (both still 1920×1080-reference values) — leave those two constants and that call site alone; they get scaled automatically now that `_green_button_ratio` scales whatever `half_w`/`half_h` it's given.

- [ ] **Step 9: Edit `check_stage`**

Current:

```python
def check_stage():
    color = pyautogui.screenshot(region=[0, 0, 1920, 1080]).getpixel((316, 32))
    if color == (187, 85, 85):
        return "in_game"
    elif color == (255, 255, 255):
        return "in_game_dead"
    else:
        color = pyautogui.screenshot(
            region=[0, 0, 1920, 1080]).getpixel((156, 35))
        if color == (155, 181, 107):
            return "in_menu"
        else:
            return "unknown"
```

Replace with:

```python
def check_stage():
    full_screen = [0, 0, SCREEN_WIDTH, SCREEN_HEIGHT]
    color = pyautogui.screenshot(region=full_screen).getpixel(scale_point(316, 32))
    if color == (187, 85, 85):
        return "in_game"
    elif color == (255, 255, 255):
        return "in_game_dead"
    else:
        color = pyautogui.screenshot(region=full_screen).getpixel(scale_point(156, 35))
        if color == (155, 181, 107):
            return "in_menu"
        else:
            return "unknown"
```

- [ ] **Step 10: Run the full suite**

Run: `source venv/bin/activate && python3 -m pytest -q`
Expected: all tests pass (previous count + the 1 new `calc_anti_stuck` test).

- [ ] **Step 11: Commit**

```bash
git add utils.py test_utils.py
git commit -m "feat: scale utils.py screen-space functions to real resolution

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Fix `get_map()` to resize the scaled crop back to 300×300

**Files:**
- Modify: `utils.py`, function `get_map`
- Test: `test_utils.py`

**Interfaces:**
- Consumes: `scale_region` (Task 1)
- Produces: `get_map() -> np.ndarray` shape `(300, 300, 3)` — unchanged return contract from before this plan (every existing caller, e.g. `get_player_position`, already assumes exactly this shape); this task's job is to keep that contract true at any resolution.

This is the correctness-critical piece flagged in the spec: `maps/*.png` are fixed 300×300 templates, and `get_map()`'s captured region size changes with resolution once it goes through `scale_region`. Without resizing back down, `get_player_location_on_map`/`calibrate_player` silently receive coordinates in the wrong pixel space at any non-1080p resolution.

- [ ] **Step 1: Write the failing test**

Add to `test_utils.py` (new import at top: `from PIL import Image`):

```python
from PIL import Image


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source venv/bin/activate && python3 -m pytest test_utils.py::test_get_map_resizes_scaled_capture_back_to_300x300 test_utils.py::test_get_map_is_a_no_op_resize_at_reference_resolution -v`
Expected: FAIL on the 4K test — `assert captured["region"] == [3200, 40, 600, 600]` fails because the old code still hardcodes `region=[1600, 20, 300, 300]` regardless of `SCREEN_WIDTH`/`SCREEN_HEIGHT`.

- [ ] **Step 3: Edit `get_map`**

Current:

```python
def get_map():
    image = pyautogui.screenshot(region=[1600, 20, 1900-1600, 320-20])
    image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    return image
```

Replace with:

```python
def get_map():
    region = scale_region(1600, 20, 300, 300)
    image = pyautogui.screenshot(region=region)
    image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    if image.shape[:2] != (300, 300):
        # maps/anthell.png、maps/desert.png、maps/ocean.png都是固定300x300模板,
        # 寻路/玩家定位那一整条链路(get_player_location_on_map、calibrate_player、
        # lazy_theta_star)全部假设坐标就活在这个300x300像素空间里 —— 分辨率一变,
        # scale_region()算出来的截图区域尺寸就不再是300x300了(比如4K下大概是
        # 600x600), 截完必须resize回300x300, 不然玩家位置检测/寻路全错位.
        image = cv2.resize(image, (300, 300), interpolation=cv2.INTER_AREA)
    return image
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source venv/bin/activate && python3 -m pytest test_utils.py::test_get_map_resizes_scaled_capture_back_to_300x300 test_utils.py::test_get_map_is_a_no_op_resize_at_reference_resolution -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `source venv/bin/activate && python3 -m pytest -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add utils.py test_utils.py
git commit -m "fix: resize get_map()'s scaled capture back to the 300x300 template space

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Scale `main.py`'s mouse-steering target

**Files:**
- Modify: `main.py`, function `move_to_position` (currently around line 186–194)

**Interfaces:**
- Consumes: `SCREEN_WIDTH`, `SCREEN_HEIGHT`, `mouse_scale()`, `clamp_to_screen` — already available in `main.py` via its existing `from utils import *` (line 1); no new import needed. (`main.py` already calls bare `pyautogui.moveTo(...)` elsewhere the same way, relying on this same wildcard import for the `pyautogui` name — same pattern, not a new one. `mouse_scale` being a plain module-level function — not a PEP 562 lazy attribute — is exactly why `from utils import *` picks it up correctly; see the amendment note on `mouse_scale()` in Task 1.)
- No new interfaces produced.

No new automated test: `move_to_position` is a long, loop-driven, real-mouse/real-screenshot function with no existing unit test coverage (`test_main_smoke.py` only checks that `main` imports and exposes a few config constants) — matching that existing convention, this task is a mechanical edit verified by the full suite + a real-machine check later (see the spec's "Verification" section), not a new unit test invented just for this plan.

- [ ] **Step 1: Edit the mouse-steering block**

Current (`main.py`, inside `move_to_position`):

```python
        # 移动鼠标指向目标
        extend = max(min(dist * 45, 500), 50)
        if dist > 0:
            extend_x = extend * dx / dist
            extend_y = extend * dy / dist
        else:
            extend_x = extend_y = 0

        mouse_pos = (1920 // 2 + extend_x, 1080 // 2 + extend_y)
        pyautogui.moveTo(mouse_pos)
```

Replace with:

```python
        # 移动鼠标指向目标
        extend = max(min(dist * 45, 500), 50) * mouse_scale()
        if dist > 0:
            extend_x = extend * dx / dist
            extend_y = extend * dy / dist
        else:
            extend_x = extend_y = 0

        mouse_pos = clamp_to_screen(SCREEN_WIDTH // 2 + extend_x, SCREEN_HEIGHT // 2 + extend_y)
        pyautogui.moveTo(mouse_pos)
```

- [ ] **Step 2: Run the full suite**

Run: `source venv/bin/activate && python3 -m pytest -q`
Expected: all tests pass (this task adds none, so the count is unchanged from Task 3's end).

- [ ] **Step 3: Sanity-check the module still imports cleanly**

Run: `source venv/bin/activate && python3 -c "import main; print('ok')"`
Expected: prints `ok` (this is exactly what `test_main_smoke.py` already checks via pytest, but running it directly here gives a fast manual double-check since this task touches `main.py` directly).

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: scale main.py mouse-steering target to real resolution

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: Make `enemy_detect.py`'s screen constants resolution-aware

**Files:**
- Modify: `enemy_detect.py` — imports, `SCREEN_CENTER`, `scan_enemies`
- Modify: `test_enemy_detect.py` — make the tests that rely on the `SCREEN_CENTER` default explicit, so they stay deterministic regardless of the resolution of the machine running them

**Interfaces:**
- Consumes: `utils.SCREEN_WIDTH`, `utils.SCREEN_HEIGHT` (Task 1)
- `enemy_detect.SCREEN_CENTER` keeps its existing type/shape (`tuple[float, float]`) — every consumer (`aim_mouse_target`, `flee_mouse_target`, `select_action`, and `main.py`'s `mouse_target != enemy_detect.SCREEN_CENTER` check at `main.py:440`) is unaffected by this task, since they only ever compare against or default to whatever `SCREEN_CENTER` currently holds.

`enemy_detect.py` currently hardcodes its own independent copy of `(960, 540)` and `1920, 1080` rather than reusing `utils.py`'s — this task makes it derive from the same source of truth instead, so there's exactly one place resolution is detected.

Five existing tests in `test_enemy_detect.py` call `select_action(...)` without passing `center=` explicitly, so they currently rely on the module-level default `center=SCREEN_CENTER` evaluated once at import time. On this dev machine that default happens to equal `(960, 540)` today (this Mac's actual screen is 1920×1080), which is why those tests pass — but once `SCREEN_CENTER` is derived from the real screen size, that default would silently follow whatever resolution the test happens to run on, making those 5 tests non-hermetic. Fix them alongside this change by passing `center=(960, 540)` explicitly, matching the pattern every other test in this file (`aim_mouse_target`/`flee_mouse_target` tests) already uses.

- [ ] **Step 1: Edit `enemy_detect.py`'s imports and `SCREEN_CENTER`**

Current (top of file):

```python
import math

import cv2
import numpy as np
import pyautogui
from ultralytics import YOLO
```

Replace with:

```python
import math

import cv2
import numpy as np
import pyautogui
from ultralytics import YOLO

import utils
```

Current:

```python
SCREEN_CENTER = (960, 540)  # 屏幕中心, 同时也是"停止移动"的鼠标位置约定(见
                              # utils.keyup()) —— aim_mouse_target/flee_mouse_target
                              # 在"保持距离"/"没有明确方向"时都退回这个值, 调用方
                              # (main.py)靠跟这个常量比较来判断"这tick是不是故意停住"。
```

Replace with:

```python
SCREEN_CENTER = (utils.SCREEN_WIDTH / 2, utils.SCREEN_HEIGHT / 2)  # 屏幕中心, 同时也是
                              # "停止移动"的鼠标位置约定(见utils.keyup()) ——
                              # aim_mouse_target/flee_mouse_target在"保持距离"/
                              # "没有明确方向"时都退回这个值, 调用方(main.py)靠跟这个
                              # 常量比较来判断"这tick是不是故意停住"。跟utils.py共用
                              # 同一份SCREEN_WIDTH/SCREEN_HEIGHT, 不再自己独立写死一份.
```

- [ ] **Step 2: Edit `scan_enemies`' screenshot region**

Current:

```python
    if image is None:
        screenshot = pyautogui.screenshot(region=[0, 0, 1920, 1080])
        image = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
```

Replace with:

```python
    if image is None:
        screenshot = pyautogui.screenshot(region=[0, 0, utils.SCREEN_WIDTH, utils.SCREEN_HEIGHT])
        image = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
```

- [ ] **Step 3: Make the implicit-`SCREEN_CENTER` tests in `test_enemy_detect.py` explicit**

In `test_enemy_detect.py`, these 5 calls currently omit `center=` (relying on the module-default `SCREEN_CENTER`):

```python
    action, payload = select_action(detections, avoid_trigger_px=400)
```
(appears in `test_select_action_flees_when_avoid_mob_in_range`)

```python
    action, target, hold_px = select_action(detections, avoid_trigger_px=400)
```
(appears in `test_select_action_ignores_avoid_mob_outside_trigger_radius`)

```python
    action, target, hold_px = select_action(detections)
```
(appears in `test_select_action_chases_best_priority_candidate`)

```python
    action, target, hold_px = select_action(detections, cautious_hold_px=250)
```
(appears in `test_select_action_holds_distance_for_cautious_target`)

```python
    action, payload = select_action(detections, avoid_trigger_px=400)
```
(appears in `test_select_action_flee_excludes_out_of_range_avoid_mobs`)

Add `center=(960, 540)` to each of these 5 call sites, e.g. the first becomes:

```python
    action, payload = select_action(detections, avoid_trigger_px=400, center=(960, 540))
```

Note: `action, payload = select_action(detections, avoid_trigger_px=400)` (no `center=`) is the
exact line in both `test_select_action_flees_when_avoid_mob_in_range` and
`test_select_action_flee_excludes_out_of_range_avoid_mobs` — both occurrences need the identical
`center=(960, 540)` addition, so a single find-and-replace-all on that line is correct here (there
is no case where one of the two should be left unchanged).

(`test_select_action_wanders_with_no_relevant_detections` calls `select_action([])` with an empty detection list — `center` never gets used in that code path, so it does not need this change.)

- [ ] **Step 4: Run the full suite**

Run: `source venv/bin/activate && python3 -m pytest -q`
Expected: all tests pass — this confirms the 5 edited tests are still green now that they no longer depend on the (now-dynamic) `SCREEN_CENTER` default.

- [ ] **Step 5: Commit**

```bash
git add enemy_detect.py test_enemy_detect.py
git commit -m "feat: derive enemy_detect.py screen constants from utils.py's detected resolution

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## After all tasks: update the README

**Files:**
- Modify: `README.md`

The "Implements" section currently says: *"For this is CLIENT-SIDE, so I haven't do any resolution support. You need to run this code in 1920x1080 with florr.io tab on the top and fullscreen."* This is no longer accurate once Tasks 1–5 land.

- [ ] **Step 1: Edit the README**

Current:

```markdown
## Implements

For this is CLIENT-SIDE, so I haven't do any resolution support.

You need to run this code in 1920x1080 with florr.io tab on the top and fullscreen.
```

Replace with:

```markdown
## Implements

Resolution is auto-detected at startup (`pyautogui.size()`) and every screen coordinate is scaled
from its original 1920x1080 calibration — see
`docs/superpowers/specs/2026-08-26-resolution-adaptation-design.md` for how, and its "Known risk"
section for the one thing that isn't verified (non-16:9 windows, if florr.io turns out to letterbox
instead of stretching its UI — recalibrate the affected constant with `debug_screen_pos.py` if so).

You need to run this code with florr.io tab on the top and fullscreen (any resolution).
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README no longer requires exactly 1920x1080

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Out of band: real-machine verification

Not a task with automated steps — this repo's test suite cannot exercise real florr.io UI alignment
from this (Mac, dev-only) box. Per the spec's "Verification" section: before considering this done,
run on the actual Windows deployment machine at 1920×1080 (regression check) and at least one other
resolution actually available there, confirming menu/death-screen buttons still get clicked, the
minimap crop still lines up (player-position detection isn't jumping around), and a normal
`lazy_theta_pathing` call completes end to end. Report back whatever's found — if a button lands
wrong on a non-16:9 screen, that confirms the "Known risk" in the spec and the affected constant
needs re-measuring with `debug_screen_pos.py`, not a change to the scaling formula itself.
