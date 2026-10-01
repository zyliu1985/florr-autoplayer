# Resolution adaptation — design

## Problem

Every screen-space coordinate in this project is a literal pixel number measured against a
1920×1080 fullscreen browser window: full-screen screenshot regions, the mouse-steering center,
the minimap crop region, and several UI-button/pixel-signature positions used for menu/death-screen
detection. README says so explicitly: "You need to run this code in 1920×1080 with florr.io tab on
the top and fullscreen." Running at any other resolution silently misaligns every one of these —
screenshots crop the wrong area, clicks land on the wrong pixel, mouse-steering aims off-center.

## Goal

Make the bot work correctly at any fullscreen browser resolution/aspect ratio, not just 1920×1080,
by detecting the real screen size once at startup and deriving every currently-hardcoded coordinate
from it instead of using the literal 1920/1080/960/540 numbers.

## Non-goals

- **Not validating florr.io's actual UI scaling behavior.** There is no way to launch real
  florr.io from this (Mac, dev/test-only per project convention — see `windows-is-real-deployment`
  memory) box. The scaling formula below is the standard assumption for a browser game's UI, not an
  observed fact. See "Known risk" below and the re-calibration path for what happens if it's wrong.
- **No multi-monitor or non-fullscreen-window support.** Matches the existing design's assumption
  (OS screen resolution == browser canvas resolution) — this spec removes the "must be exactly
  1920×1080" constraint, not the "must be fullscreen, one browser window" constraint.
- **No changes to the YOLO model or its accuracy at different resolutions.** `models/desert.pt`
  detection quality is out of scope; `main.py`'s existing comment already flags its trigger-radius
  constants as unvalidated placeholder values.
- **No manual resolution config.** Auto-detected via `pyautogui.size()` (user's explicit choice —
  zero-config, and the existing Windows DPI-awareness call already makes this return real physical
  pixels).

## Core approach: independent-axis linear scaling

At import time, `utils.py` reads the real screen size once:

```python
SCREEN_WIDTH, SCREEN_HEIGHT = pyautogui.size()
_REF_WIDTH, _REF_HEIGHT = 1920, 1080  # the resolution every hardcoded constant below was measured against
```

Every hardcoded coordinate becomes a scaled version of its original 1920×1080 measurement:

```python
def scale_x(x): return round(x * SCREEN_WIDTH / _REF_WIDTH)
def scale_y(y): return round(y * SCREEN_HEIGHT / _REF_HEIGHT)
def scale_point(x, y): return (scale_x(x), scale_y(y))
```

`scale_x`/`scale_y` are independent — `scale_x` never touches height and vice versa. This is what
makes the same formula handle both same-aspect-ratio resolutions (2560×1440, 3840×2160, 1366×768:
`scale_x == scale_y`, pure uniform scale) and different-aspect-ratio resolutions (ultrawide,
portrait: `scale_x != scale_y`, independent stretch) without a separate letterbox/pillarbox code
path.

**Known risk:** this assumes florr.io's DOM/canvas UI stretches to fill the viewport
independently per axis (e.g. percentage-based CSS positioning) — the common pattern for browser
games. If florr.io instead keeps a fixed 16:9 internal aspect and letterboxes non-16:9 windows,
button positions computed this way will be off on non-16:9 screens (same-aspect resolutions are
unaffected either way, since `scale_x == scale_y` there regardless of which model is correct).
There's no way to verify which model florr.io actually uses without a live game. If a
non-16:9 test on the real (Windows) machine shows buttons landing wrong, re-measure the affected
button with `debug_screen_pos.py` (already resolution-agnostic — it just marks wherever the mouse
currently is on a full screenshot, no hardcoded coordinates) and either replace that constant with
a direct 1-off measurement or report the offset so the scaling model can be revisited.

## Critical correctness point: `get_map()` must resize back to 300×300

