# Zoom gate for rarity detection — design

## Problem

`sample_rarity()` only reads a mob's rarity correctly when the game camera is
zoomed in far enough that the mob's HP bar renders ≥ ~4 px thick. Live `--watch`
on the real client: at bar thickness 2 (zoomed out) a real Mythic tag word gives
only 2–8 matching pixels — below the ratio floor — so every mob reads `Common`,
the Mythic latch never fires, and `select_action` picks a sandstorm instead of
the point-blank青怪. At thickness ≥ 4 the same mob reads `-> Mythic  Mythic=120+`.

The camera zoom is a manual scroll-wheel setting. The bot runs unattended, so it
must set the zoom itself before each farming round rather than relying on the user
to have zoomed in.

## Scope

In scope:
- A per-round, pre-farming step that scroll-wheel-zooms the camera in until nearby
  mobs' HP bars measure thick enough for rarity detection, then hands off to
  `auto_farming()`.
- A small reusable helper that measures HP-bar thickness across a detection list.

Not in scope:
- Zoom-out (the failure mode is always "too far out"; we only ever zoom in).
- Any change to `sample_rarity` / the rarity math itself — this is a camera-setup
  step, not a detection fix.
- Running when `enemy_ai_enabled` is false — zoom only matters for rarity, which
  only the enemy layer consumes.
- The pre-existing sandstorm red-contamination and sand_centipede no-bar issues.

## Behaviour

### `ensure_zoom_for_rarity()` — the scroll loop

Signature `ensure_zoom_for_rarity(enemy_ai_enabled) -> bool` (the flag is passed
in, matching `_maybe_scan_enemies`'s style; `run_worker` already holds it as
`w["enemy_ai_enabled"]`). Called once per round in `run_worker()`, immediately
after `lazy_theta_pathing()` reports the player reached the farming area and
**before** `auto_farming()`. Returns `False` at once (no scan, no scroll) when
`enemy_ai_enabled` is false. Overlay state `"调整视角"` while it runs. Best-effort: any exception is caught, logged as one
warning line, and the round proceeds to `auto_farming()` anyway.

On entry (once, before the loop): `pyautogui.moveTo(SCREEN_WIDTH // 2,
SCREEN_HEIGHT // 2)`. florr steers the character by mouse position and the pathing
step leaves the mouse parked off-centre, so without this re-centre the character
keeps walking during the wait / AFK / scroll-settle branches.

Per iteration:

1. **Wait-cap check at the top of the loop:** if total elapsed ≥ `ZOOM_WAIT_CAP`
   (60 s) → restore the zoom (`cdp_bridge.scroll_wheel(-applied)` if anything was rolled)
   and return. Placed first so the AFK branch's `continue` can't bypass it.
2. If `afk_watch.poll_afk_pause()` → `sleep(0.2)`, `continue` (don't count the
   iteration). Same pattern as `move_to_position` / `lazy_theta_pathing`.
3. `thicks = enemy_detect.scan_bar_thickness(model_path=ENEMY_MODEL_PATH)` — a
   dedicated wrapper parallel to `scan_enemies`: it takes its own screenshot
   (`pyautogui.screenshot` → BGR), runs YOLO, then `measure_hp_bar_thickness` over
   the boxes (one `_find_hp_bar` per box, thickness only). It deliberately skips
   the per-box `sample_rarity` that `scan_enemies` does — this step only needs bar
   thickness — so the loop never calls `scan_enemies` at all.
4. **< `ZOOM_MIN_SAMPLES` (2) thicknesses** (few/no mobs on screen, or none with a
   locatable bar): do **not** scroll. `sleep(2)`, `continue`. The top-of-loop
   wait-cap check (step 1) is what eventually ends this wait.
