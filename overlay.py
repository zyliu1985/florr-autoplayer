"""悬浮状态窗 — 全屏运行main.py时显示寻路/移动进度.

macOS: 用PyObjC(AppKit)直接建原生窗口, 不用tkinter —— 实测tkinter的-topmost
只能在同一个macOS Space内置顶, florr.io开的是原生全屏(独立Space)时, tkinter
窗口会跳到另一个桌面上, 根本盖不到游戏画面上。AppKit窗口配合
NSWindowCollectionBehaviorCanJoinAllSpaces + FullScreenAuxiliary +
screensaver级别的窗口层级(1000), 才能真正跨Space盖在全屏游戏上面。

Windows: Windows没有macOS那种独立Space, 浏览器F11全屏是普通的无边框窗口
(不是独占全屏), 常规-topmost就能盖上去, 所以用tkinter无边框窗口即可, 不需要
AppKit那套技巧。但仍需win32扩展窗口样式做到点击穿透 + 不抢键盘焦点, 见
_WindowsOverlay。
"""
import sys
import time

_IS_MACOS = sys.platform == "darwin"
_IS_WINDOWS = sys.platform == "win32"

try:
    import AppKit
    import Foundation
except ImportError:
    AppKit = None
    Foundation = None

try:
    import tkinter as tk
except ImportError:
    tk = None

try:
    import ctypes
except ImportError:
    ctypes = None


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


class _NullOverlay:
    """悬浮窗建不起来时的空壳替代品, 调用什么都不做, 绝不炸主程序."""

    def update(self, state=None, pos=None, target=None, message=None):
        return None

    def show_warning(self, message):
        return None

    def hide_warning(self):
        return None

    def close(self):
        return None


# screensaver窗口层级(CGWindowLevel), 比普通置顶(status级别~25)高得多 ——
# 实测普通status级别 + canJoinAllSpaces 依然会被系统分到另一个Space,
# 只有这个级别才能真正跨越florr.io的全屏Space显示。
_SCREENSAVER_LEVEL = 1000

# 窗口位置需避开 utils.py 的屏幕探测区域:
#   check_stage() 探测像素 (316,32) 和 (156,35)
#   get_map() 截取小地图区域 [1600,20,1900,320] (右上角)
#   abandon_game() 点击 (307,32)
# 左上角、顶部往下200px, 落在这些区域下方, 留出安全边距.
_WIDTH, _HEIGHT = 260, 150
_LEFT, _TOP_OFFSET = 20, 200

# 亮橙底+黑字: 实测过暗色半透明底(#1e1e1e)会跟游戏画面糊成一片肉眼看不见,
# 亮色才能在任意游戏背景上都保证看得见.
_BG_COLOR = (1.0, 0.6, 0.0, 0.92)  # R, G, B, alpha

_CONFIRM_WIDTH, _CONFIRM_HEIGHT = 340, 150
_CONFIRM_MESSAGE = "florr.io已就绪 — 手动进入全屏(F11)后点击下方按钮开始"
_CONFIRM_BUTTON_LABEL = "开始运行"

# 比StatusOverlay的小状态框、_MacConfirmDialog/_WindowsConfirmDialog都大 ——
# 这个是给"连续检测不到玩家位置"这种需要用户立刻注意到的警告用的, 放屏幕
# 正中央, 字也大, 不能让人错过.
_WARNING_WIDTH, _WARNING_HEIGHT = 560, 200


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


