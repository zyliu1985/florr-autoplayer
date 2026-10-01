# exe自动拉起专用Chrome — design

## Problem

[PACKAGING.md](../../../PACKAGING.md)把项目打包成了standalone `.exe`, 但`switch_server()`换服务器功能仍然要求用户手动敲命令行, 用[cdp_bridge.py](../../../cdp_bridge.py)文档里那三个参数(`--remote-debugging-port` + `--remote-allow-origins` + `--user-data-dir`)重新启动Chrome, 而且必须先手动完全退出所有Chrome窗口. 这跟"双击exe就能跑"的打包目标矛盾 —— exe面向的是不懂命令行的小白用户.

## Goal

`main.py`启动时自动完成整套Chrome准备工作: 退出现有Chrome(经用户确认)、拉起带CDP参数的专用Chrome实例、引导用户把florr账号迁移过去并手动打开florr.io、确认已进入全屏, 全程只需要按回车/点按钮, 不用敲任何命令行.

## Non-goals

- **不自动导航到florr.io.** 专用Chrome是全新空白档案, 没有用户原来的登录状态 —— 让用户自己在新窗口里迁移账号、手动打开florr.io, 比脚本代劳更不容易出错(账号迁移这步脚本管不了).
- **不自动按F11进全屏.** 全屏由用户自己操作, 脚本只提供一个"确认"按钮等用户点, 不用pyautogui模拟按键触发全屏(减少一种"哪个窗口有焦点"相关的失败模式).
- **不加"跳过自动拉起"的开关/配置项.** 保持单一路径, 面向小白不引入选择困难; 需要旧行为的开发者可以继续手动跑`python main.py`前的手动Chrome准备(本设计只影响exe/main.py的启动引导, 不动`cdp_bridge.py`其余部分).
- **Chrome本身没装时不负责自动安装**, 只报清楚的错误提示去哪下载.
- **不做"恢复上次标签页"之类的Chrome状态处理** —— 全新/持久化的独立profile不会触发这类提示.

## Approach

新增两处:

1. **[cdp_bridge.py](../../../cdp_bridge.py)**: `launch_dedicated_chrome()` — 编排"确认关闭现有Chrome → 强制退出 → 拉起专用空白实例 → 提示用户迁移账号+手动开florr.io+回车 → 轮询`find_florr_tab()`确认" 这一整条链路. 复用已有的`CDP_HOST`/`CDP_PORT`/`find_florr_tab()`.
2. **[overlay.py](../../../overlay.py)**: 新增一个**真能点击**的确认对话框组件(不同于现有点击穿透的`StatusOverlay`), 显示"手动进全屏后点击开始"提示+按钮, 阻塞等用户点击. Mac复用`StatusOverlay`已验证过的跨Space技巧(screensaver层级+collectionBehavior), 只是不设`ignoresMouseEvents_`; Windows用普通tkinter Toplevel+Button(不需要mac那套, 见overlay.py现有注释: Windows全屏不换Space).

**接入点**: `main.py`的`if __name__ == "__main__":`最开头, `apply_map("desert")`之前, 调用`cdp_bridge.launch_dedicated_chrome()`. 完成后原有的`on_start_screen()`/`click_start_game()`逻辑不受影响, 照常接手.

## Design

### `cdp_bridge.py` 新增

```python
import os
import subprocess
import sys
import time

_CHROME_PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "chrome-profile")
# sys.argv[0]: 打包成exe后是exe自己的路径, 脚本运行时是main.py的路径 —— 两种
# 情况都想要"跟可执行文件同级", 不用sys.executable(那是python.exe解释器本身
# 的路径, 脚本模式下跟main.py不在同一目录, frozen模式下才等于exe路径, 两种
# 场景表现不一致). sys.argv[0]在两种场景下语义一致, 更适合这里.

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


def _quit_all_chrome():
    """强制退出所有Chrome进程 —— 新CDP参数要求完全重启(见模块文档). 本来就
    没在跑不算失败, 静默吞掉."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/IM", "chrome.exe", "/F"],
            capture_output=True,
        )
    elif sys.platform == "darwin":
        subprocess.run(
            ["osascript", "-e", 'quit app "Google Chrome"'],
            capture_output=True,
        )
    time.sleep(1)  # 给进程真正退出、释放profile锁一点时间, 避免新实例抢锁失败


def _launch_chrome_process():
    """带三个CDP参数 + 持久独立profile拉起一个全新空白Chrome窗口. 找不到
    Chrome可执行文件时抛RuntimeError, 带安装引导."""
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
            raise RuntimeError(
                "没找到Chrome, 请先安装: https://www.google.com/chrome/"
            )
        subprocess.Popen([chrome_path] + args)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-a", "Google Chrome", "--args"] + args)
    else:
        raise RuntimeError(f"不支持的平台: {sys.platform}")


def launch_dedicated_chrome():
    """整条"准备专用Chrome"引导链路, main.py启动时调用一次. 每一步都印清楚
    中文提示, 面向不懂命令行的用户 —— 全程只需要回车/点按钮."""
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
        tab = _poll_for_florr_tab(timeout=15)
        if tab is not None:
            break
        print("   还没检测到florr.io标签页, 确认已经在那个新Chrome窗口里打开florr.io了? 重试一次.")


def _poll_for_florr_tab(timeout, interval=1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        tab = find_florr_tab()
        if tab is not None:
            return tab
        time.sleep(interval)
    return None
```

