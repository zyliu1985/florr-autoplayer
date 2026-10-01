"""GUI 版的专用 Chrome 引导 —— 把 cdp_bridge.launch_dedicated_chrome() 那套
命令行 input() 换成模态对话框. 逻辑(就绪就跳过 / 确认后杀掉重启 / 等 florr.io
标签页 / 没等到就问要不要重试)跟命令行版一一对应.

confirm 和 prompt_retry 做成可注入参数 —— 默认用 tkinter.messagebox, 单测传
假函数, 不弹真窗口.
"""
from tkinter import messagebox

import cdp_bridge


class ChromeSetupCancelled(Exception):
    """用户在引导流程里点了取消/否. 调用方(GUI 的开始按钮)应当静默中止启动."""


def _default_confirm(parent):
    return messagebox.askokcancel(
        "准备专用 Chrome",
        "即将关闭所有 Chrome 窗口以启动专用实例(未保存的标签页会丢失).\n继续?",
        parent=parent,
    )


def _default_prompt_retry(parent, chrome_reachable):
    """chrome_reachable: CDP 端口通不通 —— 决定这条提示该让用户去做什么.
    通 = Chrome 起来了, 缺的是 florr.io 标签页; 不通 = Chrome 压根没起来,
    再怎么找标签页都是白费. 这个区分命令行版一直有, GUI 版不能丢."""
    if chrome_reachable:
        return messagebox.askretrycancel(
            "还没检测到 florr.io",
            "没在专用 Chrome 窗口里检测到 florr.io 标签页.\n"
            "确认已经在那个新窗口里登录并打开了 florr.io, 然后点重试.",
            parent=parent,
        )
    return messagebox.askretrycancel(
        "专用 Chrome 好像没启动起来",
        "连不上专用 Chrome 的调试端口(9222), 它多半没启动成功.\n"
        "确认 Chrome 已装好、也没被安全软件拦住, 然后点重试.",
        parent=parent,
    )


def ensure_chrome_ready(parent, *, confirm=_default_confirm, prompt_retry=_default_prompt_retry) -> None:
    """确保专用 Chrome 就绪; 用户取消则抛 ChromeSetupCancelled."""
    # 一次性检查就绪状态，跳过整个流程
    if cdp_bridge.is_dedicated_chrome_ready():
        return
    if not confirm(parent):
        raise ChromeSetupCancelled()
    # 仅启动一次，不在重试循环中重复启动
    cdp_bridge.quit_and_launch_chrome()
    while True:
        if cdp_bridge.wait_for_florr_tab(15) is not None:
            return
        # 等待失败时只重试等待，不重新启动，用户手动处理. 探一下端口, 好让提示
        # 指向真正的问题("florr.io 没开" vs "Chrome 没起来").
        reachable = cdp_bridge.is_cdp_port_reachable()
        if not prompt_retry(parent, reachable):
            raise ChromeSetupCancelled()
