# Status overlay — design

## Problem

`main.py` runs fullscreen against florr.io and controls the real mouse/keyboard. All progress currently goes to stdout, which is hidden behind the fullscreen game — the user has no way to see pathing/movement state while it runs.

## Goal

A small always-on-top window showing live structured status while the bot runs.

## Non-goals

- Controls/buttons in the overlay (display-only for v1)
- Working over genuine macOS-native fullscreen Spaces (v1 targets browser-maximized/F11-in-same-Space; see Fallback)
- Config file / CLI flags for overlay position or theme (hardcoded constants for now)

## Approach

tkinter `Toplevel`/`Tk` window, borderless, semi-transparent, `-topmost` attribute, pinned to the screen's top-left corner (top-right is reserved by the game's own minimap).

**Dependency:** the project venv is built against Homebrew `python@3.14`, which ships without `_tkinter`. Fix: `brew install python-tk@3.14`.

**Why tkinter over pyobjc:** zero new pip dependencies, small code surface, matches this codebase's existing simplicity. Risk: if florr.io is running in genuine macOS-native fullscreen (a separate Space), OS-level window layering hides ALL other windows regardless of `-topmost` — this is an OS limitation `-topmost` cannot override. Confirmed acceptable risk with user; see Fallback.

**Fallback (not built now, only if tkinter overlay doesn't show over the real game):** rebuild the overlay on `pyobjc-framework-Cocoa`, using an `NSPanel` with `collectionBehavior` including `canJoinAllSpaces` / `fullScreenAuxiliary`, which lets a window join a fullscreen Space. Deferred per YAGNI until the simple approach is proven insufficient.

## Design

### `overlay.py` (new file)

`StatusOverlay` class:

- `__init__`: creates the Tk root, `overrideredirect(True)` (no title bar/borders), `-topmost` attribute, `-alpha` ~0.85, `geometry("260x150+20+20")`, dark background, small monospace labels for: 状态 / 位置 / 目标 / 消息 / 耗时.
- `update(state=None, pos=None, target=None, message=None)`: merges any passed fields into current display state, updates label text, calls `root.update_idletasks(); root.update()`. No `mainloop()` — refresh is driven by callers polling it inside their own loops (matches the existing synchronous while-loop structure in `utils.py`/`main.py`; avoids threading and tkinter's not-thread-safe pitfalls).
- Construction is wrapped in `try/except`. On any failure (e.g. `_tkinter` still missing), `StatusOverlay()` returns a no-op stub (`update()` is a no-arg-tolerant pass) so the automation never crashes or blocks because the overlay is unavailable.
- Elapsed timer (耗时) is tracked internally from `StatusOverlay` construction time — no caller needs to compute it.

### Integration points

A single module-level `overlay = StatusOverlay()` instance, imported wherever status changes. Calls added at existing state-transition points, no new control flow:

- `utils.py::lazy_theta_pathing`: on retry/无法检测位置, on path found, on stuck/dead/menu, on arrival.
- `utils.py::move_to_position`: each attempt tick — 状态="移动中", 位置=current, 目标=target.
- `utils.py::auto_farming`: on区域内巡逻 tick — 状态="刷怪中".
- Top-level `main.py`: 状态="完成" / "无法到达" at the end.

No new files besides `overlay.py`; existing functions gain one extra `overlay.update(...)` call each, no signature changes.

## Testing / verification

1. `brew install python-tk@3.14`, confirm `import tkinter` works in the venv.
2. Run a standalone smoke test (`overlay.py`'s own `if __name__ == "__main__":` block) that opens the window and cycles through a few fake states — confirms rendering/positioning without touching the game.
3. Run `main.py` for real with florr.io fullscreen, confirm the overlay is visible on top of the game and updates live.
4. If step 3 fails (native-fullscreen Space hides it), stop and discuss the pyobjc fallback with the user before building it.