### `overlay.py` 新增

跟现有`create_overlay()`/`StatusOverlay`/`_WindowsOverlay`/`_NullOverlay`同一套平台分发+安全兜底模式, 新增一个并列的确认对话框:

- `show_fullscreen_confirm()`: 工厂函数, 平台分发 + try/except兜底(建不出来就退化成`input("请手动进入全屏后按回车继续: ")`控制台确认, 绝不崩主程序, 跟`create_overlay()`的`_NullOverlay`哲学一致).
- Mac: 新类(比如`_MacConfirmDialog`), 复用`StatusOverlay.__init__`里的`NSWindow`+`_SCREENSAVER_LEVEL`+`collectionBehavior`那套建窗逻辑, 但:
  - **不**调用`setIgnoresMouseEvents_(True)` (按钮必须能点)
  - 加一个`NSButton`, `setTarget_`/`setAction_`指向一个Python回调, 点击后把`self._confirmed = True`
  - 阻塞方法`wait_for_confirm()`: 循环`_pump_events()` + `time.sleep(0.05)`直到`self._confirmed`, 然后`close()`
- Windows: 新类(比如`_WindowsConfirmDialog`), 普通`tk.Toplevel`(或`tk.Tk`)+`tk.Button`, `-topmost`属性即可(不需要mac那套跨Space hack, 也不需要`_WindowsOverlay`里的win32点击穿透样式 —— 这个窗口就是要能点的). `wait_for_confirm()`用`root.wait_window()`或按钮回调里`root.destroy()`退出`mainloop()`.
- 文案: `florr.io已就绪 — 手动进入全屏(F11)后点击下方按钮开始`, 按钮`开始运行`.

### `main.py` 接入

`main.py`头部import改成显式引入两个新名字(不依赖`from utils import *`间接带出`cdp_bridge`这种隐式路径 —— 显式导入, 跟`afk_watch`/`enemy_detect`现有风格一致):

```python
import cdp_bridge
from overlay import create_overlay, show_fullscreen_confirm
```

```python
if __name__ == "__main__":
    cdp_bridge.launch_dedicated_chrome()
    show_fullscreen_confirm()

    apply_map("desert")
    ...  # 原有逻辑不变
```

## Error handling

- 找不到Chrome可执行文件(Windows): 清楚的"请先安装"提示 + 抛异常终止, 不静默失败(这一步失败后面全部没法继续, 没有优雅降级的意义).
- `_quit_all_chrome()`执行时Chrome本来就没在跑: `capture_output=True`吞掉非零退出码, 不算错误.
- 轮询`find_florr_tab()`超时: 不崩溃, 打印提示循环回去重新等用户按回车(见上面`launch_dedicated_chrome()`的`while True`).
- 悬浮确认窗口建不起来(缺pyobjc/tkinter等): 退化成控制台`input()`确认, 跟现有`create_overlay()`的`_NullOverlay`降级哲学完全一致.

## 顺带更新的文档

- `README.md`: "You need to run this code with florr.io tab on the top and fullscreen" 这句改一下措辞 —— 现在florr.io本身还是要fullscreen没变, 但Chrome准备/账号迁移这段变成脚本交互式引导, 不再是运行前的手动前提.
- `.gitignore`: 加一条`chrome-profile/`, 这个持久化的专用Chrome档案目录不该被commit(体积大, 也含用户的florr账号状态).

## Testing / verification

- `test_cdp_bridge.py`新增用例, 沿用现有mock风格(`patch subprocess.Popen/run`, `patch input`, `patch cdp_bridge.find_florr_tab`, `patch time.sleep`避免真等待):
  - `_find_windows_chrome()`: 候选路径都不存在→返回None; 其中一个存在→返回它.
  - `_quit_all_chrome()`: 按平台调用正确的命令(mock `subprocess.run`断言调用参数).
  - `_launch_chrome_process()`: Windows找不到chrome.exe时抛`RuntimeError`; 找到时`subprocess.Popen`带上全部5个参数.
  - `launch_dedicated_chrome()`: mock`input`按顺序返回、mock`_poll_for_florr_tab`先返回`None`再返回一个假tab, 断言循环重试了、断言最终正常返回.
- `test_overlay.py`新增(如果现有测试对真实GUI做了隔离处理, 沿用同样手法): `show_fullscreen_confirm()`在mock掉AppKit/tkinter不可用时退化成`input()`确认, 不抛异常.
- 手动验证(这个项目的GUI/真实Chrome部分历来没法只靠单测覆盖, 见`test_overlay.py`现有注释):
  1. Mac开发机上跑一遍完整流程(哪怕不是最终部署平台), 肉眼确认: 警告→回车→Chrome真的重启成空白窗口→账号迁移提示→回车→florr.io检测到→确认弹窗真的能点→点击后放行.
  2. 用户在真实Windows机器上跑一遍同样流程, 确认`taskkill`/chrome.exe路径查找/tkinter确认弹窗都按预期工作(这是真正的部署目标, Mac验证只能覆盖代码逻辑, 覆盖不了Windows专属分支的实际效果).
