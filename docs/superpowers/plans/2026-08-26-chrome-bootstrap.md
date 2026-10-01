# exe自动拉起专用Chrome Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `main.py`启动时自动完成整套Chrome准备工作(退出现有Chrome、拉起带CDP参数的专用Chrome、引导用户迁移账号+手动打开florr.io、点击确认后放行), 让打包出来的`.exe`不再要求用户手动敲命令行.

**Architecture:** [cdp_bridge.py](../../../cdp_bridge.py)新增`launch_dedicated_chrome()`编排"确认关闭现有Chrome→强制退出→拉起专用空白实例→提示账号迁移+回车→轮询CDP找到florr.io标签页"整条链路; [overlay.py](../../../overlay.py)新增一个真能点击的确认对话框(区别于现有点击穿透的`StatusOverlay`), 阻塞等用户手动全屏后点击"开始运行"; `main.py`在`if __name__ == "__main__":`最开头依次调用这两个新入口.

**Tech Stack:** Python 3.11, `subprocess`(拉起/退出Chrome), 已有的`cdp_bridge.find_florr_tab()`(CDP轮询), `pyobjc-framework-Cocoa`(mac确认弹窗, 复用`StatusOverlay`已验证的跨Space技巧)、`tkinter`(Windows确认弹窗).

设计文档: [docs/superpowers/specs/2026-08-26-chrome-bootstrap-design.md](2026-08-26-chrome-bootstrap-design.md)

## Global Constraints

- 不自动导航到florr.io — 用户在专用Chrome里自己迁移账号、手动打开florr.io.
- 不自动按F11进全屏 — 用户自己进全屏, 脚本只提供一个点击确认.
- 不加"跳过自动拉起"的配置开关 — 保持单一路径.
- Chrome找不到时清楚报错+安装引导, 不静默失败; 其余步骤(退出现有Chrome、轮询florr.io标签页)失败/超时都不能崩主程序, 要么本来就该忽略(退出时Chrome没在跑), 要么循环重试(轮询超时打印提示回去重新等用户确认).
- 专用Chrome的`--user-data-dir`固定持久目录(跟可执行文件同级的`chrome-profile/`), 不是每次运行都清空重来.
- 悬浮确认弹窗建不出来(缺pyobjc/tkinter/不支持的平台)一律退化成控制台`input()`确认, 绝不崩主程序 — 跟现有`create_overlay()`的`_NullOverlay`降级哲学完全一致.
- 中文提示文案面向不懂命令行的用户, 全程只需要回车/点按钮.

---

## Task 1: `cdp_bridge.py` — Windows Chrome路径查找 + 强制退出所有Chrome

**Files:**
- Modify: `cdp_bridge.py` (顶部import区域, `CDP_PORT`常量下方新增)
- Test: `test_cdp_bridge.py`

**Interfaces:**
- Produces:
  - `_WINDOWS_CHROME_CANDIDATES: list[str]` — 模块级常量, Windows几个常见Chrome安装路径.
  - `_find_windows_chrome() -> str | None` — 按候选路径挨个试, 返回第一个存在的, 都没有返回`None`.
  - `_quit_all_chrome() -> None` — 按平台强制退出所有Chrome进程, 本来没在跑不算失败.

- [ ] **Step 1: 写失败测试 — `_find_windows_chrome()`**

在`test_cdp_bridge.py`现有import基础上补充(整个文件顶部import改成下面这样, 新增私有函数一起导入):

```python
import json
from unittest.mock import patch, MagicMock
from urllib.error import URLError

import pytest

import cdp_bridge
from cdp_bridge import (
    find_florr_tab, eval_js, capture_screenshot,
    _find_windows_chrome, _quit_all_chrome,
)
```

在文件末尾新增:

```python
def test_find_windows_chrome_returns_none_when_no_candidate_exists():
    with patch("cdp_bridge.os.path.isfile", return_value=False):
        assert _find_windows_chrome() is None


def test_find_windows_chrome_returns_first_existing_candidate():
    existing = cdp_bridge._WINDOWS_CHROME_CANDIDATES[1]
    with patch("cdp_bridge.os.path.isfile", side_effect=lambda p: p == existing):
        assert _find_windows_chrome() == existing
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest test_cdp_bridge.py -k find_windows_chrome -v`
Expected: FAIL — `ImportError: cannot import name '_find_windows_chrome'`(函数还不存在)

- [ ] **Step 3: 在`cdp_bridge.py`加imports和`_find_windows_chrome()`**