`maps/anthell.png`, `maps/desert.png`, `maps/ocean.png` are fixed 300×300-pixel template images.
`get_player_location_on_map`, `calibrate_player`, `lazy_theta_star`, and every map-space position
returned by `map_select.py`/`area_select.py` all assume positions are coordinates *in that exact
300×300 pixel space*. `get_map()` currently crops the literal screen region
`[1600, 20, 1900-1600, 320-20]` (= 300×300 at 1920×1080) — at any other resolution the *scaled*
crop region will not be 300×300 pixels (e.g. roughly 600×600 at 4K), which would silently corrupt
every downstream map-space coordinate if left as-is.

Fix: after capturing the scaled region, `cv2.resize` it back down (or up) to exactly `(300, 300)`
before converting to the OpenCV image used by the rest of the pipeline. This keeps every existing
map-space consumer working unmodified — they never see a resolution-dependent size.

**Non-16:9 update (real-machine evidence, supersedes the original "Non-16:9 note" below):**
`get_map()` originally computed its capture region via `scale_region` (independent per-axis
scaling), on the unverified assumption that florr.io stretches the minimap widget the same way
it stretches everything else. **Real-machine testing at 1024×768 (2026-08-26) disproved this.**

Evidence gathered: `debug_position_diag.py` (color-matches `get_map()`'s captured/resized image
against the player marker's `f8de60` color, same tolerance as `get_player_location_on_map`) found
**zero** matching pixels — `get_map()` wasn't even capturing the minimap's own black border, only
a zoomed-in fragment of its interior. `debug_screen_pos.py` was then used to click the minimap
widget's real on-screen top-left corner (measured: `(799, 20)`), and comparing the raw capture's
edge brightness against `maps/desert.png`'s clean near-black border confirmed the crop was too
small and offset. Automated contour detection on the full screenshot found the widget's true
bounding box: top-left ≈ `(797, 20)`, bottom-right ≈ `(1009, 227)` — a ~212×207px square.

`scale_region(1600, 20, 300, 300)` at 1024×768 predicts `[853, 14, 160, 214]` — a non-square,
too-narrow, mis-positioned rectangle. The real widget instead: **stays square, scales uniformly
by `SCREEN_HEIGHT / 1080` (not independently per axis), and is anchored to the top-right corner
with margins that scale by that same factor.** Verified against all four measured edges: predicted
right edge `1024 - round(20×768/1080) = 1010` vs measured `≈1009`; predicted bottom edge
`round(20×768/1080) + round(300×768/1080) = 227` vs measured `≈227`; predicted left edge `797` vs
measured `797` (exact); top edge had the largest error (predicted `14` vs measured `~20`, likely
manual-click imprecision on a thin border) but was still far closer than the old formula.

`get_map()` now computes its region directly from this formula instead of `scale_region`. At the
1920×1080 reference resolution it reduces to the original `[1600, 20, 300, 300]` (no regression),
and at any 16:9 resolution it's identical to what `scale_region` would have produced anyway (since
`scale_x == scale_y` there) — the fix only changes behavior at non-16:9 resolutions, which is
exactly where it was wrong. See `test_get_map_uses_height_based_uniform_scale_for_non_16_9` in
`test_utils.py`.

