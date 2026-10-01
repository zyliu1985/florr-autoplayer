# Sandstorm Zone enemy detection (YOLO targeting) — design

## Problem

`main.py`'s `auto_farming()` currently just wanders randomly inside the farming area (`random_walkable_point` + `move_to_position`) while the player relies on an external "always attack" hotkey to kill whatever wanders into range. There is no awareness of *what* enemies are nearby, so the bot can't preferentially chase good targets or back off from dangerous ones — it purely depends on random walking to eventually bump into something.

The current pathing/farming stack (`lazy_theta_star`, `move_to_position`, `random_walkable_point`) is entirely **minimap-space**: positions come from `get_player_position()` / `load_binary_map()`, a 300×300 grid derived from the minimap crop at screen region `[1600, 20, 1900, 320]`. Enemies rendered in the actual game world are not visible on the minimap at all — detecting them requires a full-screen, **screen-space** view. Mixing these two coordinate systems without care is exactly the class of bug already hit once in this project (see `florr-player-marker-color-bug` — wrong color list silently matched wall pixels instead of the player marker).

## Goal

Add a YOLO-based enemy-detection layer that, while farming inside the Sandstorm Zone area (already configured in `main.py` — no map/location work needed, see "Non-goals"):

- Detects the 6 mobs `desert.pt` recognizes: `scorpion`, `beetle`, `cactus`, `sandstorm`, `sand_centipede`, `soldier_fire_ant`.
- Reads each detected mob's rarity from its on-screen name-tag color.
- Picks a chase/aim target using rarity-first, species-second priority.
- Steers away from mobs classified dangerous at their current rarity, instead of relying purely on random wandering to avoid them.
- Falls back to the existing random-wander behavior whenever nothing relevant is detected.

## Non-goals

- **No new map/location work.** The Sandstorm Zone is not a separate minimap — it's the sub-area of `maps/desert.png` that `main.py`'s `location`/`farming_area` already target (confirmed with the user; recent commits already retargeted this). This spec is only about the enemy-detection layer on top of the existing farming loop.
- **`sandstorm.pt` not used by this spec (but it IS a monster detector).** Loading it shows a single `detect` class `sandstorm`. Per the user this is a dedicated detector for the sandstorm *monster* (correcting an earlier note in this doc that called it a weather-effect detector — that was wrong). This spec still uses `desert.pt` because its 6 classes cover every desert mob including `sandstorm` (id 3) in one model, so a single inference call handles the whole zone. `sandstorm.pt` remains available as a possibly-more-accurate dedicated sandstorm detector; whether `desert.pt` class 3 and `sandstorm.pt` agree / which is more reliable is a real-footage comparison, not settled here.
- **No reuse of `move_to_position`'s blocking travel loop for chasing.** See "Why not reuse `move_to_position` for chasing" below — it's built for a static minimap-space waypoint, not a continuously-moving screen-space target.
- **No build/loadout switching, no petal management.** Out of scope — the user's existing external "always attack" hotkey keeps doing the actual attacking; this feature only decides where to point the mouse and when to back off.
- **No training of a custom model.** Uses the pre-trained `desert.pt` weights as-is.

## Model provenance note

`models/desert.pt` and `models/sandstorm.pt` were downloaded by the user directly (not fetched by the assistant — third-party `.pt` files are pickle-based and can execute arbitrary code on load; downloading/executing files from an unverified source is outside what the assistant will do even with explicit permission). Both are loaded exclusively through `ultralytics.YOLO(path)`, which uses PyTorch's `weights_only` safe-loading path rather than a raw unrestricted `torch.load`. `florr-auto-sszone`'s own `main.py` (same author as the `assets` repo) was found to be heavily obfuscated when inspected — its code is **not** used anywhere in this design, only the openly-inspectable model weights.

## Environment change (already applied)