模块顶部import区域(第37-42行左右)改成:

```python
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

import websocket

CDP_HOST = "127.0.0.1"
CDP_PORT = 9222

_WINDOWS_CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]


def _find_windows_chrome():
    """按几个常见安装路径挨个试, 都没有就返回None(调用方负责报错文案)."""
    for path in _WINDOWS_CHROME_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None
```

(`find_florr_tab()`等原有函数不变, 紧接着写在后面.)

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest test_cdp_bridge.py -k find_windows_chrome -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 写失败测试 — `_quit_all_chrome()`**

```python
def test_quit_all_chrome_calls_taskkill_on_windows(monkeypatch):
    monkeypatch.setattr(cdp_bridge.sys, "platform", "win32")
    with patch("cdp_bridge.subprocess.run") as mock_run, \
         patch("cdp_bridge.time.sleep"):
        _quit_all_chrome()
    mock_run.assert_called_once_with(
        ["taskkill", "/IM", "chrome.exe", "/F"], capture_output=True
    )


def test_quit_all_chrome_calls_osascript_on_macos(monkeypatch):
    monkeypatch.setattr(cdp_bridge.sys, "platform", "darwin")
    with patch("cdp_bridge.subprocess.run") as mock_run, \
         patch("cdp_bridge.time.sleep"):
        _quit_all_chrome()
    mock_run.assert_called_once_with(
        ["osascript", "-e", 'quit app "Google Chrome"'], capture_output=True
    )
```

（这两个用例还需要把`_quit_all_chrome`加进上面Step 1的import列表里.）

- [ ] **Step 6: 跑测试确认失败**

Run: `venv/bin/python -m pytest test_cdp_bridge.py -k quit_all_chrome -v`
Expected: FAIL — `ImportError: cannot import name '_quit_all_chrome'`

- [ ] **Step 7: 实现`_quit_all_chrome()`**

紧接在`_find_windows_chrome()`后面加:

```python
def _quit_all_chrome():
    """强制退出所有Chrome进程 —— 专用CDP实例要求Chrome完全重启后新参数才生效
    (见模块文档). 本来就没在跑不算失败, 静默吞掉非零退出码."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/IM", "chrome.exe", "/F"], capture_output=True)
    elif sys.platform == "darwin":
        subprocess.run(["osascript", "-e", 'quit app "Google Chrome"'], capture_output=True)
    time.sleep(1)  # 给进程真正退出、释放profile锁一点时间, 避免新实例抢锁失败
```

- [ ] **Step 8: 跑测试确认通过**

Run: `venv/bin/python -m pytest test_cdp_bridge.py -v`
Expected: 全部PASS(包括原有用例, 确认没有破坏现有测试)

- [ ] **Step 9: Commit**

```bash
git add cdp_bridge.py test_cdp_bridge.py
git commit -m "feat: add Windows Chrome discovery and quit-all helpers to cdp_bridge"
```

---

## Task 2: `cdp_bridge.py` — 拉起专用Chrome + 轮询florr.io标签页 + 整条引导链路

**Files:**
- Modify: `cdp_bridge.py`
- Test: `test_cdp_bridge.py`

**Interfaces:**
- Consumes: `_find_windows_chrome() -> str | None`, `_quit_all_chrome() -> None`(Task 1), `find_florr_tab() -> dict | None`(已有), `CDP_PORT`(已有)
- Produces:
  - `_CHROME_PROFILE_DIR: str` — 模块级常量, 持久化profile目录路径.
  - `_launch_chrome_process() -> None` — 带CDP参数拉起专用Chrome, 找不到可执行文件抛`RuntimeError`.
  - `_poll_for_florr_tab(timeout, interval=1) -> dict | None` — 轮询直到找到florr.io标签页或超时.
  - `launch_dedicated_chrome() -> None` — 整条引导链路, `main.py`调用的公开入口.

- [ ] **Step 1: 写失败测试 — `_launch_chrome_process()`**

import列表补充`_launch_chrome_process`:

