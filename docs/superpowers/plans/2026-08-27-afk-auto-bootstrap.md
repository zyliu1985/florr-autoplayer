# 自动准备florr-auto-afk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `main.py`启动时自动确保florr-auto-afk(独立的AFK弹窗自动处理程序)在本地可用并已打开——没有就问要不要下载, 下载/已有都会打开它并提醒用户点"run"按钮, 全程不阻塞主流程, 且只在Windows上做。

**Architecture:** [afk_watch.py](../../../afk_watch.py)的`LATEST_LOG_PATH`从硬编码个人路径改成基于`sys.argv[0]`计算得出(跟`cdp_bridge.py`的`_CHROME_PROFILE_DIR`同一个套路); 新增`ensure_florr_auto_afk_running()`编排"检查→(经确认)下载解压→打开"整条链路; `main.py`在`if __name__ == "__main__":`里`launch_dedicated_chrome()`之后调用一次。

**Tech Stack:** Python stdlib(`urllib.request`+`zipfile`+`ssl`), `certifi`(已是项目依赖, 复用`server_lookup.py`已验证过的SSLContext模式)。

设计文档: [docs/superpowers/specs/2026-08-27-afk-auto-bootstrap-design.md](2026-08-27-afk-auto-bootstrap-design.md)

## Global Constraints

- 整套逻辑(检查/下载/解压/打开)只在`sys.platform=="win32"`时跑, 其余平台`ensure_florr_auto_afk_running()`直接return, 不打印、不联网、不调用`input()`.
- 下载前必须显式问用户(URL+大小+目标路径), 输入`n`跳过时不阻塞——打印一句提示后主程序照常继续.
- 网络失败/zip损坏/exe打开失败, 任何一步出错都不能崩主程序——捕获异常, 打印清楚原因, 该清理的半成品文件清理掉, 返回让调用方决定怎么继续.
- 真正的可执行文件名是`segment.exe`, 不是"florr-auto-afk.exe"(已用真实release zip验证过).
- `LATEST_LOG_PATH`的下游用法(`_read_new_lines()`里的`os.path.getsize`/`open`)不变, 只改这个常量的**值**怎么算出来.
- 下载URL/安装目录名/exe文件名都是写死的常量, 不做版本检测/自动更新.
- 不检测florr-auto-afk是否已经在运行——每次都无条件`subprocess.Popen`一次.

---

## Task 1: `afk_watch.py` — `LATEST_LOG_PATH`改成计算得出 + 新增网络/证书常量

**Files:**
- Modify: `afk_watch.py`(文件头部: docstring、import区域、常量区域)
- Test: `test_afk_watch.py`

**Interfaces:**
- Produces:
  - `_INSTALL_DIR_NAME: str`, `_EXE_NAME: str`, `_DOWNLOAD_URL: str` — 模块级常量.
  - `_INSTALL_ROOT: str`, `_INSTALL_DIR: str`, `_EXE_PATH: str` — 模块级计算路径.
  - `LATEST_LOG_PATH: str` — 值从硬编码字符串改成`os.path.join(_INSTALL_DIR, "latest.log")`, 名字/下游用法不变.
  - `_SSL_CONTEXT`, `_USER_AGENT` — 供Task 2的下载函数用.

- [ ] **Step 1: 写失败测试**

在`test_afk_watch.py`顶部import基础上(不用改, `import afk_watch`已经有了), 文件末尾新增:

```python
def test_latest_log_path_is_computed_from_install_dir():
    assert afk_watch.LATEST_LOG_PATH == os.path.join(afk_watch._INSTALL_DIR, "latest.log")


def test_install_dir_is_named_after_the_release_folder():
    assert afk_watch._INSTALL_DIR == os.path.join(afk_watch._INSTALL_ROOT, "florr-auto-afk-v1.1.1-auto")


def test_exe_path_uses_the_real_executable_name_not_florr_auto_afk_exe():
    # 实测过release zip内部结构确认的真实文件名 —— 不是"florr-auto-afk.exe"
    # 这种直觉猜测的名字, 写死一个测试防止以后被改错.
    assert afk_watch._EXE_PATH == os.path.join(afk_watch._INSTALL_DIR, "segment.exe")
```