**Residual risk:** this formula is validated at exactly one non-16:9 resolution (1024×768). It's
plausible but *not yet verified* at other non-16:9 shapes (ultrawide, portrait) or that every
other UI element in this spec (buttons, `check_stage`'s pixel-signature points) follows the same
height-uniform-scale rule rather than the original independent-axis assumption — those still carry
the original "Known risk" below unless/until similarly measured. Do not assume this fix generalizes
past `get_map()` without equivalent evidence.

<details>
<summary>Original speculative note (kept for history, no longer the operative model for <code>get_map()</code>)</summary>

`scale_region` scales width and height by independent factors, so at a non-16:9 resolution the
*captured* region before the 300×300 resize was a non-square rectangle (e.g. ~400×300 at
2560×1080). Forcing that back to a 300×300 square would only be geometrically correct if florr.io
stretched every UI element — including the minimap widget itself — independently per axis. If the
minimap widget instead held a fixed square aspect regardless of window shape, that resize would
compress/stretch real minimap content and corrupt player-position detection. This was raised
during implementation and the user initially chose to ship as-is rather than guess between two
equally-unverified models — the real-machine test above replaced that guess with evidence, and
confirmed the second model (fixed/uniform scaling, not independent-axis stretch) was the correct
one, at least for this widget at this resolution.

</details>

## Changes by file

**`utils.py`**
- Add `SCREEN_WIDTH`/`SCREEN_HEIGHT` (detected once, module level) and `scale_x`/`scale_y`/
  `scale_point`/`scale_region` helpers.
- `calc_anti_stuck`: `screen_center` and the `np.clip` bounds become `(SCREEN_WIDTH/2,
  SCREEN_HEIGHT/2)` / `SCREEN_WIDTH`,`SCREEN_HEIGHT`.
- `execute_anti_stuck`: screenshot region becomes `[0, 0, SCREEN_WIDTH, SCREEN_HEIGHT]`;
  `screen_center` matches `calc_anti_stuck`.
- `keydown`/`keyup`: center becomes `(SCREEN_WIDTH/2, SCREEN_HEIGHT/2)`; the `delta=500` steering
  offset scales by `min(SCREEN_WIDTH/1920, SCREEN_HEIGHT/1080)` and the resulting mouse position is
  clamped inside screen bounds (small margin) so `pyautogui.moveTo` can't be asked to move off-screen
  on a smaller display.
- `abandon_game`, `_START_BUTTON_POS`, `_CONTINUE_BUTTON_POS`: computed via `scale_point(...)` of
  their original 1920×1080 measurements.
- `_green_button_ratio`: `half_w`/`half_h` sample-box size scaled the same way as any other
  distance.
- `check_stage`: screenshot region becomes full `SCREEN_WIDTH`×`SCREEN_HEIGHT`; the two pixel-
  signature points (`(316,32)`, `(156,35)`) go through `scale_point`.
- `get_map`: crop region computed via `scale_point`/`scale_region` of `(1600,20)`–`(1900,320)`,
  then `cv2.resize(...,(300,300))` as described above.

**`main.py`**
- The mouse-steering target (`mouse_pos = (1920 // 2 + extend_x, 1080 // 2 + extend_y)`) uses
  `utils.SCREEN_WIDTH/2`, `utils.SCREEN_HEIGHT/2` instead of the literals. The `extend` clamp
  (`max(min(dist * 45, 500), 50)`) scales by the same `min(scale_x, scale_y)` factor used in
  `keydown`, for the same off-screen-`moveTo` safety reason.

**`enemy_detect.py`**
- `SCREEN_CENTER` and the full-screen screenshot region in `scan_enemies` are no longer a second,
  independently-hardcoded `(960,540)`/`1920×1080` — both are derived from `utils.SCREEN_WIDTH`/
  `SCREEN_HEIGHT` so there is one source of truth for screen size.

## Testing

- New unit tests for `scale_x`/`scale_y`/`scale_point`/`scale_region` pure math, monkeypatching
  `utils.SCREEN_WIDTH`/`SCREEN_HEIGHT` to a few resolutions including a non-16:9 one — verifies the
  arithmetic, not florr.io's actual UI (can't be tested without a live game; see "Known risk").
  Existing tests (`if_in_area`, `_ensure_grayscale_2d`, `_pick_server_id`) are untouched by this
  change and stay green.

## Verification (real machine, out of band)

Not exercisable from this (Mac, dev-only) box. Before calling this done, run on the actual Windows
deployment machine at least at 1920×1080 (regression check) and one other resolution the user
actually has available, confirming: menu/death-screen buttons get clicked correctly, the minimap
crop still lines up (player-position detection isn't jumping around), and pathing completes a
normal `lazy_theta_pathing` call end to end.