```python
def test_launch_chrome_process_raises_when_windows_chrome_not_found(monkeypatch):
    monkeypatch.setattr(cdp_bridge.sys, "platform", "win32")
    with patch("cdp_bridge._find_windows_chrome", return_value=None):
        with pytest.raises(RuntimeError, match="没找到Chrome"):
            _launch_chrome_process()


def test_launch_chrome_process_windows_passes_correct_args(monkeypatch):
    monkeypatch.setattr(cdp_bridge.sys, "platform", "win32")
    chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    with patch("cdp_bridge._find_windows_chrome", return_value=chrome_path), \
         patch("cdp_bridge.subprocess.Popen") as mock_popen:
        _launch_chrome_process()
    called_args = mock_popen.call_args[0][0]
    assert called_args[0] == chrome_path
    assert f"--remote-debugging-port={cdp_bridge.CDP_PORT}" in called_args
    assert "--remote-allow-origins=*" in called_args
    assert any(a.startswith("--user-data-dir=") for a in called_args)
    assert "--no-first-run" in called_args
    assert "--no-default-browser-check" in called_args


def test_launch_chrome_process_macos_uses_open_dash_a(monkeypatch):
    monkeypatch.setattr(cdp_bridge.sys, "platform", "darwin")
    with patch("cdp_bridge.subprocess.Popen") as mock_popen:
        _launch_chrome_process()
    called_args = mock_popen.call_args[0][0]
    assert called_args[:4] == ["open", "-a", "Google Chrome", "--args"]
    assert f"--remote-debugging-port={cdp_bridge.CDP_PORT}" in called_args
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest test_cdp_bridge.py -k launch_chrome_process -v`
Expected: FAIL — `ImportError: cannot import name '_launch_chrome_process'`

- [ ] **Step 3: 实现`_CHROME_PROFILE_DIR` + `_launch_chrome_process()`**

在`_WINDOWS_CHROME_CANDIDATES`定义前面加(放在`CDP_PORT = 9222`下面):

```python
_CHROME_PROFILE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(sys.argv[0])), "chrome-profile"
)
# sys.argv[0]: 打包成exe后是exe自己的路径, 脚本模式下是main.py的路径 —— 两种
# 情况都想要"跟可执行文件同级". 不用sys.executable(那是python解释器本身的
# 路径, 脚本模式下跟main.py不在同一目录, 只有frozen模式才等于exe路径, 两种
# 场景表现不一致), sys.argv[0]在两种场景下语义更一致.
```

`_quit_all_chrome()`后面加:

```python
def _launch_chrome_process():
    """带三个CDP参数 + 持久独立profile拉起一个全新空白Chrome窗口. 找不到Chrome
    可执行文件(仅Windows需要按路径找; macOS靠`open -a`按应用名找, 找不到时
    `open`自己会报错, 不用额外检测)时抛RuntimeError, 带清楚的安装引导."""
    args = [
        f"--remote-debugging-port={CDP_PORT}",
        "--remote-allow-origins=*",
        f"--user-data-dir={_CHROME_PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if sys.platform == "win32":
        chrome_path = _find_windows_chrome()
        if chrome_path is None:
            raise RuntimeError("没找到Chrome, 请先安装: https://www.google.com/chrome/")
        subprocess.Popen([chrome_path] + args)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-a", "Google Chrome", "--args"] + args)
    else:
        raise RuntimeError(f"不支持的平台: {sys.platform}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest test_cdp_bridge.py -k launch_chrome_process -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 写失败测试 — `_poll_for_florr_tab()`**

```python
def test_poll_for_florr_tab_returns_tab_immediately_when_found():
    fake_tab = {"url": "https://florr.io/", "id": "1"}
    with patch("cdp_bridge.find_florr_tab", return_value=fake_tab), \
         patch("cdp_bridge.time.sleep") as mock_sleep:
        result = _poll_for_florr_tab(timeout=5)
    assert result == fake_tab
    mock_sleep.assert_not_called()


def test_poll_for_florr_tab_returns_none_after_timeout():
    with patch("cdp_bridge.find_florr_tab", return_value=None), \
         patch("cdp_bridge.time.sleep"):
        result = _poll_for_florr_tab(timeout=0.05, interval=0.01)
    assert result is None


def test_poll_for_florr_tab_retries_until_found():
    with patch("cdp_bridge.find_florr_tab", side_effect=[None, None, {"url": "https://florr.io/"}]), \
         patch("cdp_bridge.time.sleep") as mock_sleep:
        result = _poll_for_florr_tab(timeout=5, interval=1)
    assert result is not None
    assert mock_sleep.call_count == 2