(需要在文件顶部补一行`import os` —— 目前`test_afk_watch.py`没有.)

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest test_afk_watch.py -k "install_dir or exe_path or latest_log_path_is_computed" -v`
Expected: FAIL — `AttributeError: module 'afk_watch' has no attribute '_INSTALL_DIR'`(这几个常量还不存在)

- [ ] **Step 3: 改`afk_watch.py`文件头部**

把整个文件开头(从`"""florr-auto-afk协同...`到`_warned_unreadable = False`那一段, 即原文件第1-30行)替换成:

```python
"""florr-auto-afk协同 —— 监听它写的latest.log, 检测到"发现AFK弹窗"事件后,
让本项目的寻路循环暂停一段时间, 别跟它的YOLO拖拽方案抢鼠标.

florr-auto-afk只在检测到弹窗那一刻写一条会落盘的日志(`log_ret`默认save=True):
    ... EVENT: Found AFK window
它清场后的"No AFK window found"硬编码save=False, 不管verbose开不开都不落盘,
没法拿来当"解除暂停"信号用 —— 这里只能是触发器, 不是起止对: 看到触发行就暂停
固定时长, 时间到自动恢复, 不去猜它到底解完没解完. 详见
docs/superpowers/specs/2026-08-11-afk-check-coexistence-design.md.