class StatusOverlay:
    _FIELDS = ("state", "pos", "target", "message")
    _LABELS_ZH = {"state": "状态", "pos": "位置", "target": "目标", "message": "消息"}

    def __init__(self):
        self._start = time.time()
        self._state = {"state": "-", "pos": None, "target": None, "message": "-"}
        # 一旦update/close在窗口没了之后抛异常, 就锁死后续调用为空操作, 绝不再炸主程序.
        self._dead = False
        # 警告弹窗惰性建一次, 后续show_warning/hide_warning只切换显示/隐藏 —— 跟
        # StatusOverlay主窗口本身"建一次、update多次"是同一个模式.
        self._warning_window = None
        self._warning_label = None

        app = AppKit.NSApplication.sharedApplication()
        # Accessory: 不占Dock图标、不抢应用切换的焦点, 纯后台悬浮窗.
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        self._app = app

        screen_frame = AppKit.NSScreen.mainScreen().frame()
        screen_height = screen_frame.size.height
        y_origin = screen_height - _TOP_OFFSET - _HEIGHT
        rect = Foundation.NSMakeRect(_LEFT, y_origin, _WIDTH, _HEIGHT)

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
        # 悬浮窗绝不能挡鼠标点击/抢键盘焦点 —— main.py靠鼠标位置和空格键控制游戏,
        # 焦点被抢走这些输入就发不到游戏里了. 只用orderFrontRegardless()显示,
        # 不调用makeKeyAndOrderFront_/activateIgnoringOtherApps_.
        window.setIgnoresMouseEvents_(True)
        self._window = window

        content = window.contentView()
        title = self._make_label(0, "florr auto-pathing", bold=True)
        content.addSubview_(title)

        self._field_labels = {}
        for i, field in enumerate(self._FIELDS):
            label = self._make_label(i + 1, f"{self._LABELS_ZH[field]}: -")
            content.addSubview_(label)
            self._field_labels[field] = label

        self._elapsed_label = self._make_label(len(self._FIELDS) + 1, "耗时: 00:00", small=True)
        content.addSubview_(self._elapsed_label)

        window.orderFrontRegardless()
        self._pump_events()

    def _make_label(self, row, text, bold=False, small=False):
        """按行号从上往下建一个不可编辑的文本标签(Cocoa坐标原点在左下角)."""
        row_height = 22
        y = _HEIGHT - 12 - (row + 1) * row_height
        frame = Foundation.NSMakeRect(8, y, _WIDTH - 16, row_height)
        label = AppKit.NSTextField.alloc().initWithFrame_(frame)
        label.setStringValue_(text)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        size = 10 if small else 12
        weight = AppKit.NSFont.boldSystemFontOfSize_ if bold else AppKit.NSFont.systemFontOfSize_
        label.setFont_(weight(size))
        label.setTextColor_(
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.15, 1.0) if not small
            else AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.3, 1.0)
        )
        return label

    def _pump_events(self):
        """非阻塞地把Cocoa事件循环转几圈, 让刚设置的内容真正画到屏幕上.

        没有跑常规的NSApp.run()主循环(跟main.py现有的同步while循环轮询模型
        保持一致, 不引入线程), 所以每次update()都要手动拉一下事件循环。
        """
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

    def update(self, state=None, pos=None, target=None, message=None):
        if self._dead:
            return None
        try:
            self._state = _merge_state(
                self._state, state=state, pos=pos, target=target, message=message,
            )
            for field in self._FIELDS:
                value = self._state[field]
                display = _format_pos(value) if field in ("pos", "target") else value
                self._field_labels[field].setStringValue_(f"{self._LABELS_ZH[field]}: {display}")
            self._elapsed_label.setStringValue_(
                f"耗时: {_format_elapsed(time.time() - self._start)}"
            )
            self._pump_events()
        except Exception:
            self._dead = True
        return None

    def show_warning(self, message):
        """屏幕正中央弹一个大号警告(比如"连续多次检测不到玩家位置") —— 跟主HUD
        一样点击穿透/不抢焦点(main.py这时候pyautogui还在正常操作鼠标键盘),
        窗口只建一次, 重复调用只是更新文字+重新显示."""
        if self._dead:
            return None
        try:
            if self._warning_window is None:
                screen_frame = AppKit.NSScreen.mainScreen().frame()
                x = (screen_frame.size.width - _WARNING_WIDTH) / 2
                y = (screen_frame.size.height - _WARNING_HEIGHT) / 2
                rect = Foundation.NSMakeRect(x, y, _WARNING_WIDTH, _WARNING_HEIGHT)

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
                window.setIgnoresMouseEvents_(True)

                label = AppKit.NSTextField.alloc().initWithFrame_(
                    Foundation.NSMakeRect(20, 20, _WARNING_WIDTH - 40, _WARNING_HEIGHT - 40)
                )
                label.setBezeled_(False)
                label.setDrawsBackground_(False)
                label.setEditable_(False)
                label.setSelectable_(False)
                label.setFont_(AppKit.NSFont.boldSystemFontOfSize_(18))
                label.setTextColor_(AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.15, 1.0))
                label.setAlignment_(AppKit.NSTextAlignmentCenter)
                # 默认NSTextField单行截断, 这段文案太长必须显式开自动换行, 不然
                # 大半句话会被裁掉看不见(实测验证过: cellSizeForBounds_算出来的
                # 高度会随文字换行真的变高, 不开这两个的话文字会被切在一行里).
                label.cell().setWraps_(True)
                label.setUsesSingleLineMode_(False)
                window.contentView().addSubview_(label)

                self._warning_window = window
                self._warning_label = label

            self._warning_label.setStringValue_(message)
            self._warning_window.orderFrontRegardless()
            self._pump_events()
        except Exception:
            self._dead = True
        return None

    def hide_warning(self):
        if self._dead or self._warning_window is None:
            return None
        try:
            self._warning_window.orderOut_(None)
            self._pump_events()
        except Exception:
            self._dead = True
        return None

    def close(self):
        if self._dead:
            return None
        try:
            self._window.close()
            if self._warning_window is not None:
                self._warning_window.close()
            self._dead = True
        except Exception:
            self._dead = True
        return None


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


