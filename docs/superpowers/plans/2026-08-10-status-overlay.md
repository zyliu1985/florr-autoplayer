# Status Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a small always-on-top status window (`overlay.py`) that shows live pathing/movement state while `main.py` runs fullscreen against florr.io.

**Architecture:** A `StatusOverlay` class wraps a borderless, semi-transparent, `-topmost` tkinter window pinned to the screen's top-left corner. It has no `mainloop()` — callers refresh it by calling `.update(...)` from inside their own existing polling loops (`move_to_position`, `lazy_theta_pathing`, `auto_farming` in `main.py`), so no threading is introduced. If tkinter/the window can't be created, a no-op stub (`_NullOverlay`) is used instead so the automation never crashes because of the overlay.

**Tech Stack:** Python stdlib `tkinter` (requires `brew install python-tk@3.14` for this venv's Homebrew Python), `pytest` (new dev dependency, for the overlay's pure-logic unit tests).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-10-status-overlay-design.md`.
- Overlay is display-only (no buttons/controls) — v1 scope per spec's Non-goals.
- Window: borderless, `-topmost`, `-alpha` ≈0.85, geometry `260x150+20+20` (top-left corner — top-right is reserved by the game's own minimap).
- Displayed fields: 状态 (state) / 位置 (pos) / 目标 (target) / 消息 (message) / 耗时 (elapsed, computed internally from construction time).
- **Correction vs. spec:** the spec's integration-points list said `utils.py::lazy_theta_pathing` / `move_to_position` / `auto_farming` — those three functions actually live in **`main.py`** (verified by reading the file; `utils.py` only holds `get_player_position`, `check_stage`, `load_binary_map`, etc.). This plan wires the overlay into `main.py`, not `utils.py`.
- **Git repo + worktree, added after this plan was first drafted.** The project had no git repo when this plan was written (hence the original "no commit" steps below); a repo was since initialized and this feature is developed in an isolated worktree at `/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/status-overlay` on branch `status-overlay`. Every task DOES commit now — every `cd` in this plan targets the worktree path, not the main checkout. Never run these commands from the main checkout (`.../florr-auto-pathing-main 2` without the `.worktrees/status-overlay` suffix) — that pollutes the user's live `main` branch.
- venv is at `./venv` (project root) — use `./venv/bin/python` / `./venv/bin/pip` for every command, not bare `python`/`pip`.

---

### Task 1: Fix tkinter availability

**Files:** none (environment only).

**Interfaces:** none — this unblocks Task 3's `import tkinter`.

- [ ] **Step 1: Install the Tk framework for this Python**

Run:
```bash
brew install python-tk@3.14
```
Expected: brew installs (or reports already-installed) `python-tk@3.14` and its `tcl-tk` dependency.

- [ ] **Step 2: Verify `_tkinter` now imports in the venv**

Run:
```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/status-overlay" && ./venv/bin/python -c "import tkinter; print('tkinter OK', tkinter.TkVersion)"
```
Expected: prints `tkinter OK 8.6` (or similar version), no `ModuleNotFoundError`.

If it still fails: the venv's Python may need to be recreated against the now-Tk-enabled Homebrew Python (`./venv/bin/python -m venv --upgrade "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/venv"` from outside the venv, or delete and recreate the venv with `/usr/local/bin/python3.14 -m venv venv`). Do this only if Step 2 fails — don't pre-emptively recreate the venv.

- [ ] **Step 3: Mark task done**

Nothing to commit (this task changed no tracked files) — proceed to Task 2.

---

### Task 2: Pure helper functions + unit tests

**Files:**
- Create: `overlay.py` (pure-function section only for this task)
- Test: `test_overlay.py`

**Interfaces:**
- Produces: `_format_elapsed(seconds: float) -> str`, `_format_pos(pos) -> str`, `_merge_state(current: dict, **fields) -> dict` — used by `StatusOverlay.update()` in Task 3.

- [ ] **Step 1: Install pytest into the venv**

Run:
```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/status-overlay" && ./venv/bin/pip install pytest
```
Expected: pytest installs successfully.

- [ ] **Step 2: Write the failing tests**

Create `test_overlay.py`:
```python
from overlay import _format_elapsed, _format_pos, _merge_state


def test_format_elapsed_zero():
    assert _format_elapsed(0) == "00:00"


def test_format_elapsed_under_a_minute():
    assert _format_elapsed(45) == "00:45"