florr-auto-afk本身是完全独立的另一个程序(不是这个repo的一部分), 用户得自己
有一份能跑. ensure_florr_auto_afk_running()负责在Windows上自动确保它在跑
(没装就问要不要下, 装了就打开它); LATEST_LOG_PATH跟着它实际的安装位置算出来,
不再是写死的个人路径. 详见
docs/superpowers/specs/2026-08-27-afk-auto-bootstrap-design.md.
"""
import os
import ssl
import subprocess
import sys
import time
import urllib.request
import zipfile

import certifi

# florr-auto-afk发行包解压后自带这个顶层目录名, 直接沿用不改名.
_INSTALL_DIR_NAME = "florr-auto-afk-v1.1.1-auto"
# 实测过release zip内部结构确认的真实可执行文件名 —— 不是"florr-auto-afk.exe"
# 这种直觉猜测的名字.
_EXE_NAME = "segment.exe"
_DOWNLOAD_URL = (
    "https://github.com/sunluca668/auto-afk/releases/download/"
    "123er4/florr-auto-afk-v1.1.1-auto.zip"
)

# 跟cdp_bridge.py的_CHROME_PROFILE_DIR同一个套路: 打包成exe后是exe自己所在
# 目录, 脚本模式下是main.py所在目录 —— 两种场景下"跟可执行文件同级"语义一致,
# 不用sys.executable(脚本模式下那是python解释器路径, 跟main.py不在同一目录).
_INSTALL_ROOT = os.path.dirname(os.path.abspath(sys.argv[0]))
_INSTALL_DIR = os.path.join(_INSTALL_ROOT, _INSTALL_DIR_NAME)
_EXE_PATH = os.path.join(_INSTALL_DIR, _EXE_NAME)

# florr-auto-afk.exe(现在自动下载安装到_INSTALL_DIR了)双击时CWD是它自己所在
# 目录, latest.log就落在这个目录下.
LATEST_LOG_PATH = os.path.join(_INSTALL_DIR, "latest.log")
# 覆盖YOLO检测+分割+拖拽执行的时间; 若在florr-auto-afk配置里关掉moveAfterAFK可以调低.
PAUSE_SECONDS = 12

_FOUND_MARKER = "EVENT: Found AFK window"

# server_lookup.py的_SSL_CONTEXT/_USER_AGENT原样复制 —— Windows上urllib默认
# 不读系统证书链, 显式传certifi的证书链才不会CERTIFICATE_VERIFY_FAILED(见
# venv-setup-deps项目memory的certifi那条). 两个都是模块私有常量, 不跨模块
# import, 沿用这个repo"平台/职责专属模块各自小段重复"的既有约定.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
_USER_AGENT = "florr-auto-pathing (github.com/greatluca666/florr-auto-pathing)"

_last_offset = 0
_pause_until = 0.0
# 还没做过第一次真实读取 —— 用来区分"模块刚加载, 从没poll过"和"poll过, offset
# 合法为0"这两种情况, 好让下面的截断重置逻辑(size < _last_offset)只在后一种
# 情况下生效. 参见_read_new_lines()里的用法.
_initialized = False
# 日志读取失败(路径没配对/文件不存在/是目录/权限不够)只在第一次发生时打印一次
# 警告, 别每次poll(一秒好几次)都刷屏.
_warned_unreadable = False
```

(原文件从`def _read_new_lines():`开始到文件末尾`def poll_afk_pause():`那一整段不变, 紧接在这段后面.)

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest test_afk_watch.py -v`
Expected: 全部PASS(包括原有11条用例+3条新增, 原有用例本来就monkeypatch覆盖`LATEST_LOG_PATH`, 不受这次改动影响)

- [ ] **Step 5: Commit**

```bash
git add afk_watch.py test_afk_watch.py
git commit -m "refactor: compute LATEST_LOG_PATH instead of hardcoding a personal path"
```

---

## Task 2: `afk_watch.py` — 下载确认 + 下载解压

**Files:**
- Modify: `afk_watch.py`
- Test: `test_afk_watch.py`

**Interfaces:**
- Consumes: `_DOWNLOAD_URL`, `_INSTALL_ROOT`, `_INSTALL_DIR_NAME`, `_INSTALL_DIR`, `_SSL_CONTEXT`, `_USER_AGENT`(Task 1)
- Produces:
  - `_prompt_download_confirm() -> bool` — 问用户要不要下, 输入`n`(不分大小写, 允许前后空白)返回`False`, 其余(包括直接回车)返回`True`.
  - `_download_and_extract() -> bool` — 下载+解压成功返回`True`, 任何异常返回`False`且清理临时文件.

- [ ] **Step 1: 写失败测试 — `_prompt_download_confirm()`**

```python
def test_prompt_download_confirm_returns_true_on_enter(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert afk_watch._prompt_download_confirm() is True


def test_prompt_download_confirm_returns_false_on_n(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert afk_watch._prompt_download_confirm() is False


def test_prompt_download_confirm_returns_false_on_n_case_insensitive_with_whitespace(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "  N  ")
    assert afk_watch._prompt_download_confirm() is False


def test_prompt_download_confirm_prints_url_size_and_destination(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _: "")
    afk_watch._prompt_download_confirm()
    captured = capsys.readouterr()
    assert afk_watch._DOWNLOAD_URL in captured.out
    assert afk_watch._INSTALL_DIR in captured.out
```

（`input()`的提示文本本身作为参数传给`input(...)`, 不是`print()`出来的 —— pytest的`capsys`默认不会捕获到`input()`的prompt参数. 用`monkeypatch.setattr("builtins.input", ...)`换成一个把收到的prompt参数原样`print()`出来再返回空字符串的假函数, 这样`capsys`才能验证到内容.）

把上面最后一条测试改成这样(不能直接用`capsys`验证`input()`的prompt参数):

```python
def test_prompt_download_confirm_prints_url_size_and_destination(monkeypatch):
    captured_prompt = {}

    def fake_input(prompt):
        captured_prompt["text"] = prompt
        return ""

    monkeypatch.setattr("builtins.input", fake_input)
    afk_watch._prompt_download_confirm()
    assert afk_watch._DOWNLOAD_URL in captured_prompt["text"]
    assert afk_watch._INSTALL_DIR in captured_prompt["text"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest test_afk_watch.py -k prompt_download_confirm -v`
Expected: FAIL — `AttributeError: module 'afk_watch' has no attribute '_prompt_download_confirm'`

- [ ] **Step 3: 实现`_prompt_download_confirm()`**

在`poll_afk_pause()`函数后面(文件末尾)加:

```python
def _prompt_download_confirm():
    """问用户要不要下载florr-auto-afk. 回车/任何不是'n'的输入都算同意;
    输入n(不分大小写, 允许前后空白)算跳过."""
    answer = input(
        f"\n🤖 没检测到florr-auto-afk(AFK弹窗自动处理用). 现在下载吗?\n"
        f"   来源: {_DOWNLOAD_URL}\n"
        f"   大小: 约260MB, 解压到: {_INSTALL_DIR}\n"
        f"   (回车=下载, 输入n=跳过, 之后AFK弹窗不会自动处理): "
    )
    return answer.strip().lower() != "n"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest test_afk_watch.py -k prompt_download_confirm -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 写失败测试 — `_download_and_extract()`**

```python
import io
import zipfile
from unittest.mock import patch, MagicMock


def _fake_zip_bytes():
    """造一个真实的、内存里的zip文件内容, 里面有一个占位文件 —— 用来让
    zipfile.ZipFile(真实的模块, 不mock)在测试里真的能解压出东西, 断言解压
    后的文件确实落在了预期目录, 而不是只断言"函数被调用过"这种空心测试."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("florr-auto-afk-v1.1.1-auto/segment.exe", b"fake exe content")
    return buf.getvalue()


