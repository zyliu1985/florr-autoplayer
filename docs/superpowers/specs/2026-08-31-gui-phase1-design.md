# GUI 控制面板 阶段1 — design

## Problem

[main.py](../../../main.py) 现在是双击即跑的阻塞死循环，所有可调项（`apply_map("desert")`、`location = (22, 32)`、`farming_area = [(9, 8), (51, 56)]`、`farming_duration = 300`、`CONSECUTIVE_SHORT_ROUND_LIMIT = 2`）硬编码在 `if __name__ == "__main__"` 里。换地图/换刷怪区要改源码重打包。启动引导（[cdp_bridge.py](../../../cdp_bridge.py) 的 `launch_dedicated_chrome()`、[afk_watch.py](../../../afk_watch.py) 的 `ensure_florr_auto_afk_running()` / `_prompt_download_confirm()`）靠命令行 `input()` 等回车，没有可视界面。

用户要一个好看的 GUI：右下角一个大的「自动检测 AFK」开关，能在界面里配地图 / 刷怪区域 / 目标点、能开关索敌 AI（[enemy_detect.py](../../../enemy_detect.py) 的 YOLO 追击/规避），以及（阶段2）账号管理 + 每日时间表调度。

## Goal

阶段1：CustomTkinter 深色 GUI，侧栏导航（`控制台` 页可用，`账号` / `时间表` 页占位、灰置），右下角大 AFK 开关。控制台页能：选地图、在地图上点目标点 / 拖框刷怪区、设时长、开关索敌 AI、开关自动换服务器、看 worker 实时日志、一键开始/停止。所有配置落到 exe 同级 `config.json`，worker 子进程读它跑。启动引导从命令行 `input()` 搬进 GUI 模态框。

阶段2（**本方案不实现**，只留占位页 + config 预留位）：账号管理、每日时间表（某账号 HH:MM–HH:MM 刷）。

## Non-goals

- **账号管理 / 时间表调度** —— 阶段2 另开 spec。本方案只放两个灰置的侧栏页 + 不写 config 键。
- **索敌 AI 细分参数**（`AVOID_TRIGGER_PX` / `CAUTIOUS_HOLD_PX` / `ENEMY_SCAN_INTERVAL` 等）—— 只做一个总开关，细调仍改源码。
- **多语言 / 换肤 / 自定义主题** —— CustomTkinter 默认深色一套到底。
- **worker 打包成独立 exe** —— 单 exe + `--worker` 参数自我 spawn。
- **游戏内 HUD [overlay.py](../../../overlay.py) 改动** —— 完全不动，worker 照常拉起，跟桌面上的 GUI 窗口互不相干。
- **地图选择器的平移/多级缩放打磨** —— 阶段1 只做「适配窗口显示 + 单指针缩放」的最简版，拖动平移可后置。
- **保留第二套命令行交互引导** —— `launch_dedicated_chrome()` 里的 `input()` 流程不再被 worker 调用；直接 `python main.py --worker` 调试时，Chrome 没就绪就报错退出，不再等回车。

## 进程模型

`main.py` 加 `argparse`：

- 无参 / `--gui` → `import gui_app; gui_app.main()`
- `--worker` → 现有 `__main__` 循环体搬进 `run_worker(cfg)` 后调用它

**GUI 进程**（CustomTkinter 主循环）点「开始」：

1. GUI 线程内跑交互引导：`gui_chrome_flow.ensure_chrome_ready(parent)`（模态框，见下）；若 AFK 开关为开，按需下载 / 启动 florr-auto-afk。
2. `app_config.save_config(cfg)` 写 `config.json`。
3. `proc = subprocess.Popen(worker_command(), stdout=PIPE, stderr=STDOUT, text=True, bufsize=1, ...)`。
   `worker_command()`：frozen 时 `[sys.executable, "--worker"]`（`sys.executable` 就是 exe）；脚本时 `[sys.executable, "-u", os.path.abspath("main.py"), "--worker"]`（`-u` 让子进程 stdout 行缓冲，日志实时进面板；frozen 时改设 `env["PYTHONUNBUFFERED"]="1"`）。
4. 起一个守护读取线程：逐行读 `proc.stdout`，`app.after(0, append_log, line)` 回主线程刷日志控件。

**停止**：`proc.terminate()`。worker 装 `signal.SIGTERM`（Windows 上 `SIGBREAK`）处理器 → `reset_keyboard()` → `sys.exit(0)`。GUI 等 3s，还没退就 `proc.kill()`。

**worker 自己结束**（崩溃 / 循环 return）：读取线程 EOF + `proc.poll()` 非 None → `app.after` 把按钮弹回「开始」，日志追加一行退出码。

**直接 `python main.py --worker`**（调试用）：`run_worker()` 开头调 `cdp_bridge.is_dedicated_chrome_ready()`，False 就 `print` 「请从 GUI 启动（Chrome 未就绪）」并 `sys.exit(1)`。不再维护命令行版的交互引导。

## 文件改动

