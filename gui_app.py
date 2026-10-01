"""florr-auto-pathing 的控制面板 GUI. 无参跑 `python main.py` / 双击 exe 就进这里.

进程模型: 这个窗口不跑寻路逻辑 —— 点"开始"时它 (1) 用模态框引导好专用 Chrome
和 florr-auto-afk, (2) 把界面上的配置写进 config.json, (3) subprocess.Popen 一个
`main.py --worker` 子进程, 把它的 stdout 逐行灌进日志框. "停止"关掉子进程的 stdin
管道(EOF), worker 那边的看门线程读到 EOF 就先 reset_keyboard() 再退出.
"""
import os
import subprocess
import sys
import threading
import traceback
from tkinter import messagebox

import customtkinter as ctk

import app_config
import afk_watch
import cdp_bridge
import gui_chrome_flow

_IS_WINDOWS = sys.platform == "win32"
_LOG_MAX_LINES = 2000  # 日志框最多留这么多行, 再多就从头截掉


def worker_command():
    """拉起 worker 子进程的命令行. frozen(PyInstaller)时 sys.executable 就是我们
    自己的 exe, 直接带 --worker; 脚本模式下要显式 python + main.py 路径, 加 -u 让
    子进程 stdout 行缓冲(日志实时进面板)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--worker"]
    main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    return [sys.executable, "-u", main_py, "--worker"]


def build_worker_config(*, map_name, location, area, duration, short_limit,
                        enemy_ai, auto_switch, afk, farming_path=None,
                        avoid_death_spot=True):
    """界面上的值 -> app_config schema 的 dict. 坐标统一转成 list(JSON 里没有 tuple).
    farming_path 给定时(固定路径模式)写进 config, 否则为 None(区域模式).
    avoid_death_spot: 死亡后避让开关, 默认开."""
    cfg = {
        "map": map_name,
        "location": [int(location[0]), int(location[1])],
        "farming_duration": int(duration),
        "consecutive_short_round_limit": int(short_limit),
        "enemy_ai_enabled": bool(enemy_ai),
        "auto_switch_server": bool(auto_switch),
        "afk_enabled": bool(afk),
        "avoid_death_spot": bool(avoid_death_spot),
    }
    # 固定路径模式: area 是 None, 写默认区域占位(config schema 要 farming_area 键).
    if area:
        cfg["farming_area"] = [[int(area[0][0]), int(area[0][1])],
                               [int(area[1][0]), int(area[1][1])]]
    else:
        cfg["farming_area"] = [[int(app_config.DEFAULTS["farming_area"][0][0]),
                                int(app_config.DEFAULTS["farming_area"][0][1])],
                               [int(app_config.DEFAULTS["farming_area"][1][0]),
                                int(app_config.DEFAULTS["farming_area"][1][1])]]
    cfg["farming_path"] = [[int(x), int(y)] for (x, y) in farming_path] if farming_path else None
    return cfg


def parse_positive_ints(*strs):
    """把界面上的数字框字符串批量转成正整数. 任一非法(非整数 / <=0)返回 None.
    不用 assert —— python -O 会把 assert 整个剥掉."""
    out = []
    for s in strs:
        try:
            n = int(s)
        except (TypeError, ValueError):
            return None
        if n <= 0:
            return None
        out.append(n)
    return out


_MAP_PX = 300           # maps/*.png 都是 300x300; 派生坐标 clamp 到 [0, _MAP_PX-1]
_DERIVED_AREA_HALF = 12  # 只点了目标点没框区域时, 以点为中心生成的方块半边长(图像像素)


def _clamp_px(v):
    return max(0, min(_MAP_PX - 1, int(v)))


def resolve_point_and_area(point, area):
    """目标点和刷怪区域二选一即可(都给也行). 返回补全后的 (point, area);
    两个都没有则返回 (None, None), 让调用方报错.
      - 只有区域: 目标点 = 区域中心
      - 只有点: 刷怪区域 = 以点为中心的小方块(clamp 进地图范围)
    """
    if point is None and area is None:
        return None, None
    if area is None:
        x, y = int(point[0]), int(point[1])
        h = _DERIVED_AREA_HALF
        area = [(_clamp_px(x - h), _clamp_px(y - h)),
                (_clamp_px(x + h), _clamp_px(y + h))]
    if point is None:
        (x1, y1), (x2, y2) = area
        point = ((int(x1) + int(x2)) // 2, (int(y1) + int(y2)) // 2)
    return point, area


def start_afk(*, exe_exists, running, confirm_download):
    """AFK 开关打开时的决策(纯函数, 副作用由调用方按返回值执行).
    already: 已在跑, 什么都不用做
    declined: exe 缺, 用户拒绝下载
    downloaded / download_failed: exe 缺, 下过了(成/败)
    started: exe 在, 需要调用方去 ensure_florr_auto_afk_running()
    """
    if running:
        return "already"
    if not exe_exists:
        if not confirm_download():
            return "declined"
        return "downloaded" if afk_watch.download_florr_auto_afk() else "download_failed"
    return "started"


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("florr-auto-pathing")
        self.geometry("880x560")
        ctk.set_appearance_mode("dark")

        self._cfg = app_config.load_config()
        self.proc = None
        self._reader = None
        self._closing = False

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ---- 侧栏 ----
        side = ctk.CTkFrame(self, width=120, corner_radius=0)
        side.grid(row=0, column=0, sticky="nsew")
        side.grid_rowconfigure(4, weight=1)  # spacer 行
        self._pages = {}
        for i, name in enumerate(("控制台", "账号", "时间表")):
            btn = ctk.CTkButton(side, text=name, anchor="w",
                                command=lambda n=name: self._show_page(n))
            btn.grid(row=i, column=0, padx=10, pady=(10 if i == 0 else 4, 4), sticky="ew")
            if name != "控制台":
                btn.configure(state="disabled")  # 阶段2

        # 底部大 AFK 开关
        afk_box = ctk.CTkFrame(side, fg_color="transparent")
        afk_box.grid(row=5, column=0, padx=10, pady=14, sticky="ew")
        ctk.CTkLabel(afk_box, text="自动检测 AFK", font=("", 12)).pack()
        self.afk_switch = ctk.CTkSwitch(afk_box, text="", command=self._on_afk_toggle)
        self.afk_switch.pack(pady=4)
        if self._cfg["afk_enabled"]:
            self.afk_switch.select()
            # 光 select() 只是把开关画成"开", 不代表 florr-auto-afk 真在跑 ——
            # 上次开着关掉程序 / 重启电脑后 segment.exe 多半已经没了, 而 poll_afk_pause()
            # 读不到它的日志就永远不暂停. 启动时按持久化的状态主动补一次 ensure
            # (跟用户手动拨到"开"一个效果). 用 after() 推到窗口建好之后再跑.
            self.after(400, self._ensure_afk)
        if not _IS_WINDOWS:
            self.afk_switch.configure(state="disabled")
            ctk.CTkLabel(afk_box, text="(仅 Windows)", font=("", 9),
                         text_color="gray").pack()

        # ---- 控制台页 ----
        self.content = ctk.CTkFrame(self)
        self.content.grid(row=0, column=1, sticky="nsew", padx=12, pady=12)
        self.content.grid_columnconfigure(0, weight=3)
        self.content.grid_columnconfigure(1, weight=2)
        self.content.grid_rowconfigure(1, weight=1)

        self.map_menu = ctk.CTkOptionMenu(
            self.content, values=list(app_config._VALID_MAPS),
            command=self._on_map_change)
        self.map_menu.set(self._cfg["map"])
        self.map_menu.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        from gui_map_picker import MapPicker
        self.picker = MapPicker(
            self.content,
            on_point_change=self._on_picker_point,
            on_area_change=self._on_picker_area,
            on_path_change=self._on_picker_path)
        self.picker.grid(row=1, column=0, sticky="nsew", padx=(0, 10))

        right = ctk.CTkFrame(self.content, fg_color="transparent")
        right.grid(row=1, column=1, sticky="nsew")

        # 刷怪模式: 随机区域(默认, 框区域随机走) / 固定路径(画折线沿它走).
        self.mode_menu = ctk.CTkSegmentedButton(
            right, values=["随机区域", "固定路径"], command=self._on_mode_change)
        self.mode_menu.set("随机区域")
        self.mode_menu.pack(anchor="w", pady=(0, 6))

        self.duration_entry = self._labeled_entry(right, "刷怪时长 (秒)",
                                                  str(self._cfg["farming_duration"]))
        self.enemy_switch = ctk.CTkSwitch(right, text="索敌 AI (YOLO 追击/规避)")
        self.enemy_switch.pack(anchor="w", pady=6)
        if self._cfg["enemy_ai_enabled"]:
            self.enemy_switch.select()
        self.short_entry = self._labeled_entry(
            right, "连续短局阈值", str(self._cfg["consecutive_short_round_limit"]))
        self.autoswitch_check = ctk.CTkCheckBox(right, text="连续没刷满自动换服务器")
        self.autoswitch_check.pack(anchor="w", pady=6)
        if self._cfg["auto_switch_server"]:
            self.autoswitch_check.select()
        self.avoid_death_check = ctk.CTkCheckBox(
            right, text="死亡后避让死亡区域（没换服时）")
        self.avoid_death_check.pack(anchor="w", pady=6)
        if self._cfg.get("avoid_death_spot", True):
            self.avoid_death_check.select()

        self.log_box = ctk.CTkTextbox(right, font=("Menlo", 11), state="disabled")
        self.log_box.pack(fill="both", expand=True, pady=(8, 0))

        self.status_label = ctk.CTkLabel(self.content, text="状态：未运行", anchor="w")
        self.status_label.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 4))

        self.start_btn = ctk.CTkButton(self.content, text="▶ 开始", height=40,
                                       command=self._on_start_stop)
        self.start_btn.grid(row=3, column=0, columnspan=2, sticky="ew")

        # 初值灌进选择器
        self.picker.load_map(self._cfg["map"])
        self.picker.set_point(tuple(self._cfg["location"]))
        self.picker.set_area([tuple(p) for p in self._cfg["farming_area"]])
        self._point = tuple(self._cfg["location"])
        self._area = [tuple(p) for p in self._cfg["farming_area"]]
        self._path = None
        self._mode = "随机区域"
        saved_path = self._cfg.get("farming_path")
        if saved_path:
            # config 里存过固定路径 → 恢复它, 并把模式切到固定路径.
            self._path = [tuple(p) for p in saved_path]
            self._point = tuple(saved_path[0])
            self.picker.set_path(saved_path)
            self.picker.set_mode("path")
            self.mode_menu.set("固定路径")
            self._mode = "固定路径"

        # 放在最后: 这个钩子会往 self.log_box 里写, 得等控件都建好.
        self.report_callback_exception = self._report_exception

    def _report_exception(self, exc_type, exc_value, exc_tb):
        # console=False 后 Tk 回调里未捕获的异常本来会写进一个丢弃一切的 stderr ——
        # 这是这个窗口唯一能把"出错了"告诉用户的地方.
        self._log_line(f"❌ {exc_type.__name__}: {exc_value}\n")
        traceback.print_exception(exc_type, exc_value, exc_tb)

    def _labeled_entry(self, parent, label, initial):
        ctk.CTkLabel(parent, text=label, anchor="w").pack(anchor="w")
        e = ctk.CTkEntry(parent)
        e.insert(0, initial)
        e.pack(anchor="w", fill="x", pady=(0, 6))
        return e

    def _on_map_change(self, name):
        self.picker.load_map(name)
        self.picker.set_point(None)
        self.picker.set_area(None)
        self.picker.clear_path()
        self._point = None
        self._area = None
        self._path = None
        self._log_line(f"已切到 {name}，请重新点目标点 / 框刷怪区 / 画固定路径\n")

    def _on_mode_change(self, mode):
        self._mode = mode
        self.picker.set_mode("path" if mode == "固定路径" else "area")
        if mode == "固定路径":
            self._log_line("固定路径模式：在地图上左键依次点路径点（第一个点即目标点），"
                           "右键撤销最后一点；画完点开始\n")
        else:
            self._log_line("随机区域模式：左键点目标点 / 拖框刷怪区\n")

    def _on_picker_point(self, pt):
        self._point = pt

    def _on_picker_area(self, area):
        self._area = [tuple(area[0]), tuple(area[1])]

    def _on_picker_path(self, path):
        self._path = [tuple(p) for p in path] if path else None
        if self._path:
            self._point = self._path[0]   # 路径起点即目标点

    def _current_values(self):
        return dict(
            map_name=self.map_menu.get(),
            location=self._point,
            area=self._area,
            duration=self.duration_entry.get(),
            short_limit=self.short_entry.get(),
            enemy_ai=bool(self.enemy_switch.get()),
            auto_switch=bool(self.autoswitch_check.get()),
            afk=bool(self.afk_switch.get()),
            avoid_death_spot=bool(self.avoid_death_check.get()),
            mode=self._mode,
            # 只有固定路径模式才把路径写进 config —— 否则切回随机区域后 _path
            # 残留上次画的路径, config 里 farming_path 非空, worker 还是按路径刷.
            farming_path=self._path if self._mode == "固定路径" else None,
        )

    def _log_line(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text)
        # bot 是按天跑的, 日志框不能无限长: 超过上限就把最老的那些行整段删掉,
        # 只留最近 _LOG_MAX_LINES 行.
        lines = int(self.log_box.index("end-1c").split(".")[0])
        if lines > _LOG_MAX_LINES:
            self.log_box.delete("1.0", f"end-{_LOG_MAX_LINES}l")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _on_start_stop(self):
        if self.proc and self.proc.poll() is None:
            self._stop_worker()
        else:
            self._start_worker()

    def _start_worker(self):
        # fail-fast: 先跑本地那几个便宜的校验, 再走重活儿 —— Chrome 重启 / AFK 下载
        # (最多卡 90 秒) / 切全屏都在校验之后. 点了开始却没框刷怪区 / 数字填错的用户
        # 立刻收到提示, 不会先被一串对话框拖一遍. 顺序:
        # 校验 location/area → 校验数字 → 专用 Chrome 就绪 → (开关开着才)确保
        # florr-auto-afk → 切全屏确认(紧贴 Popen 之前). 任一步取消就静默中止.
        vals = self._current_values()
        if vals["mode"] == "固定路径":
            # 固定路径: 必须画了 ≥2 个点; 目标点 = 路径起点(已由 picker 同步).
            if not vals["farming_path"] or len(vals["farming_path"]) < 2:
                self._log_line("⚠️ 固定路径模式：请在地图上左键依次点至少 2 个路径点\n")
                return
            point, area = vals["farming_path"][0], None
            vals["location"] = point
            vals["area"] = area
        else:
            # 随机区域模式: config 里绝不能残留 farming_path —— 上次切固定路径时
            # 保存的 config.json 还带着旧路径, 不盖掉的话 worker 读出来仍是路径刷怪.
            vals["farming_path"] = None
            point, area = resolve_point_and_area(vals["location"], vals["area"])
            if point is None:
                self._log_line("⚠️ 请在地图上点一个目标点, 或框一个刷怪区域(二选一即可)\n")
                return
            vals["location"] = point
            vals["area"] = area
        # 只要校验结果, 转好的数字用不上 —— build_worker_config() 自己会
        # int() 一遍 vals 里的原始字符串.
        if parse_positive_ints(vals["duration"], vals["short_limit"]) is None:
            self._log_line("⚠️ 时长 / 短局阈值必须是正整数\n")
            return

        # Chrome 引导: 只有"专用 Chrome 没就绪"时才会关掉现有 Chrome 并开一个新的.
        # 上一轮开始已经拉起过、还开着的话, is_dedicated_chrome_ready() 为真, 这里
        # 直接跳过 —— 用户会觉得"点了开始却没开浏览器", 所以把跳过的原因也打到日志里.
        self._log_line("检查专用 Chrome…\n")
        self.attributes("-topmost", True)   # 让引导弹窗浮到全屏游戏之上
        try:
            if cdp_bridge.is_dedicated_chrome_ready():
                self._log_line("专用 Chrome 已就绪(9222 端口 + florr.io 标签页), 不重开\n")
            else:
                self._log_line("启动专用 Chrome —— 会先关掉现有 Chrome, 请在弹出的确认框点\"确定\"\n")
                gui_chrome_flow.ensure_chrome_ready(self)
                self._log_line("专用 Chrome 就绪\n")
        except gui_chrome_flow.ChromeSetupCancelled:
            self._log_line("已取消(专用 Chrome 未就绪)\n")
            return
        except RuntimeError as e:
            self._log_line(f"❌ 启动专用 Chrome 失败: {e}\n")
            return
        finally:
            self.attributes("-topmost", False)

        if bool(self.afk_switch.get()):
            self._ensure_afk()

        # 这一步之后 florr.io 多半已经在 F11 全屏了; Windows 的前台锁会让一个
        # 普通窗口弹出来的对话框排在全屏游戏后面(用户只看到画面卡住, 找不到框).
        # 临时把主窗口置顶, 让对话框跟着浮到全屏之上, 问完立刻取消置顶.
        self.attributes("-topmost", True)
        try:
            ok = messagebox.askokcancel(
                "把 florr.io 切到全屏",
                "开始前请把 florr.io 切到全屏(任意分辨率), 然后点确定。",
                parent=self)
        finally:
            self.attributes("-topmost", False)
        if not ok:
            self._log_line("已取消(未确认全屏)\n")
            return

        vals.pop("mode", None)   # mode 只用于上面的分流判断, 不落 config
        cfg = build_worker_config(**vals)
        # 固定路径坐标钳进 [0, 299] —— 用户可能点在放大视图边缘外.
        if cfg.get("farming_path"):
            cfg["farming_path"] = [
                [_clamp_px(px), _clamp_px(py)] for (px, py) in cfg["farming_path"]
            ]
            cfg["location"] = cfg["farming_path"][0]   # 路径起点即目标点
        app_config.save_config(cfg)
        self._cfg = cfg

        # 两个平台都给子进程 PYTHONUNBUFFERED=1: frozen(PyInstaller)build 没走
        # worker_command() 里的 -u, 不设这个 Windows 下子进程 stdout 会块缓冲,
        # 日志框只能几 KB 一跳.
        # PYTHONIOENCODING=utf-8: 中文 Windows 上子进程 stdout 默认按 locale(GBK)
        # 编码, worker 打的 emoji/中文里带的字节 GBK 解不了, _pump_log 那边整个
        # 线程会 UnicodeDecodeError 崩掉. 两端都钉 utf-8, 再在读取侧 errors=replace
        # 兜底任何漏网的坏字节.
        kwargs = {"env": {**os.environ, "PYTHONUNBUFFERED": "1",
                          "PYTHONIOENCODING": "utf-8"}}

        # stdin=PIPE 不是为了往里写东西, 而是为了能"关"它: 关掉管道 = 给 worker
        # 发停止信号(见 _stop_worker). 不给 PIPE 的话子进程会继承 GUI 的 stdin,
        # 打包成 console=False 的 exe 后那是个死句柄, 永远读不到 EOF.
        self.proc = subprocess.Popen(
            worker_command(), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            bufsize=1, **kwargs)
        self._log_line("—— worker 已启动 ——\n")
        self.start_btn.configure(text="■ 停止")
        self._reader = threading.Thread(target=self._pump_log, args=(self.proc,),
                                        daemon=True)
        self._reader.start()

    def _pump_log(self, proc):
        # 这是后台线程: 窗口一旦 destroy 掉, self.after() 会抛 TclError(而
        # console=False 下那个 traceback 谁也看不到). _closing 一置起就安静收摊.
        for line in proc.stdout:
            if self._closing:
                return
            self.after(0, self._log_line, line)
            self.after(0, lambda l=line: self.status_label.configure(
                text="状态：" + l.strip()[:60]) if l.strip() else None)
        code = proc.wait()
        if self._closing:
            return
        # 把 proc 绑进回调: 这条 pump 属于哪个进程, 回调就只对那个进程生效 ——
        # 否则一个慢半拍的旧 pump 会把刚启动的新 worker 的状态给清了.
        self.after(0, self._on_worker_exit, proc, code)

    def _stop_worker(self):
        if not self.proc:
            return
        try:
            if self.proc.stdin:
                # 关掉管道 = worker 那边 stdin 读到 EOF, 它自己 reset_keyboard()
                # 再退出. 之前用的 CTRL_BREAK_EVENT 只能发给"调用方所在的控制台
                # 进程组" —— 打包成 console=False 的 exe 后 GUI 根本没有控制台,
                # 那一发必然失败, 3 秒后直接 kill, 按住的 space+WASD 全留在游戏里.
                self.proc.stdin.close()
        except Exception as e:
            self._log_line(f"发送停止信号失败: {e}\n")
        if not _IS_WINDOWS:
            try:
                self.proc.terminate()   # POSIX 双保险: SIGTERM 处理器也会 reset_keyboard()
            except Exception:
                pass
        self.after(3000, lambda p=self.proc: self._force_kill_if_alive(p))

    def _force_kill_if_alive(self, proc):
        # 3 秒前排这个兜底时对的是 proc 那个进程; 期间它可能已经退干净、用户还
        # 又点了一次开始 —— 那 self.proc 就是另一个进程了, 这一发不能打到它身上.
        if proc is None or proc is not self.proc:
            return
        if proc.poll() is None:
            proc.kill()
            self._log_line("—— worker 未响应, 已强制结束 ——\n")

    def _on_worker_exit(self, proc, code):
        if proc is not self.proc:
            return
        self._log_line(f"—— worker 结束 (退出码 {code}) ——\n")
        self.status_label.configure(text="状态：未运行")
        self.start_btn.configure(text="▶ 开始")
        self.proc = None

    def _show_page(self, name):
        pass  # Task 8: 控制台是唯一可用页, 其余灰置

    def _persist_afk(self, enabled):
        cfg = app_config.load_config()
        cfg["afk_enabled"] = bool(enabled)
        app_config.save_config(cfg)
        self._cfg = cfg

    def _busy_modal(self, text):
        """一个没有关闭按钮的小提示框, 显示"后台正在干重活儿". 不 grab_set() ——
        AFK 准备(最长几分钟的下载)期间主窗口要保持能点, 不能把整个界面锁死.
        返回 toplevel, 调用方负责 destroy()."""
        top = ctk.CTkToplevel(self)
        top.title("")
        top.geometry("320x90")
        top.resizable(False, False)
        top.transient(self)
        top.attributes("-topmost", True)
        top.protocol("WM_DELETE_WINDOW", lambda: None)   # 不给关
        ctk.CTkLabel(top, text=text).pack(expand=True, padx=20, pady=20)
        return top

    def _ensure_afk(self):
        """AFK 开关打开时确保 florr-auto-afk 在跑. 决策(要不要下载)留在主线程 ——
        它得弹模态框; 真正的重活儿(350MB 下载 / 最长 90 秒的启动等待)扔进后台线程,
        否则 Tk 主循环一卡好几分钟, Windows 会把窗口画成"未响应"让用户去强杀它.
        本函数起完线程就返回, 不等结果(AFK 助手晚几秒起来没关系, worker 那边
        poll_afk_pause() 只是在 tail 它的日志)."""
        if not _IS_WINDOWS:
            return
        if getattr(self, "_afk_busy", False):
            return   # 已经有一个准备线程在跑, 别再起一个

        # download_florr_auto_afk() 本身也是重活儿, 但它藏在 start_afk() 里 ——
        # 把"确认下载"这一步之后的全部动作(下载 + 启动)一起放进后台线程, 只留
        # askyesno 在主线程上问.
        exe_exists = os.path.isfile(afk_watch._EXE_PATH)
        running = afk_watch.is_florr_auto_afk_running()
        if running:
            self._log_line("AFK: already\n")
            return
        if not exe_exists:
            if not messagebox.askyesno(
                    "下载 florr-auto-afk?",
                    "没检测到 florr-auto-afk(处理 AFK 弹窗用). 现在下载? 约 350MB.",
                    parent=self):
                self._log_line("AFK: declined\n")
                return

        self._afk_busy = True
        self.afk_switch.configure(state="disabled")   # 准备期间不许再拨
        modal = self._busy_modal("AFK 助手准备中，请稍候…\n(界面仍可操作)")

        def _work():
            try:
                outcome = start_afk(exe_exists=exe_exists, running=False,
                                    confirm_download=lambda: True)
                if outcome in ("started", "downloaded"):
                    afk_watch.ensure_florr_auto_afk_running()
            except Exception as e:                       # 后台线程里没人接异常
                outcome = f"出错: {e}"
            self.after(0, self._finish_ensure_afk, modal, outcome)

        threading.Thread(target=_work, daemon=True).start()

    def _finish_ensure_afk(self, modal, outcome):
        self._afk_busy = False
        try:
            self.afk_switch.configure(state="normal")
        except Exception:
            pass
        try:
            modal.destroy()
        except Exception:
            pass
        self._log_line(f"AFK: {outcome}\n")

    def _on_afk_toggle(self):
        enabled = bool(self.afk_switch.get())
        if enabled:
            self._ensure_afk()
        else:
            afk_watch.stop_florr_auto_afk()
            self._log_line("AFK: 已停止 florr-auto-afk\n")
        self._persist_afk(enabled)

    def on_closing(self):
        # _stop_worker 只 self.after(3000, ...) 排一个兜底 kill —— 关窗时 mainloop
        # 马上就结束了, 那个回调根本不会跑. 所以这里同步、有上限地收干净子进程,
        # 否则慢 / 卡死 / 收不到停止信号的 worker(连同它的 segment.exe 孙进程)会被
        # 甩给 init 继续跑, 没有 UI 能再停它.
        self._closing = True        # 让还在跑的 _pump_log 线程别再往已死的窗口排回调
        proc = self.proc
        if proc and proc.poll() is None:
            self._stop_worker()     # 关 stdin(EOF) + POSIX 上补一发 SIGTERM
            try:
                proc.wait(timeout=3)
            except Exception:
                pass
            if proc.poll() is None:
                proc.kill()
        self.destroy()


def main():
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