def test_download_and_extract_success(tmp_path, monkeypatch):
    monkeypatch.setattr(afk_watch, "_INSTALL_ROOT", str(tmp_path))
    monkeypatch.setattr(afk_watch, "_INSTALL_DIR", str(tmp_path / "florr-auto-afk-v1.1.1-auto"))

    zip_bytes = _fake_zip_bytes()
    fake_resp = MagicMock()
    fake_resp.headers = {"Content-Length": str(len(zip_bytes))}
    fake_resp.read.side_effect = [zip_bytes, b""]  # 第一次读到全部内容, 第二次读到空表示结束
    fake_resp.__enter__ = lambda self: fake_resp
    fake_resp.__exit__ = lambda self, *a: False

    with patch("afk_watch.urllib.request.urlopen", return_value=fake_resp):
        result = afk_watch._download_and_extract()

    assert result is True
    extracted_exe = tmp_path / "florr-auto-afk-v1.1.1-auto" / "segment.exe"
    assert extracted_exe.read_bytes() == b"fake exe content"
    # 临时zip用完就删, 不该留在目标目录里.
    assert not (tmp_path / "florr-auto-afk-v1.1.1-auto.zip.download").exists()


def test_download_and_extract_returns_false_and_cleans_up_on_network_error(tmp_path, monkeypatch):
    monkeypatch.setattr(afk_watch, "_INSTALL_ROOT", str(tmp_path))
    monkeypatch.setattr(afk_watch, "_INSTALL_DIR", str(tmp_path / "florr-auto-afk-v1.1.1-auto"))

    with patch("afk_watch.urllib.request.urlopen", side_effect=OSError("network unreachable")):
        result = afk_watch._download_and_extract()

    assert result is False
    assert not (tmp_path / "florr-auto-afk-v1.1.1-auto.zip.download").exists()


def test_download_and_extract_returns_false_on_corrupt_zip(tmp_path, monkeypatch):
    monkeypatch.setattr(afk_watch, "_INSTALL_ROOT", str(tmp_path))
    monkeypatch.setattr(afk_watch, "_INSTALL_DIR", str(tmp_path / "florr-auto-afk-v1.1.1-auto"))

    garbage = b"this is not a zip file"
    fake_resp = MagicMock()
    fake_resp.headers = {"Content-Length": str(len(garbage))}
    fake_resp.read.side_effect = [garbage, b""]
    fake_resp.__enter__ = lambda self: fake_resp
    fake_resp.__exit__ = lambda self, *a: False

    with patch("afk_watch.urllib.request.urlopen", return_value=fake_resp):
        result = afk_watch._download_and_extract()

    assert result is False
    assert not (tmp_path / "florr-auto-afk-v1.1.1-auto.zip.download").exists()