```

- [ ] **Step 6: 跑测试确认失败**

Run: `venv/bin/python -m pytest test_cdp_bridge.py -k poll_for_florr_tab -v`
Expected: FAIL — `ImportError`

- [ ] **Step 7: 实现`_poll_for_florr_tab()`**

`_launch_chrome_process()`后面加:

```python
def _poll_for_florr_tab(timeout, interval=1):
    """每隔interval秒查一次find_florr_tab(), 直到找到或超时(超时返回None,
    不抛异常 —— 调用方决定要不要重试)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        tab = find_florr_tab()
        if tab is not None:
            return tab
        time.sleep(interval)
    return None
```

- [ ] **Step 8: 跑测试确认通过**

Run: `venv/bin/python -m pytest test_cdp_bridge.py -k poll_for_florr_tab -v`
Expected: PASS (3 passed)

- [ ] **Step 9: 写失败测试 — `launch_dedicated_chrome()`**

```python
def test_launch_dedicated_chrome_happy_path_calls_everything_once():
    with patch("builtins.input", return_value="") as mock_input, \
         patch("cdp_bridge._quit_all_chrome") as mock_quit, \
         patch("cdp_bridge._launch_chrome_process") as mock_launch, \
         patch("cdp_bridge._poll_for_florr_tab", return_value={"url": "https://florr.io/"}) as mock_poll:
        cdp_bridge.launch_dedicated_chrome()
    mock_quit.assert_called_once()
    mock_launch.assert_called_once()
    mock_poll.assert_called_once_with(timeout=15)
    assert mock_input.call_count == 2  # 一次关闭确认 + 一次"已打开florr.io"确认


def test_launch_dedicated_chrome_retries_when_tab_not_found_yet():
    with patch("builtins.input", return_value="") as mock_input, \
         patch("cdp_bridge._quit_all_chrome"), \
         patch("cdp_bridge._launch_chrome_process"), \
         patch("cdp_bridge._poll_for_florr_tab", side_effect=[None, {"url": "https://florr.io/"}]) as mock_poll:
        cdp_bridge.launch_dedicated_chrome()
    assert mock_poll.call_count == 2
    assert mock_input.call_count == 3  # 关闭确认 + 2次"已打开florr.io"确认(第一次没找到, 重试一次)
```

- [ ] **Step 10: 跑测试确认失败**

Run: `venv/bin/python -m pytest test_cdp_bridge.py -k launch_dedicated_chrome -v`
Expected: FAIL — `AttributeError: module 'cdp_bridge' has no attribute 'launch_dedicated_chrome'`

- [ ] **Step 11: 实现`launch_dedicated_chrome()`**

`_poll_for_florr_tab()`后面加:

```python
def launch_dedicated_chrome():
    """整条"准备专用Chrome"引导链路, main.py启动时调用一次. 面向不懂命令行的
    用户, 全程只需要回车 —— 没有任何一步要求手动敲参数."""
    input(
        "⚠️ 即将关闭所有Chrome窗口以启动专用实例(未保存的标签页/内容会丢失).\n"
        "   按回车继续, Ctrl+C取消: "
    )
    _quit_all_chrome()
    _launch_chrome_process()

    while True:
        input(
            "\n🌐 专用Chrome已启动. 请在这个新窗口里把你的florr账号迁移过来,"
            "\n   迁移完成后打开florr.io, 回到这里按回车继续: "
        )
        if _poll_for_florr_tab(timeout=15) is not None:
            return
        print("   还没检测到florr.io标签页, 确认已经在那个新Chrome窗口里打开florr.io了? 重试一次.")
```

- [ ] **Step 12: 跑测试确认通过**

Run: `venv/bin/python -m pytest test_cdp_bridge.py -v`
Expected: 全部PASS

- [ ] **Step 13: Commit**

```bash
git add cdp_bridge.py test_cdp_bridge.py
git commit -m "feat: add launch_dedicated_chrome() bootstrap flow to cdp_bridge"
```

---

## Task 3: `overlay.py` — Mac确认弹窗(真能点击, 区别于StatusOverlay)

**Files:**
- Modify: `overlay.py`
- Test: `test_overlay.py`

**Interfaces:**
- Consumes: `AppKit`/`Foundation`(已有的try/except导入), `_SCREENSAVER_LEVEL`/`_BG_COLOR`(已有常量)
- Produces:
  - `_CONFIRM_WIDTH`, `_CONFIRM_HEIGHT`, `_CONFIRM_MESSAGE`, `_CONFIRM_BUTTON_LABEL` — 模块级常量.
  - `_ConfirmButtonTarget`(仅`Foundation is not None`时定义) — NSObject子类, 桥接按钮点击回Python回调.
  - `_MacConfirmDialog` — `__init__()`建窗+按钮, `wait_for_confirm()`阻塞到点击.

**先验证过的关键技术点(已在本机跑通, 不是猜测):** `NSButton.performClick_(None)`能在没有真实鼠标事件的情况下触发`target`/`action`回调, 这也是本任务测试里"模拟点击"的手段.

- [ ] **Step 1: 写失败测试**

`test_overlay.py`顶部已有`import overlay as overlay_module`, 不用改import. 文件末尾新增:

```python
def test_mac_confirm_dialog_click_sets_confirmed_and_wait_returns():
    dialog = overlay_module._MacConfirmDialog()
    assert dialog._confirmed is False
    dialog._button.performClick_(None)  # 跟真实鼠标点击走同一条target/action路径
    assert dialog._confirmed is True
    dialog.wait_for_confirm()  # 已经confirmed了, 应该立刻返回(不阻塞)并关闭窗口
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest test_overlay.py -k mac_confirm_dialog -v`
Expected: FAIL — `AttributeError: module 'overlay' has no attribute '_MacConfirmDialog'`

- [ ] **Step 3: 实现`_ConfirmButtonTarget`和`_MacConfirmDialog`**

在`overlay.py`里`_BG_COLOR = (1.0, 0.6, 0.0, 0.92)`那行后面(`class StatusOverlay:`之前)加:

```python
_CONFIRM_WIDTH, _CONFIRM_HEIGHT = 340, 150
_CONFIRM_MESSAGE = "florr.io已就绪 — 手动进入全屏(F11)后点击下方按钮开始"
_CONFIRM_BUTTON_LABEL = "开始运行"


if Foundation is not None:
    class _ConfirmButtonTarget(Foundation.NSObject):
        """桥接NSButton点击事件回Python回调. 必须真的subclass NSObject, 类
        定义本身就引用了Foundation —— 所以这个class只能在Foundation可用时
        定义(Windows上pyobjc压根没装, Foundation是None, 定义这个class会在
        import overlay.py时就报AttributeError, 必须用if守住, 不能让整个模块
        导入失败, 拖累Windows上本来能正常工作的_WindowsOverlay)."""

        def setCallback_(self, callback):
            self._callback = callback

        def buttonClicked_(self, sender):
            if getattr(self, "_callback", None) is not None:
                self._callback()
else:
    _ConfirmButtonTarget = None
```

紧接着, `class StatusOverlay:`那整个类定义结束之后(在`# --- Win32扩展窗口样式...`那行注释之前)加:

```python
class _MacConfirmDialog:
    """真能点击的确认弹窗 —— 跟StatusOverlay不同, 不设ignoresMouseEvents_(那个
    是给"不能抢游戏焦点"的状态HUD用的; 这个就是要能点). 复用StatusOverlay已经
    验证过的跨Space技巧(screensaver层级+collectionBehavior), 保证florr.io进了
    原生全屏Space之后这个弹窗依然显示在最上层、能点."""

    def __init__(self):
        self._confirmed = False

        app = AppKit.NSApplication.sharedApplication()
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        self._app = app

        screen_frame = AppKit.NSScreen.mainScreen().frame()
        x = (screen_frame.size.width - _CONFIRM_WIDTH) / 2
        y = (screen_frame.size.height - _CONFIRM_HEIGHT) / 2
        rect = Foundation.NSMakeRect(x, y, _CONFIRM_WIDTH, _CONFIRM_HEIGHT)

        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, AppKit.NSWindowStyleMaskBorderless, AppKit.NSBackingStoreBuffered, False,
        )
        window.setLevel_(_SCREENSAVER_LEVEL)
        window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorStationary
        )
        window.setOpaque_(False)
        window.setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(*_BG_COLOR)
        )
        self._window = window

        content = window.contentView()

        label = AppKit.NSTextField.alloc().initWithFrame_(
            Foundation.NSMakeRect(12, _CONFIRM_HEIGHT - 90, _CONFIRM_WIDTH - 24, 70)
        )
        label.setStringValue_(_CONFIRM_MESSAGE)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setFont_(AppKit.NSFont.systemFontOfSize_(13))
        label.setTextColor_(AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.15, 1.0))
        content.addSubview_(label)

        # 保留target的强引用(self._target) —— PyObjC不会自动帮Python侧保住这个
        # 对象, target提前被GC掉的话setTarget_指向的就是悬空对象, 点击不会
        # 触发任何反应(没有异常, 静默不响应, 很难查).
        self._target = _ConfirmButtonTarget.alloc().init()
        self._target.setCallback_(self._on_confirmed)

        button = AppKit.NSButton.alloc().initWithFrame_(
            Foundation.NSMakeRect((_CONFIRM_WIDTH - 140) / 2, 16, 140, 32)
        )
        button.setTitle_(_CONFIRM_BUTTON_LABEL)
        button.setBezelStyle_(AppKit.NSBezelStyleRounded)
        button.setTarget_(self._target)
        button.setAction_("buttonClicked:")
        content.addSubview_(button)
        self._button = button

        window.orderFrontRegardless()
        self._pump_events()

    def _on_confirmed(self):
        self._confirmed = True

    def _pump_events(self):
        while True:
            event = self._app.nextEventMatchingMask_untilDate_inMode_dequeue_(
                AppKit.NSEventMaskAny,
                Foundation.NSDate.dateWithTimeIntervalSinceNow_(0),
                AppKit.NSDefaultRunLoopMode,
                True,
            )
            if event is None:
                break
            self._app.sendEvent_(event)

    def wait_for_confirm(self):
        while not self._confirmed:
            self._pump_events()
            time.sleep(0.05)
        self._window.close()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest test_overlay.py -v`
Expected: 全部PASS(包括原有用例)

- [ ] **Step 5: Commit**

```bash
git add overlay.py test_overlay.py
git commit -m "feat: add clickable macOS fullscreen-confirm dialog to overlay"
```

---

## Task 4: `overlay.py` — Windows确认弹窗 + `show_fullscreen_confirm()`平台分发

**Files:**
- Modify: `overlay.py`
- Test: `test_overlay.py`

**Interfaces:**
- Consumes: `tk`(已有的try/except导入), `_BG_HEX`/`_FG_HEX`(已有常量), `_CONFIRM_WIDTH`等(Task 3), `_MacConfirmDialog`(Task 3)
- Produces:
  - `_WindowsConfirmDialog` — `__init__()`建tkinter窗+按钮, `wait_for_confirm()`阻塞到点击.
  - `show_fullscreen_confirm() -> None` — 公开入口, 平台分发 + 建不出来时退化成控制台`input()`确认.

**已知限制**: `_WindowsConfirmDialog`只依赖标准`tkinter`(不像`_WindowsOverlay`还要`ctypes.windll`), 但这台Mac开发机的Python(Homebrew `python@3.11`)没装`_tkinter`, 本任务测试覆盖不到这个类的真实构造(跟仓库里已有的`_WindowsOverlay`一样, 现有测试也没覆盖它的真实构造) —— 只能靠真机(Windows)手动验证, 见Task 5末尾的手动验证清单.

- [ ] **Step 1: 写失败测试(先测能覆盖到的部分: mac分支的真实fallback行为)**

```python
def test_show_fullscreen_confirm_falls_back_to_console_when_appkit_is_none(monkeypatch):
    monkeypatch.setattr(overlay_module, "AppKit", None)
    with patch("builtins.input", return_value="") as mock_input:
        overlay_module.show_fullscreen_confirm()
    mock_input.assert_called_once()


def test_show_fullscreen_confirm_falls_back_to_console_when_mac_dialog_construction_fails(monkeypatch):
    def raise_error(*args, **kwargs):
        raise RuntimeError("no display")

    monkeypatch.setattr(overlay_module, "_MacConfirmDialog", raise_error)
    with patch("builtins.input", return_value="") as mock_input:
        overlay_module.show_fullscreen_confirm()
    mock_input.assert_called_once()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest test_overlay.py -k show_fullscreen_confirm -v`
Expected: FAIL — `AttributeError: module 'overlay' has no attribute 'show_fullscreen_confirm'`

- [ ] **Step 3: 实现`_WindowsConfirmDialog`**

在`overlay.py`里, `_WindowsOverlay`类定义结束之后、`def create_overlay():`之前加:

```python
class _WindowsConfirmDialog:
    """真能点击的确认弹窗. Windows不需要mac那套跨Space hack(F11全屏不换Space,
    见_WindowsOverlay类文档开头那段说明), 也不需要_WindowsOverlay的win32点击
    穿透样式 —— 这个窗口就是要能点的, 普通tkinter -topmost就够."""

    def __init__(self):
        self._confirmed = False

        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=_BG_HEX)

        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        x = (screen_w - _CONFIRM_WIDTH) // 2
        y = (screen_h - _CONFIRM_HEIGHT) // 2
        root.geometry(f"{_CONFIRM_WIDTH}x{_CONFIRM_HEIGHT}+{x}+{y}")
        self._root = root

        label = tk.Label(
            root, text=_CONFIRM_MESSAGE, bg=_BG_HEX, fg=_FG_HEX,
            font=("Segoe UI", 11), wraplength=_CONFIRM_WIDTH - 24, justify="center",
        )
        label.pack(pady=(20, 10))

        button = tk.Button(root, text=_CONFIRM_BUTTON_LABEL, command=self._on_confirmed)
        button.pack(pady=10)
        self._button = button

        root.update_idletasks()
        root.update()

    def _on_confirmed(self):
        self._confirmed = True

    def wait_for_confirm(self):
        while not self._confirmed:
            self._root.update_idletasks()
            self._root.update()
            time.sleep(0.05)
        self._root.destroy()
```

- [ ] **Step 4: 实现`show_fullscreen_confirm()`**

`create_overlay()`函数后面(整个函数结束、文件`if __name__ == "__main__":`之前)加:

```python
def _console_fallback(reason):
    print(f"⚠️ 确认弹窗启动失败: {reason}, 改用控制台确认", file=sys.stderr)
    input("请手动进入全屏后按回车继续: ")


def show_fullscreen_confirm():
    """弹一个真能点击的确认对话框, 阻塞到用户点击"开始运行"为止. 悬浮窗建不
    出来就退化成控制台input()确认, 绝不崩主程序 —— 跟create_overlay()的
    _NullOverlay降级哲学一致."""
    if _IS_MACOS:
        if AppKit is None:
            return _console_fallback("pyobjc(AppKit) 不可用")
        try:
            _MacConfirmDialog().wait_for_confirm()
            return
        except Exception as e:
            return _console_fallback(str(e))

    if _IS_WINDOWS:
        if tk is None:
            return _console_fallback("tkinter 不可用")
        try:
            _WindowsConfirmDialog().wait_for_confirm()
            return
        except Exception as e:
            return _console_fallback(str(e))

    return _console_fallback(f"不支持的平台 {sys.platform}")
```

- [ ] **Step 5: 跑测试确认通过**

Run: `venv/bin/python -m pytest test_overlay.py -v`
Expected: 全部PASS

- [ ] **Step 6: Commit**

```bash
git add overlay.py test_overlay.py
git commit -m "feat: add Windows fullscreen-confirm dialog and show_fullscreen_confirm() dispatch"
```

---

## Task 5: `main.py`接入 + 文档更新

**Files:**
- Modify: `main.py:1-7`(import区域), `main.py:516`(`if __name__ == "__main__":`开头)
- Modify: `README.md`(Implements小节那句fullscreen前提说明)
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `cdp_bridge.launch_dedicated_chrome()`(Task 2), `overlay.show_fullscreen_confirm()`(Task 4)

- [ ] **Step 1: 改`main.py`顶部import**

当前([main.py:1-7](../../../main.py)):

```python
from utils import *
from overlay import create_overlay
import time
import random
import afk_watch
import enemy_detect
```

改成:

```python
from utils import *
from overlay import create_overlay, show_fullscreen_confirm
import cdp_bridge
import time
import random
import afk_watch
import enemy_detect
```

- [ ] **Step 2: 在`main.py`的`if __name__ == "__main__":`开头插入两行调用**

当前([main.py:516-517](../../../main.py)):

```python
if __name__ == "__main__":
    apply_map("desert")
```

改成:

```python
if __name__ == "__main__":
    cdp_bridge.launch_dedicated_chrome()
    show_fullscreen_confirm()

    apply_map("desert")
```

- [ ] **Step 3: 跑现有smoke test确认import没炸**

Run: `venv/bin/python -m pytest test_main_smoke.py -v`
Expected: PASS — `import main`不会触发`if __name__=="__main__":`里的代码(guard擋住了), 只验证新增的两行import本身不出错.

- [ ] **Step 4: 跑全量测试套件确认没有破坏任何东西**

Run: `venv/bin/python -m pytest -v`
Expected: 全部PASS(除了本来就跳过的、依赖`models/desert.pt`真实存在的那几个)

- [ ] **Step 5: 改`README.md`**

找到([README.md](../../../README.md))里这句:

```
You need to run this code with florr.io tab on the top and fullscreen (any resolution).

Go run `main.py`. To package it as a standalone Windows `.exe` instead, see [PACKAGING.md](PACKAGING.md).
```

改成:

```
`main.py`启动时会自动引导你准备好一个专用Chrome(退出现有Chrome、拉起新实例、
提示迁移账号并打开florr.io) —— 全程只需要按回车/点确认按钮, 不用手动敲命令行.
florr.io本身仍然需要全屏(任意分辨率), 但进全屏是流程里最后一步手动操作,
点了确认按钮之后自动开始寻路/刷怪.

Go run `main.py`. To package it as a standalone Windows `.exe` instead, see [PACKAGING.md](PACKAGING.md).
```

- [ ] **Step 6: 改`.gitignore`**

在[.gitignore](../../../.gitignore)里`dist/`那一段附近加一行:

```
chrome-profile/
```

- [ ] **Step 7: Commit**

```bash
git add main.py README.md .gitignore
git commit -m "feat: wire dedicated-Chrome bootstrap into main.py entrypoint"
```

- [ ] **Step 8: 手动验证清单(不是自动化测试, 是给实现者/用户的真机检查表)**

在能跑真实florr.io的机器上(至少Mac开发机跑一遍, **Windows是真正的部署目标, 必须再由用户在Windows上跑一遍**, 见[[windows-is-real-deployment]]):

1. `venv/bin/python main.py`(或Windows下打包好的`.exe`), 确认看到"⚠️ 即将关闭所有Chrome窗口..."提示, 按回车.
2. 确认现有Chrome窗口真的被关掉了, 一个全新空白Chrome窗口弹出来.
3. 确认看到"🌐 专用Chrome已启动. 请...迁移账号..."提示. 在新Chrome里随便打开一个网站(不是florr.io), 按回车 —— 确认打印"还没检测到florr.io标签页..."并循环回去重新等.
4. 在新Chrome里打开florr.io, 按回车 —— 确认这次通过, 继续往下走.
5. 确认弹出确认对话框(mac上应该是能点的悬浮窗, Windows上是tkinter小窗口), 文案是"florr.io已就绪 — 手动进入全屏(F11)后点击下方按钮开始".
6. 手动按F11进全屏, 点"开始运行"按钮 —— 确认对话框关闭, 寻路/刷怪主循环开始跑(能看到`main.py`原有的"🎮 开始自动寻路+刷怪..."打印).
7. 关掉脚本, 重新跑一遍 —— 确认第2步"退出所有Chrome"这次连专用Chrome自己也被关掉重开了(而不是复用旧的), 且第4步florr.io账号状态还在(因为`chrome-profile/`是持久目录, 不是每次清空).

---

## Self-Review

**Spec覆盖检查:**
- ✅ 确认关闭现有Chrome(回车阻塞) — Task 2 Step 11.
- ✅ 强制退出所有Chrome(taskkill/osascript) — Task 1.
- ✅ 拉起专用空白Chrome(5个参数, 持久profile) — Task 2.
- ✅ 账号迁移提示 + 轮询florr.io标签页 + 失败重试循环 — Task 2 Step 11.
- ✅ 真能点击的确认弹窗(mac真实实现+测试, windows实现+已知的本机测试限制) — Task 3, Task 4.
- ✅ `show_fullscreen_confirm()`平台分发 + 控制台兜底 — Task 4.
- ✅ `main.py`接入点 — Task 5.
- ✅ 文档更新(README措辞、.gitignore) — Task 5, spec里提到的"顺带更新的文档"两项都覆盖了.

**Placeholder扫描:** 无TBD/TODO, 每个Step都有完整可运行代码, 无"类似Task N"式的引用.

**类型/签名一致性检查:** `_poll_for_florr_tab(timeout, interval=1)`在Task 2定义, Task 2自己的`launch_dedicated_chrome()`调用`_poll_for_florr_tab(timeout=15)`(用默认`interval=1`), 签名一致. `_MacConfirmDialog`/`_WindowsConfirmDialog`都提供`wait_for_confirm()`(无参数, 无返回值)这个统一接口, `show_fullscreen_confirm()`两个分支调用方式完全对称. `_CONFIRM_WIDTH`/`_CONFIRM_HEIGHT`/`_CONFIRM_MESSAGE`/`_CONFIRM_BUTTON_LABEL`四个常量在Task 3定义、Task 4的`_WindowsConfirmDialog`直接复用, 没有重复定义或改名.
