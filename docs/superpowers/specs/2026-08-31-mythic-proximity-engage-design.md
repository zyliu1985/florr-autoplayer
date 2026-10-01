# Mythic-proximity engage ("先清青怪") — design

## Problem

While farming sandstorm in `auto_farming()`, the bot picks its chase target purely
by `select_action()`'s rarity-first priority and closes on it with a single
straight-line `aim_mouse_target()` per tick (ENGAGE = `hold_px=None`, ram). A
Mythic-rarity mob ("青怪", 青 = Mythic's cyan tag colour) that wanders point-blank
gets rammed like anything else and, the moment its detection flickers or it drifts
out of range, the bot reverts to sandstorm even though the Mythic is still alive
next to the player.

The user wants: when a Mythic mob of one of five desert species is close, **latch
onto it**, kite it with a **per-species** movement policy until it's gone, then
resume sandstorm farming.

## Scope

In scope:
- A proximity-triggered, hysteresis-released latch that pins the bot to one Mythic
  mob, slotted between `flee` and normal `chase`/`wander` in `auto_farming()`.
- Three movement policies keyed by species (strafe / ram / hold).
- Mythic-target selection (which of several qualifying Mythic mobs to take).

Not in scope:
- **Mob heading/orientation estimation.** desert.pt gives box + class only. The
  "遛甲虫/火蚁" kite is done as a *perpendicular circle-strafe* computed from the
  mob's screen position relative to screen centre — no per-mob tracking, no
  velocity vector (user confirmed 垂直绕侧 is acceptable).
- **Mythic sandstorm.** desert.pt class 3. Excluded from the latch — it's the farm
  target; `select_action` already ranks Mythic top and rams it (user confirmed 直接打).
- **A separate config.json toggle.** The feature lives under the existing
  `enemy_ai_enabled` gate. A module-level `MYTHIC_LATCH_ENABLED = True` in main.py's
  tuning block is the only kill switch.
- **Strafe-direction flipping near the farming-area boundary.** v1 uses a fixed
  perpendicular; document as a future tune.

## Behaviour

### Trigger / release (hysteresis)

- **Trigger:** a qualifying Mythic detection within `MYTHIC_ENGAGE_PX` (650) of
  screen centre → latch on. (Started at 450; live `--watch` showed the Mythic
  scorpion/beetle a player calls "point-blank" sit ~450–540px from screen centre,
  so 450 filtered every one of them out.)
- **While latched:** search radius widens to `MYTHIC_RELEASE_PX` (850).
- **Release:** `MYTHIC_RELEASE_MISSES` (3) consecutive enemy scans with no
  qualifying Mythic in the (widened) radius → latch off. ~0.36 s at
  `ENEMY_SCAN_INTERVAL = 0.12`, enough to ride out single-frame detection dropouts.
- **`flee` clears the latch immediately** (see Precedence). Entering `wander` does
  **not** — a Mythic dropout usually also yields `wander`, so clearing there would
  collapse the 3-miss hysteresis to zero. The latch releases only via
  `MYTHIC_RELEASE_MISSES` consecutive misses, plus the pre-scan early-`continue`
  resets (AFK pause / lost position / out-of-area).

"Qualifying" = `rarity == "Mythic"` **and** `species in MYTHIC_KITE_SPECIES`
**and** `confidence >= CHASE_MIN_CONF` (0.55, same phantom-box gate as chase).

### Which Mythic (when several qualify)

Rank by `MYTHIC_TARGET_RANK`, then nearest to centre as the tiebreak:

```
beetle 5  >  soldier_fire_ant 4  >  scorpion 3  >  sand_centipede 2  >  cactus 1
```

(Distinct from `SPECIES_RANK` used for ordinary chase — this order is the user's,
for the Mythic-latch only.)

### Per-species movement policy

`center` = `SCREEN_CENTER` (the player, camera-locked). `v = target_pos - center`,
`d = |v|`, `u = v / d`. `perp = (-u_y, u_x)` (fixed side). `max_extend` scaled by
`utils.mouse_scale()` as elsewhere.

| Species | Policy | Mouse target |
|---|---|---|
| `scorpion`, `sand_centipede` | **ram** | `aim_mouse_target(target_pos, hold_px=None, repel_positions=repel)` — unchanged straight-in chase |
| `cactus` | **hold** | `d > hold*1.15` → approach along `u`; `d < hold*0.85` → back off along `-u`; between → orbit along `perp`. `hold = MYTHIC_CACTUS_HOLD_PX` (220) |
| `beetle`, `soldier_fire_ant` | **strafe** | circle-strafe: `dir = normalise(perp + u * (d - r)/r * K_RADIAL)`, `r = MYTHIC_STRAFE_RADIUS` (180), `K_RADIAL = 0.8`. Mouse = `center + dir * max_extend` |

`repel` (AVOID positions + non-target CAUTIOUS positions) from the same tick's
`select_action` result is threaded into the **ram** and **hold-approach** cases via
the existing `aim_mouse_target(..., repel_positions=)` path. Strafe skips repel in
v1 (documented future tune).

Result is `clamp_to_screen()`'d and `pyautogui.moveTo()`'d, then `time.sleep(0.05)`
— identical cadence to the existing chase/flee branch.

### Precedence in `auto_farming()`'s loop

Per tick, after `_maybe_scan_enemies()`:

1. **`flee`** (from `select_action` — an AVOID mob in `AVOID_TRIGGER_PX`): honour it,
   `mythic_latch = False`, `mythic_misses = 0`. **Does not** clear
   `chase_pos_history` — `flee` re-enters every tick and clearing would hold the
   window under `CHASE_STALL_WINDOW` forever, permanently disabling flee
   anti-stuck. The shared stall-check owns that history. → 躲优先.
2. **Mythic latch** (only if `MYTHIC_LATCH_ENABLED` and action ≠ flee): call
   `pick_mythic_target(detections, ...)`. Update latch/miss counters. If latched
   with a target → run the per-species policy, reuse the chase stall-check
   (`chase_pos_history` + `chase_is_stalled` → `execute_anti_stuck`), `moveTo`,
   `continue`.
3. **`chase` / `wander`** (existing): unchanged. Reached only when not fleeing and
   no Mythic is latched.

The `wander` leg calls the blocking `move_to_position()`, which suspends the outer
loop (and therefore all enemy scanning) for up to ~1 s. `move_to_position()` takes
an `on_tick` hook; `auto_farming` passes `_wander_enemy_watch`, which runs a
throttled `_maybe_scan_enemies` each inner tick and returns `"enemy"` (aborting the
leg) when the fresh decision is `flee`/`chase` or `pick_mythic_target` finds a
target. So a Mythic can latch — and a fast AVOID mob can trigger flee — mid-wander,
not only at leg boundaries.

### Stuck handling

The Mythic branch reuses `chase_pos_history` + `enemy_detect.chase_is_stalled()`
verbatim (net minimap displacement over a ~1.25 s window). A circle-strafe has real
net displacement so it won't false-trigger; a wall-pinned kite reads ~0 and hands
off to `execute_anti_stuck()` like the chase branch. Same "don't append a sample on
a `SCREEN_CENTER` deliberate-stop tick" rule.

## Architecture

### `enemy_detect.py` — new

```
MYTHIC_KITE_SPECIES = {          # Mythic 时按物种分策略; 不在表里(sandstorm)不走这套
    "beetle": "strafe", "soldier_fire_ant": "strafe",
    "scorpion": "ram",  "sand_centipede": "ram",
    "cactus": "hold",
}
MYTHIC_TARGET_RANK = {"beetle": 5, "soldier_fire_ant": 4,
                      "scorpion": 3, "sand_centipede": 2, "cactus": 1}

def mythic_candidates(detections, chase_min_conf=CHASE_MIN_CONF) -> list[dict]
    # rarity == "Mythic" and species in MYTHIC_KITE_SPECIES and conf >= gate

def pick_mythic_target(detections, center, latched, engage_px, release_px,
                       chase_min_conf=CHASE_MIN_CONF) -> dict | None
    # radius = release_px if latched else engage_px
    # among candidates within radius: max by (MYTHIC_TARGET_RANK[sp], -dist)

def mythic_move_target(target, center, *, strafe_radius, cactus_hold_px,
                       max_extend=None, repel_positions=None) -> (x, y)
    # dispatch on MYTHIC_KITE_SPECIES[target["species"]]
```

`select_action` is **unchanged** — the Mythic path reads `detections` directly. The
latch lives in `auto_farming()` next to `chase_pos_history`.

### `main.py` — changes

- `_maybe_scan_enemies()` returns `(decision, detections, last_enemy_scan, scanned)`
  — stop discarding `detections`; on a throttled tick return the previous
  `detections`. `scanned` is `True` only when YOLO actually ran this call (scan-due
  and scan-error branches), `False` on the disabled/throttled branches — the
  Mythic miss counter advances only when `scanned`, so the hysteresis is measured
  in scans (hardware-independent), not loop ticks.
- Tuning block adds:
  ```
  MYTHIC_LATCH_ENABLED  = True
  MYTHIC_ENGAGE_PX      = 450
  MYTHIC_RELEASE_PX     = 600
  MYTHIC_RELEASE_MISSES = 3
  MYTHIC_STRAFE_RADIUS  = 180
  MYTHIC_CACTUS_HOLD_PX = 220
  MYTHIC_STRAFE_K_RADIAL = 0.8
  ```
  (all "占位默认, 实机调" like the neighbours)
- `auto_farming()` loop: `mythic_latch = False`, `mythic_misses = 0` init; the
  precedence block above; a new overlay state, e.g. `state="清青怪"`,
  `message=f"遛 {species}({policy})"`.

## Error handling

- No qualifying Mythic / empty detections → latch decays and releases; falls through
  to `chase`/`wander`. Never raises.
- `pick_mythic_target` on `None`/empty detections → `None`.
- `mythic_move_target` with `d == 0` → return `center` (no direction; skip this tick,
  no history sample — same convention as `aim_mouse_target`).
- Whole path is under `enemy_ai_enabled` and the `_maybe_scan_enemies` try/except —
  a raise inside degrades the tick to `wander`, never breaks the farm loop.

## Testing (`test_enemy_detect.py`)

- `mythic_candidates`: filters by rarity, species (sandstorm excluded), conf gate.
- `pick_mythic_target`: radius = engage vs release by `latched`; rank order
  (beetle > … > cactus); nearest tiebreak within a rank; `None` when nothing in
  radius.
- `mythic_move_target`:
  - ram → equals `aim_mouse_target(pos, hold_px=None)`.
  - hold → approaches when far, backs off when inside `0.85*hold`, orbits (moves
    perpendicular, roughly constant `d`) in the band.
  - strafe → move direction is ~perpendicular to `u` when `d ≈ r`; has an inward
    component when `d > r`, outward when `d < r`.
- Latch state machine is loop glue in `main.py`; cover the counter/hysteresis in a
  `_maybe_scan_enemies`-style extracted helper if it's non-trivial, else leave to
  live testing (same posture as the rest of the farm loop).

## Tuning knobs (all need real-machine calibration)

`MYTHIC_ENGAGE_PX`, `MYTHIC_RELEASE_PX`, `MYTHIC_RELEASE_MISSES`,
`MYTHIC_STRAFE_RADIUS`, `MYTHIC_CACTUS_HOLD_PX`, `K_RADIAL`, and the fixed strafe
side — all placeholder defaults, tuned against live footage like
`AVOID_TRIGGER_PX` / `CAUTIOUS_HOLD_PX`.