```

- [ ] **Step 6: 跑测试确认失败**

Run: `venv/bin/python -m pytest test_afk_watch.py -k download_and_extract -v`
Expected: FAIL — `AttributeError: module 'afk_watch' has no attribute '_download_and_extract'`

- [ ] **Step 7: 实现`_download_and_extract()`**

`_prompt_download_confirm()`后面加:

```python
def _download_and_extract():
    """流式下载到临时文件+zipfile解压, 完了删掉临时zip. 网络失败/zip损坏都
    不抛异常出去 —— 返回False, 让调用方(ensure_florr_auto_afk_running())
    决定怎么继续, 主程序不受影响."""
    tmp_path = os.path.join(_INSTALL_ROOT, f"{_INSTALL_DIR_NAME}.zip.download")
    try:
        req = urllib.request.Request(_DOWNLOAD_URL, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CONTEXT) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 1024)  # 1MB一块
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        print(f"\r   下载中... {downloaded / 1e6:.0f}MB / {total / 1e6:.0f}MB", end="")
            print()  # 结束下载进度那行, 换行

        with zipfile.ZipFile(tmp_path) as zf:
            zf.extractall(_INSTALL_ROOT)

        print(f"✅ florr-auto-afk已下载解压到 {_INSTALL_DIR}")
        return True
    except Exception as e:
        print(f"⚠️ 下载/解压florr-auto-afk失败(不影响主程序, 之后AFK弹窗不会自动处理): {e}")
        return False
    finally:
        if os.path.isfile(tmp_path):
            os.remove(tmp_path)
```

- [ ] **Step 8: 跑测试确认通过**

Run: `venv/bin/python -m pytest test_afk_watch.py -v`
Expected: 全部PASS

- [ ] **Step 9: Commit**

```bash
git add afk_watch.py test_afk_watch.py
git commit -m "feat: add florr-auto-afk download-confirm and download-and-extract to afk_watch"
```

---

## Task 3: `afk_watch.py` — `ensure_florr_auto_afk_running()`编排

**Files:**
- Modify: `afk_watch.py`
- Test: `test_afk_watch.py`

**Interfaces:**
- Consumes: `_EXE_PATH`(Task 1), `_prompt_download_confirm()`, `_download_and_extract()`(Task 2)
- Produces: `ensure_florr_auto_afk_running() -> None` — `main.py`调用的公开入口.

- [ ] **Step 1: 写失败测试**

```python
def test_ensure_florr_auto_afk_running_skips_entirely_on_non_windows(monkeypatch):
    monkeypatch.setattr(afk_watch.sys, "platform", "darwin")
    with patch("builtins.input") as mock_input, \
         patch("afk_watch.subprocess.Popen") as mock_popen, \
         patch("afk_watch.urllib.request.urlopen") as mock_urlopen:
        afk_watch.ensure_florr_auto_afk_running()
    mock_input.assert_not_called()
    mock_popen.assert_not_called()
    mock_urlopen.assert_not_called()


def test_ensure_florr_auto_afk_running_opens_directly_when_already_installed(monkeypatch):
    monkeypatch.setattr(afk_watch.sys, "platform", "win32")
    with patch("afk_watch.os.path.isfile", return_value=True), \
         patch("builtins.input") as mock_input, \
         patch("afk_watch.subprocess.Popen") as mock_popen:
        afk_watch.ensure_florr_auto_afk_running()
    mock_input.assert_not_called()  # 已经装了, 不该问下载
    mock_popen.assert_called_once_with([afk_watch._EXE_PATH])


def test_ensure_florr_auto_afk_running_skips_when_user_declines_download(monkeypatch):
    monkeypatch.setattr(afk_watch.sys, "platform", "win32")
    with patch("afk_watch.os.path.isfile", return_value=False), \
         patch("afk_watch._prompt_download_confirm", return_value=False), \
         patch("afk_watch._download_and_extract") as mock_download, \
         patch("afk_watch.subprocess.Popen") as mock_popen:
        afk_watch.ensure_florr_auto_afk_running()
    mock_download.assert_not_called()
    mock_popen.assert_not_called()