| 文件 | 改动 |
|---|---|
| `app_config.py` **新** | `DEFAULTS`（= 现硬编码值）、`load_config()`、`save_config(cfg)`、`_coerce(cfg)`（逐键类型校验，坏值回默认 + `print` 警告）。GUI 与 worker 共用，**不 import 任何 GUI 库**。`CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "config.json")`（跟 `_CHROME_PROFILE_DIR` / `_INSTALL_ROOT` 同一套 `sys.argv[0]` 语义）|
| `gui_app.py` **新** | GUI 入口。主窗口、侧栏（3 页 + 底部 AFK 开关）、控制台页装配、worker `Popen`/读取线程/启停编排、日志 `CTkTextbox`。`main()` 函数供 `main.py` 调 |
| `gui_map_picker.py` **新** | `MapPicker(ctk.CTkFrame)`：内嵌 `tkinter.Canvas`，Pillow 读 `maps/<map>.png` → `ImageTk` 显示；左键点=目标点、左键拖=矩形、滚轮=缩放；叠加十字线 + 矩形标记。坐标变换 `widget_to_image(px, py, view)` / `image_to_widget(...)` 抽成模块级纯函数（可脱离 tk 单测）。回调 `on_point_change` / `on_area_change` 吐出图像像素坐标 |
| `gui_chrome_flow.py` **新** | `ensure_chrome_ready(parent)`：`cdp_bridge.is_dedicated_chrome_ready()` 真则直接返回；否则模态①「即将关闭所有 Chrome，继续？/ 取消」→ `cdp_bridge.quit_and_launch_chrome()` → 模态②「在新窗口迁移账号并打开 florr.io，完成后点『检测』」按钮 → `cdp_bridge.wait_for_florr_tab(15)`，未找到提示重试，循环到找到或用户取消（取消 → 抛 `ChromeSetupCancelled`，GUI 捕获后按钮弹回「开始」）|
| `main.py` **改** | 顶部 `argparse`；`__main__` 循环体 → `def run_worker(cfg)`；`cfg = app_config.load_config()`；`apply_map(cfg["map"])`、`location = tuple(cfg["location"])`、`farming_area = [tuple(p) for p in cfg["farming_area"]]`、`farming_duration = cfg["farming_duration"]`、`CONSECUTIVE_SHORT_ROUND_LIMIT = cfg["consecutive_short_round_limit"]`；`auto_farming(..., enemy_ai_enabled=cfg["enemy_ai_enabled"])`；换服务器分支加 `if cfg["auto_switch_server"]` 门。SIGTERM/SIGBREAK 处理器调 `reset_keyboard()` 后退出 |
| `cdp_bridge.py` **改** | 新增公有：`is_dedicated_chrome_ready()`（= 现 `_is_dedicated_chrome_ready`）、`quit_and_launch_chrome()`（`_quit_all_chrome()` + `_launch_chrome_process()`，非交互）、`wait_for_florr_tab(timeout, interval=1)`（包 `_poll_for_florr_tab`）。`launch_dedicated_chrome()` 原样保留（不再被 worker 调，留给可能的手动使用），内部改成调这几个新公有函数去重 |
| `afk_watch.py` **改** | 新增公有：`is_florr_auto_afk_running()`（= 现 `_is_florr_auto_afk_running`）、`stop_florr_auto_afk()`（`taskkill /IM segment.exe /F`，非 win32 直接 return，吞非零码）、`download_florr_auto_afk()`（= 现 `_download_and_extract`，去掉 `_prompt_download_confirm()`，确认交给 GUI）。`ensure_florr_auto_afk_running()` 保留 |
| `requirements.txt` **改** | 加 `customtkinter`（`pillow` 已在）|
| `main.spec` **改** | `hiddenimports += ["customtkinter", "PIL._tkinter_finder"]`；`for pkg in (..., "customtkinter")` 加进 `collect_all` 循环（它的主题 JSON 是 data 文件）；`EXE(..., console=False)`（GUI 无控制台窗；worker 由 `Popen(stdout=PIPE)` 收日志，不依赖控制台）|
| `PACKAGING.md` **改** | 说明新入口（双击 = GUI）、`config.json` 位置、`console=False` 的取舍 |

## `config.json` schema

```json
{
  "map": "desert",
  "location": [22, 32],
  "farming_area": [[9, 8], [51, 56]],
  "farming_duration": 300,
  "consecutive_short_round_limit": 2,
  "enemy_ai_enabled": true,
  "auto_switch_server": true,
  "afk_enabled": false
}
```

- `DEFAULTS` 里每个值 = 现在 `main.py` 硬编码的那个。
- `load_config()`：文件不存在 → 返回 `deepcopy(DEFAULTS)`；能读 → 逐键 `_coerce`：缺键补默认、类型/结构不对（如 `location` 不是 2 个数）回该键默认 + `print` 一句警告。整份 JSON 坏（顶层不是 dict）→ 全默认 + 警告，**不动坏文件**（跟 `afk_watch._write_afk_config` 备份 `.bak` 的谨慎一致；这里只读不写，不需要备份）。
- `map` 合法值 = `maps/` 下的 png 去扩展名（`desert` / `ocean` / `anthell`）。非法 → 回 `"desert"` + 警告。
- `enemy_ai_enabled=false`：`auto_farming()` 里跳过 `enemy_detect.scan_enemies` 整块，强制 `enemy_decision = ("wander", None)`（等价于「没有可打/需规避目标」，纯随机漫游）。flag 由 `run_worker` 透传进 `auto_farming(..., enemy_ai_enabled=...)`。
- `afk_enabled`：仅 GUI 用（重启后恢复开关态 + 决定是否启动 florr-auto-afk）。worker **不读**它——`poll_afk_pause()` 只读日志，florr-auto-afk 没跑就恒返回 False，无害。