def test_format_elapsed_minutes_and_seconds():
    assert _format_elapsed(125) == "02:05"


def test_format_elapsed_negative_clamps_to_zero():
    assert _format_elapsed(-5) == "00:00"


def test_format_pos_none():
    assert _format_pos(None) == "-"


def test_format_pos_tuple():
    assert _format_pos((14, 45)) == "(14, 45)"


def test_merge_state_overwrites_only_provided_fields():
    current = {"state": "idle", "pos": None, "target": None, "message": "-"}
    updated = _merge_state(current, state="寻路中", pos=(1, 2))
    assert updated == {"state": "寻路中", "pos": (1, 2), "target": None, "message": "-"}


def test_merge_state_none_values_do_not_overwrite():
    current = {"state": "寻路中", "pos": (1, 2), "target": None, "message": "-"}
    updated = _merge_state(current, state=None, message="卡住了")
    assert updated == {"state": "寻路中", "pos": (1, 2), "target": None, "message": "卡住了"}


def test_merge_state_does_not_mutate_input():
    current = {"state": "idle"}
    _merge_state(current, state="移动中")
    assert current == {"state": "idle"}
```

- [ ] **Step 3: Run tests to verify they fail**

Run:
```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/status-overlay" && ./venv/bin/python -m pytest test_overlay.py -v
```
Expected: FAIL / collection error — `overlay.py` doesn't exist yet (`ModuleNotFoundError: No module named 'overlay'`).

- [ ] **Step 4: Write the minimal implementation**

Create `overlay.py`:
```python
"""悬浮状态窗 — 全屏运行main.py时显示寻路/移动进度."""
import time
import tkinter


def _format_elapsed(seconds):
    """把秒数格式化成 mm:ss, 负数按0算."""
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _format_pos(pos):
    """把 (x, y) 或 None 格式化成显示用字符串."""
    if pos is None:
        return "-"
    return f"({pos[0]}, {pos[1]})"


def _merge_state(current, **fields):
    """把非None的字段合并进当前状态, 不修改传入的dict."""
    updated = dict(current)
    for key, value in fields.items():
        if value is not None:
            updated[key] = value
    return updated
```

- [ ] **Step 5: Run tests to verify they pass**

Run:
```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/status-overlay" && ./venv/bin/python -m pytest test_overlay.py -v
```
Expected: all 9 tests PASS.

- [ ] **Step 6: Commit**

```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/status-overlay" && git add overlay.py test_overlay.py && git commit -m "feat: add overlay pure helper functions with tests"
```

Proceed to Task 3.

---

### Task 3: `StatusOverlay` window + fallback stub

**Files:**
- Modify: `overlay.py` (append to the file created in Task 2)
- Test: `test_overlay.py` (append)

**Interfaces:**
- Consumes: `_format_elapsed`, `_format_pos`, `_merge_state` (from Task 2, same file).
- Produces: `create_overlay() -> StatusOverlay | _NullOverlay`, both exposing `.update(state=None, pos=None, target=None, message=None)` and `.close()` — used by `main.py` in Tasks 4-6.

- [ ] **Step 1: Write the failing test for the fallback path**

Append to `test_overlay.py`:
```python
import overlay as overlay_module


def test_create_overlay_falls_back_when_tk_unavailable(monkeypatch):
    def raise_tcl_error(*args, **kwargs):
        raise RuntimeError("no display")

    monkeypatch.setattr(overlay_module.tkinter, "Tk", raise_tcl_error)
    result = overlay_module.create_overlay()
    assert isinstance(result, overlay_module._NullOverlay)
    # must never raise, whatever it's called with
    result.update(state="寻路中", pos=(1, 2), target=(3, 4), message="test")
    result.close()


def test_null_overlay_update_ignores_all_args():
    stub = overlay_module._NullOverlay()
    assert stub.update(state="x", pos=(1, 1), target=(2, 2), message="y") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/status-overlay" && ./venv/bin/python -m pytest test_overlay.py -v
```
Expected: FAIL — `AttributeError: module 'overlay' has no attribute 'tkinter'` (or `'_NullOverlay'`/`'create_overlay'`) since none of that exists yet.

- [ ] **Step 3: Write the minimal implementation**

Append to `overlay.py` (after the three functions from Task 2; `import tkinter` is already at the top of the file from Task 2):
```python
class _NullOverlay:
    """悬浮窗建不起来时的空壳替代品, 调用什么都不做, 绝不炸主程序."""

    def update(self, state=None, pos=None, target=None, message=None):
        return None

    def close(self):
        return None