# --- Win32扩展窗口样式(GWL_EXSTYLE) + SetWindowPos/SetLayeredWindowAttributes常量 ---
_WIN_GWL_EXSTYLE = -20
_WIN_WS_EX_LAYERED = 0x00080000
_WIN_WS_EX_TRANSPARENT = 0x00000020
_WIN_WS_EX_NOACTIVATE = 0x08000000
_WIN_WS_EX_TOOLWINDOW = 0x00000080
_WIN_LWA_ALPHA = 0x2
_WIN_HWND_TOPMOST = -1
_WIN_SWP_NOMOVE = 0x0002
_WIN_SWP_NOSIZE = 0x0001
_WIN_SWP_NOACTIVATE = 0x0010
_WIN_SWP_SHOWWINDOW = 0x0040
_WIN_SWP_FRAMECHANGED = 0x0020

_BG_HEX = "#ff9900"  # 与_BG_COLOR同一个亮橙色, 供tkinter使用
_FG_HEX = "#262626"  # 与mac版白值0.15对应的深灰(近黑)字色
_FG_DIM_HEX = "#4d4d4d"  # 与mac版白值0.3对应, 给耗时这行用的浅一点的字色
_ALPHA_BYTE = int(0.92 * 255)  # 跟mac版0.92透明度对齐


def _setup_user32():
    """给用到的user32函数声明正确的argtypes/restype再返回user32.

    不声明的话ctypes默认按C int(32位)编组参数, 但64位Windows上HWND是64位
    指针 —— 不声明时GetParent/SetWindowLongW/SetWindowPos拿到的hwnd会被
    截断成坏句柄, 静默操作在错误的(或无效的)窗口上, 不抛异常也看不到任何
    效果。这就是"没有警告日志但悬浮窗压根不显示"的根因。
    """
    user32 = ctypes.windll.user32
    user32.GetParent.restype = ctypes.c_void_p
    user32.GetParent.argtypes = [ctypes.c_void_p]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
    user32.SetLayeredWindowAttributes.restype = ctypes.c_int
    user32.SetLayeredWindowAttributes.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_ubyte, ctypes.c_uint,
    ]
    user32.SetWindowPos.restype = ctypes.c_int
    user32.SetWindowPos.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, ctypes.c_uint,
    ]
    return user32


def _apply_clickthrough_topmost(user32, hwnd):
    """给hwnd叠加点击穿透/不抢焦点/不进任务栏的扩展样式, 再强制置顶+设置透明度+
    显示. _WindowsOverlay的主HUD窗口和警告弹窗共用同一套win32调用(同一件事,
    不该写两遍) —— 谁的hwnd传进来就处理谁."""
    style = user32.GetWindowLongW(hwnd, _WIN_GWL_EXSTYLE)
    style |= (
        _WIN_WS_EX_LAYERED | _WIN_WS_EX_TRANSPARENT
        | _WIN_WS_EX_NOACTIVATE | _WIN_WS_EX_TOOLWINDOW
    )
    user32.SetWindowLongW(hwnd, _WIN_GWL_EXSTYLE, style)
    user32.SetLayeredWindowAttributes(hwnd, 0, _ALPHA_BYTE, _WIN_LWA_ALPHA)
    # SWP_FRAMECHANGED: 改完GWL_EXSTYLE后必须带这个flag, 否则新样式不会
    # 立即生效渲染(微软文档明确要求); 顺带把窗口顶到最上层+确保显示出来.
    user32.SetWindowPos(
        hwnd, _WIN_HWND_TOPMOST, 0, 0, 0, 0,
        _WIN_SWP_NOMOVE | _WIN_SWP_NOSIZE | _WIN_SWP_NOACTIVATE
        | _WIN_SWP_SHOWWINDOW | _WIN_SWP_FRAMECHANGED,
    )


