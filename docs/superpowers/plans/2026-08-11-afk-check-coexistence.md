# AFK-Check Coexistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `afk_watch.py`, a small log-tailing watcher that pauses `main.py`'s pathing loops whenever the separate `florr-auto-afk` program (running unmodified, elsewhere — typically a Windows VM) reports it has found florr.io's "AFK CHECK" popup, so the two programs never fight over the OS mouse cursor.

**Architecture:** `afk_watch.poll_afk_pause()` tails florr-auto-afk's `latest.log` for one known marker line (`EVENT: Found AFK window`), which florr-auto-afk always persists to disk the moment it detects the popup. There is no persisted "cleared" event to pair it with (confirmed by reading florr-auto-afk's source — the corresponding line is hardcoded `save=False`), so pausing is duration-based: seeing the marker opens a fixed-length pause window; time running out is what resumes pathing, not a second log line. `main.py`'s three loops (`move_to_position`, `lazy_theta_pathing`, `auto_farming`) call this at the top of each iteration, the same spot they already check `on_death_screen()`/`on_start_screen()`.

**Tech Stack:** Python stdlib only (`os`, `time`) — no new pip dependency. `pytest` (already installed in the project venv).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-11-afk-check-coexistence-design.md`.
- No YOLO/torch/ultralytics/models in this repo — florr-auto-afk (run separately, unmodified) does all the solving. This plan only adds a pause/resume watcher.
- `afk_watch.py` must never raise, regardless of whether the log file exists or `LATEST_LOG_PATH` is misconfigured — same never-crash-the-bot posture as `overlay.py`'s `_NullOverlay`.
- Default `PAUSE_SECONDS = 12`; default `LATEST_LOG_PATH = "./latest.log"` (a placeholder relative path — real deployment onto the VM requires pointing it at florr-auto-afk's actual `latest.log`; this plan does not attempt that from inside this repo).
- **Isolated worktree, matching this repo's established pattern** (see `docs/superpowers/plans/2026-08-10-status-overlay.md`'s Global Constraints for precedent): branch `afk-check-coexistence`, worktree at `/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/afk-check-coexistence`. Every `cd` in this plan targets that worktree path, never the main checkout. The venv is not copied into the worktree — Task 1 Step 1 symlinks it back to the main checkout's real `venv/` (this repo's `.gitignore` already has a `/venv` rule anticipating exactly this).
- Use `./venv/bin/python` / `./venv/bin/pip` for every command inside the worktree, never a bare `python`/`pip`.

---

### Task 1: `afk_watch.py` core + tests

**Files:**
- Create: `afk_watch.py`
- Test: `test_afk_watch.py`

**Interfaces:**
- Produces: `LATEST_LOG_PATH: str` (module attribute, reassignable), `PAUSE_SECONDS: float` (module attribute, reassignable), `poll_afk_pause() -> bool` — used by `main.py` in Task 2.

- [ ] **Step 1: Create the worktree**

Run:
```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2" && git worktree add .worktrees/afk-check-coexistence -b afk-check-coexistence
```
Expected: creates the worktree and switches the new directory to a fresh branch `afk-check-coexistence` off current `main`.

Then link the venv back to the main checkout (do not create a real venv in the worktree):
```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/afk-check-coexistence" && ln -s ../../venv venv && ./venv/bin/python -c "import pytest; print('pytest', pytest.__version__)"
```
Expected: prints `pytest 9.1.1` (or similar) — confirms the symlink resolves to the main checkout's already-provisioned venv.

- [ ] **Step 2: Write the failing tests**

Create `test_afk_watch.py`:
```python
import time

import afk_watch


def _reset(monkeypatch, log_path):
    monkeypatch.setattr(afk_watch, "LATEST_LOG_PATH", str(log_path))
    monkeypatch.setattr(afk_watch, "_last_offset", 0)
    monkeypatch.setattr(afk_watch, "_pause_until", 0.0)


def test_poll_afk_pause_false_when_log_file_missing(tmp_path, monkeypatch):
    _reset(monkeypatch, tmp_path / "does_not_exist.log")
    assert afk_watch.poll_afk_pause() is False