`venv/` was rebuilt on Python 3.11 (was 3.14 — no `torch` wheels exist for 3.14 yet). All prior dependencies (`opencv-python`, `numpy`, `pyautogui`, `pillow`, `pyobjc-framework-Cocoa`, `pytest`) were reinstalled; existing test suite (29 tests) verified green after the rebuild. Added: `torch==2.2.2`, `ultralytics==8.4.120`, `numpy<2` / `opencv-python<5` (pinned down from the newer defaults specifically because `torch==2.2.2` — the newest version this environment's index carries — was compiled against NumPy 1.x and warns/risks crashing under NumPy 2.x). MPS (Apple GPU) backend confirmed available (`torch.backends.mps.is_available() == True`).

`models/desert.pt` classes (confirmed by loading): `{0: 'scorpion', 1: 'beetle', 2: 'cactus', 3: 'sandstorm', 4: 'sand_centipede', 5: 'soldier_fire_ant'}`.

## Rarity detection

florr.io rarity tiers, low → high: `Common, Unusual, Rare, Epic, Legendary, Mythic, Ultra, Super, Eternal, Unique`.

Color table (sourced from a public Greasyfork florr.io userscript that does the same name-tag-color detection; `Eternal` wasn't in that script's table and is filled in as a placeholder — **all of these need real-machine calibration against actual screenshots before being trusted**, the same way `f8de60` for the player marker only became reliable after real-world verification):

| Rarity | Hex |
|---|---|
| Common | `7EEF6D` |
| Unusual | `FFE65D` |
| Rare | `4D52E3` |
| Epic | `861FDE` |
| Legendary | `DE1F1F` |
| Mythic | `1FDBDE` |
| Ultra | `FF2B75` |
| Super | `2BFFA3` |
| Eternal | *(unverified — placeholder, treat as Super's neighbor until sampled for real)* |
| Unique | `555555` |

`sample_rarity(image, bbox)` samples a small region above the bbox's top-center (where florr.io renders name tags), then nearest-matches against this table within a tolerance. No match within tolerance → default `Common` (the most permissive/normal-engagement default, so a failed color read never accidentally triggers avoidance behavior against a harmless target).

## Action classification

Per (species, rarity), each detection is classified into exactly one bucket:

- **`ENGAGE`** — Mythic and below, any species. Normal behavior: this can become the chase/aim target, closing to normal range.
- **`AVOID`** — Ultra `scorpion` or `beetle`. Never a chase candidate; if one is within `AVOID_TRIGGER_PX` of screen center, it overrides everything else and triggers a flee vector this tick.
- **`CAUTIOUS`** — Ultra `sandstorm`, `cactus`, `sand_centipede`, or `soldier_fire_ant`. Still a valid chase candidate (can be aimed at / prioritized), but movement stops closing once within `CAUTIOUS_HOLD_PX` of it — held at that distance rather than pressed into melee range.
- Anything rarer than Ultra (`Super`/`Eternal`/`Unique`) is not expected to actually spawn here (confirmed by the user) but is still given a defined fallback so classification never raises on an unlisted combination: treated as `AVOID` — the fail-closed choice, since these tiers are rarer (and presumably tougher) than the already-`AVOID`-worthy Ultra `scorpion`/`beetle`, with no observed rule to say otherwise.

## Priority (target selection)

`priority_score(species, rarity) = (RARITY_RANK[rarity], SPECIES_RANK[species])`, compared as a tuple — **rarity dominates outright**; species order (`sandstorm > cactus > beetle > scorpion > {sand_centipede, soldier_fire_ant}` tied lowest) is only a tiebreaker among detections that share the same rarity tier. `AVOID`-classified detections are excluded from the candidate pool entirely before this comparison runs — no rarity/priority math can pull an `AVOID` mob back in as a chase target.

## Why not reuse `move_to_position` for chasing

`move_to_position(current_pos, target_pos)` is a blocking loop that repeatedly diffs two **minimap-space** points, and treats "distance not shrinking for `stall_limit` ticks" as stuck → triggers the anti-stuck routine. Two problems if reused verbatim for chasing a detected mob:

1. **Coordinate space mismatch.** `current_pos` there comes from `get_player_position()` (minimap-space); a detected mob's position is a raw screen pixel from `scan_enemies()` (screen-space). Diffing across the two spaces produces a meaningless direction vector — the same category of bug as the player-marker color mismatch already fixed in this repo.
2. **Stall detection assumes a static target.** A chased mob moves every tick; "distance not shrinking" is normal/expected mid-chase (it dodges, repositions), not a sign of being stuck. Reusing the stall logic as-is would misfire into the anti-stuck routine mid-chase.

Instead, chasing/fleeing is a **single direct mouse-aim computation per outer-loop tick**, entirely in screen-space: `current = (960, 540)` (screen center — where the player always renders, since the camera follows the player), `target = mob's screen_pos`. This sidesteps both problems: same coordinate space on both sides, and no blocking sub-loop with its own stall detection — each tick just re-aims once and lets the next scan correct course. Pure wandering (no relevant detection) is unaffected and keeps calling `move_to_position` exactly as today, in minimap-space, for travel to a random point.

## Architecture

### New file: `enemy_detect.py`

- `load_enemy_model()` — loads `models/desert.pt` once via `ultralytics.YOLO(...)`, module-level singleton (mirrors `overlay.py`'s create-once pattern).
- `scan_enemies(conf=0.4) -> list[dict]` — screenshots the full game viewport (`[0, 0, 1920, 1080]`), runs inference, returns `{species, rarity, screen_pos: (cx, cy), bbox, confidence}` per detection. `rarity` comes from `sample_rarity()` on each box.
- `sample_rarity(image, bbox) -> str` — as described above.
- `classify_action(species, rarity) -> Literal["ENGAGE", "CAUTIOUS", "AVOID"]`.
- `priority_score(species, rarity) -> tuple[int, int]`.
- `select_action(detections) -> tuple[str, ...]` — the decision function for one tick:
  - Any `AVOID` detection within `AVOID_TRIGGER_PX` of screen center → `("flee", flee_vector)`, where `flee_vector` is a simple sum of unit vectors pointing away from screen center toward each such detection (same repulsion *concept* as `calc_anti_stuck`, but computed from exact known mob positions instead of an uncertain wall-color point cloud — a new, simpler implementation, not a call into the existing function).
  - Else, best-priority `ENGAGE`/`CAUTIOUS` candidate exists → `("chase", target, hold_px)` — `hold_px = CAUTIOUS_HOLD_PX` if the target is `CAUTIOUS`, else `None` (no distance cap: aim continues directly at the target's `screen_pos` every tick, with no held-back stopping distance, letting the player close all the way in for a normal engage).
  - Else → `("wander", None)`.

### Config additions (`main.py`, alongside the existing `location`/`farming_area` block)

```python
ENEMY_MODEL_PATH = "models/desert.pt"
ENEMY_SCAN_INTERVAL = 0.3        # seconds between YOLO scans; not every ~50ms tick
AVOID_TRIGGER_PX = 400           # screen-space radius that triggers a flee override
CAUTIOUS_HOLD_PX = 250           # screen-space distance CAUTIOUS targets are held at
```

Defaults are placeholders for the user to tune after real-machine testing — same posture as `PAUSE_SECONDS` in the AFK-watch design.

### Integration into `auto_farming()`'s loop

Existing per-tick checks (AFK pause, death/menu screen) stay first and unchanged. Then, each tick:

1. If `ENEMY_SCAN_INTERVAL` has elapsed since the last scan, call `scan_enemies()` and `select_action()`; cache the result for ticks in between (avoids paying inference cost every ~50ms tick).
2. `"flee"` → this tick's mouse move is the flee vector (screen-space), skip the rest of the wander logic.
3. `"chase"` → this tick's mouse move aims at the target's `screen_pos` (capped/held back at `hold_px` if set), skip the rest of the wander logic.
4. `"wander"` (or no cached decision yet) → unchanged: `random_walkable_point` + blocking `move_to_position`.
5. `overlay.update(state=...)` gets new states (e.g. `"索敌中"`, `"规避Ultra蝎子"`) reusing the existing overlay plumbing — no changes needed to `overlay.py` itself.

## Error handling

- Model load failure, inference exception, or zero detections → treated identically to `"wander"`. Enemy detection is additive; it must never be able to block or crash the core farming loop it sits on top of.
- Unlisted (species, rarity) combination in `classify_action` → falls back to `AVOID` (see above) rather than raising.

## Testing

Unit tests (`test_enemy_detect.py`, following this repo's existing `test_utils.py` style):

- `classify_action` — every (species, rarity) combination used by the ruleset resolves to the right bucket; combinations above Ultra fall back to `AVOID` without raising.
- `priority_score` ordering — a low-species-priority mob at a higher rarity tier outranks a high-species-priority mob at a lower tier (e.g. Rare `sand_centipede` > Common `sandstorm`); within the same rarity tier, species order matches `sandstorm > cactus > beetle > scorpion > {sand_centipede, soldier_fire_ant}`.
- `sample_rarity` — synthetic small images filled with each table color resolve to the right tier within tolerance; an unmatched color falls back to `Common`.
- `select_action` — given canned detection lists: an `AVOID` mob within radius produces `"flee"` regardless of what else is present; no `AVOID` mob in range but an `ENGAGE`/`CAUTIOUS` candidate present produces `"chase"` with the right `hold_px`; no relevant detections produces `"wander"`.

Not unit-testable (needs the real game, same posture as the rest of this repo's game-dependent behavior): actual YOLO inference quality against live footage, and real-machine calibration of the rarity color table / `AVOID_TRIGGER_PX` / `CAUTIOUS_HOLD_PX` constants — flagged for the user to verify and tune against real screenshots (this repo's established pattern — see `debug_*.py` scripts) rather than guessed further here.
