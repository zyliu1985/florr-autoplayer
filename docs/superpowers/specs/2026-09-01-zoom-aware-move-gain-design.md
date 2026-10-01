# Zoom-aware move gain + area leash — design

## Problem

The zoom gate (`ensure_zoom_for_rarity`, 2026-09-01) zooms the game camera in
before each round so `sample_rarity` can read rarity tags. Live logs: on rounds
where the zoom **succeeded** (`✅ 视角OK (血条中位厚度 4.0)`) the bot walked out of
the farming area within ~3 wander legs (`离开刷怪区域 (当前: (26, 126))` — 60+
minimap units past a boundary at y=63); on the one round where the zoom **failed**
(`视角调整: 超时`) the bot farmed normally, 19 legs.

`move_to_position` aims the mouse at `extend = max(min(dist * 45, 500), 50) *
mouse_scale()` px from screen centre, where `dist` is minimap distance to target.
The `* 45` gain (minimap-units → mouse-px) was tuned at the zoomed-**out** camera.
Zoomed in, florr's cursor-distance → player-speed control is more sensitive, so the
same `extend` overshoots the target; `move_to_position`'s overshoot guard
(`dist > last_dist + progress_epsilon` → "算到达") returns early after the player
has already flown well past, and a few of those compound into a big excursion. The
outer-loop area check only runs *between* legs, so a single leg can carry the
player 60 units out before anything reacts.

Camera zoom is one global knob: rarity wants it high, pathing wants it low. The fix
is to make pathing zoom-aware, not to stop zooming.

## Scope

In scope:
- `ensure_zoom_for_rarity` reports the zoom level it achieved (median HP-bar
  thickness) so the farm loop can compensate.
- `auto_farming` scales `move_to_position`'s gain by that zoom level for its
  **wander** legs only.
- A mid-leg area leash: the wander `on_tick` hook aborts the leg the moment the
  player leaves the farming area, so an overshoot can't run 60 units before the
  repath.

Not in scope:
- `execute_path` / `lazy_theta_pathing` travel legs — they run at whatever zoom the
  client happens to be at (usually the tuned zoom-out, since the zoom gate runs
  *after* travel). They keep the default gain.
- Retuning the `500` / `50` `extend` clamps or `mouse_scale()`.
- Any change to how zoom is set, or to `sample_rarity`.
- Making the zoom↔gain relationship exact. `gain ∝ 1 / zoom_thickness` is a
  first-order model; it self-adjusts to whatever the gate lands on and is a tuning
  knob if the constant is off.

## Behaviour

### `ensure_zoom_for_rarity(enemy_ai_enabled) -> float | None`

Return type changes from `bool`:
- **Success** (`median >= ZOOM_MIN_THICK`) → return `median` (the achieved median
  HP-bar thickness, a float ≥ 4).
- **Every give-up path** (`enemy_ai_enabled` false / wait-cap / scroll-cap /
  both-directions-regress / caught exception) → return `None`.

Nothing else in the function changes (the scroll loop, restores, mouse re-centre,
`try/except` all stay).

### `run_worker` call site

```python
        zoom_thick = ensure_zoom_for_rarity(w["enemy_ai_enabled"])
        if w["enemy_ai_enabled"] and zoom_thick is None:
            print("⚠️ 视角未调到位, 本轮稀有度识别可能不准 (Mythic 锁定可能不触发)")
        auto_farming(farming_area, farming_duration,
                     enemy_ai_enabled=w["enemy_ai_enabled"], zoom_thick=zoom_thick)
```

### `_move_gain_for_zoom(zoom_thick) -> float` — pure helper (main.py)

```python
def _move_gain_for_zoom(zoom_thick):
    """wander 腿的转向增益: 相机拉得越近 (血条越厚), 同样鼠标偏移玩家动得越多,
    增益要按比例调小. zoom_thick=None (没测到 / 没缩放) → 用基准 MOVE_EXTEND_GAIN.
    模型: gain ∝ 1 / 厚度, 以 ZOOM_BASELINE_THICK (基准增益调校时的厚度) 归一.
    夹一个下限, 免得某次厚度读特别大把移动缩到几乎不动."""
    if zoom_thick is None or zoom_thick <= 0:
        return MOVE_EXTEND_GAIN
    scaled = MOVE_EXTEND_GAIN * ZOOM_BASELINE_THICK / zoom_thick
    return max(scaled, MOVE_EXTEND_GAIN * 0.3)
```

`MOVE_EXTEND_GAIN = 45`, `ZOOM_BASELINE_THICK = 2`. `zoom_thick = 4` → `45 * 2/4 =
22.5`. `zoom_thick = 20` → `45 * 2/20 = 4.5`, floored to `13.5`.

### `move_to_position(..., extend_gain=None)`

New keyword-only-ish trailing param (positional-compatible with all existing
calls). Inside: `gain = extend_gain if extend_gain is not None else
MOVE_EXTEND_GAIN`, and `extend = max(min(dist * gain, 500), 50) * mouse_scale()`.
`extend_gain=None` (every current caller: `execute_path`, `lazy_theta_pathing`,
and the anti-stuck path) → `MOVE_EXTEND_GAIN` = 45 = **behaviour unchanged**.

The literal `45` in `move_to_position` becomes `MOVE_EXTEND_GAIN`.

### `auto_farming(farming_area, duration=300, *, enemy_ai_enabled=True, zoom_thick=None)`

- Compute once, before the loop: `move_gain = _move_gain_for_zoom(zoom_thick)`.
  Log it: `print(f"🎯 wander 转向增益: {move_gain:.1f} (zoom 厚度 {zoom_thick})")`.