def test_poll_afk_pause_ignores_unrelated_lines(tmp_path, monkeypatch):
    log_path = tmp_path / "latest.log"
    log_path.write_text(
        "[2026-08-11 00:00:00] <segment.py:1> <afk_thread()> EVENT: something else\n"
    )
    _reset(monkeypatch, log_path)
    assert afk_watch.poll_afk_pause() is False


def test_poll_afk_pause_true_after_marker_line_written(tmp_path, monkeypatch):
    log_path = tmp_path / "latest.log"
    log_path.write_text(
        "[2026-08-11 00:00:00] <segment.py:127> <afk_thread()> EVENT: Found AFK window\n"
    )
    _reset(monkeypatch, log_path)
    assert afk_watch.poll_afk_pause() is True


def test_poll_afk_pause_expires_after_pause_seconds(tmp_path, monkeypatch):
    log_path = tmp_path / "latest.log"
    log_path.write_text(
        "[2026-08-11 00:00:00] <segment.py:127> <afk_thread()> EVENT: Found AFK window\n"
    )
    _reset(monkeypatch, log_path)
    monkeypatch.setattr(afk_watch, "PAUSE_SECONDS", 0.05)
    assert afk_watch.poll_afk_pause() is True
    time.sleep(0.1)
    assert afk_watch.poll_afk_pause() is False


def test_poll_afk_pause_does_not_retrigger_from_already_read_line(tmp_path, monkeypatch):
    log_path = tmp_path / "latest.log"
    log_path.write_text(
        "[2026-08-11 00:00:00] <segment.py:127> <afk_thread()> EVENT: Found AFK window\n"
    )
    _reset(monkeypatch, log_path)
    assert afk_watch.poll_afk_pause() is True
    # 暂停窗口手动过期(模拟时间流逝), 日志文件没有新行 —— 不该重新触发.
    monkeypatch.setattr(afk_watch, "_pause_until", 0.0)
    assert afk_watch.poll_afk_pause() is False


def test_poll_afk_pause_rereads_from_start_after_truncation(tmp_path, monkeypatch):
    log_path = tmp_path / "latest.log"
    log_path.write_text(
        "[2026-08-11 00:00:00] <segment.py:1> <afk_thread()> EVENT: " + ("x" * 200) + "\n"
    )
    _reset(monkeypatch, log_path)
    assert afk_watch.poll_afk_pause() is False
    # 模拟florr-auto-afk重启/日志轮转: 文件被换成更短的新内容.
    log_path.write_text(
        "[2026-08-11 00:00:01] <segment.py:127> <afk_thread()> EVENT: Found AFK window\n"
    )
    assert afk_watch.poll_afk_pause() is True
```

- [ ] **Step 3: Run tests to verify they fail**

Run:
```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/afk-check-coexistence" && ./venv/bin/python -m pytest test_afk_watch.py -v
```
Expected: FAIL / collection error — `ModuleNotFoundError: No module named 'afk_watch'`.

- [ ] **Step 4: Write the minimal implementation**

Create `afk_watch.py`:
```python
"""florr-auto-afk协同 —— 监听它写的latest.log, 检测到"发现AFK弹窗"事件后,
让本项目的寻路循环暂停一段时间, 别跟它的YOLO拖拽方案抢鼠标.

florr-auto-afk只在检测到弹窗那一刻写一条会落盘的日志(`log_ret`默认save=True):
    ... EVENT: Found AFK window
它清场后的"No AFK window found"硬编码save=False, 不管verbose开不开都不落盘,
没法拿来当"解除暂停"信号用 —— 这里只能是触发器, 不是起止对: 看到触发行就暂停
固定时长, 时间到自动恢复, 不去猜它到底解完没解完. 详见
docs/superpowers/specs/2026-08-11-afk-check-coexistence-design.md.
"""
import os
import time

# 部署时改成florr-auto-afk实际launch目录下的latest.log绝对路径(VM里那个程序的工作目录).
LATEST_LOG_PATH = "./latest.log"
# 覆盖YOLO检测+分割+拖拽执行的时间; 若在florr-auto-afk配置里关掉moveAfterAFK可以调低.
PAUSE_SECONDS = 12

_FOUND_MARKER = "EVENT: Found AFK window"

_last_offset = 0
_pause_until = 0.0


