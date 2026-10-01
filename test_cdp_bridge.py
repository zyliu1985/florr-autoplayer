import json
from unittest.mock import patch, MagicMock
from urllib.error import URLError

import pytest

import cdp_bridge
from cdp_bridge import (
    find_florr_tab, eval_js, capture_screenshot,
    _find_windows_chrome, _quit_all_chrome, _launch_chrome_process, _poll_for_florr_tab,
)

# 不能依赖"这台机器9222端口有没有真Chrome在监听"这种外部状态 —— 之前这样写过,
# 开发机上真跑起了CDP-enabled Chrome后这些测试就全炸了(find_florr_tab意外真的
# 找到标签页, eval_js意外真的没报错). 全部mock掉urllib请求, 让测试结果只取决于
# 代码逻辑本身, 不取决于谁凑巧在跑什么.


def test_find_florr_tab_returns_none_when_cdp_port_not_listening():
    with patch("cdp_bridge.urllib.request.urlopen", side_effect=URLError("refused")):
        assert find_florr_tab() is None


def test_find_florr_tab_returns_none_when_no_florr_tab_open():
    other_tabs = json.dumps([{"url": "https://example.com/", "id": "1"}]).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = other_tabs
    with patch("cdp_bridge.urllib.request.urlopen", return_value=mock_resp):
        assert find_florr_tab() is None


def test_find_florr_tab_finds_florr_among_other_tabs():
    tabs = json.dumps([
        {"url": "https://example.com/", "id": "1"},
        {"url": "https://florr.io/", "id": "2", "webSocketDebuggerUrl": "ws://x"},
    ]).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = tabs
    with patch("cdp_bridge.urllib.request.urlopen", return_value=mock_resp):
        tab = find_florr_tab()
        assert tab is not None
        assert tab["id"] == "2"


def test_eval_js_raises_clear_error_when_no_tab_found():
    with patch("cdp_bridge.find_florr_tab", return_value=None):
        with pytest.raises(RuntimeError, match="florr.io标签页"):
            eval_js("1 + 1")


def test_capture_screenshot_raises_clear_error_when_no_tab_found():
    with patch("cdp_bridge.find_florr_tab", return_value=None):
        with pytest.raises(RuntimeError, match="florr.io标签页"):
            capture_screenshot()


def test_eval_js_raises_clear_error_when_wrong_websocket_package_installed():
    # 实机在Windows上复现过: 装的是PyPI上不相关的"websocket"包(不是
    # "websocket-client"), 两个包都叫`websocket`这个模块名, 装错了就没有
    # create_connection这个函数. 用一个没有create_connection属性的假模块
    # 模拟这种情况, 不用真的卸载/装错包来测.
    fake_tab = {"webSocketDebuggerUrl": "ws://x", "url": "https://florr.io/"}
    fake_websocket_module = MagicMock(spec=[])  # spec=[]: 除了标准MagicMock属性,
                                                 # 不允许访问任何没显式声明的属性,
                                                 # hasattr(..., 'create_connection')
                                                 # 会正确判False, 不会被MagicMock
                                                 # 的自动属性生成骗过去.
    with patch("cdp_bridge.find_florr_tab", return_value=fake_tab), \
         patch("cdp_bridge.websocket", fake_websocket_module):
        with pytest.raises(RuntimeError, match="websocket-client"):
            eval_js("1 + 1")


def test_find_windows_chrome_returns_none_when_no_candidate_exists():
    with patch("cdp_bridge.os.path.isfile", return_value=False):
        assert _find_windows_chrome() is None


def test_find_windows_chrome_returns_first_existing_candidate():
    existing = cdp_bridge._WINDOWS_CHROME_CANDIDATES[1]
    with patch("cdp_bridge.os.path.isfile", side_effect=lambda p: p == existing):
        assert _find_windows_chrome() == existing


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


def test_launch_dedicated_chrome_happy_path_calls_everything_once():
    with patch("builtins.input", return_value="") as mock_input, \
         patch("cdp_bridge.is_dedicated_chrome_ready", return_value=False), \
         patch("cdp_bridge._quit_all_chrome") as mock_quit, \
         patch("cdp_bridge._launch_chrome_process") as mock_launch, \
         patch("cdp_bridge._poll_for_florr_tab", return_value={"url": "https://florr.io/"}) as mock_poll:
        cdp_bridge.launch_dedicated_chrome()
    mock_quit.assert_called_once()
    mock_launch.assert_called_once()
    mock_poll.assert_called_once_with(15, 1.0)
    assert mock_input.call_count == 2  # 一次关闭确认 + 一次"已打开florr.io"确认