5. `median = statistics.median(thicks)`.
   - `median >= ZOOM_MIN_THICK` (4) → log "视角OK (血条中位厚度 {median})" and
     return `True` (success) — the zoom is left where we want it, not restored.
   - Else zoom in: `cdp_bridge.scroll_wheel(scroll_amount)` (a CDP
     `Input.dispatchMouseEvent` type `mouseWheel` at the page centre — **not**
     `pyautogui.scroll`, whose OS wheel event needs the Chrome window focused and
     the cursor over the canvas, which it usually isn't while the bot runs; same
     reason `capture_screenshot` uses CDP). `applied += scroll_amount`, `sleep(0.4)`,
     increment `scroll_count`. `ZOOM_SCROLL_AMOUNT` is a DOM wheel `deltaY`
     (default `-120` ≈ one notch up = zoom in; sign self-corrects).
6. **Direction self-correction (flip at most once):** keep `prev_median`. If
   `median < prev_median - 0.5` (thinner than before → wrong way): the first time,
   `scroll_amount = -scroll_amount`, log "视角: 滚轮方向反了, 已翻转"; if it
   regresses *again* after the flip, both directions are losing → log
   "视角调整: 两个方向都没改善, 撤销并放弃", restore the zoom
   (`cdp_bridge.scroll_wheel(-applied)`) and return `False`. This bounds the damage a
   noisy reading can do — the old unconditional flip could commit the loop to
   scrolling out to the cap.
7. `scroll_count >= ZOOM_MAX_SCROLLS` (15) → log "视角调整: 滚了 15 次仍没到目标厚度
   (可能已到最大 zoom), 照常开刷", restore the zoom (`cdp_bridge.scroll_wheel(-applied)`)
   and return `False`.

`applied` is the signed sum of every deltaY passed to `cdp_bridge.scroll_wheel`. Every
give-up path (wait-cap, scroll-cap, both-directions-regress) restores the camera
with `cdp_bridge.scroll_wheel(-applied)` (skipped when `applied == 0`) so a failed run
never leaves the zoom worse than it found it. The success path does **not**
restore.

Return value: `bool` (reached target or not) — the caller logs one warning line
when it is `False` *and* `enemy_ai_enabled` is true; the round always proceeds to
`auto_farming()` regardless.

### `enemy_detect.measure_hp_bar_thickness(detections, image)` — the helper

```
def measure_hp_bar_thickness(detections, image) -> list[int]:
    """每个检测框跑 _find_hp_bar, 收集找到的血条厚度 (第4个返回值), 跳过 None.
    顺序跟 detections 一致. 用来判断相机 zoom 够不够 sample_rarity 工作."""
    out = []
    for d in detections:
        bar = _find_hp_bar(image, d["bbox"])
        if bar is not None:
            out.append(bar[3])
    return out
```

Pure, no I/O. `_find_hp_bar` already exists and returns
`(bar_x0, bar_y, bar_x1, thick)`.

## Architecture

### `enemy_detect.py`

- Add `measure_hp_bar_thickness(detections, image) -> list[int]` next to
  `_find_hp_bar` / `sample_rarity`.
- Add `scan_bar_thickness(image=None, conf=0.4, model_path=...) -> list[int]` next
  to `scan_enemies` — screenshot (if `image is None`) + YOLO +
  `measure_hp_bar_thickness`, no `sample_rarity` / `screen_pos`. This is what
  `ensure_zoom_for_rarity` calls; `scan_enemies` is left untouched.

### `main.py`

- Tuning block (`# ===== 索敌配置 =====`) gains:
  ```
  ZOOM_MIN_THICK    = 4    # 血条中位厚度到这个像素数, sample_rarity 才稳
  ZOOM_MIN_SAMPLES  = 2    # 至少要几条血条样本才据此判定 (少于就等 mob)
  ZOOM_SCROLL_AMOUNT = -120 # CDP 滚轮 deltaY (负=往上=拉近; 方向不对循环里会自翻转)
  ZOOM_MAX_SCROLLS  = 15   # 滚这么多次还没到就放弃 (可能已是最大 zoom)
  ZOOM_WAIT_CAP     = 60   # 周围没 mob 时最多等这么多秒, 之后照常开刷
  ```
- `cdp_bridge.scroll_wheel(delta_y)` — new: a CDP `Input.dispatchMouseEvent` type
  `mouseWheel` at the page centre. Used instead of `pyautogui.scroll` because the
  OS wheel event needs the Chrome window focused / cursor over canvas, which the
  bot usually doesn't have.