## 控制台页布局（方案 A / A1）

**侧栏**（宽 ~110px）：`控制台`（选中）/ `账号`（`state=disabled`）/ `时间表`（`state=disabled`），`spacer` 撑开，底部大 AFK 开关 —— 竖排：小标签「自动检测 AFK」+ 大号 `CTkSwitch`。常驻，不随页切换消失。

**主区**：
- 顶：地图下拉 `CTkOptionMenu`（desert / ocean / anthell）。
- 中：`flex` 行。左半 `MapPicker`（`flex:1.1`，最小高 ~150px）：左键点 = 目标点（画十字线）、左键拖 = 刷怪矩形（画框）、滚轮 = 整数 1–4× 缩放，视图中心跟随指针（不做独立拖动平移；平移后置）。右半竖排：`时长` 数字框、`索敌 AI` `CTkSwitch`、`短局阈值` 数字框、`连续没刷满自动换服务器` `CTkCheckBox`（默认勾）、日志 `CTkTextbox`（`flex:1`，只读，等宽字体）。
- 状态行：读 worker 日志里最近一次 `overlay.update` 打的状态，读不到显「未运行」。（阶段1 简化：直接显示 worker 最后一条非空日志行；结构化状态解析可后置。）
- 底：大按钮 `▶ 开始` ↔ `■ 停止`（运行态切换文字 + 颜色）。

数字框与 `MapPicker` 双向绑定：框里改数字 → `MapPicker.set_point/set_area` 重画；图上操作 → 回调写回框。

**切地图**：清空当前点/区域（坐标是每图独立的），日志提示「已切到 <map>，请重新框选」。（阶段2 的时间表若要「不同账号刷不同图」，再引入 per-map 存储并迁移 config；阶段1 扁平结构够用。）

## AFK 开关行为

- **开**：`afk_watch.is_florr_auto_afk_running()` 为真 → 什么都不做。否 → `_EXE_PATH` 存在则静默 `ensure_florr_auto_afk_running()`（它内部会写 config + Popen + 等启动）；`_EXE_PATH` 缺 → 模态「下载 florr-auto-afk？约 350MB，解压到 <路径>」，确认 → `afk_watch.download_florr_auto_afk()` 成功后再 `ensure_florr_auto_afk_running()`。写 `afk_enabled=true`。
- **关**：`afk_watch.stop_florr_auto_afk()`。写 `afk_enabled=false`。
- **Mac**：开关 `state="disabled"`，旁注「仅 Windows」。GUI / MapPicker / worker 其余功能照跑。

## 测试

- `test_app_config.py` **新**：`load_config` 缺文件 → 全默认；往返 `save`→`load` 一致；部分键 / 坏类型（`location` 给字符串、`farming_area` 给 3 元组、`map` 给非法名）逐一回默认；顶层非 dict → 全默认。
- `test_gui_map_picker.py` **新**：`widget_to_image` / `image_to_widget` 纯函数 —— 缩放 1×/2×/4×、边界 clamp、往返互逆。不实例化任何 tk 控件。
- `test_main_worker.py` **新**：`argparse` 分派（无参 → gui、`--worker` → worker）；`run_worker` 读 `config.json` 后各局部变量取值正确（`enemy_detect` / `cdp_bridge` / `pyautogui` 等重依赖 `monkeypatch` 打桩，只验配置接线，不真跑循环）。
- `test_cdp_bridge.py` **扩**：`is_dedicated_chrome_ready` / `quit_and_launch_chrome` / `wait_for_florr_tab` 三个新公有函数（沿用现有 `urlopen` / `subprocess` 打桩风格）。
- `test_afk_watch.py` **扩**：`is_florr_auto_afk_running` / `stop_florr_auto_afk`（`subprocess.run` 打桩）/ `download_florr_auto_afk`（无 `input()` 路径）。
- 现有测试全绿（`test_afk_watch.py` / `test_cdp_bridge.py` / `test_overlay.py` / `test_main_smoke.py` / …）。

## 阶段2 预留（不实现）

- 侧栏 `账号` / `时间表` 页：阶段1 是灰置占位。
- `config.json`：阶段1 不加账号/时间表键；阶段2 再引入 `accounts` / `schedule`，并处理「切地图 per-map 坐标」的迁移。
- worker 接口：`run_worker(cfg)` 已按「一次一份 config」设计，阶段2 的调度器 = GUI 按时间表改 `config.json` + 重启 worker，不需要 worker 侧改动。