class StatusOverlay:
    _FIELDS = ("state", "pos", "target", "message")
    _LABELS_ZH = {"state": "状态", "pos": "位置", "target": "目标", "message": "消息"}

    def __init__(self):
        self._start = time.time()
        self._state = {"state": "-", "pos": None, "target": None, "message": "-"}

        self._root = tkinter.Tk()
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        self._root.attributes("-alpha", 0.85)
        self._root.geometry("260x150+20+20")
        self._root.configure(bg="#1e1e1e")

        tkinter.Label(
            self._root, text="florr auto-pathing", fg="#f8de60", bg="#1e1e1e",
            font=("Menlo", 12, "bold"),
        ).pack(anchor="w", padx=8, pady=(8, 4))

        self._field_labels = {}
        for field in self._FIELDS:
            label = tkinter.Label(
                self._root, text=f"{self._LABELS_ZH[field]}: -", fg="white",
                bg="#1e1e1e", font=("Menlo", 11), anchor="w", justify="left",
            )
            label.pack(anchor="w", padx=8, fill="x")
            self._field_labels[field] = label

        self._elapsed_label = tkinter.Label(
            self._root, text="耗时: 00:00", fg="#aaaaaa", bg="#1e1e1e", font=("Menlo", 10),
        )
        self._elapsed_label.pack(anchor="w", padx=8, pady=(4, 8))

        self._root.update_idletasks()
        self._root.update()

    def update(self, state=None, pos=None, target=None, message=None):
        self._state = _merge_state(
            self._state, state=state, pos=pos, target=target, message=message,
        )
        for field in self._FIELDS:
            value = self._state[field]
            display = _format_pos(value) if field in ("pos", "target") else value
            self._field_labels[field].config(text=f"{self._LABELS_ZH[field]}: {display}")
        self._elapsed_label.config(text=f"耗时: {_format_elapsed(time.time() - self._start)}")
        self._root.update_idletasks()
        self._root.update()

    def close(self):
        try:
            self._root.destroy()
        except tkinter.TclError:
            pass


def create_overlay():
    """建悬浮窗, 建不起来(没tkinter/没display)就退化成空壳, 不炸主程序."""
    try:
        return StatusOverlay()
    except Exception:
        return _NullOverlay()


if __name__ == "__main__":
    # 手动烟雾测试: 开窗, 循环几个假状态, 肉眼确认渲染/位置对不对.
    demo = create_overlay()
    fake_states = [
        {"state": "寻路中", "pos": (53, 144), "target": (14, 45), "message": "规划路径..."},
        {"state": "移动中", "pos": (30, 90), "target": (14, 45), "message": "移动到 (30, 90)"},
        {"state": "卡住", "pos": (30, 90), "target": (14, 45), "message": "移动受阻"},
        {"state": "完成", "pos": (14, 45), "target": (14, 45), "message": "已到达目标区域"},
    ]
    for fake in fake_states:
        demo.update(**fake)
        time.sleep(2)
    demo.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/status-overlay" && ./venv/bin/python -m pytest test_overlay.py -v
```
Expected: all 11 tests PASS.

- [ ] **Step 5: Manual smoke test — confirm the window actually renders**

Run:
```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/status-overlay" && ./venv/bin/python overlay.py
```
Expected: a small dark window appears top-left of the screen, cycles through 4 fake states every 2s (state/pos/target/message text changes, 耗时 counts up), then closes after ~8s. This does not touch florr.io — safe to run any time.

If the window never appears: stop, re-check Task 1 (tkinter import), don't proceed to Task 4.

- [ ] **Step 6: Commit**

```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/status-overlay" && git add overlay.py test_overlay.py && git commit -m "feat: add StatusOverlay window with tkinter fallback"
```

Proceed to Task 4.

---

### Task 4: Wire overlay into `move_to_position`

**Files:**
- Modify: `main.py:1-3` (imports) and `main.py:100-145` (`move_to_position`)

**Interfaces:**
- Consumes: `create_overlay` from `overlay.py` (Task 3).

- [ ] **Step 1: Add the import and module-level singleton**

In `main.py`, replace:
```python
from utils import *
import time
import random
```
with:
```python
from utils import *
from overlay import create_overlay
import time
import random