- Add `ensure_zoom_for_rarity(enemy_ai_enabled) -> bool` near `_maybe_scan_enemies`.
- `run_worker()`: after `if lazy_theta_pathing(...)` succeeds and before
  `auto_farming(...)`, call `ensure_zoom_for_rarity(w["enemy_ai_enabled"])` into a
  `zoom_ok` var; when `enemy_ai_enabled` is true and `zoom_ok` is false, log one
  warning line ("视角未调到位, 本轮稀有度识别可能不准"). The function still has its
  own internal `enemy_ai_enabled` guard (returns `False` at once when off).

`scan_enemies` is untouched — the zoom loop calls the dedicated
`scan_bar_thickness` wrapper instead.

## Error handling

- `ensure_zoom_for_rarity` wraps its body in `try/except Exception` → log
  `⚠️ 视角调整出错, 照常开刷: {e}` and return `False`. It must never break the
  round loop (same posture as `_maybe_scan_enemies`).
- `pyautogui.screenshot` / `scroll` failure → caught by the same wrapper.
- Worker stop (SIGTERM / stdin EOF) interrupts the `sleep` calls normally — no
  special handling.

## Testing

`test_enemy_detect.py`:
- `measure_hp_bar_thickness` — synthetic image with two green HP bars of known
  thickness → returns `[t1, t2]`; a detection whose bbox has no bar is skipped;
  empty detections → `[]`.
- `scan_bar_thickness` — blank image → `[]`. Guarded with
  `@pytest.mark.skipif(not _HAS_MODEL)` (it loads `models/desert.pt`, which is
  gitignored / user-supplied), matching its `scan_enemies` sibling.

`test_main_worker.py` (monkeypatch style already used for `move_to_position` /
`_maybe_scan_enemies`), via the `_stub_zoom_env` helper:
- Stub `enemy_detect.scan_bar_thickness` (returns successive thickness lists),
  `main.cdp_bridge.scroll_wheel` / `main.pyautogui.moveTo` (both recorded), `main.time.time` /
  `main.time.sleep` (fake clock), `main.overlay`, `main.afk_watch.poll_afk_pause`.
- **reaches target:** `scan_bar_thickness` returns `[2,2]` then `[3,3]` then
  `[4,4]` on successive calls → returns `True`, `scroll` called exactly twice.
- **scroll cap:** always returns `[2,2]` → returns `False` after
  `ZOOM_MAX_SCROLLS` forward scrolls **+ one restore scroll**; net `sum(scroll)`
  is 0; no infinite loop.
- **both directions regress:** `[3,3] → [2,2] → [1,1]` → flips once, regresses
  again, gives up; `sum(scroll) == 0` (everything applied is undone), and a
  positive-then-negative flip is visible in the recorded scrolls.
- **no mobs then mobs:** returns `[]` twice (no scroll, sleeps) then `[4,4]` →
  `True`, `scroll` never called for the empty rounds.
- **wait cap:** always `[]`, fake clock → returns `False` at `ZOOM_WAIT_CAP`,
  `scroll` never called; also holds when `poll_afk_pause` is always `True` (the
  top-of-loop check still fires).
- **re-centres on entry:** with `[]` (no scroll happens), `moveTo` is still
  recorded with `(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)` at least once.
- **direction flip:** `[3,3]` → scroll → `[2,2]` (got worse) → next scroll uses the
  negated amount; assert the sign of the value passed to `scroll` flipped.
- **success does not restore:** `[2,2] → [4,4]` → `True`, `sum(scroll)` equals the
  one forward `ZOOM_SCROLL_AMOUNT` (not undone).
- **disabled:** `ensure_zoom_for_rarity(False)` returns immediately (`False`), no
  scan, no scroll.

## Tuning knobs (need real-machine calibration)

All five `ZOOM_*` constants are placeholder defaults. `ZOOM_SCROLL_AMOUNT`'s sign
in particular is a guess (the loop self-corrects, but a correct initial sign saves
one wasted scroll); `ZOOM_MAX_SCROLLS` and the `0.4 s` post-scroll settle time
depend on the client's zoom animation speed.
