# Zoom Gate for Rarity Detection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Before each farming round, auto scroll-wheel-zoom the game camera in until nearby mobs' HP bars measure a median thickness ≥ 4px, so `sample_rarity` can actually read the rarity tag.

**Architecture:** One pure helper in `enemy_detect.py` (`measure_hp_bar_thickness`) plus a thin screenshot-and-measure wrapper (`scan_bar_thickness`) parallel to `scan_enemies`. `main.py` gets `ensure_zoom_for_rarity(enemy_ai_enabled)` — a best-effort scroll loop with scroll/wait caps and scroll-direction self-correction — called once per round in `run_worker()` between `lazy_theta_pathing` and `auto_farming`.

**Tech Stack:** Python 3.11, pytest, numpy, `pyautogui` (already deps). `statistics` (stdlib). YOLO model `models/desert.pt`.

## Global Constraints

- Tests run with `./venv/bin/python -m pytest` (`venv/`, NOT `.venv/`).
- `_find_hp_bar(image, bbox)` returns `(bar_x0, bar_y, bar_x1, thick)` or `None`. `thick` is `bar[3]`.
- Detection dict shape from `scan_enemies`: `{"species","rarity","screen_pos","bbox","confidence"}`. `bbox` is `(x1,y1,x2,y2)` floats.
- `test_enemy_detect.py` already defines `_BG_BGR = (120, 170, 210)` (sand, non-green) and `_BAR_BGR = (48, 208, 112)` (HP-bar green that passes `_find_hp_bar`'s mask). Reuse them.
- Placeholder tuning constants — use verbatim, do not agonize: `ZOOM_MIN_THICK = 4`, `ZOOM_MIN_SAMPLES = 2`, `ZOOM_SCROLL_AMOUNT = 2`, `ZOOM_MAX_SCROLLS = 15`, `ZOOM_WAIT_CAP = 60`.
- `ensure_zoom_for_rarity` is **best-effort**: it must never raise out — wrap the body in `try/except Exception`, print one warning line, return `False`. It must never block the `run_worker` round loop.
- Chinese comments / print strings, matching surrounding style. English commit messages, Conventional Commits, ending with the trailer `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.
- Only `enemy_detect.py`, `test_enemy_detect.py`, `main.py`, `test_main_worker.py` change.

---

## Task 1: `measure_hp_bar_thickness` + `scan_bar_thickness` in `enemy_detect.py`

**Files:**
- Modify: `enemy_detect.py` — add `measure_hp_bar_thickness` right after `sample_rarity` (the function ending ~line 145, before the `SPECIES_RANK` block); add `scan_bar_thickness` right after `scan_enemies` (ending ~line 513).
- Test: `test_enemy_detect.py` — append near the other `_name_tag_image`-based tests and the `test_scan_enemies_returns_empty_list_for_blank_image` test.

**Interfaces:**
- Consumes: `_find_hp_bar(image, bbox) -> (x0,y,x1,thick)|None`, `load_enemy_model(path)`, `pyautogui`, `cv2`, `np`, `utils.SCREEN_WIDTH/SCREEN_HEIGHT` (all already imported in `enemy_detect.py`).
- Produces:
  - `measure_hp_bar_thickness(detections, image) -> list[int]` — for each detection, `_find_hp_bar(image, d["bbox"])`; collect `bar[3]` when not `None`; skip when `None`. Order follows `detections`. Pure, no I/O.
  - `scan_bar_thickness(image=None, conf=0.4, model_path="models/desert.pt") -> list[int]` — screenshot (or use `image`), run the model, build a minimal `[{"bbox": ...}]` list, return `measure_hp_bar_thickness(that_list, image)`.

- [ ] **Step 1: Write the failing tests**

Append to `test_enemy_detect.py`:

```python
from enemy_detect import measure_hp_bar_thickness, scan_bar_thickness


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


def test_scan_bar_thickness_empty_for_blank_image():
    blank = np.zeros((480, 640, 3), dtype=np.uint8)
    assert scan_bar_thickness(image=blank) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `./venv/bin/python -m pytest test_enemy_detect.py -k "measure_hp_bar_thickness or scan_bar_thickness" -v`
Expected: FAIL — `ImportError: cannot import name 'measure_hp_bar_thickness'`.

- [ ] **Step 3: Implement `measure_hp_bar_thickness`**

In `enemy_detect.py`, immediately after `sample_rarity`'s `return best_name` line and its blank line:

```python
def measure_hp_bar_thickness(detections, image):
    """每个检测框跑 _find_hp_bar, 收集找到的血条厚度 (第4个返回值), 跳过 None.
    顺序跟 detections 一致, 纯函数无 I/O. 给 ensure_zoom_for_rarity 判相机 zoom
    够不够 —— 实测血条 厚<4 时 sample_rarity 的稀有度词像素太少, 全读 Common."""
    out = []
    for d in detections:
        bar = _find_hp_bar(image, d["bbox"])
        if bar is not None:
            out.append(bar[3])
    return out
```

- [ ] **Step 4: Implement `scan_bar_thickness`**

In `enemy_detect.py`, immediately after `scan_enemies` returns (after its final `return detections` and blank line, before `_model = None` / `load_enemy_model` — actually those are ABOVE `scan_enemies`; put this at end of file after `scan_enemies`):

```python
def scan_bar_thickness(image=None, conf=0.4, model_path="models/desert.pt"):
    """截一帧 + YOLO + measure_hp_bar_thickness, 返回这一帧里能定位到的血条厚度
    列表. 跟 scan_enemies 平行, 但只关心血条粗细 (给 ensure_zoom_for_rarity 判
    zoom 够不够), 不算 screen_pos / rarity, 也就省掉每框一次 sample_rarity."""
    if image is None:
        screenshot = pyautogui.screenshot(region=[0, 0, utils.SCREEN_WIDTH, utils.SCREEN_HEIGHT])
        image = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

    model = load_enemy_model(model_path)
    results = model.predict(image, conf=conf, verbose=False)
    if not results:
        return []
    dets = [{"bbox": tuple(float(v) for v in box.xyxy[0])} for box in results[0].boxes]
    return measure_hp_bar_thickness(dets, image)
```

- [ ] **Step 5: Run to verify pass**

Run: `./venv/bin/python -m pytest test_enemy_detect.py -q`
Expected: PASS, no regressions (4 new tests + existing).

- [ ] **Step 6: Commit**

```bash
git add enemy_detect.py test_enemy_detect.py
git commit -m "feat: measure_hp_bar_thickness + scan_bar_thickness

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: `ensure_zoom_for_rarity` in `main.py` + wire into `run_worker`

**Files:**
- Modify: `main.py` — add `import statistics` to the import block (after `import threading`); add 5 `ZOOM_*` constants to the `# ===== 索敌配置 =====` block after `MYTHIC_STRAFE_K_RADIAL` and before the `# 以上数值...` comment; add `ensure_zoom_for_rarity` right after `_drive_and_check_stall` (ends ~line 445, before `def auto_farming`); wire one call into `run_worker` (~line 722-725).
- Test: `test_main_worker.py` — append after the `_update_mythic_latch` / `move_to_position` tests.

**Interfaces:**
- Consumes (from Task 1): `enemy_detect.scan_bar_thickness(model_path=...) -> list[int]`.
- Consumes (existing): `overlay.update(state=, message=)`, `afk_watch.poll_afk_pause() -> bool`, `pyautogui.moveTo(x, y)`, `pyautogui.scroll(clicks)`, `time.time()`, `time.sleep(s)`, `SCREEN_WIDTH`, `SCREEN_HEIGHT`, `ENEMY_MODEL_PATH`.
- Produces: `ensure_zoom_for_rarity(enemy_ai_enabled) -> bool`. Returns `True` only when the measured median bar thickness reached `ZOOM_MIN_THICK`; `False` on disabled / scroll cap / wait cap / exception. Caller ignores the value (round always proceeds).

- [ ] **Step 1: Write the failing tests**

Append to `test_main_worker.py`:

```python
def _stub_zoom_env(monkeypatch, thick_seq):
    """thick_seq: list of lists — successive scan_bar_thickness() return values
    (last entry repeats once exhausted). Returns a dict recording calls."""
    import types
    calls = {"scan": 0, "scroll": [], "sleep": 0.0}
    seq = list(thick_seq)

    def fake_scan(**k):
        i = min(calls["scan"], len(seq) - 1)
        calls["scan"] += 1
        return list(seq[i])

    monkeypatch.setattr(main.enemy_detect, "scan_bar_thickness", fake_scan)
    monkeypatch.setattr(main, "overlay",
                        types.SimpleNamespace(update=lambda **k: None), raising=False)
    monkeypatch.setattr(main.afk_watch, "poll_afk_pause", lambda: False)
    monkeypatch.setattr(main.pyautogui, "moveTo", lambda *a, **k: None)
    monkeypatch.setattr(main.pyautogui, "scroll", lambda amt, *a, **k: calls["scroll"].append(amt))
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
    assert len(calls["scroll"]) == main.ZOOM_MAX_SCROLLS


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
```

- [ ] **Step 2: Run to verify it fails**

Run: `./venv/bin/python -m pytest test_main_worker.py -k "ensure_zoom" -v`
Expected: FAIL — `AttributeError: module 'main' has no attribute 'ensure_zoom_for_rarity'` (and `ZOOM_*`).

- [ ] **Step 3: Add the import and constants**

In `main.py`, in the import block add after `import threading`:

```python
import statistics
```

In the `# ===== 索敌配置 =====` block, after the `MYTHIC_STRAFE_K_RADIAL = 0.8` line and before `# 以上数值是没实机测过的占位默认值`:

```python
ZOOM_MIN_THICK     = 4    # 血条中位厚度到这个像素数, sample_rarity 才稳 (实测)
ZOOM_MIN_SAMPLES   = 2    # 至少几条可测血条才据此判定 (少于就等 mob 出现)
ZOOM_SCROLL_AMOUNT = 2    # 每次滚轮往里推的量 (正=拉近; 方向不对循环里会自翻转)
ZOOM_MAX_SCROLLS   = 15   # 滚这么多次还没到就放弃 (可能已是最大 zoom)
ZOOM_WAIT_CAP      = 60   # 周围没 mob 时最多等这么多秒, 之后照常开刷
```

- [ ] **Step 4: Implement `ensure_zoom_for_rarity`**

In `main.py`, immediately after `_drive_and_check_stall` (its `return "moved"` line and blank line) and before `def auto_farming`:

```python
def ensure_zoom_for_rarity(enemy_ai_enabled):
    """开刷前把相机滚轮拉近到"血条中位厚度 >= ZOOM_MIN_THICK" —— 低于这个厚度
    sample_rarity 读不出稀有度词 (实测 厚<4 时 Mythic 名牌就几个青像素, 全读
    Common, Mythic 锁定永不触发). best-effort:
      - enemy_ai_enabled=False → 直接返回 False (zoom 只影响稀有度, 索敌关了不用管)
      - 够不到 ZOOM_MIN_SAMPLES 条可测血条 (周围没 mob) → 不滚, 等, 最多 ZOOM_WAIT_CAP 秒
      - 滚 ZOOM_MAX_SCROLLS 次仍没到 (可能已最大 zoom) → 放弃
      - 滚一下中位厚度反而变小 → 方向反了, 翻转 ZOOM_SCROLL_AMOUNT 符号
      - 任何异常 → 打一行警告返回 False
    返回是否达到目标厚度; 调用方 (run_worker) 只打日志, 不管返回值都照常开刷."""
    if not enemy_ai_enabled:
        return False
    overlay.update(state="调整视角", message="拉近相机以便读稀有度...")
    scroll_amount = ZOOM_SCROLL_AMOUNT
    scroll_count = 0
    prev_median = None
    start = time.time()
    try:
        while True:
            if afk_watch.poll_afk_pause():
                overlay.update(state="AFK弹窗处理中", message="等待florr-auto-afk解题")
                time.sleep(0.2)
                continue

            thicks = enemy_detect.scan_bar_thickness(model_path=ENEMY_MODEL_PATH)

            if len(thicks) < ZOOM_MIN_SAMPLES:
                if time.time() - start >= ZOOM_WAIT_CAP:
                    print("⚠️ 视角调整: 周围一直没有可测的怪, 照常开刷")
                    return False
                time.sleep(2)
                continue

            median = statistics.median(thicks)
            if median >= ZOOM_MIN_THICK:
                print(f"✅ 视角OK (血条中位厚度 {median})")
                return True

            if prev_median is not None and median < prev_median - 0.5:
                scroll_amount = -scroll_amount
                print("↔️ 视角: 滚轮方向反了, 已翻转")
            prev_median = median

            if scroll_count >= ZOOM_MAX_SCROLLS:
                print(f"⚠️ 视角调整: 滚了 {scroll_count} 次仍没到目标厚度 "
                      f"(可能已最大 zoom), 照常开刷")
                return False

            pyautogui.moveTo(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)
            pyautogui.scroll(scroll_amount)
            scroll_count += 1
            time.sleep(0.4)
    except Exception as e:
        print(f"⚠️ 视角调整出错, 照常开刷: {e}")
        return False
```

- [ ] **Step 5: Wire into `run_worker`**

Find (~line 722):

```python
        if lazy_theta_pathing(location, [farming_area]):
            print("✅ 到达刷怪区域！")
            auto_farming(farming_area, farming_duration,
                         enemy_ai_enabled=w["enemy_ai_enabled"])
```

Replace with:

```python
        if lazy_theta_pathing(location, [farming_area]):
            print("✅ 到达刷怪区域！")
            ensure_zoom_for_rarity(w["enemy_ai_enabled"])
            auto_farming(farming_area, farming_duration,
                         enemy_ai_enabled=w["enemy_ai_enabled"])
```

- [ ] **Step 6: Run the tests**

Run: `./venv/bin/python -m pytest test_main_worker.py -k "ensure_zoom" -v`
Expected: PASS (7 tests).

Run: `./venv/bin/python -m py_compile main.py enemy_detect.py`
Run: `./venv/bin/python -m pytest -q`
Expected: PASS — whole suite, no regressions.

- [ ] **Step 7: Commit + push**

```bash
git add main.py test_main_worker.py
git commit -m "feat: ensure_zoom_for_rarity — auto-zoom the camera before farming

Before each round (when enemy AI is on), scroll-wheel zoom in until the
median HP-bar thickness of nearby mobs is >= 4px, the point below which
sample_rarity cannot read the rarity tag. Waits when no mobs are
measurable; self-corrects a wrong scroll sign; best-effort with a
15-scroll / 60s cap, always falls through to auto_farming.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
git push origin main
```

---

## Self-Review

**1. Spec coverage:**

| Spec item | Task |
|---|---|
| `measure_hp_bar_thickness(detections, image) -> list[int]`, pure, skips `None`, order-preserving | Task 1 |
| Per-round, after `lazy_theta_pathing` success, before `auto_farming` | Task 2 Step 5 |
| Skipped (returns `False` at once) when `enemy_ai_enabled` false | Task 2 (`if not enemy_ai_enabled: return False`) + `test_ensure_zoom_disabled_returns_immediately` |
| Overlay state `"调整视角"` | Task 2 impl |
| AFK pause → `sleep(0.2)`, `continue` | Task 2 impl |
| `< ZOOM_MIN_SAMPLES` thicknesses → don't scroll, `sleep(2)`, wait-cap `ZOOM_WAIT_CAP` | Task 2 impl + `test_ensure_zoom_waits_for_mobs_then_succeeds` / `test_ensure_zoom_wait_cap` |
| median `>= ZOOM_MIN_THICK` → success | Task 2 impl + `test_ensure_zoom_reaches_target` / `test_ensure_zoom_already_ok_no_scroll` |
| else zoom in: `moveTo(center)` + `scroll(amount)` + `sleep(0.4)` | Task 2 impl |
| direction self-correction on `median < prev_median - 0.5` | Task 2 impl + `test_ensure_zoom_flips_scroll_direction` |
| `scroll_count >= ZOOM_MAX_SCROLLS` → give up | Task 2 impl + `test_ensure_zoom_scroll_cap` |
| best-effort: `try/except Exception` → warn + `return False`, never raises | Task 2 impl |
| one screenshot feeds both boxes and thickness | Task 1: `scan_bar_thickness` measures on the same frame it detects on |
| 5 `ZOOM_*` constants in the tuning block | Task 2 Step 3 |
| tuning-knob note (placeholder defaults, scroll sign a guess) | carried by the spec; constants' comments in Task 2 Step 3 mirror it |

No gaps.

**2. Placeholder scan:** No "TBD"/"handle errors"/"similar to Task N". Every step has real code. Error handling is the explicit `try/except` block. Edge cases (empty detections, no bar, scroll cap, wait cap, wrong direction) each have a named test with concrete inputs.

**3. Type consistency:**
- `measure_hp_bar_thickness(detections, image) -> list[int]` — same signature in Task 1 interface, impl, and every test; consumed by `scan_bar_thickness` (Task 1) with a `[{"bbox": ...}]` list, matching the `d["bbox"]` access.
- `scan_bar_thickness(image=None, conf=0.4, model_path=...) -> list[int]` — same in Task 1 impl/test and Task 2's `enemy_detect.scan_bar_thickness(model_path=ENEMY_MODEL_PATH)` call.
- `ensure_zoom_for_rarity(enemy_ai_enabled) -> bool` — same in Task 2 impl, all 7 tests, and the `run_worker` call site `ensure_zoom_for_rarity(w["enemy_ai_enabled"])`.
- Constant names `ZOOM_MIN_THICK / ZOOM_MIN_SAMPLES / ZOOM_SCROLL_AMOUNT / ZOOM_MAX_SCROLLS / ZOOM_WAIT_CAP` — identical in Step 3, the impl, and the tests (`main.ZOOM_MAX_SCROLLS`, `main.ZOOM_WAIT_CAP`, `main.ZOOM_SCROLL_AMOUNT`).

No inconsistencies.