def _read_new_lines():
    """读取上次读到的位置之后新增的行. 文件比上次记录的offset还小(轮转/程序
    重启)就当成新文件, 从头重读."""
    global _last_offset
    try:
        size = os.path.getsize(LATEST_LOG_PATH)
    except OSError:
        return []
    if size < _last_offset:
        _last_offset = 0
    with open(LATEST_LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
        f.seek(_last_offset)
        lines = f.readlines()
        _last_offset = f.tell()
    return lines


def poll_afk_pause():
    """轮询一次. 发现新的"Found AFK window"事件就开一段暂停窗口; 返回当前是否
    还在暂停中. 日志文件不存在(florr-auto-afk还没启动, 或者LATEST_LOG_PATH没配对)
    时永远返回False, 不抛异常 —— 这个探测器绝不能把主程序带崩.
    """
    global _pause_until
    for line in _read_new_lines():
        if _FOUND_MARKER in line:
            _pause_until = time.time() + PAUSE_SECONDS
            print(f"⏸️  检测到florr-auto-afk发现AFK弹窗, 暂停操作{PAUSE_SECONDS}秒...")
            break
    return time.time() < _pause_until
```

- [ ] **Step 5: Run tests to verify they pass**

Run:
```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/afk-check-coexistence" && ./venv/bin/python -m pytest test_afk_watch.py -v
```
Expected: all 6 tests PASS.

- [ ] **Step 6: Commit**

```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/afk-check-coexistence" && git add afk_watch.py test_afk_watch.py && git commit -m "feat: add afk_watch log-tail pause detector"
```

Proceed to Task 2.

---

### Task 2: Wire the watcher into `main.py`'s three loops

**Files:**
- Modify: `main.py:1-6` (imports), `main.py:103-189` (`move_to_position`), `main.py:215-292` (`lazy_theta_pathing`), `main.py:313-409` (`auto_farming`)

**Interfaces:**
- Consumes: `afk_watch.poll_afk_pause()` (Task 1), module-level `overlay` singleton (already exists in `main.py`).

- [ ] **Step 1: Add the import**

In `main.py`, replace:
```python
from utils import *
from overlay import create_overlay
import time
import random

overlay = create_overlay()
```
with:
```python
from utils import *
from overlay import create_overlay
import time
import random
import afk_watch

overlay = create_overlay()
```

- [ ] **Step 2: Pause `move_to_position`'s tick loop**

Replace:
```python
    last_dist = None
    stall_count = 0
    attempts = 0
    while attempts < max_attempts:
        current_pos = get_player_position()
        if current_pos is None:
```
with:
```python
    last_dist = None
    stall_count = 0
    attempts = 0
    while attempts < max_attempts:
        if afk_watch.poll_afk_pause():
            overlay.update(state="AFK弹窗处理中", message="等待florr-auto-afk解题")
            time.sleep(0.2)
            continue

        current_pos = get_player_position()
        if current_pos is None:
```

This `continue` skips straight back to the `while` check without reaching the tick's `attempts += 1` near the bottom of the loop — a paused tick doesn't count against `stall_limit`/`max_attempts`.

- [ ] **Step 3: Pause `lazy_theta_pathing`'s loop**

Replace:
```python
    retry_count = 0

    while True:
        # 死亡/开局画面的检查必须放在最前面、且不能只在"pos is None"分支里做 ——
```
with:
```python
    retry_count = 0

    while True:
        if afk_watch.poll_afk_pause():
            overlay.update(state="AFK弹窗处理中", message="等待florr-auto-afk解题")
            time.sleep(0.2)
            continue

        # 死亡/开局画面的检查必须放在最前面、且不能只在"pos is None"分支里做 ——
```

- [ ] **Step 4: Pause `auto_farming`'s loop**

Replace:
```python
    start_time = time.time()
    move_count = 0
    exit_reason = "timeout"

    while time.time() - start_time < duration:
        # 死亡/开局画面检查放在循环最前面、不依赖"位置测不到" —— 死亡结算画面上
```
with:
```python
    start_time = time.time()
    move_count = 0
    exit_reason = "timeout"

    while time.time() - start_time < duration:
        if afk_watch.poll_afk_pause():
            overlay.update(state="AFK弹窗处理中", message="等待florr-auto-afk解题")
            time.sleep(0.2)
            continue

        # 死亡/开局画面检查放在循环最前面、不依赖"位置测不到" —— 死亡结算画面上
```

- [ ] **Step 5: Verify the module still imports cleanly**

Run:
```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/afk-check-coexistence" && ./venv/bin/python -c "import main" 2>&1 | tail -20
```
Expected: no traceback. (This briefly opens the status overlay window since `overlay = create_overlay()` runs at import time, same as before this change — it does not touch the mouse/keyboard or read any real florr-auto-afk log, since `afk_watch.LATEST_LOG_PATH` still points at a nonexistent `./latest.log` in the worktree, which `poll_afk_pause()` handles by returning `False`.)

- [ ] **Step 6: Re-run the full test suite to confirm nothing broke**

Run:
```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/afk-check-coexistence" && ./venv/bin/python -m pytest -v
```
Expected: all tests across `test_utils.py`, `test_overlay.py`, and `test_afk_watch.py` still PASS.

- [ ] **Step 7: Commit**

```bash
cd "/Users/macmima1234/Downloads/florr-auto-pathing-main 2/.worktrees/afk-check-coexistence" && git add main.py && git commit -m "feat: pause pathing loops while florr-auto-afk is solving an AFK check"
```

**Stop here — do not run Task 3.** Task 3 requires a live florr-auto-afk process and a live florr.io session; it is a controller/human step, never delegated to an implementer subagent. Report DONE with Tasks 1-2 complete.

---

### Task 3: Real-world verification (controller/human only, not part of the implementer's task)

**Files:** none in this repo — this task edits florr-auto-afk's own `config.json` (a different project, `~/florr-auto-afk` or wherever it's deployed in the VM) and runs both programs together.

- [ ] **Step 1: Configure florr-auto-afk for always-on scanning**

In florr-auto-afk's `config.json` (wherever it runs — typically inside the Windows VM), set:
```json
"runs": {
    "autoTakeOverWhenIdle": false,
    "moveAfterAFK": false
}
```
`autoTakeOverWhenIdle: false` makes it scan on a fixed interval regardless of mouse activity (its own idle-mouse gate would otherwise never fire, since florr-auto-pathing keeps the mouse moving continuously). `moveAfterAFK: false` disables its post-solve "wander a bit" — a second source of input conflict that florr-auto-pathing's own movement/anti-stuck already covers.

- [ ] **Step 2: Point `afk_watch.LATEST_LOG_PATH` at the real log**

In `afk_watch.py` (in the worktree, or wherever this repo is actually deployed to run alongside florr-auto-afk), change:
```python
LATEST_LOG_PATH = "./latest.log"
```
to the absolute path of florr-auto-afk's `latest.log` in that environment, e.g.:
```python
LATEST_LOG_PATH = "C:/florr-auto-afk/latest.log"
```
(exact path depends on wherever florr-auto-afk.exe is actually launched from in the VM — commit this change locally in that deployment, don't merge a placeholder machine-specific path back to `main`).

- [ ] **Step 3: Run both programs together**

In the VM, with florr.io fullscreen at 1920x1080:
1. Launch florr-auto-afk first, confirm its `latest.log` appears.
2. Launch `main.py` from this repo.
3. Wait for (or trigger) one real AFK check.

Expected: florr-auto-pathing's console prints the `⏸️  检测到florr-auto-afk发现AFK弹窗...` notice and the overlay shows "AFK弹窗处理中"; florr-auto-afk solves the popup without the character's mouse-steering fighting the drag; pathing resumes on its own once the pause window elapses.

- [ ] **Step 4: Tune `PAUSE_SECONDS` from what was actually observed**

If pathing resumed while florr-auto-afk was still mid-drag (`PAUSE_SECONDS` too short), or the bot sat idle well past the point the popup was already gone (too long), adjust the constant in `afk_watch.py` based on the real observed timing from Step 3 — don't keep guessing without a real timestamp to anchor it.

- [ ] **Step 5: Mark plan complete**

Once Steps 1-4 look right in practice, this feature is done. Merging the `afk-check-coexistence` branch back into `main` (and removing the worktree) is a separate step — use the `superpowers:finishing-a-development-branch` skill for that once verification passes.
