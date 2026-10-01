# 自动准备florr-auto-afk — design

## Problem

[afk_watch.py](../../../afk_watch.py)靠读florr-auto-afk写的`latest.log`来判断"AFK弹窗出现了, 暂停寻路"，但florr-auto-afk本身是完全独立的另一个程序——`LATEST_LOG_PATH`目前硬编码成用户自己机器上的一个固定路径(`C:/Users/luca/Desktop/florr-auto-afk-v1.1.1-auto/latest.log`)，用户得自己手动下载、解压、打开它，这整套跟这个项目"双击exe就能跑"的打包目标(见[2026-08-26-chrome-bootstrap-design.md](2026-08-26-chrome-bootstrap-design.md))完全脱节——小白用户根本不知道还要另外搞一个程序。

## Goal

`main.py`启动时自动检查本地有没有florr-auto-afk，没有就询问是否下载(经确认后下载+解压)，有(不管是刚下的还是本来就有的)就自动打开它，并提醒用户点界面上的"run"按钮开启AFK弹窗自动处理。全程可跳过、不阻塞主流程——AFK自动处理本来就是可选增强，不是寻路/刷怪的前提。

## Non-goals

- **不把florr-auto-afk的代码/YOLO模型搬进这个repo** —— 沿用[2026-08-11-afk-check-coexistence-design.md](2026-08-11-afk-check-coexistence-design.md)里早就定好的边界，它是完全独立的~700MB程序(torch+cv2+两个YOLO模型)，没道理重新实现或打包进来。
- **不检测florr-auto-afk是否已经在运行** —— 每次调用都`subprocess.Popen`打开一次，不查有没有重复实例。这是已知限制，不是遗漏(见Non-goals说明)。
- **不做版本更新检测/自动更新** —— 下载URL是写死的常量，release换了要手动改代码里的常量，不构建通用的"检查新版本"机制。
- **不管release包装干净不干净** —— 只认目录结构里我们要用到的几个路径(`segment.exe`、`latest.log`)，`.DS_Store`之类的杂项不关心。
- **不在非Windows平台做任何事** —— florr-auto-afk是Windows专属GUI程序，这整套逻辑(检查/下载/解压/打开)只在`sys.platform=="win32"`时跑；其余平台(包括这个项目的mac开发机)整个跳过，不打印、不联网。

## Approach

新逻辑加进[afk_watch.py](../../../afk_watch.py)(它已经是这个项目里"跟florr-auto-afk打交道"的专属模块，`LATEST_LOG_PATH`这个常量本来就在这里)：

1. **`LATEST_LOG_PATH`从硬编码字符串改成计算得出** —— 跟[cdp_bridge.py](../../../cdp_bridge.py)的`_CHROME_PROFILE_DIR`同一个套路(`sys.argv[0]`所在目录 + 固定子路径)，因为下载目标目录是我们自己定的，从一开始就能算出最终路径，不需要"运行时探测到哪里再回填"这种动态配置。
2. **`ensure_florr_auto_afk_running()`** —— 新的公开入口，`main.py`启动时调用一次(跟`cdp_bridge.launch_dedicated_chrome()`并列，各管各的，不互相依赖)。

## Design

### 常量

```python
_DOWNLOAD_URL = "https://github.com/sunluca668/auto-afk/releases/download/123er4/florr-auto-afk-v1.1.1-auto.zip"
_INSTALL_DIR_NAME = "florr-auto-afk-v1.1.1-auto"  # zip解压后自带的顶层目录名, 直接沿用
_EXE_NAME = "segment.exe"  # 实测过zip内部结构确认的真实可执行文件名, 不是"florr-auto-afk.exe"

_INSTALL_ROOT = os.path.dirname(os.path.abspath(sys.argv[0]))
_INSTALL_DIR = os.path.join(_INSTALL_ROOT, _INSTALL_DIR_NAME)
_EXE_PATH = os.path.join(_INSTALL_DIR, _EXE_NAME)

LATEST_LOG_PATH = os.path.join(_INSTALL_DIR, "latest.log")
```

（原来`LATEST_LOG_PATH`的用法——`_read_new_lines()`里`os.path.getsize(LATEST_LOG_PATH)`等——完全不用改，只是这个常量的**值**从写死字符串变成了算出来的路径。）

### `ensure_florr_auto_afk_running()`

```python
def ensure_florr_auto_afk_running():
    """确保florr-auto-afk在跑——没装就问要不要下, 装了(不管刚下的还是本来就有
    的)就打开它。只在Windows上做, 其余平台整个跳过(florr-auto-afk是Windows
    专属GUI程序)。全程不阻塞主流程——这是可选增强, 不是寻路/刷怪的前提, 任何
    一步失败/用户跳过都只打印一句提示, 照常往下走。"""
    if sys.platform != "win32":
        return

    if not os.path.isfile(_EXE_PATH):
        if not _prompt_download_confirm():
            print("   跳过florr-auto-afk, 之后AFK弹窗不会自动处理.")
            return
        if not _download_and_extract():
            return  # 失败信息已经在_download_and_extract()里打印过了

    try:
        subprocess.Popen([_EXE_PATH])
        print("🪟 已打开florr-auto-afk, 请在它的界面里点\"run\"按钮开启AFK弹窗"
              "自动处理(不点也不影响寻路/刷怪, 只是不会自动处理AFK弹窗).")
    except Exception as e:
        print(f"⚠️ 打开florr-auto-afk失败(不影响主程序): {e}")
```

