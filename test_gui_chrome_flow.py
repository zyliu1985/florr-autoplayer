import pytest

import gui_chrome_flow as flow


@pytest.fixture(autouse=True)
def stub_cdp(monkeypatch):
    state = {"ready": False, "launched": 0, "tab_results": [], "ready_calls": 0,
             "wait_timeouts": [], "port_reachable": True}

    def stub_ready():
        state["ready_calls"] += 1
        return state["ready"]

    def stub_wait(timeout, *a, **k):
        state["wait_timeouts"].append(timeout)
        return state["tab_results"].pop(0)

    monkeypatch.setattr(flow.cdp_bridge, "is_dedicated_chrome_ready", stub_ready)
    monkeypatch.setattr(flow.cdp_bridge, "quit_and_launch_chrome",
                        lambda: state.__setitem__("launched", state["launched"] + 1))
    monkeypatch.setattr(flow.cdp_bridge, "wait_for_florr_tab", stub_wait)
    # 别让重试分支真去连 127.0.0.1:9222
    monkeypatch.setattr(flow.cdp_bridge, "is_cdp_port_reachable",
                        lambda: state["port_reachable"])
    return state


def test_returns_immediately_when_already_ready(stub_cdp):
    stub_cdp["ready"] = True
    flow.ensure_chrome_ready(None, confirm=lambda p: pytest.fail("不该问确认"),
                             prompt_retry=lambda p, r: pytest.fail("不该问重试"))
    assert stub_cdp["launched"] == 0


def test_cancel_at_confirm_raises(stub_cdp):
    with pytest.raises(flow.ChromeSetupCancelled):
        flow.ensure_chrome_ready(None, confirm=lambda p: False,
                                 prompt_retry=lambda p, r: True)
    assert stub_cdp["launched"] == 0


def test_confirm_then_tab_found_succeeds(stub_cdp):
    stub_cdp["tab_results"] = [{"url": "https://florr.io/"}]
    flow.ensure_chrome_ready(None, confirm=lambda p: True,
                             prompt_retry=lambda p, r: pytest.fail("不该问重试"))
    assert stub_cdp["launched"] == 1
    # 确保 wait_for_florr_tab 被调用时指定了 15 秒超时
    assert stub_cdp["wait_timeouts"] == [15]


def test_retry_once_then_found(stub_cdp):
    stub_cdp["tab_results"] = [None, {"url": "https://florr.io/"}]
    retries = []
    flow.ensure_chrome_ready(None, confirm=lambda p: True,
                             prompt_retry=lambda p, r: retries.append(r) or True)
    assert len(retries) == 1
    # 端口通 = "Chrome 起来了, 就差 florr.io 标签页"
    assert retries == [True]
    assert stub_cdp["launched"] == 1
    # 确保就绪状态仅在初始检查时被查询一次，重试循环中不会重新轮询
    assert stub_cdp["ready_calls"] == 1


def test_retry_declined_raises(stub_cdp):
    stub_cdp["tab_results"] = [None]
    with pytest.raises(flow.ChromeSetupCancelled):
        flow.ensure_chrome_ready(None, confirm=lambda p: True,
                                 prompt_retry=lambda p, r: False)


def test_retry_prompt_says_chrome_unreachable_when_port_closed(stub_cdp):
    """CDP 端口都连不上时, 重试提示要说的是"Chrome 没起来", 而不是让用户
    再去那个根本不存在的窗口里找 florr.io 标签页."""
    stub_cdp["tab_results"] = [None]
    stub_cdp["port_reachable"] = False
    seen = []
    with pytest.raises(flow.ChromeSetupCancelled):
        flow.ensure_chrome_ready(None, confirm=lambda p: True,
                                 prompt_retry=lambda p, r: seen.append(r) or False)
    assert seen == [False]


def test_default_prompt_retry_picks_message_by_reachability(monkeypatch):
    """默认实现(真拿 messagebox 的那个)也要分两套文案."""
    shown = []
    monkeypatch.setattr(flow.messagebox, "askretrycancel",
                        lambda title, msg, **k: shown.append((title, msg)) or True)
    flow._default_prompt_retry(None, True)
    flow._default_prompt_retry(None, False)
    assert "florr.io" in shown[0][0]
    assert "Chrome" in shown[1][0] and "florr.io" not in shown[1][0]