def test_launch_dedicated_chrome_retries_when_tab_not_found_yet():
    with patch("builtins.input", return_value="") as mock_input, \
         patch("cdp_bridge.is_dedicated_chrome_ready", return_value=False), \
         patch("cdp_bridge._quit_all_chrome"), \
         patch("cdp_bridge._launch_chrome_process"), \
         patch("cdp_bridge._poll_for_florr_tab", side_effect=[None, {"url": "https://florr.io/"}]) as mock_poll:
        cdp_bridge.launch_dedicated_chrome()
    assert mock_poll.call_count == 2
    assert mock_input.call_count == 3  # 关闭确认 + 2次"已打开florr.io"确认(第一次没找到, 重试一次)


def test_is_cdp_port_reachable_is_public():
    """gui_chrome_flow 也要靠它区分"Chrome 没起来"和"florr.io 没开", 所以它是
    公有 API —— 别再留一个下划线版本让调用方去够私有名字."""
    assert callable(cdp_bridge.is_cdp_port_reachable)
    assert not hasattr(cdp_bridge, "_is_cdp_port_reachable")


def test_is_cdp_port_reachable_returns_false_when_port_not_listening():
    with patch("cdp_bridge.urllib.request.urlopen", side_effect=URLError("refused")):
        assert cdp_bridge.is_cdp_port_reachable() is False


def test_is_cdp_port_reachable_returns_true_when_port_listening():
    mock_resp = MagicMock()
    with patch("cdp_bridge.urllib.request.urlopen", return_value=mock_resp):
        assert cdp_bridge.is_cdp_port_reachable() is True


def test_launch_dedicated_chrome_blames_user_when_port_reachable_but_no_florr_tab(capsys):
    with patch("builtins.input", return_value="") as mock_input, \
         patch("cdp_bridge.is_dedicated_chrome_ready", return_value=False), \
         patch("cdp_bridge._quit_all_chrome"), \
         patch("cdp_bridge._launch_chrome_process"), \
         patch("cdp_bridge._poll_for_florr_tab", side_effect=[None, {"url": "https://florr.io/"}]), \
         patch("cdp_bridge.is_cdp_port_reachable", return_value=True):
        cdp_bridge.launch_dedicated_chrome()
    assert mock_input.call_count == 3
    out = capsys.readouterr().out
    assert "还没检测到florr.io标签页" in out
    assert "CDP端口连不上" not in out


def test_launch_dedicated_chrome_blames_chrome_when_port_unreachable(capsys):
    with patch("builtins.input", return_value="") as mock_input, \
         patch("cdp_bridge.is_dedicated_chrome_ready", return_value=False), \
         patch("cdp_bridge._quit_all_chrome"), \
         patch("cdp_bridge._launch_chrome_process"), \
         patch("cdp_bridge._poll_for_florr_tab", side_effect=[None, {"url": "https://florr.io/"}]), \
         patch("cdp_bridge.is_cdp_port_reachable", return_value=False):
        cdp_bridge.launch_dedicated_chrome()
    assert mock_input.call_count == 3
    out = capsys.readouterr().out
    assert "CDP端口连不上" in out
    assert "还没检测到florr.io标签页" not in out


def test_is_dedicated_chrome_ready_returns_true_when_a_cdp_command_round_trips():
    with patch("cdp_bridge.eval_js", return_value={"result": {"result": {"value": 1}}}):
        assert cdp_bridge.is_dedicated_chrome_ready() is True


def test_is_dedicated_chrome_ready_returns_false_when_no_florr_tab():
    with patch("cdp_bridge.eval_js", side_effect=RuntimeError("找不到florr.io标签页(CDP)")):
        assert cdp_bridge.is_dedicated_chrome_ready() is False


def test_is_dedicated_chrome_ready_returns_false_when_websocket_handshake_rejected():
    # 端口通、标签页也在列表里, 但启动参数漏了--remote-allow-origins=* —— 只有
    # 真的发一条CDP命令时才会露出403. 这种"看起来好了其实不能用"的Chrome必须
    # 判False, 不然下面launch_dedicated_chrome()会跳过重启, 用户永远修不好.
    with patch("cdp_bridge.eval_js", side_effect=Exception("Handshake status 403 Forbidden")):
        assert cdp_bridge.is_dedicated_chrome_ready() is False