def test_ensure_florr_auto_afk_running_downloads_then_opens_when_confirmed(monkeypatch):
    monkeypatch.setattr(afk_watch.sys, "platform", "win32")
    with patch("afk_watch.os.path.isfile", return_value=False), \
         patch("afk_watch._prompt_download_confirm", return_value=True), \
         patch("afk_watch._download_and_extract", return_value=True) as mock_download, \
         patch("afk_watch.subprocess.Popen") as mock_popen:
        afk_watch.ensure_florr_auto_afk_running()
    mock_download.assert_called_once()
    mock_popen.assert_called_once_with([afk_watch._EXE_PATH])


def test_ensure_florr_auto_afk_running_does_not_open_when_download_fails(monkeypatch):
    monkeypatch.setattr(afk_watch.sys, "platform", "win32")
    with patch("afk_watch.os.path.isfile", return_value=False), \
         patch("afk_watch._prompt_download_confirm", return_value=True), \
         patch("afk_watch._download_and_extract", return_value=False), \
         patch("afk_watch.subprocess.Popen") as mock_popen:
        afk_watch.ensure_florr_auto_afk_running()
    mock_popen.assert_not_called()


def test_ensure_florr_auto_afk_running_does_not_crash_when_popen_raises(monkeypatch):
    monkeypatch.setattr(afk_watch.sys, "platform", "win32")
    with patch("afk_watch.os.path.isfile", return_value=True), \
         patch("afk_watch.subprocess.Popen", side_effect=OSError("no permission")):
        afk_watch.ensure_florr_auto_afk_running()  # 不该抛异常