### `_prompt_download_confirm()`

```python
def _prompt_download_confirm():
    answer = input(
        f"\n🤖 没检测到florr-auto-afk(AFK弹窗自动处理用). 现在下载吗?\n"
        f"   来源: {_DOWNLOAD_URL}\n"
        f"   大小: 约260MB, 解压到: {_INSTALL_DIR}\n"
        f"   (回车=下载, 输入n=跳过, 之后AFK弹窗不会自动处理): "
    )
    return answer.strip().lower() != "n"
```

### `_download_and_extract()`

```python
def _download_and_extract():
    """流式下载到临时文件+zipfile解压, 完了删掉临时zip。网络失败/中断都不
    抛异常出去——返回False, 调用方打印过下载确认提示了, 这里只管失败的
    具体原因, 让主程序继续跑不受影响。"""
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

需要新增的模块级import/常量: `import sys`(补充, 目前没有), `import subprocess`, `import urllib.request`, `import zipfile`, `import certifi`, `import ssl`;

```python
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
_USER_AGENT = "florr-auto-pathing (github.com/greatluca666/florr-auto-pathing)"
```

这两个是[server_lookup.py](../../../server_lookup.py)里`_SSL_CONTEXT`/`_USER_AGENT`的原样复制(Windows上urllib不读系统证书链, 显式传certifi的证书链, 见[[venv-setup-deps]]的certifi那条)——两个都是模块私有常量(前缀`_`), 不跨模块import, 沿用这个repo里`cdp_bridge.py`/`overlay.py`平台专属类之间"小段重复优于共享"的既有约定, 不专门为两行常量建一个共享模块。

### `main.py`接入

```python
if __name__ == "__main__":
    cdp_bridge.launch_dedicated_chrome()
    afk_watch.ensure_florr_auto_afk_running()
    show_fullscreen_confirm()

    overlay = create_overlay()
    apply_map("desert")
```

放在Chrome引导之后、全屏确认之前——这样用户在最后点"开始运行"之前，florr-auto-afk已经开好了(如果选择要的话)，不会错过"点run按钮"这个提示被寻路日志刷走。

## Error handling

- 网络失败/超时/GitHub不可达: `_download_and_extract()`捕获所有异常, 打印清楚原因, 删掉半成品zip, 返回False, `ensure_florr_auto_afk_running()`直接return, 主程序照常继续.
- zip损坏/解压失败: 同上, 落在同一个`except Exception`里.
- `segment.exe`打开失败(比如文件被杀毒软件删了、权限问题): 单独try/except包住`subprocess.Popen`, 打印警告, 不崩.
- 用户主动跳过下载(输入`n`): 打印一句提示, 直接return, 不算错误.

## Testing / verification

- `test_afk_watch.py`新增用例(沿用现有mock风格——这个文件已经测过`_read_new_lines()`/`poll_afk_pause()`, mock掉真实文件系统状态那套):
  - `LATEST_LOG_PATH`路径计算: 断言等于`_INSTALL_ROOT`+固定子路径拼出来的值.
  - `ensure_florr_auto_afk_running()`非Windows平台: monkeypatch `sys.platform`, 断言函数直接return, 不调用`input`/`subprocess.Popen`/`urllib`.
  - `ensure_florr_auto_afk_running()`Windows + exe已存在: mock `os.path.isfile`返回True, 断言直接调`subprocess.Popen`, 不触发下载确认.
  - `ensure_florr_auto_afk_running()`Windows + exe不存在 + 用户输入n: mock `input`返回`"n"`, 断言不下载、不打开、直接return.
  - `ensure_florr_auto_afk_running()`Windows + exe不存在 + 用户确认下载 + 下载成功: mock `input`返回`""`、mock `_download_and_extract`返回True, 断言下载后仍然会调`subprocess.Popen`打开.
  - `_download_and_extract()`: mock `urllib.request.urlopen`返回假的chunked响应+mock `zipfile.ZipFile`, 断言正常路径下写临时文件、解压、删临时文件、返回True; mock `urlopen`抛异常, 断言捕获+清理临时文件(如果部分写了)+返回False.
- 手动验证(真实网络请求+真实Windows exe这部分, 跟Chrome bootstrap一样没法完全自动化测): 在真实Windows机器上跑一遍`main.py`, 确认: 首次没装时问下载→确认→真的下载解压到位、`segment.exe`真的能被打开、界面上点"run"后`latest.log`真的开始有内容、`afk_watch.poll_afk_pause()`真的能读到"Found AFK window"事件.
