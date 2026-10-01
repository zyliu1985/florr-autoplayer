# AFK-check coexistence with florr-auto-afk — design

## Problem

florr.io periodically shows an anti-bot "AFK CHECK" popup that requires dragging a circle along a doodled path to a target. `florr-auto-pathing` (this repo) has no awareness of it — the pathing loop keeps steering (`pyautogui.moveTo`) right through the popup. Left unhandled, the account risks being kicked/flagged idle.

A separate project, `florr-auto-afk` (same author, `~/florr-auto-afk`, source available), already solves this popup reliably using two YOLO models (window/start/end detection + path segmentation). It is a large, Windows-only, GUI-based program (Tkinter, `pywin32`, `pygetwindow`, browser-extension bridge, OBS hooks, GitHub telemetry) built around `torch`/`ultralytics`.

## Goal

Run florr-auto-afk unmodified (in a Windows VM alongside florr.io itself) to do the actual solving, while florr-auto-pathing pauses its own mouse control for the duration of a solve — so the two programs don't fight over the OS mouse cursor.

## Non-goals

- No YOLO/torch/ultralytics/models ported into this repo. A working solver already exists; porting it would only add ~1GB of dependencies and re-implementation risk for no new capability.
- No IPC/socket integration with florr-auto-afk's extension server (`extensions.enable`) — not used in this setup.
- No changes to florr-auto-afk's own code.
- Not a general-purpose log viewer — the new code only watches for one known line.

## Why not integrate the YOLO code directly (rejected alternative)

Originally scoped as: copy `afk-det.pt`/`afk-seg.pt` into this repo, add a `torch`+`ultralytics`-based solver module, trigger it on a throttled timer from the existing pathing loop. Rejected once a Windows VM running the existing compiled/source program became the plan instead — it reuses an already-working solver, avoids ~1GB of new dependencies in this repo's venv, and avoids re-deriving path-solving logic (dijkstra-on-distance-transform, RDP simplification, drag execution) that florr-auto-afk has already had real-world tuning against.

## Approach

florr-auto-afk writes structured events to `./latest.log` (relative to its own working directory) via `log_ret(event, type, shared_logger)`, whose `save` parameter defaults to `True` — the line is persisted to disk. Confirmed by reading `segment_utils.py`/`segment.py` in `~/florr-auto-afk`: the moment it detects the popup, it calls `log_ret("Found AFK window", "EVENT", shared_logger)`, producing a line ending in:

```
EVENT: Found AFK window
```

**No reliable persisted "cleared" event exists.** The corresponding "no window found" log call is `log("No AFK window found", "EVENT", save=False)` — hardcoded `save=False`, so it never reaches `latest.log` regardless of the `verbose` config flag. It cannot be used as a stop signal.

Consequence: this can only be a *trigger*, not a start/stop pair. Pausing is duration-based — on seeing the trigger line, pause for a fixed window generous enough to cover detection + solve + (optional) post-solve wander, then resume automatically. This trades a few seconds of idle pathing time (acceptable) for not needing a stop signal that doesn't exist.

**Required florr-auto-afk config changes** (its `config.json`, not code):

- `runs.autoTakeOverWhenIdle: false` — its idle-mouse gate (`test_idle_thread`) would otherwise never fire, since florr-auto-pathing keeps the mouse moving continuously; with it forced to `false`, florr-auto-afk's own startup code sets its idle flag permanently `True`, so it scans on a fixed interval regardless of mouse activity.
- `runs.moveAfterAFK: false` — its post-solve "wander a bit" (`move_a_bit()`) sends its own simulated WASD input, a second source of input conflict. florr-auto-pathing already has its own movement/anti-stuck; disabling this also shortens the pause window `afk_watch.py` needs.

## Design

### `afk_watch.py` (new file, this repo)

- Constants: `LATEST_LOG_PATH` (absolute path to florr-auto-afk's `latest.log` — set by whoever deploys this to the VM, since it depends on where the exe is launched from), `PAUSE_SECONDS` (default `12` — covers YOLO det+seg inference plus drag execution on a modest VM; revisit once `moveAfterAFK` is off and real timing is observed), `_FOUND_MARKER = "EVENT: Found AFK window"`.
- Module-level state: last-read byte offset into the log file, and `_pause_until` (an absolute timestamp). Tracks offset instead of re-reading the whole file each poll, since the log grows for the life of a long session.
- `poll_afk_pause() -> bool`:
  - Opens `LATEST_LOG_PATH`, seeks to the stored offset, reads any new bytes, updates the stored offset.
  - If the file's current size is smaller than the stored offset (log rotated/truncated/program restarted), resets offset to `0` and re-reads from the start.
  - If any newly-read line contains `_FOUND_MARKER`, sets `_pause_until = time.time() + PAUSE_SECONDS` and prints a one-line notice (only on the transition into pause, not every poll).
  - Returns `time.time() < _pause_until`.
  - Missing file (florr-auto-afk not started yet, or `LATEST_LOG_PATH` misconfigured) is treated as "never paused" and never raises — matches this codebase's never-crash-the-bot posture (`overlay.py`'s `_NullOverlay` follows the same rule for a missing overlay backend).

### Integration points

Same shape as the existing `on_death_screen()`/`on_start_screen()` checkpoints in `main.py` — checked at the top of the hot loops, before any `pyautogui` movement call:

- `move_to_position`'s tick loop (top): if `afk_watch.poll_afk_pause()`, call `overlay.update(state="AFK弹窗处理中", message="等待florr-auto-afk解题")`, `time.sleep(0.2)`, `continue` — skip this tick's `moveTo` and don't count it toward `stall_limit`/`max_attempts` (a paused tick isn't a stuck tick).
- `lazy_theta_pathing`'s loop top, alongside the existing death/start-screen check.
- `auto_farming`'s loop top, same spot as its death/start-screen check.

No new pip dependencies — `afk_watch.py` only uses `os`/`time`, both already used elsewhere in this repo.

## Operational runbook (environment setup, not code)

1. Windows VM (VMware Fusion) running florr.io fullscreen in-browser at 1920×1080 — the resolution this repo's pixel constants already assume.
2. florr-auto-afk: edit its `config.json` per the two settings above, launch it, confirm `latest.log` appears next to wherever it was launched from.
3. florr-auto-pathing: run the same repo inside the VM's Windows Python. `pyautogui`/`opencv-python`/`numpy` are cross-platform; `overlay.py` already degrades to a no-op when `AppKit` import fails, so no code change is needed for it to run on Windows. Set `afk_watch.LATEST_LOG_PATH` to florr-auto-afk's actual `latest.log` absolute path.
4. Launch florr-auto-afk first, then `main.py`. Trigger or wait for one real AFK check; confirm florr-auto-pathing's console shows the pause notice and doesn't steer mid-solve.

## Testing / verification

1. `afk_watch.poll_afk_pause()` against a throwaway log file: append the marker line, confirm the pause window opens and expires on schedule; confirm a missing file never raises and always reports "not paused".
2. Manual, in the VM: one full real AFK-check cycle, watching that florr-auto-pathing pauses and florr-auto-afk solves without visible mouse fighting.
3. If `PAUSE_SECONDS` is observed to be too short (pathing resumes mid-drag) or too long (idle wait every cycle for no reason), tune the constant from the observed real timing rather than guessing further — flag this to the user instead of silently changing it.