- The **wander** branch's `move_to_position(...)` call passes `extend_gain=move_gain`.
  No other `move_to_position` call in the file gains the param.

### Area leash in `_wander_enemy_watch`

Rename its ignored `_pos` param to `pos` and use it. Prepend, before the enemy
scan:

```python
        if not if_in_area([farming_area], pos):
            return "out_of_area"
```

`move_to_position` already returns whatever truthy value `on_tick` yields (from the
`on_tick` feature), so `"out_of_area"` propagates out as `move_result`.

Wander branch: change `if move_result == "enemy":` to
`if move_result in ("enemy", "out_of_area"):` — same handling (a bare `continue`;
the outer loop's own `if not if_in_area(...)` check then repaths, now from ~1 unit
out instead of ~60). The `print` in that block gets a line noting which.

`farming_area` and `if_in_area` are both in `_wander_enemy_watch`'s enclosing scope
already (`farming_area` is the normalised `auto_farming` param; `if_in_area` comes
from `from utils import *`).

## Architecture

### `main.py`

- Tuning block (`# ===== 索敌配置 =====`, or a new `# ===== 移动 =====` line — put
  it right after the `ZOOM_*` block): add
  ```
  MOVE_EXTEND_GAIN   = 45   # move_to_position 的 minimap距离→鼠标px 增益 (基准, 没缩放相机)
  ZOOM_BASELINE_THICK = 2   # MOVE_EXTEND_GAIN 是在血条厚度≈这个值时调的; 拉近后按比例缩
  ```
- `_move_gain_for_zoom(zoom_thick) -> float` — new pure helper, next to
  `_update_mythic_latch` / `_drive_and_check_stall`.
- `move_to_position` — new `extend_gain=None` trailing param; `45` → `MOVE_EXTEND_GAIN`
  / `gain`.
- `ensure_zoom_for_rarity` — return `median` on success, `None` on every give-up.
- `auto_farming` — new `zoom_thick=None` keyword-only param; `move_gain =
  _move_gain_for_zoom(zoom_thick)` before the loop; wander `move_to_position` call
  passes `extend_gain=move_gain`.
- `_wander_enemy_watch` — `_pos` → `pos`; area-leash check returns `"out_of_area"`.
- Wander branch — `move_result in ("enemy", "out_of_area")`.
- `run_worker` — `zoom_ok` → `zoom_thick`; warn on `is None`; pass through to
  `auto_farming`.

### No other files change.

## Error handling

- `_move_gain_for_zoom(None)` and non-positive input → base gain (no divide-by-zero,
  no absurd scaling).
- `ensure_zoom_for_rarity` still swallows all exceptions → `None` → base gain, no
  leash regression (the leash is independent of zoom).
- The area leash is best-effort: if `if_in_area` somehow raised it would propagate
  out of `_wander_enemy_watch` into `move_to_position`'s tick — but `if_in_area` is
  pure arithmetic on the passed tuple, and the outer loop already calls it every
  iteration, so this adds no new failure mode.

## Testing

`test_main_worker.py` (monkeypatch style already established):

- `_move_gain_for_zoom`:
  - `None` → `MOVE_EXTEND_GAIN`.
  - `0` / negative → `MOVE_EXTEND_GAIN`.
  - `ZOOM_BASELINE_THICK` (2) → `MOVE_EXTEND_GAIN` (scale = 1).
  - `4` → `MOVE_EXTEND_GAIN * 0.5` (22.5).
  - `100` → floored at `MOVE_EXTEND_GAIN * 0.3`.
- `move_to_position` gain wiring: with the standard env stub (patch
  `get_player_position` constant, `pyautogui.moveTo` recording, `time.sleep`,
  `overlay`, `afk`, `on_death/start_screen`), call once with `extend_gain=10` and
  once with `extend_gain=40` toward the same far target from the same start;
  assert the recorded `moveTo` offset from centre is 4× larger in the second case
  (until the `500` clamp — pick a `dist` small enough that `dist*40 < 500`).
  Also `extend_gain=None` → same offset as `extend_gain=MOVE_EXTEND_GAIN`.
- `_wander_enemy_watch` area leash: hard to unit-test the closure directly. Extract
  the check into a trivial pure helper `_leaving_area(pos, farming_area) -> bool`
  (`return not if_in_area([farming_area], pos)`) and unit-test that: point inside
  → `False`, point outside → `True`. The closure calls the helper.
- `ensure_zoom_for_rarity` return-type change: update the existing `test_ensure_zoom_*`
  — `is True` → `== 4.0` (success returns the median, and the stubs feed `[4, 4]`
  so median is `4.0`); `is False` → `is None` (disabled / wait-cap / scroll-cap /
  both-regress). `test_ensure_zoom_reaches_target` / `_already_ok_no_scroll` /
  `_success_does_not_restore` assert the float; the give-up tests assert `None`.
- `run_worker` warn condition: the existing `run_worker` smoke test (if any covers
  this line) — the warn now fires on `zoom_thick is None`; update its stub of
  `ensure_zoom_for_rarity` to return `None` / a float accordingly. If no test
  covers it, add a 1-liner asserting `auto_farming` is called with a `zoom_thick`
  kwarg (via `inspect.signature` like `test_auto_farming_accepts_enemy_ai_enabled_kwarg`).

`test_enemy_detect.py`: unaffected (no enemy_detect change).

## Tuning knobs (need live calibration)

`MOVE_EXTEND_GAIN` (45 — unchanged, the pre-zoom baseline), `ZOOM_BASELINE_THICK`
(2 — the thickness that baseline was tuned at; if the real relationship isn't
`1/thick` the observed overshoot at `视角OK` rounds will say which way to nudge
it), and the `0.3` gain floor. All in the main.py tuning block.