class _WindowsOverlay:
    """Windows版悬浮窗 — tkinter无边框窗口 + 纯win32 API做置顶/透明度/点击穿透.

    florr.io在Windows上一般是浏览器"F11"式无边框全屏(不是独占全屏的游戏),
    常规-topmost窗口就能盖上去, 不需要macOS那套跨Space的screensaver级别技巧。
    置顶/透明度/点击穿透全部用SetWindowPos/SetLayeredWindowAttributes/
    SetWindowLongW直接做, 不用tkinter自己的-topmost/-alpha —— 两边都改同一个
    底层窗口属性会打架(改GWL_EXSTYLE不配套重发SetLayeredWindowAttributes,
    实测会导致窗口整个变不可见), 全交给win32 API一条路管到底更可靠。
    另需两件事保证main.py的输入不被打断:
      - WS_EX_TRANSPARENT: 悬浮窗完全不吃鼠标点击, 点击穿透到游戏画面.
      - WS_EX_NOACTIVATE:  悬浮窗永不抢键盘焦点(main.py靠空格键控制游戏).
    """

    _FIELDS = ("state", "pos", "target", "message")
    _LABELS_ZH = {"state": "状态", "pos": "位置", "target": "目标", "message": "消息"}

    def __init__(self):
        self._start = time.time()
        self._state = {"state": "-", "pos": None, "target": None, "message": "-"}
        # 一旦update/close在窗口没了之后抛异常, 就锁死后续调用为空操作, 绝不再炸主程序.
        self._dead = False
        # 警告弹窗惰性建一次(Toplevel挂在主HUD的root下, 不是独立的tk.Tk() ——
        # 一个进程里开两个Tcl解释器是自找麻烦), 后续show_warning/hide_warning
        # 只切换显示/隐藏.
        self._warning_window = None
        self._warning_label = None
        self._warning_hwnd = None

        root = tk.Tk()
        root.overrideredirect(True)  # 无标题栏/边框, 也不进Alt+Tab切换
        root.configure(bg=_BG_HEX)
        root.geometry(f"{_WIDTH}x{_HEIGHT}+{_LEFT}+{_TOP_OFFSET}")
        root.resizable(False, False)
        # 先落一次事件循环, 确保win32那边真正建出HWND, 再去取winfo_id()才有效.
        root.update_idletasks()
        self._root = root

        self._user32 = _setup_user32()
        # winfo_id()是绘图表面的句柄, 其父窗口才是真正被DWM管理的顶层HWND
        # (overrideredirect窗口下两者常常是同一个, GetParent失败就兜底用
        # winfo_id()本身, 双保险不炸)。
        hwnd = self._user32.GetParent(root.winfo_id())
        self._hwnd = hwnd if hwnd else root.winfo_id()
        self._apply_window_styles()

        title = tk.Label(
            root, text="florr auto-pathing", bg=_BG_HEX, fg=_FG_HEX,
            font=("Segoe UI", 11, "bold"), anchor="w",
        )
        title.place(x=8, y=6, width=_WIDTH - 16, height=20)

        self._field_labels = {}
        for i, field in enumerate(self._FIELDS):
            label = tk.Label(
                root, text=f"{self._LABELS_ZH[field]}: -", bg=_BG_HEX, fg=_FG_HEX,
                font=("Segoe UI", 10), anchor="w",
            )
            label.place(x=8, y=6 + (i + 1) * 22, width=_WIDTH - 16, height=20)
            self._field_labels[field] = label

        self._elapsed_label = tk.Label(
            root, text="耗时: 00:00", bg=_BG_HEX, fg=_FG_DIM_HEX,
            font=("Segoe UI", 8), anchor="w",
        )
        self._elapsed_label.place(
            x=8, y=6 + (len(self._FIELDS) + 1) * 22, width=_WIDTH - 16, height=18,
        )

        root.update_idletasks()
        root.update()

    def _apply_window_styles(self):
        """叠加点击穿透/不抢焦点/不进任务栏的扩展样式, 再强制置顶+设置透明度+显示."""
        _apply_clickthrough_topmost(self._user32, self._hwnd)

    def update(self, state=None, pos=None, target=None, message=None):
        if self._dead:
            return None
        try:
            self._state = _merge_state(
                self._state, state=state, pos=pos, target=target, message=message,
            )
            for field in self._FIELDS:
                value = self._state[field]
                display = _format_pos(value) if field in ("pos", "target") else value
                self._field_labels[field].configure(text=f"{self._LABELS_ZH[field]}: {display}")
            self._elapsed_label.configure(
                text=f"耗时: {_format_elapsed(time.time() - self._start)}"
            )
            # 部分游戏/浏览器切换全屏时会把自己重新拉到最顶层, 每次update都
            # 重新断言一次置顶, 防止悬浮窗被压到游戏画面下面.
            self._user32.SetWindowPos(
                self._hwnd, _WIN_HWND_TOPMOST, 0, 0, 0, 0,
                _WIN_SWP_NOMOVE | _WIN_SWP_NOSIZE | _WIN_SWP_NOACTIVATE,
            )
            self._root.update_idletasks()
            self._root.update()
        except Exception:
            self._dead = True
        return None

    def show_warning(self, message):
        """屏幕正中央弹一个大号警告. Toplevel挂在主HUD的root下(共用同一个Tcl
        解释器), 跟主HUD一样点击穿透/不抢焦点, 窗口只建一次, 重复调用只更新
        文字+重新显示+重新断言置顶."""
        if self._dead:
            return None
        try:
            if self._warning_window is None:
                win = tk.Toplevel(self._root)
                win.overrideredirect(True)
                win.configure(bg=_BG_HEX)

                screen_w = self._root.winfo_screenwidth()
                screen_h = self._root.winfo_screenheight()
                x = (screen_w - _WARNING_WIDTH) // 2
                y = (screen_h - _WARNING_HEIGHT) // 2
                win.geometry(f"{_WARNING_WIDTH}x{_WARNING_HEIGHT}+{x}+{y}")
                win.resizable(False, False)
                win.update_idletasks()

                hwnd = self._user32.GetParent(win.winfo_id())
                self._warning_hwnd = hwnd if hwnd else win.winfo_id()

                label = tk.Label(
                    win, bg=_BG_HEX, fg=_FG_HEX, font=("Segoe UI", 16, "bold"),
                    wraplength=_WARNING_WIDTH - 40, justify="center",
                )
                label.place(x=20, y=20, width=_WARNING_WIDTH - 40, height=_WARNING_HEIGHT - 40)

                self._warning_window = win
                self._warning_label = label

            self._warning_label.configure(text=message)
            _apply_clickthrough_topmost(self._user32, self._warning_hwnd)
            self._root.update_idletasks()
            self._root.update()
        except Exception:
            self._dead = True
        return None

    def hide_warning(self):
        if self._dead or self._warning_window is None:
            return None
        try:
            self._warning_window.withdraw()
            self._root.update_idletasks()
            self._root.update()
        except Exception:
            self._dead = True
        return None

    def close(self):
        if self._dead:
            return None
        try:
            self._root.destroy()
            self._dead = True
        except Exception:
            self._dead = True
        return None


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