def test_launch_dedicated_chrome_leaves_running_chrome_alone_when_already_ready(capsys):
    # 用户报的bug: 已经有一个满足条件的专用Chrome在跑时(main.py上次跑崩了重开、
    # 或者同时开了两份), 照样无条件杀掉所有Chrome重开 —— 用户白迁移一次账号.
    with patch("cdp_bridge.is_dedicated_chrome_ready", return_value=True), \
         patch("builtins.input") as mock_input, \
         patch("cdp_bridge._quit_all_chrome") as mock_quit, \
         patch("cdp_bridge._launch_chrome_process") as mock_launch, \
         patch("cdp_bridge._poll_for_florr_tab") as mock_poll:
        cdp_bridge.launch_dedicated_chrome()
    mock_input.assert_not_called()   # 不该弹"即将关闭所有Chrome窗口"那句确认
    mock_quit.assert_not_called()
    mock_launch.assert_not_called()
    mock_poll.assert_not_called()    # 已经确认标签页能用了, 不用再等
    assert "跳过" in capsys.readouterr().out


def test_is_dedicated_chrome_ready_true_when_eval_succeeds():
    with patch("cdp_bridge.eval_js", return_value={"result": {}}):
        assert cdp_bridge.is_dedicated_chrome_ready() is True


def test_is_dedicated_chrome_ready_false_when_eval_raises():
    with patch("cdp_bridge.eval_js", side_effect=RuntimeError("403")):
        assert cdp_bridge.is_dedicated_chrome_ready() is False


def test_quit_and_launch_chrome_quits_then_launches():
    calls = []
    with patch("cdp_bridge._quit_all_chrome", side_effect=lambda: calls.append("quit")), \
         patch("cdp_bridge._launch_chrome_process", side_effect=lambda: calls.append("launch")):
        cdp_bridge.quit_and_launch_chrome()
    assert calls == ["quit", "launch"]


def test_wait_for_florr_tab_returns_tab_when_found():
    tab = {"url": "https://florr.io/", "webSocketDebuggerUrl": "ws://x"}
    with patch("cdp_bridge.find_florr_tab", return_value=tab):
        assert cdp_bridge.wait_for_florr_tab(5) == tab


def test_wait_for_florr_tab_returns_none_on_timeout():
    with patch("cdp_bridge.find_florr_tab", return_value=None):
        assert cdp_bridge.wait_for_florr_tab(0) is None


def test_scroll_wheel_unwraps_runtime_evaluate_and_dispatches_wheel():
    # Runtime.evaluate 的响应是双层 result (returnByValue=True): 之前 scroll_wheel
    # 少剥一层 -> KeyError: 'value' -> zoom 每轮都"出错". 这里钉死正确形状 + 事件参数.
    sent = []

    def fake_send(method, params=None, timeout=5):
        sent.append((method, params))
        if method == "Runtime.evaluate":
            return {"id": 1, "result": {"result": {"type": "object", "value": [812, 456]}}}
        return {"id": 1, "result": {}}

    with patch("cdp_bridge._send_cdp_command", side_effect=fake_send):
        cdp_bridge.scroll_wheel(-120)

    methods = [m for m, _ in sent]
    assert methods == ["Runtime.evaluate", "Input.dispatchMouseEvent"]
    _, wheel = sent[1]
    assert wheel["type"] == "mouseWheel"
    assert (wheel["x"], wheel["y"]) == (812, 456)
    assert wheel["deltaY"] == -120 and wheel["deltaX"] == 0


def test_scroll_wheel_falls_back_to_default_centre_on_bad_eval_shape():
    sent = []

    def fake_send(method, params=None, timeout=5):
        sent.append(method)
        if method == "Runtime.evaluate":
            return {"id": 1, "result": {}}          # no nested result/value
        return {"id": 1, "result": {}}

    with patch("cdp_bridge._send_cdp_command", side_effect=fake_send):
        cdp_bridge.scroll_wheel(120)

    assert sent == ["Runtime.evaluate", "Input.dispatchMouseEvent"]