```

- [ ] **Step 2: 跑测试确认失败**

Run: `venv/bin/python -m pytest test_afk_watch.py -k ensure_florr_auto_afk_running -v`
Expected: FAIL — `AttributeError: module 'afk_watch' has no attribute 'ensure_florr_auto_afk_running'`

- [ ] **Step 3: 实现`ensure_florr_auto_afk_running()`**

`_download_and_extract()`后面加:

```python
def ensure_florr_auto_afk_running():
    """确保florr-auto-afk在跑 —— 没装就问要不要下, 装了(不管刚下的还是本来就
    有的)就打开它. 只在Windows上做, 其余平台整个跳过(florr-auto-afk是Windows
    专属GUI程序). 全程不阻塞主流程 —— 这是可选增强, 不是寻路/刷怪的前提, 任何
    一步失败/用户跳过都只打印一句提示, main.py照常往下走."""
    if sys.platform != "win32":
        return

    if not os.path.isfile(_EXE_PATH):
        if not _prompt_download_confirm():
            print("   跳过florr-auto-afk, 之后AFK弹窗不会自动处理.")
            return
        if not _download_and_extract():
            return  # 失败原因已经在_download_and_extract()里打印过了

    try:
        subprocess.Popen([_EXE_PATH])
        print(
            "🪟 已打开florr-auto-afk, 请在它的界面里点\"run\"按钮开启AFK弹窗"
            "自动处理(不点也不影响寻路/刷怪, 只是不会自动处理AFK弹窗)."
        )
    except Exception as e:
        print(f"⚠️ 打开florr-auto-afk失败(不影响主程序): {e}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `venv/bin/python -m pytest test_afk_watch.py -v`
Expected: 全部PASS

- [ ] **Step 5: Commit**

```bash
git add afk_watch.py test_afk_watch.py
git commit -m "feat: add ensure_florr_auto_afk_running() orchestration to afk_watch"
```

---

## Task 4: `main.py`接入

**Files:**
- Modify: `main.py`(`if __name__ == "__main__":`开头)

**Interfaces:**
- Consumes: `afk_watch.ensure_florr_auto_afk_running()`(Task 3) —— `afk_watch`模块本身`main.py`已经`import`过了, 不用新加import.

- [ ] **Step 1: 在`main.py`的`if __name__ == "__main__":`里加一行调用**

当前(`main.py`, `if __name__ == "__main__":`开头几行, Chrome bootstrap那次改过之后的样子):

```python
if __name__ == "__main__":
    cdp_bridge.launch_dedicated_chrome()
    show_fullscreen_confirm()
    overlay = create_overlay()

    apply_map("desert")
```

改成:

```python
if __name__ == "__main__":
    cdp_bridge.launch_dedicated_chrome()
    afk_watch.ensure_florr_auto_afk_running()
    show_fullscreen_confirm()
    overlay = create_overlay()

    apply_map("desert")
```

放在Chrome引导之后、全屏确认之前 —— 这样用户在最后点"开始运行"按钮之前, florr-auto-afk已经问完/开好了(如果选择要的话), 不会错过"点run按钮"这条提示被寻路日志刷走.

- [ ] **Step 2: 跑现有smoke test确认import没炸**

Run: `venv/bin/python -m pytest test_main_smoke.py -v`
Expected: PASS —— `import main`不会触发`if __name__=="__main__":`里的代码(guard挡住了), 这一行新代码本身不在导入路径上.

- [ ] **Step 3: 跑全量测试套件确认没有破坏任何东西**

Run: `venv/bin/python -m pytest -v`
Expected: 全部PASS

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: wire ensure_florr_auto_afk_running() into main.py entrypoint"
```

- [ ] **Step 5: 手动验证清单(不是自动化测试, 是给实现者/用户的真机检查表)**

**必须在真实Windows机器上跑**(这个功能是Windows专属, mac开发机上`ensure_florr_auto_afk_running()`直接return, 没有真机行为可验证):

1. 确认本地还没有`florr-auto-afk-v1.1.1-auto/`目录, 跑`main.py`(或打包好的`.exe`), 走到这一步时确认看到下载确认提示(URL+大小+目标路径).
2. 输入`n`, 确认打印"跳过florr-auto-afk"提示后主程序继续往下走(不卡住).
3. 再跑一次, 这次回车确认下载 —— 确认看到下载进度、解压完成提示, 且`florr-auto-afk-v1.1.1-auto/segment.exe`真的落在预期目录.
4. 确认`segment.exe`真的被打开(弹出florr-auto-afk自己的GUI窗口), 控制台打印了"请点run按钮"提示.
5. 在florr-auto-afk界面里点"run", 确认`latest.log`开始有内容写入.
6. 再跑一次`main.py`(florr-auto-afk已经装过了) —— 确认这次跳过下载确认, 直接打开.
7. 触发一次真实AFK弹窗(或等它自然出现), 确认`afk_watch.poll_afk_pause()`真的检测到"Found AFK window"并暂停寻路12秒.

---

## Self-Review

**Spec覆盖检查:**
- ✅ `LATEST_LOG_PATH`改成计算得出 —— Task 1.
- ✅ 检查已装/问下载/下载解压/打开 + 全程不阻塞 —— Task 2, Task 3.
- ✅ 只在Windows上做 —— Task 3 Step 3的`sys.platform`分支 + 对应测试.
- ✅ `segment.exe`是真实文件名(不是"florr-auto-afk.exe") —— Task 1的`_EXE_NAME`常量 + 专门写了一条测试锁死这个值.
- ✅ 网络/解压/打开失败都不崩主程序 —— Task 2/Task 3每个失败分支都有对应测试.
- ✅ `main.py`接入点、顺序(Chrome引导之后、全屏确认之前) —— Task 4.
- ✅ 手动验证清单 —— Task 4 Step 5.

**Placeholder扫描:** 无TBD/TODO, 每个Step都有完整可运行代码.

**类型/签名一致性检查:** `_prompt_download_confirm() -> bool`在Task 2定义, Task 3的`ensure_florr_auto_afk_running()`按布尔值分支调用, 一致. `_download_and_extract() -> bool`同理. `_EXE_PATH`在Task 1定义为字符串路径, Task 2/3里`subprocess.Popen([_EXE_PATH])`/`os.path.isfile(_EXE_PATH)`都按字符串路径用, 没有类型不一致. `_INSTALL_ROOT`/`_INSTALL_DIR`在Task 2的测试里用`monkeypatch.setattr`覆盖成`tmp_path`, 跟`_download_and_extract()`内部读的是同一个模块属性, 一致.