def create_overlay():
    """建悬浮窗, 建不起来(缺依赖/没display)就退化成空壳, 不炸主程序."""
    if _IS_MACOS:
        if AppKit is None:
            print("⚠️ 悬浮窗启动失败: pyobjc(AppKit) 不可用", file=sys.stderr)
            return _NullOverlay()
        try:
            return StatusOverlay()
        except Exception as e:
            print(f"⚠️ 悬浮窗启动失败: {e}", file=sys.stderr)
            return _NullOverlay()

    if _IS_WINDOWS:
        if tk is None or ctypes is None:
            print("⚠️ 悬浮窗启动失败: tkinter/ctypes 不可用", file=sys.stderr)
            return _NullOverlay()
        try:
            return _WindowsOverlay()
        except Exception as e:
            print(f"⚠️ 悬浮窗启动失败: {e}", file=sys.stderr)
            return _NullOverlay()

    print(f"⚠️ 悬浮窗启动失败: 不支持的平台 {sys.platform}", file=sys.stderr)
    return _NullOverlay()


def _console_fallback(reason):
    print(f"⚠️ 确认弹窗启动失败: {reason}, 改用控制台确认", file=sys.stderr)
    input("请手动进入全屏(F11)后按回车继续: ")


def show_fullscreen_confirm():
    """弹一个真能点击的确认对话框, 阻塞到用户点击"开始运行"为止. 悬浮窗建不
    出来就退化成控制台input()确认, 绝不崩主程序 —— 跟create_overlay()的
    _NullOverlay降级哲学一致."""
    if _IS_MACOS:
        if AppKit is None:
            return _console_fallback("pyobjc(AppKit) 不可用")
        try:
            print("请在弹窗里手动进入全屏(F11)后点击「开始运行」")
            _MacConfirmDialog().wait_for_confirm()
            return
        except Exception as e:
            return _console_fallback(str(e))

    if _IS_WINDOWS:
        if tk is None:
            return _console_fallback("tkinter 不可用")
        try:
            print("请在弹窗里手动进入全屏(F11)后点击「开始运行」")
            _WindowsConfirmDialog().wait_for_confirm()
            return
        except Exception as e:
            return _console_fallback(str(e))

    return _console_fallback(f"不支持的平台 {sys.platform}")


if __name__ == "__main__":
    # 手动烟雾测试: 开窗, 循环几个假状态, 肉眼确认渲染/位置/跨Space显示对不对.
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