overlay = create_overlay()
```

- [ ] **Step 2: Update `move_to_position` to report state each tick**

Replace:
```python
def move_to_position(current_pos, target_pos, max_attempts=30):
    """移动到目标位置 - 简化版本"""
    if current_pos is None or target_pos is None:
        return "stuck"
    
    attempts = 0
    while attempts < max_attempts:
        current_pos = get_player_position()
        if current_pos is None:
            return "stuck"
        
        # 计算方向
        dx = target_pos[0] - current_pos[0]
        dy = target_pos[1] - current_pos[1]
        dist = math.sqrt(dx**2 + dy**2)
        
        # 如果已到达目标
        if dist < 5:
            reset_keyboard()
            return True
        
        # 移动鼠标指向目标
        extend = max(min(dist * 45, 500), 50)
        if dist > 0:
            extend_x = extend * dx / dist
            extend_y = extend * dy / dist
        else:
            extend_x = extend_y = 0
        
        mouse_pos = (1920 // 2 + extend_x, 1080 // 2 + extend_y)
        pyautogui.moveTo(mouse_pos)
        
        # 检查游戏状态
        stage = check_stage()
        if stage == "in_game_dead":
            reset_keyboard()
            return "in_game_dead"
        elif stage == "in_menu":
            reset_keyboard()
            return "in_menu"
        
        attempts += 1
        time.sleep(0.05)
    
    reset_keyboard()
    return "stuck"
```
with:
```python
def move_to_position(current_pos, target_pos, max_attempts=30):
    """移动到目标位置 - 简化版本"""
    if current_pos is None or target_pos is None:
        return "stuck"
    
    attempts = 0
    while attempts < max_attempts:
        current_pos = get_player_position()
        if current_pos is None:
            overlay.update(state="无法检测位置", message="移动中丢失玩家位置")
            return "stuck"
        
        # 计算方向
        dx = target_pos[0] - current_pos[0]
        dy = target_pos[1] - current_pos[1]
        dist = math.sqrt(dx**2 + dy**2)
        
        overlay.update(state="移动中", pos=current_pos, target=target_pos)
        
        # 如果已到达目标
        if dist < 5:
            reset_keyboard()
            return True
        
        # 移动鼠标指向目标
        extend = max(min(dist * 45, 500), 50)
        if dist > 0:
            extend_x = extend * dx / dist
            extend_y = extend * dy / dist
        else:
            extend_x = extend_y = 0
        
        mouse_pos = (1920 // 2 + extend_x, 1080 // 2 + extend_y)
        pyautogui.moveTo(mouse_pos)
        
        # 检查游戏状态
        stage = check_stage()
        if stage == "in_game_dead":
            reset_keyboard()
            overlay.update(state="已死亡")
            return "in_game_dead"
        elif stage == "in_menu":
            reset_keyboard()
            overlay.update(state="菜单中")
            return "in_menu"
        
        attempts += 1
        time.sleep(0.05)
    
    reset_keyboard()
    overlay.update(state="卡住", message=f"{max_attempts}次尝试后仍未到达")
    return "stuck"
```

- [ ] **Step 3: Verify the module still imports cleanly**

Run:
```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/status-overlay" && ./venv/bin/python -c "import main" 2>&1 | tail -20
```
Expected: no traceback. (This will briefly open the overlay window since `overlay = create_overlay()` runs at import time — that's expected; it does NOT touch the mouse/keyboard. Close the window manually or let the process exit.)

- [ ] **Step 4: Re-run the full test suite to confirm nothing broke**

Run:
```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/status-overlay" && ./venv/bin/python -m pytest test_overlay.py -v
```
Expected: all 11 tests still PASS (this task didn't touch `overlay.py`'s logic, just consumes it).

- [ ] **Step 5: Commit**

```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/status-overlay" && git add main.py && git commit -m "feat: wire status overlay into move_to_position"
```

Proceed to Task 5.

---

### Task 5: Wire overlay into `lazy_theta_pathing`

**Files:**
- Modify: `main.py:172-229` (`lazy_theta_pathing`)

**Interfaces:**
- Consumes: module-level `overlay` singleton (from Task 4).

- [ ] **Step 1: Update `lazy_theta_pathing` to report state at each transition**

Replace:
```python
def lazy_theta_pathing(location, area=[]):
    """寻路到目标区域"""
    retry_count = 0
    max_retries = 3
    
    while True:
        pos = get_player_position()
        
        if pos is None:
            retry_count += 1
            print(f"⚠️ 无法检测玩家位置，重试 {retry_count}/{max_retries}...")
            if retry_count >= max_retries:
                print("❌ 多次重试失败")
                return False
            time.sleep(1)
            continue
        
        retry_count = 0
        print(f"\n📍 寻路: {pos} -> {location}")
        time_now = time.time()
        
        binary_map = load_binary_map()
        if binary_map is None:
            print("❌ 地图加载失败")
            return False
        
        path = lazy_theta_star(binary_map, pos, location)
        print(f"⏱️  寻路耗时: {time.time() - time_now:.2f}秒")
        
        if path is None:
            print("❌ 路径规划失败")
            return False
        
        print(f"✅ 找到路径，共 {len(path)} 个点")
        stat = execute_path(path)
        
        # 检查是否到达目标区域
        current_pos = get_player_position()
        if current_pos and if_in_area(area, current_pos):
            print(f"✅ 已到达目标区域！位置: {current_pos}\n")
            return True
        
        if current_pos == location:
            print(f"✅ 已到达目标位置！\n")
            return True
        
        if stat == "stuck":
            print("🔄 检测到卡住")
            return False
        
        stage = check_stage()
        if stage == "in_game_dead":
            print("💀 玩家已死亡")
            return False
        elif stage == "in_menu":
            print("📋 玩家在菜单中")
            return False
```
with:
```python
def lazy_theta_pathing(location, area=[]):
    """寻路到目标区域"""
    retry_count = 0
    max_retries = 3
    
    while True:
        pos = get_player_position()
        
        if pos is None:
            retry_count += 1
            print(f"⚠️ 无法检测玩家位置，重试 {retry_count}/{max_retries}...")
            overlay.update(state="无法检测位置", message=f"重试 {retry_count}/{max_retries}")
            if retry_count >= max_retries:
                print("❌ 多次重试失败")
                overlay.update(state="出错", message="多次重试失败")
                return False
            time.sleep(1)
            continue
        
        retry_count = 0
        print(f"\n📍 寻路: {pos} -> {location}")
        overlay.update(state="寻路中", pos=pos, target=location, message="规划路径...")
        time_now = time.time()
        
        binary_map = load_binary_map()
        if binary_map is None:
            print("❌ 地图加载失败")
            overlay.update(state="出错", message="地图加载失败")
            return False
        
        path = lazy_theta_star(binary_map, pos, location)
        print(f"⏱️  寻路耗时: {time.time() - time_now:.2f}秒")
        
        if path is None:
            print("❌ 路径规划失败")
            overlay.update(state="出错", message="路径规划失败")
            return False
        
        print(f"✅ 找到路径，共 {len(path)} 个点")
        overlay.update(message=f"找到路径, 共{len(path)}个点")
        stat = execute_path(path)
        
        # 检查是否到达目标区域
        current_pos = get_player_position()
        if current_pos and if_in_area(area, current_pos):
            print(f"✅ 已到达目标区域！位置: {current_pos}\n")
            overlay.update(state="完成", pos=current_pos, message="已到达目标区域")
            return True
        
        if current_pos == location:
            print(f"✅ 已到达目标位置！\n")
            overlay.update(state="完成", pos=current_pos, message="已到达目标位置")
            return True
        
        if stat == "stuck":
            print("🔄 检测到卡住")
            overlay.update(state="卡住", message="移动受阻")
            return False
        
        stage = check_stage()
        if stage == "in_game_dead":
            print("💀 玩家已死亡")
            overlay.update(state="已死亡")
            return False
        elif stage == "in_menu":
            print("📋 玩家在菜单中")
            overlay.update(state="菜单中")
            return False
```

- [ ] **Step 2: Verify the module still imports cleanly**

Run:
```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/status-overlay" && ./venv/bin/python -c "import main" 2>&1 | tail -20
```
Expected: no traceback.

- [ ] **Step 3: Commit**

```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/status-overlay" && git add main.py && git commit -m "feat: wire status overlay into lazy_theta_pathing"
```

Proceed to Task 6.

---

### Task 6: Wire overlay into `auto_farming` and the `__main__` block

**Files:**
- Modify: `main.py:231-323` (`auto_farming` and the `if __name__ == "__main__":` block)

**Interfaces:**
- Consumes: module-level `overlay` singleton (from Task 4).

- [ ] **Step 1: Update `auto_farming` to report state each tick**

Replace:
```python
def auto_farming(farming_area, duration=300, move_interval=2.0):
    """自动刷怪逻辑（依赖一直攻击按钮）"""
    x1, y1 = farming_area[0]
    x2, y2 = farming_area[1]
    
    min_x, max_x = min(x1, x2), max(x1, x2)
    min_y, max_y = min(y1, y2), max(y1, y2)
    farming_area = [(min_x, min_y), (max_x, max_y)]
    
    print(f"\n🎮 开始在区域 {farming_area} 进行自动刷怪...")
    print(f"⏱️  刷怪时长: {duration}秒")
    print(f"⏰ 每次停留: {move_interval}秒（一直攻击模式）\n")
    
    start_time = time.time()
    move_count = 0
    
    while time.time() - start_time < duration:
        current_pos = get_player_position()
        
        if current_pos is None:
            print("⚠️ 无法检测玩家位置")
            time.sleep(1)
            continue
        
        # 检查是否还在刷怪区域
        if not if_in_area([farming_area], current_pos):
            print(f"⚠️ 离开刷怪区域 (当前: {current_pos})，重新寻路回去")
            target_x = (farming_area[0][0] + farming_area[1][0]) // 2
            target_y = (farming_area[0][1] + farming_area[1][1]) // 2
            if not lazy_theta_pathing((target_x, target_y), [farming_area]):
                print("❌ 无法回到刷怪区域")
                break
            continue
        
        # 在区域内随机选择一个目标点
        random_x = random.randint(farming_area[0][0], farming_area[1][0])
        random_y = random.randint(farming_area[0][1], farming_area[1][1])
        
        # 移动到目标点
        print(f"🚶 移动到 ({random_x}, {random_y})")
        move_result = move_to_position(current_pos, (random_x, random_y))
        
        if move_result == "stuck":
            print("⚠️ 移动受阻")
        elif move_result in ["in_game_dead", "in_menu"]:
            print(f"⚠️ 游戏状态变化: {move_result}")
            break
        
        # 在位置停留，依赖一直攻击按钮自动攻击
        print(f"⚔️  停留 {move_interval}秒...")
        time.sleep(move_interval)
        
        move_count += 1
        
        # 检查游戏状态
        stage = check_stage()
        if stage == "in_game_dead":
            print("💀 玩家已死亡")
            break
        elif stage == "in_menu":
            print("📋 玩家在菜单中")
            break
    
    elapsed = time.time() - start_time
    print(f"\n" + "="*50)
    print(f"✅ 刷怪完成！")
    print(f"   实际耗时: {elapsed:.1f}秒")
    print(f"   移动次数: {move_count}")
    print(f"="*50)
```
with:
```python
def auto_farming(farming_area, duration=300, move_interval=2.0):
    """自动刷怪逻辑（依赖一直攻击按钮）"""
    x1, y1 = farming_area[0]
    x2, y2 = farming_area[1]
    
    min_x, max_x = min(x1, x2), max(x1, x2)
    min_y, max_y = min(y1, y2), max(y1, y2)
    farming_area = [(min_x, min_y), (max_x, max_y)]
    
    print(f"\n🎮 开始在区域 {farming_area} 进行自动刷怪...")
    print(f"⏱️  刷怪时长: {duration}秒")
    print(f"⏰ 每次停留: {move_interval}秒（一直攻击模式）\n")
    overlay.update(state="刷怪中", message=f"区域 {farming_area}")
    
    start_time = time.time()
    move_count = 0
    
    while time.time() - start_time < duration:
        current_pos = get_player_position()
        
        if current_pos is None:
            print("⚠️ 无法检测玩家位置")
            overlay.update(state="无法检测位置")
            time.sleep(1)
            continue
        
        # 检查是否还在刷怪区域
        if not if_in_area([farming_area], current_pos):
            print(f"⚠️ 离开刷怪区域 (当前: {current_pos})，重新寻路回去")
            overlay.update(state="离开刷怪区域", pos=current_pos, message="重新寻路回去")
            target_x = (farming_area[0][0] + farming_area[1][0]) // 2
            target_y = (farming_area[0][1] + farming_area[1][1]) // 2
            if not lazy_theta_pathing((target_x, target_y), [farming_area]):
                print("❌ 无法回到刷怪区域")
                overlay.update(state="出错", message="无法回到刷怪区域")
                break
            continue
        
        # 在区域内随机选择一个目标点
        random_x = random.randint(farming_area[0][0], farming_area[1][0])
        random_y = random.randint(farming_area[0][1], farming_area[1][1])
        
        # 移动到目标点
        print(f"🚶 移动到 ({random_x}, {random_y})")
        move_result = move_to_position(current_pos, (random_x, random_y))
        
        if move_result == "stuck":
            print("⚠️ 移动受阻")
        elif move_result in ["in_game_dead", "in_menu"]:
            print(f"⚠️ 游戏状态变化: {move_result}")
            break
        
        # 在位置停留，依赖一直攻击按钮自动攻击
        print(f"⚔️  停留 {move_interval}秒...")
        overlay.update(state="刷怪中", pos=(random_x, random_y), message=f"停留 {move_interval}秒 (第{move_count + 1}次)")
        time.sleep(move_interval)
        
        move_count += 1
        
        # 检查游戏状态
        stage = check_stage()
        if stage == "in_game_dead":
            print("💀 玩家已死亡")
            overlay.update(state="已死亡")
            break
        elif stage == "in_menu":
            print("📋 玩家在菜单中")
            overlay.update(state="菜单中")
            break
    
    elapsed = time.time() - start_time
    print(f"\n" + "="*50)
    print(f"✅ 刷怪完成！")
    print(f"   实际耗时: {elapsed:.1f}秒")
    print(f"   移动次数: {move_count}")
    print(f"="*50)
    overlay.update(state="完成", message=f"刷怪结束, 共移动{move_count}次")
```

- [ ] **Step 2: Update the `__main__` block to report the final outcome**

Replace:
```python
if __name__ == "__main__":
    apply_map("desert")
    
    # ===== 配置部分 =====
    location = (14, 45)
    farming_area = [(20, 15), (9, 76)]
    farming_duration = 300  # 5 分钟
    move_interval = 2.0     # 每次停留时间
    # ====================
    
    print("🎮 开始自动寻路到刷怪区域...")
    print(f"📍 目标区域: {farming_area}\n")
    
    # 寻路到目标区域
    if lazy_theta_pathing(location, [farming_area]):
        print("✅ 到达刷怪区域！")
        # 开始刷怪
        auto_farming(farming_area, farming_duration, move_interval)
    else:
        print("❌ 无法到达目标区域")
    
    print("\n🏁 脚本结束")
```
with:
```python
if __name__ == "__main__":
    apply_map("desert")
    
    # ===== 配置部分 =====
    location = (14, 45)
    farming_area = [(20, 15), (9, 76)]
    farming_duration = 300  # 5 分钟
    move_interval = 2.0     # 每次停留时间
    # ====================
    
    print("🎮 开始自动寻路到刷怪区域...")
    print(f"📍 目标区域: {farming_area}\n")
    overlay.update(state="启动", target=location, message="开始自动寻路到刷怪区域")
    
    # 寻路到目标区域
    if lazy_theta_pathing(location, [farming_area]):
        print("✅ 到达刷怪区域！")
        # 开始刷怪
        auto_farming(farming_area, farming_duration, move_interval)
    else:
        print("❌ 无法到达目标区域")
        overlay.update(state="出错", message="无法到达目标区域")
    
    print("\n🏁 脚本结束")
```

- [ ] **Step 3: Verify the module still imports cleanly**

Run:
```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/status-overlay" && ./venv/bin/python -c "import main" 2>&1 | tail -20
```
Expected: no traceback.

- [ ] **Step 4: Commit**

```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/status-overlay" && git add main.py && git commit -m "feat: wire status overlay into auto_farming and main entry point"
```

**Stop here — do not run Step 5.** Step 5 drives the real mouse/keyboard against a live florr.io session; it is a controller/human step, never delegated to an implementer subagent. Report DONE with this task's scope (Steps 1-4) complete.

- [ ] **Step 5: Full real-world verification (controller/human only, not part of the implementer's task)**

With florr.io open fullscreen 1920x1080 (per the running conditions in `README.md`):
```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/status-overlay" && ./venv/bin/python main.py
```
Expected: the overlay window is visible on top of the game at all times, and its 状态/位置/目标/消息/耗时 fields update live as pathing runs. This is the acceptance test from the spec — if the overlay is hidden behind the game, stop and discuss the pyobjc fallback (spec's Fallback section) instead of continuing to patch the tkinter approach.

- [ ] **Step 6: Mark plan complete**

No further commit needed.
