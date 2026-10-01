# florr-auto-afk自动启动 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让`main.py`启动后自动把florr-auto-afk静默开起来并确认它真的进入AFK检测状态,用户不用点任何按钮。

**Architecture:** 两部分。**第一部分**(本仓库外,附录里的runbook):fork `Shiny-Ladybug/florr-auto-afk`的`v1.1.1` tag,加3行`runs.autoStart`补丁,让Actions出新release。**第二部分**(Task 1-5,本仓库):`afk_watch.py`修解压路径、启动前写好它的`config.json`、启动后读`latest.log`确认真的起来了。两部分独立开工——第二部分全部可以在mac上用mock测完,不依赖第一部分的构建。

**Tech Stack:** Python 3.11 / pytest / `unittest.mock` / 标准库`json`+`zipfile`+`subprocess`。不引入任何新pip依赖。

设计文档: [2026-08-28-afk-autostart-design.md](../specs/2026-08-28-afk-autostart-design.md)

## Global Constraints

- **绝不抛异常出`ensure_florr_auto_afk_running()`** —— AFK自动处理是可选增强,不是寻路/刷怪的前提。任何一步失败都只打印一句中文提示,`main.py`照常往下走
- **绝不阻塞主循环** —— 只有启动确认那一步会等,上限`_START_TIMEOUT_SECONDS = 90`秒,超时就放弃继续往下走
- **只在Windows上做事** —— `sys.platform != "win32"`时`ensure_florr_auto_afk_running()`整段return(已实现,不要动这个前置检查)
- **不引入新pip依赖** —— 只用标准库
- **控制台输出用中文**,沿用现有emoji前缀约定:`✅`成功 / `⚠️`警告 / `🪟`打开窗口 / `⏸️`暂停 / `🤖`询问
- **测试全部mock掉`subprocess`和网络**,文件系统用`tmp_path`;Windows专属分支用`monkeypatch.setattr(afk_watch.sys, "platform", "win32")`(沿用[test_afk_watch.py](../../../test_afk_watch.py)现有套路)
- **测试命令固定用`venv/bin/python -m pytest`** —— 仓库里的`.venv/`没装pytest,`venv/`装了
- **不碰模块级状态`_last_offset`/`_initialized`/`_pause_until`** —— 那是`poll_afk_pause()`的地盘,新代码用自己的局部offset

---

## File Structure

**Modify:**
- `afk_watch.py` —— 全部改动都在这一个文件里(它已经是这个项目"跟florr-auto-afk打交道"的专属模块)。新增3个私有函数(`_write_afk_config`/`_current_log_size`/`_wait_for_segment_started`)、2个私有常量,改`_download_and_extract()`的解压目标,改`ensure_florr_auto_afk_running()`的尾段
- `test_afk_watch.py` —— 新增测试;**并且要改动几个既有测试**(见Task 1和Task 4,那些测试的假设被这次改动打破了,不改会挂或者会真的等90秒)

**不新建文件。** `main.py`不动:`ensure_florr_auto_afk_running()`签名和调用点(`main.py:529`)都不变。

**仓库外(附录A):** fork里的`segment.py`、`config.json`、`constants.py`。

---

## Task 1: 修`_download_and_extract()`的解压目标

**Files:**
- Modify: `afk_watch.py:133-167`(`_download_and_extract()`)
- Test: `test_afk_watch.py:196-262`(`_fake_zip_bytes()`和3个`test_download_and_extract_*`)

**Interfaces:**
- Consumes: 无(第一个task)
- Produces: `_download_and_extract() -> bool` —— 签名不变。语义收紧:返回`True`时保证`_EXE_PATH`这个文件真的存在

**背景:** 官方zip没有顶层目录(`Compress-Archive -Path ./dist/segment/*`),现在的`zf.extractall(_INSTALL_ROOT)`会把4500个文件铺在`main.py`旁边,`_EXE_PATH`永远不存在。既有测试的假zip自带一个顶层目录,正好把这个bug盖住了。

- [ ] **Step 1: 把假zip改成跟官方一致的结构,并补齐路径monkeypatch**

`test_afk_watch.py`里,把`_fake_zip_bytes()`整个替换掉,并在它上面加一个共用的路径patch助手:

```python
def _patch_install_paths(monkeypatch, tmp_path):
    """把三个安装路径常量都指到tmp_path下 —— _EXE_PATH也要patch, 不然
    _download_and_extract()新增的"解压完exe在不在"检查会去看真实机器上的路径."""
    install_dir = tmp_path / "florr-auto-afk-v1.1.1-auto"
    monkeypatch.setattr(afk_watch, "_INSTALL_ROOT", str(tmp_path))
    monkeypatch.setattr(afk_watch, "_INSTALL_DIR", str(install_dir))
    monkeypatch.setattr(afk_watch, "_EXE_PATH", str(install_dir / "segment.exe"))
    return install_dir


def _fake_zip_bytes():
    """跟官方release zip一致的结构: 条目直接在根, 没有顶层目录(它v1.1.1的
    workflow用`Compress-Archive -Path ./dist/segment/*`打的包). 这个fixture
    早先假设有顶层目录, 正好把"解压到_INSTALL_ROOT会把4500个文件铺在main.py
    旁边"这个bug盖住了.

    真zip里的分隔符是反斜杠(Compress-Archive的产物): zipfile在Windows上会把它
    当目录分隔符正确展开(os.sep就是反斜杠), 在POSIX上则会变成一个带反斜杠的平
    文件名. 测试用正斜杠, 断言的是"没有顶层目录"这个真正要紧的性质."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("segment.exe", b"fake exe content")
        zf.writestr("models/afk-det.pt", b"fake model")
    return buf.getvalue()


def _fake_zip_without_exe_bytes():
    """结构对但少了segment.exe —— 上游改了打包布局时该被当成失败, 不能返回True
    让调用方以为装好了."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("models/afk-det.pt", b"fake model")
    return buf.getvalue()
```

再把3个既有测试里那两行`monkeypatch.setattr(afk_watch, "_INSTALL_ROOT"/"_INSTALL_DIR", ...)`换成`_patch_install_paths(monkeypatch, tmp_path)`(`test_download_and_extract_success`、`..._returns_false_and_cleans_up_on_network_error`、`..._returns_false_on_corrupt_zip`三个)。

- [ ] **Step 2: 跑测试确认红了**

Run: `venv/bin/python -m pytest test_afk_watch.py -q -k download_and_extract`
Expected: `test_download_and_extract_success` FAIL —— 假zip现在没有顶层目录了,`extractall(_INSTALL_ROOT)`把`segment.exe`丢在`tmp_path`根上,断言的`tmp_path/florr-auto-afk-v1.1.1-auto/segment.exe`不存在(`FileNotFoundError`)。

- [ ] **Step 3: 加一个"zip里没有exe就算失败"的测试**

```python
def test_download_and_extract_returns_false_when_zip_has_no_exe(tmp_path, monkeypatch):
    _patch_install_paths(monkeypatch, tmp_path)
    with patch(
        "afk_watch.urllib.request.urlopen",
        return_value=_fake_response(_fake_zip_without_exe_bytes()),
    ):
        result = afk_watch._download_and_extract()
    assert result is False
```

- [ ] **Step 4: 跑测试确认这条也红了**

Run: `venv/bin/python -m pytest test_afk_watch.py -q -k no_exe`
Expected: FAIL —— 现在的实现无脑返回True。

- [ ] **Step 5: 改实现**

`afk_watch.py`的`_download_and_extract()`里,把解压那两行和成功分支换成:

```python
        with zipfile.ZipFile(tmp_path) as zf:
            # 解压到_INSTALL_DIR而不是_INSTALL_ROOT: 官方release zip没有顶层
            # 目录(它的workflow用`Compress-Archive -Path ./dist/segment/*`),
            # 解压到ROOT会把4500个文件直接铺在main.py旁边, _EXE_PATH永远不存在.
            zf.extractall(_INSTALL_DIR)

        if not os.path.isfile(_EXE_PATH):
            # 上游换了打包布局(比如哪天套了层顶层目录)时不能假装装好了 —— 这里
            # 报出来, 比拖到Popen那步炸掉容易看懂.
            print(
                f"⚠️ florr-auto-afk解压完没找到{_EXE_NAME}(zip结构跟预期不符), "
                f"之后AFK弹窗不会自动处理: {_INSTALL_DIR}"
            )
            return False

        print(f"✅ florr-auto-afk已下载解压到 {_INSTALL_DIR}")
        return True
```

- [ ] **Step 6: 跑全套测试确认绿**

Run: `venv/bin/python -m pytest -q`
Expected: 全部PASS,输出干净(没有warning/error)。

- [ ] **Step 7: Commit**

```bash
git add afk_watch.py test_afk_watch.py
git commit -m "fix: extract florr-auto-afk into its own install dir"
```

---

## Task 2: `_write_afk_config()` —— 启动前写好它的config.json

> **⚠️ 事后修订(2026-08-28,最终 review 的修复波之后)** —— 这一节的代码块**已经过时**,别照抄。落地的代码在 [afk_watch.py](../../../afk_watch.py),权威说明在 [design 的 `### 2`](../specs/2026-08-28-afk-autostart-design.md)。三处变了:
> 1. **强制的键从 5 个减到 3 个**(`runs.autoStart` / `runs.autoTakeOverWhenIdle` / `runs.moveAfterAFK`)。`advanced.verbose` 去掉了 —— 我当时给的理由(保证事件落盘)是错的,`log_ret("Found AFK window", ...)` 无条件落盘,verbose 只管控制台那些硬编码 `save=False` 的行。`advanced.skipUpdate` 去掉了 —— 强制它会永久关掉上游唯一的模型自修复(`segment.py:158-165` 加载失败时删掉两个 `.pt` 和 `models/version`,等下次 `update_models()` 重下,而那个重下只看 `skipUpdate`)
> 2. **重建的底不是 `{}`,是 `_DEFAULT_CONFIG`** —— 发行包自带的那份完整 config.json(`git show v1.1.1:config.json`)。我当时写的"它的 `get_config()` 对其余键有默认值兜底"是**假的**:`segment_utils.py:27-29` 就是裸的 `open` + `json.load`,~38 个直接下标点,其中 `segment_utils.py:266` 是 import 期求值的默认参数 `speed=get_config()["advanced"]["mouseSpeed"]` —— 只写我们那几个键的话,`segment.exe` 在 GUI 出来之前就 `KeyError` 挂掉
> 3. **读不出来的原文件先改名成 `config.json.bak`** 再写新的,不直接冲掉

**Files:**
- Modify: `afk_watch.py`(顶部加`import json`;在`_download_and_extract()`后面加常量`_REQUIRED_CONFIG`和函数`_write_afk_config()`)
- Test: `test_afk_watch.py`(文件末尾追加)

**Interfaces:**
- Consumes: `_INSTALL_DIR`(模块常量,测试里被`_patch_install_paths()`重定向)
- Produces: `_write_afk_config() -> bool` —— 成功写盘返回`True`,失败打印警告返回`False`,永不抛异常。Task 4会在`Popen`之前调它

- [ ] **Step 1: 写失败的测试**

追加到`test_afk_watch.py`:

```python
def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_write_afk_config_overwrites_our_keys_and_keeps_the_rest(tmp_path, monkeypatch):
    install_dir = _patch_install_paths(monkeypatch, tmp_path)
    install_dir.mkdir()
    (install_dir / "config.json").write_text(json.dumps({
        "runs": {"autoStart": False, "moveAfterAFK": True, "idleTimeThreshold": 10},
        "advanced": {"verbose": False, "mouseSpeed": 250},
        "yoloConfig": {"segModel": "./models/afk-seg.pt"},
    }))

    assert afk_watch._write_afk_config() is True

    config = _read_json(install_dir / "config.json")
    assert config["runs"]["autoStart"] is True
    assert config["runs"]["autoTakeOverWhenIdle"] is False
    assert config["runs"]["moveAfterAFK"] is False
    assert config["advanced"]["verbose"] is True
    assert config["advanced"]["skipUpdate"] is True
    # 用户自己调过的键不能被冲掉 —— 我们只负责自己依赖的那几个.
    assert config["runs"]["idleTimeThreshold"] == 10
    assert config["advanced"]["mouseSpeed"] == 250
    assert config["yoloConfig"] == {"segModel": "./models/afk-seg.pt"}


def test_write_afk_config_creates_minimal_config_when_file_missing(tmp_path, monkeypatch):
    install_dir = _patch_install_paths(monkeypatch, tmp_path)
    install_dir.mkdir()

    assert afk_watch._write_afk_config() is True

    config = _read_json(install_dir / "config.json")
    assert config["runs"]["autoStart"] is True
    assert config["advanced"]["skipUpdate"] is True
```

```python
def test_write_afk_config_rebuilds_when_json_is_corrupt(tmp_path, monkeypatch):
    install_dir = _patch_install_paths(monkeypatch, tmp_path)
    install_dir.mkdir()
    (install_dir / "config.json").write_text("{this is not json")

    assert afk_watch._write_afk_config() is True

    config = _read_json(install_dir / "config.json")
    assert config["runs"]["autoStart"] is True


def test_write_afk_config_replaces_section_that_is_not_a_dict(tmp_path, monkeypatch):
    # 真见过配置被写坏成标量的情况; .update()会在这种值上炸AttributeError.
    install_dir = _patch_install_paths(monkeypatch, tmp_path)
    install_dir.mkdir()
    (install_dir / "config.json").write_text(json.dumps({"runs": 5}))

    assert afk_watch._write_afk_config() is True
    assert _read_json(install_dir / "config.json")["runs"]["autoStart"] is True


def test_write_afk_config_returns_false_without_raising_when_dir_missing(tmp_path, monkeypatch, capsys):
    # install目录压根不存在(用户手动删了/解压失败): 写不进去, 但不能把主程序带崩.
    _patch_install_paths(monkeypatch, tmp_path / "nope")

    assert afk_watch._write_afk_config() is False
    assert "config.json" in capsys.readouterr().out
```

测试文件顶部的import要加`import json`。

- [ ] **Step 2: 跑测试确认红了**

Run: `venv/bin/python -m pytest test_afk_watch.py -q -k write_afk_config`
Expected: 5条全部FAIL,报`AttributeError: <module 'afk_watch'> does not have the attribute '_write_afk_config'`(函数还不存在)。

- [ ] **Step 3: 写实现**

`afk_watch.py`顶部import区加`import json`(按字母序放在`import os`前面)。`_download_and_extract()`后面加:

```python
# 我们依赖的配置键 —— 只覆盖这几个, config.json里其余键(用户自己调的mouseSpeed
# 之类)原样保留. autoStart是fork里加的开关(见附录A), 其余四个的理由见
# docs/superpowers/specs/2026-08-11-afk-check-coexistence-design.md.
_REQUIRED_CONFIG = {
    "runs": {
        "autoStart": True,              # 启动即开始检测, 不用手点"run"
        "autoTakeOverWhenIdle": False,  # 我们一直在动鼠标, 它的idle门永远不触发
        "moveAfterAFK": False,          # 它解完题的WASD乱走会跟我们的移动打架
    },
    "advanced": {
        "verbose": True,                # 保证事件真的落进latest.log
        "skipUpdate": True,             # 免得启动时联网查模型更新, 拖时间/失败
    },
}


def _write_afk_config():
    """把我们依赖的几个键写进florr-auto-afk自己的config.json, 其它键原样保留.
    文件不存在/JSON坏了就从空的开始, 写一份只含这几个键的最小config —— 它的
    get_config()对其余键有默认值兜底. 失败只打印警告返回False, 不抛 —— 配置没写上
    最多是"还得手点run", 不该拦住寻路/刷怪."""
    config_path = os.path.join(_INSTALL_DIR, "config.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        if not isinstance(config, dict):
            config = {}
    except Exception:
        config = {}

    for section, values in _REQUIRED_CONFIG.items():
        section_config = config.get(section)
        if not isinstance(section_config, dict):
            section_config = {}
        section_config.update(values)
        config[section] = section_config

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"⚠️ 写florr-auto-afk的config.json失败(可能还需要手动点run): {e}")
        return False
```

- [ ] **Step 4: 跑测试确认绿**

Run: `venv/bin/python -m pytest -q`
Expected: 全部PASS。

- [ ] **Step 5: Commit**

```bash
git add afk_watch.py test_afk_watch.py
git commit -m "feat: write florr-auto-afk config keys we depend on before launching it"
```

---

## Task 3: 启动确认 —— `_current_log_size()` + `_wait_for_segment_started()`

**Files:**
- Modify: `afk_watch.py`(常量区加`_STARTED_MARKER`/`_START_TIMEOUT_SECONDS`;`_write_afk_config()`后面加两个函数)
- Test: `test_afk_watch.py`(文件末尾追加)

**Interfaces:**
- Consumes: `LATEST_LOG_PATH`(模块常量,测试里用`monkeypatch.setattr`重定向)
- Produces:
  - `_current_log_size() -> int` —— `latest.log`当前字节数,文件不存在返回`0`
  - `_wait_for_segment_started(start_offset: int, timeout: float = _START_TIMEOUT_SECONDS, interval: float = 1.0) -> bool`
  - Task 4按这个顺序用:先`_current_log_size()`划线 → `Popen` → `_wait_for_segment_started(那条线)`

**关键约束:** `latest.log`是用`'a'`打开的(`segment_utils.py:67`/`:101`),跨次运行**不清空**。所以必须先划线、只看新增内容,否则上次运行留下的marker会被误判成本次启动成功。

- [ ] **Step 1: 写失败的测试**

追加到`test_afk_watch.py`:

```python
def test_current_log_size_returns_zero_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(afk_watch, "LATEST_LOG_PATH", str(tmp_path / "nope.log"))
    assert afk_watch._current_log_size() == 0


def test_current_log_size_returns_real_size(tmp_path, monkeypatch):
    log_path = tmp_path / "latest.log"
    log_path.write_text("hello")
    monkeypatch.setattr(afk_watch, "LATEST_LOG_PATH", str(log_path))
    assert afk_watch._current_log_size() == 5


def test_wait_for_segment_started_true_when_marker_written_after_offset(tmp_path, monkeypatch):
    log_path = tmp_path / "latest.log"
    log_path.write_text("[old] EVENT: something else\n")
    monkeypatch.setattr(afk_watch, "LATEST_LOG_PATH", str(log_path))
    start_offset = log_path.stat().st_size
    with open(log_path, "a") as f:
        f.write("[new] <segment.py:202> INFO: Segment process started\n")

    with patch("afk_watch.time.sleep"):
        assert afk_watch._wait_for_segment_started(start_offset, timeout=0.05, interval=0.01) is True


def test_wait_for_segment_started_ignores_marker_from_a_previous_run(tmp_path, monkeypatch):
    # 这条是这个函数存在的理由: 日志是追加的, 上次运行的"started"还在文件里,
    # 整文件搜就会立刻误判成功, 于是"其实没起来"永远不会被发现.
    log_path = tmp_path / "latest.log"
    log_path.write_text("[old run] INFO: Segment process started\n")
    monkeypatch.setattr(afk_watch, "LATEST_LOG_PATH", str(log_path))
    start_offset = log_path.stat().st_size

    with patch("afk_watch.time.sleep"):
        assert afk_watch._wait_for_segment_started(start_offset, timeout=0.05, interval=0.01) is False
```

```python
def test_wait_for_segment_started_matches_variant_with_trailing_period(tmp_path, monkeypatch):
    # v1.1.1里两处都会写: segment.py:202不带句号, :234带句号. 子串匹配两个都要命中.
    log_path = tmp_path / "latest.log"
    log_path.write_text("INFO: Segment process started.\n")
    monkeypatch.setattr(afk_watch, "LATEST_LOG_PATH", str(log_path))

    with patch("afk_watch.time.sleep"):
        assert afk_watch._wait_for_segment_started(0, timeout=0.05, interval=0.01) is True


def test_wait_for_segment_started_false_without_raising_when_log_never_appears(tmp_path, monkeypatch):
    monkeypatch.setattr(afk_watch, "LATEST_LOG_PATH", str(tmp_path / "never.log"))
    with patch("afk_watch.time.sleep"):
        assert afk_watch._wait_for_segment_started(0, timeout=0.05, interval=0.01) is False


def test_wait_for_segment_started_rereads_from_start_when_log_shrank(tmp_path, monkeypatch):
    # 它启动时若发现latest.log不存在会新建(segment_utils.py:113), 用户也可能手动
    # 删掉 —— 文件比划线时还小就说明不是同一份, 从头读.
    log_path = tmp_path / "latest.log"
    log_path.write_text("INFO: Segment process started\n")
    monkeypatch.setattr(afk_watch, "LATEST_LOG_PATH", str(log_path))

    with patch("afk_watch.time.sleep"):
        assert afk_watch._wait_for_segment_started(9999, timeout=0.05, interval=0.01) is True


def test_wait_for_segment_started_does_not_touch_poll_module_state(tmp_path, monkeypatch):
    # poll_afk_pause()的offset状态是另一套账 —— 确认这个函数没顺手改掉它, 否则
    # 主循环第一次poll的"跳到文件末尾"行为会变.
    log_path = tmp_path / "latest.log"
    log_path.write_text("INFO: Segment process started\n")
    monkeypatch.setattr(afk_watch, "LATEST_LOG_PATH", str(log_path))
    monkeypatch.setattr(afk_watch, "_last_offset", 0)
    monkeypatch.setattr(afk_watch, "_initialized", False)

    with patch("afk_watch.time.sleep"):
        afk_watch._wait_for_segment_started(0, timeout=0.05, interval=0.01)

    assert afk_watch._last_offset == 0
    assert afk_watch._initialized is False
```

- [ ] **Step 2: 跑测试确认红了**

Run: `venv/bin/python -m pytest test_afk_watch.py -q -k "current_log_size or wait_for_segment"`
Expected: 8条全部FAIL,报`AttributeError: ... does not have the attribute '_current_log_size'` / `'_wait_for_segment_started'`。

- [ ] **Step 3: 写实现**

`afk_watch.py`常量区(`_FOUND_MARKER`那行下面)加:

```python
# florr-auto-afk真正进入检测状态时写的日志(log_ret的save默认True会落盘).
# v1.1.1里两处都会写这句, segment.py:234那处末尾带句号 —— 用子串匹配都能命中.
_STARTED_MARKER = "Segment process started"
# 等它起来的上限: PyInstaller解包 + torch + 两个YOLO模型加载, 它自己FAQ说初始化
# 要10秒以上, 慢机器上留足余量. 超时只是少一句确认, 不影响主程序继续跑.
_START_TIMEOUT_SECONDS = 90
```

`_write_afk_config()`后面加:

```python
def _current_log_size():
    """latest.log当前字节数, 文件不存在算0. 用来在启动前划一条线 —— 它是用'a'
    追加打开的(segment_utils.py), 跨次运行不清空, 整文件搜marker会把上次运行的
    记录误判成本次启动成功."""
    try:
        return os.path.getsize(LATEST_LOG_PATH)
    except OSError:
        return 0


def _wait_for_segment_started(start_offset, timeout=_START_TIMEOUT_SECONDS, interval=1.0):
    """轮询latest.log在start_offset之后的新增内容, 等它写出"检测已启动"那条.
    找到返回True, 超时返回False —— 不抛异常, 调用方只是打印不同的提示.

    每轮都从start_offset重读一遍尾巴, 不做增量offset推进: 这段日志很小, 重读几十
    次的代价远小于维护offset的复杂度, 也天然没有"marker刚好被切在两次读中间"的漏检.
    """
    deadline = time.time() + timeout
    while True:
        try:
            size = os.path.getsize(LATEST_LOG_PATH)
            with open(LATEST_LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
                # 比划线时还小说明不是同一份文件了(被删/被换), 从头读.
                f.seek(0 if size < start_offset else start_offset)
                if _STARTED_MARKER in f.read():
                    return True
        except Exception:
            pass  # 文件还没出现: 它还在初始化, 继续等
        if time.time() >= deadline:
            return False
        time.sleep(interval)
```

- [ ] **Step 4: 跑全套测试确认绿**

Run: `venv/bin/python -m pytest -q`
Expected: 全部PASS。跑完总时长应该跟改之前差不多(几秒),如果明显变慢说明有测试在真的等——回去检查`timeout`参数和`patch("afk_watch.time.sleep")`。

- [ ] **Step 5: Commit**

```bash
git add afk_watch.py test_afk_watch.py
git commit -m "feat: confirm florr-auto-afk really started by watching its log"
```

---

## Task 4: 接线进`ensure_florr_auto_afk_running()`

**Files:**
- Modify: `afk_watch.py`(`ensure_florr_auto_afk_running()`的docstring和尾段`try/except`)
- Test: `test_afk_watch.py`(改3个既有测试 + 追加3条新测试)

**Interfaces:**
- Consumes: `_write_afk_config()`(Task 2)、`_current_log_size()`和`_wait_for_segment_started()`(Task 3)
- Produces: `ensure_florr_auto_afk_running() -> None` —— 签名不变,`main.py:529`不用动

**顺序必须是:** 写config → 划线(`_current_log_size()`)→ `Popen` → 等marker。划线一定要在`Popen`**之前**,不然它启动瞬间写的那条marker可能已经在线的左边,永远等不到。

- [ ] **Step 1: 先给既有测试补上patch(否则它们会真的写盘/真的等90秒)**

这3个既有测试现在会走到新代码:`_write_afk_config()`会往真实机器上的安装目录写文件,`_wait_for_segment_started()`会真的等满90秒。给它们补patch:

```python
def test_ensure_florr_auto_afk_running_opens_directly_when_already_installed(monkeypatch):
    monkeypatch.setattr(afk_watch.sys, "platform", "win32")
    with patch("afk_watch.os.path.isfile", return_value=True), \
         patch("afk_watch._is_florr_auto_afk_running", return_value=False), \
         patch("afk_watch._write_afk_config"), \
         patch("afk_watch._wait_for_segment_started", return_value=True), \
         patch("builtins.input") as mock_input, \
         patch("afk_watch.subprocess.Popen") as mock_popen:
        afk_watch.ensure_florr_auto_afk_running()
    mock_input.assert_not_called()  # 已经装了, 不该问下载
    # cwd必须是它自己的安装目录 —— segment.exe内部读的是相对路径'./config.json',
    # 它写的latest.log也落在CWD; 继承我们的CWD会让它一启动就FileNotFoundError.
    mock_popen.assert_called_once_with([afk_watch._EXE_PATH], cwd=afk_watch._INSTALL_DIR)


def test_ensure_florr_auto_afk_running_downloads_then_opens_when_confirmed(monkeypatch):
    monkeypatch.setattr(afk_watch.sys, "platform", "win32")
    with patch("afk_watch.os.path.isfile", return_value=False), \
         patch("afk_watch._is_florr_auto_afk_running", return_value=False), \
         patch("afk_watch._prompt_download_confirm", return_value=True), \
         patch("afk_watch._download_and_extract", return_value=True) as mock_download, \
         patch("afk_watch._write_afk_config"), \
         patch("afk_watch._wait_for_segment_started", return_value=True), \
         patch("afk_watch.subprocess.Popen") as mock_popen:
        afk_watch.ensure_florr_auto_afk_running()
    mock_download.assert_called_once()
    mock_popen.assert_called_once_with([afk_watch._EXE_PATH], cwd=afk_watch._INSTALL_DIR)


def test_ensure_florr_auto_afk_running_does_not_crash_when_popen_raises(monkeypatch):
    monkeypatch.setattr(afk_watch.sys, "platform", "win32")
    with patch("afk_watch.os.path.isfile", return_value=True), \
         patch("afk_watch._is_florr_auto_afk_running", return_value=False), \
         patch("afk_watch._write_afk_config"), \
         patch("afk_watch._wait_for_segment_started") as mock_wait, \
         patch("afk_watch.subprocess.Popen", side_effect=OSError("no permission")):
        afk_watch.ensure_florr_auto_afk_running()  # 不该抛异常
    mock_wait.assert_not_called()  # 压根没启动成功, 没什么可等的
```

另外两个既有测试(`..._skips_when_user_declines_download`、`..._does_not_open_when_download_fails`)不用改 —— 它们在写config之前就return了。

- [ ] **Step 2: 写失败的测试(新增3条)**

追加到`test_afk_watch.py`:

```python
def test_ensure_florr_auto_afk_running_marks_log_offset_before_launching(monkeypatch):
    # 划线必须在Popen之前: 它启动瞬间就写marker, 划线晚了那条就落在线左边, 永远等不到.
    calls = []
    monkeypatch.setattr(afk_watch.sys, "platform", "win32")
    with patch("afk_watch.os.path.isfile", return_value=True), \
         patch("afk_watch._is_florr_auto_afk_running", return_value=False), \
         patch("afk_watch._write_afk_config", side_effect=lambda: calls.append("config")), \
         patch("afk_watch._current_log_size", side_effect=lambda: (calls.append("mark"), 123)[1]), \
         patch("afk_watch.subprocess.Popen", side_effect=lambda *a, **k: calls.append("popen")), \
         patch("afk_watch._wait_for_segment_started", return_value=True) as mock_wait:
        afk_watch.ensure_florr_auto_afk_running()
    assert calls == ["config", "mark", "popen"]
    mock_wait.assert_called_once_with(123)


def test_ensure_florr_auto_afk_running_reports_success_without_asking_for_a_click(monkeypatch, capsys):
    monkeypatch.setattr(afk_watch.sys, "platform", "win32")
    with patch("afk_watch.os.path.isfile", return_value=True), \
         patch("afk_watch._is_florr_auto_afk_running", return_value=False), \
         patch("afk_watch._write_afk_config"), \
         patch("afk_watch._current_log_size", return_value=0), \
         patch("afk_watch.subprocess.Popen"), \
         patch("afk_watch._wait_for_segment_started", return_value=True):
        afk_watch.ensure_florr_auto_afk_running()
    out = capsys.readouterr().out
    assert "已开启" in out
    assert "run" not in out  # 确认成功时不再要求用户点run


def test_ensure_florr_auto_afk_running_falls_back_to_manual_hint_on_timeout(monkeypatch, capsys):
    monkeypatch.setattr(afk_watch.sys, "platform", "win32")
    with patch("afk_watch.os.path.isfile", return_value=True), \
         patch("afk_watch._is_florr_auto_afk_running", return_value=False), \
         patch("afk_watch._write_afk_config"), \
         patch("afk_watch._current_log_size", return_value=0), \
         patch("afk_watch.subprocess.Popen"), \
         patch("afk_watch._wait_for_segment_started", return_value=False):
        afk_watch.ensure_florr_auto_afk_running()  # 不该抛异常
    assert "run" in capsys.readouterr().out
```

- [ ] **Step 3: 跑测试确认红了**

Run: `venv/bin/python -m pytest test_afk_watch.py -q -k ensure_florr`
Expected: 新增那3条FAIL(`_current_log_size`还没被调用 → `calls == ["config", "popen"]`、`assert "已开启" in out`失败、`assert "run" in out`失败);Step 1改过的3条应该已经PASS(那些patch只是防真实副作用)。

- [ ] **Step 4: 改实现**

`afk_watch.py`里,把`ensure_florr_auto_afk_running()`的docstring和尾段`try/except`换成:

```python
def ensure_florr_auto_afk_running():
    """确保florr-auto-afk在跑 —— 已经在跑就不动, 没装就问要不要下, 装了没跑就写好
    config并静默打开它, 然后读它的日志确认真的进入了检测状态. 用户不需要点任何按钮
    (自动启动靠config里的runs.autoStart, 见docs/superpowers/specs/2026-08-28-afk-autostart-design.md
    附录A那个fork补丁). 只在Windows上做, 其余平台整段跳过. 全程不阻塞主流程 ——
    这是可选增强, 任何一步失败/用户跳过都只打印一句提示, main.py照常往下走."""
    if sys.platform != "win32":
        return

    # 在跑就直接返回, 连"装没装"都不用查(能跑起来说明装过了 —— 哪怕是用户自己
    # 另一份拷贝, 也不该再开一个). 多开两个实例会各自做YOLO拖拽, 互相抢鼠标.
    if _is_florr_auto_afk_running():
        print("✅ florr-auto-afk已经在跑, 不重复打开.")
        return

    if not os.path.isfile(_EXE_PATH):
        if not _prompt_download_confirm():
            print("   跳过florr-auto-afk, 之后AFK弹窗不会自动处理.")
            return
        if not _download_and_extract():
            return  # 失败原因已经在_download_and_extract()里打印过了

    _write_afk_config()
    # 划线必须在Popen之前 —— 它启动瞬间就写marker, 晚划就落在线左边, 永远等不到.
    start_offset = _current_log_size()
    try:
        # cwd=_INSTALL_DIR是必须的, 不能让它继承我们的CWD: segment.exe内部读的是
        # 相对路径'./config.json'(get_config()), 继承我们的CWD时它一启动就
        # FileNotFoundError: './config.json'挂掉; 而且它写的latest.log也落在CWD,
        # 不指定的话LATEST_LOG_PATH指向的位置永远不会有文件, AFK检测静默失效.
        subprocess.Popen([_EXE_PATH], cwd=_INSTALL_DIR)
    except Exception as e:
        print(f"⚠️ 打开florr-auto-afk失败(不影响主程序): {e}")
        return

    print(f"🪟 已在后台打开florr-auto-afk, 确认它开始检测(最多等{_START_TIMEOUT_SECONDS}秒)...")
    if _wait_for_segment_started(start_offset):
        print("✅ AFK弹窗自动处理已开启")
    else:
        print(
            "⚠️ 没能确认florr-auto-afk已开始检测(autoStart没生效, 或者初始化特别慢). "
            "需要的话去它窗口里手动点\"run\"(不点也不影响寻路/刷怪, 只是AFK弹窗不会自动处理)."
        )
```

- [ ] **Step 5: 跑全套测试确认绿**

Run: `venv/bin/python -m pytest -q`
Expected: 全部PASS,总时长跟改之前一个量级。

- [ ] **Step 6: Commit**

```bash
git add afk_watch.py test_afk_watch.py
git commit -m "feat: start florr-auto-afk silently and verify it began detecting"
```

---

## Task 5: 切到打过补丁的release(**依赖附录A的构建产物**)

**Files:**
- Modify: `afk_watch.py`(模块docstring、`_INSTALL_DIR_NAME`、`_DOWNLOAD_URL`、新增`_DOWNLOAD_SHA256`、`_STARTED_MARKER`、`_REQUIRED_CONFIG`、`_DEFAULT_CONFIG`、`_download_and_extract()`)
- Test: `test_afk_watch.py`(改9个既有测试 + 追加2条新测试)

**Interfaces:**
- Consumes: 附录A产出的release zip URL
- Produces: 无新接口

**⚠️ 这个task卡在附录A上** —— Task 1-4全部可以先做完提交。附录A的Actions绿了、release zip拿到URL之后再做这个。

**为什么要顺手改`_INSTALL_DIR_NAME`:** 已经装了旧版(没补丁的官方v1.1.1)的用户,`_EXE_PATH`是存在的 → `ensure_florr_auto_afk_running()`不会重新下载 → 那份exe没有`autoStart`,每次都走到超时提示。换个目录名,打过补丁的这版就落到全新目录,不会被旧安装挡住。

**最终 review 要求一并折进这个 task 的四件事**(2026-08-28,Tasks 1-4 落地之后追加):

1. ~~**`_DOWNLOAD_URL` 加 SHA-256 校验**~~ —— **已做**(2026-08-28)。`_DOWNLOAD_SHA256 = "74488ef5...e7d74"`,`_download_and_extract()`边下边`hashlib.sha256().update(chunk)`,下完不符就打印期望/实际两个摘要并返回False,解压之前就停住
2. ~~**`_STARTED_MARKER` 换成更严的信号**~~ —— **已做**(2026-08-28),按括号里那个可选方案走:marker换成子进程写的`Running indefinitely`,`runningCountDown: -1`一起进了`_REQUIRED_CONFIG`
3. ~~**`_DEFAULT_CONFIG` 跟 `_DOWNLOAD_URL` 要同版本**~~ —— **已做**(2026-08-28)。fork发行包的`config.json`只在`runs`最前面多了`"autoStart": false`,其余跟`git show v1.1.1:config.json`逐键一致;`_DEFAULT_CONFIG`已补上这个键(值仍由`_REQUIRED_CONFIG`覆盖成`true`)
4. **更新用户文档** —— ✅ 已做(2026-08-28,Task 5 的修复轮里)。`docs/bilibili/视频2-安装教程-脚本与分镜.md` 的第11行和第7镜:尺寸改成约350MB,去掉"点一下 run"的口播和给按钮打红框的指示,改成"它自己起来、自己最小化,控制台打出 `✅ AFK弹窗自动处理已开启` 就成了",只在超时警告那种情况下才提手动点 run。那个目录还没进 git,所以不在提交范围里

`_START_TIMEOUT_SECONDS = 90` 落地后要按实机真实耗时调(见附录A9)。**注意换marker之后余量变小了**:它比父进程那条晚,还要多等一个首次运行的`test_environment()`。

- [x] **Step 1: 改常量**

**实际落地的值**(zip名跟官方原件同名,不是`-autostart-auto.zip` —— 见下面Step 1b):

```python
# 打过autoStart补丁的fork构建(见docs/superpowers/specs/2026-08-28-afk-autostart-design.md
# 第一部分). 故意跟官方原版用不同的目录名: 已经装了旧版官方包的用户, _EXE_PATH是存在的,
# 不换名字就永远不会重新下载, 而旧版那个exe没有autoStart, 每次都白等到超时.
_INSTALL_DIR_NAME = "florr-auto-afk-v1.1.1-autostart"
_EXE_NAME = "segment.exe"
_DOWNLOAD_URL = (
    "https://github.com/greatluca666/florr-auto-afk/releases/download/"
    "v1.1.1/florr-auto-afk-v1.1.1-auto.zip"
)
_DOWNLOAD_SHA256 = "74488ef58966d123ace6d19ebb11c05d7ac8ee992abd949289714a8a866e7d74"
```

下载确认提示里的大小跟着asset实际值改成`约350MB`(347,277,698字节)。

- [x] **Step 1b: 修正asset名预期 —— 附录A5故意没做**

原计划(A5)把fork的`constants.py`里`VERSION_INFO`改成`"1.1.1-autostart"`,release tag和zip名会跟着变成`v1.1.1-autostart` / `florr-auto-afk-v1.1.1-autostart-auto.zip`。**没有那么做**:同一个常量还要喂给它自己的更新检查,`check_update()`里的`parse_version()`是`tuple(map(int, version.split('.')))`(`segment_utils.py:171-175`),非数字后缀直接`ValueError`。

所以tag是`v1.1.1`、asset名`florr-auto-afk-v1.1.1-auto.zip`,跟官方原件同名 —— A7/A8里写的那个`-autostart-auto.zip`预期作废。**区分官方原件靠URL里的账号名,不是文件名**;`-autostart`后缀只留在我们自己的`_INSTALL_DIR_NAME`上(那才是"强制旧安装重新下载"要的东西)。

- [x] **Step 2: 更新模块docstring**

`afk_watch.py`开头那段里,把"(已经在跑就不动, 没装就问要不要下, 装了没跑就打开它)"改成"装了没跑就静默打开并确认它真的开始检测",并补一段说清楚:`_DOWNLOAD_URL`钉的是`greatluca666/florr-auto-afk`(GPL-3.0 fork,`v1.1.1` + 一个`runs.autoStart`开关),官方原版没这个键 —— 改回上游就等于每次启动都白等满`_START_TIMEOUT_SECONDS`再回落到"请手动点run"。详见`docs/superpowers/specs/2026-08-28-afk-autostart-design.md`。

- [x] **Step 3: 检查别处有没有写死旧URL/旧目录名**

```bash
grep -rn "florr-auto-afk-v1.1.1-auto\|sunluca668" --include="*.py" --include="*.md" --include="*.spec" --include="*.bat" . | grep -v "^./docs/superpowers/"
```

结果:除`afk_watch.py`/`test_afk_watch.py`外,只剩`docs/bilibili/`(还没进git,见上面第4条)。README/PACKAGING/build脚本里一处都没有。`docs/superpowers/`下的历史spec/plan是当时的记录,不改。

- [x] **Step 4: 跑全套测试**

Run: `venv/bin/python -m pytest -q`
Expected: 全部PASS。**注意**:钉了`_DOWNLOAD_SHA256`之后,原来那几个喂假zip的`_download_and_extract`测试全会红(假zip对不上真摘要)—— 修法是在测试里`monkeypatch.setattr(afk_watch, "_DOWNLOAD_SHA256", hashlib.sha256(payload).hexdigest())`现算,**不能**削弱生产校验、也不要把假摘要写成字面量。marker改了之后`_wait_for_segment_started`那几条的日志fixture也要跟着换成`Running indefinitely`。

- [x] **Step 5: Commit**

实际分了三个commit:下载侧(常量 + SHA-256校验)、启动确认侧(marker + `runningCountDown` + `_DEFAULT_CONFIG`)、文档。

---

## 附录A: fork florr-auto-afk并出打过补丁的release(手动runbook)

这部分不在本仓库里,也没法在mac上验证(`segment.exe`是Windows专属)。跟Task 1-4并行做,做完把release URL填进Task 5。

**前提:** 本地已经有源码clone在`~/florr-auto-afk`(remote指向`Shiny-Ladybug/florr-auto-afk`),`v1.1.1` tag在。GPL-3.0允许fork后修改,fork本身就满足公开改动源码的义务。

- [ ] **A1: 在GitHub上fork `Shiny-Ladybug/florr-auto-afk`**

- [ ] **A2: 本地从`v1.1.1` tag开分支**

```bash
cd ~/florr-auto-afk
git remote add fork git@github.com:<你的账号>/florr-auto-afk.git
git checkout -b autostart v1.1.1
```

- [ ] **A3: 打补丁 —— `segment.py`,`root.mainloop()`(v1.1.1的418行)之前**

改动前:

```python
        sv_ttk.set_theme(theme)
        apply_theme_to_titlebar(root)

        root.mainloop()
```

改动后:

```python
        sv_ttk.set_theme(theme)
        apply_theme_to_titlebar(root)

        # runs.autoStart: 启动即开始检测, 不用人点"run". 给florr-auto-pathing这类
        # 外部程序用 —— 它没法从外面驱动这个Tk GUI(没有CLI参数/IPC, 按钮坐标又随
        # 系统DPI缩放漂移). .get()兜底: 老的config.json里没这个键也不炸.
        if get_config()["runs"].get("autoStart", False):
            toggle_segment_process()
            root.iconify()  # 最小化到任务栏, 不挡游戏; 不用withdraw()(那个连任务栏
                            # 图标都没有, 想手动停就只能去任务管理器杀进程)

        root.mainloop()
```

`toggle_segment_process()`在v1.1.1里不带参数(v1.3.2才要`capture_windows`);`capture_windows`默认是`[]`(`gui_utils.py`),走全屏检测,正是我们要的。

- [ ] **A4: `config.json`的`runs`里加`autoStart`,默认`false`(不改上游行为)**

```json
    "runs": {
        "autoStart": false,
        "autoTakeOverWhenIdle": true,
        "runningCountDown": -1,
```

(本仓库的`_write_afk_config()`会把它改成`true` —— 这里默认`false`是为了单独跑这个fork时行为跟官方一致。)

- [ ] **A5: `constants.py`里`VERSION_INFO = "1.1.1"` → `"1.1.1-autostart"`**

release tag和zip名都是从这个常量推出来的,改了才能一眼跟官方原件区分开,不会哪天下错。

> **最终没做,故意的** —— 见Task 5 Step 1b:这个常量还要喂给`check_update()`里的`parse_version()`(`tuple(map(int, version.split('.')))`),带非数字后缀那边直接`ValueError`。tag/asset名因此跟官方原件同名,区分靠账号名。

- [ ] **A6: 提交并推到fork的`main`**

它的workflow只在`push`到`main`时触发(`on: push: branches: ["main"]`),所以必须落在`main`上:

```bash
git commit -am "feat: add runs.autoStart to start detecting without clicking run"
git push -f fork autostart:main
```

`-f`是因为fork的`main`本来是上游的最新代码,我们要把它替换成"v1.1.1 + 补丁"。**这是强推,只影响你自己那个fork,不碰上游。**

- [ ] **A7: 等Actions跑完,确认release建出来了**

产物应该是release `v1.1.1-autostart`,asset名`florr-auto-afk-v1.1.1-autostart-auto.zip`(名字里那个`-auto`后缀是v1.1.1 workflow自己写死的,跟本功能无关)。

> **实际**:A5没做,所以release是`v1.1.1`、asset名`florr-auto-afk-v1.1.1-auto.zip`(347,277,698字节),见Task 5 Step 1b。

Actions红了大概率是构建腐烂:它用的是第三方action `sayyid5416/pyinstaller@v1` + 2025年的`py311-requirements.txt`,隔了一年多依赖解析可能已经不通。先看日志,通常是给`torch`/`ultralytics`钉版本就能过。

- [ ] **A8: 把asset URL填进Task 5的`_DOWNLOAD_URL`**

形如`https://github.com/<你的账号>/florr-auto-afk/releases/download/v1.1.1-autostart/florr-auto-afk-v1.1.1-autostart-auto.zip`

- [ ] **A9: Windows实机验证(Task 5做完之后)**

在Windows那台上跑`main.py`,确认三件事:
1. florr-auto-afk自己起来了,窗口是最小化状态(没挡游戏)
2. 控制台打出`✅ AFK弹窗自动处理已开启`(不是超时那条)
3. `<安装目录>/latest.log`里能看到`Running indefinitely`(Task 5把marker从父进程那条`Segment process started`换成了这条,见Task 5折进来的第2件事)

第2条超时但第3条有 → 说明marker文本对不上或者90秒不够,回来调`_STARTED_MARKER`/`_START_TIMEOUT_SECONDS`。第3条也没有、但有`Segment process started` → 子进程起了但没进检测循环,先看`latest.log`里有没有`YOLO models are corrupted`。


---

## 遗留(deferred) —— review 提过、决定先不做的

Tasks 1-5 的 review 一共提了约 20 条 Minor,下面是还值得记着的。都不影响当前行为,按需再捡。

**配置写入(`_write_afk_config()`)**
- 非原子写:`open(..., "w")` 先截断再 `json.dump`,中途失败会毁掉用户配置。临时文件 + `os.replace` 三行可闭合
- `.bak` 只有一份:第二次写坏会把第一次备份的真配置覆盖掉(复合丢失路径)
- rename 在 write 之前:极端情况下(rename 成功、write 失败)一个 `config.json` 都不剩
- `ensure_ascii=False` 写 raw UTF-8。上游自己也是这么写的,所以不算缺陷,只是注意

**启动确认(`_wait_for_segment_started()`)**
- 完全静默的 `except Exception`,跟 `_read_new_lines()` 的 warn-once 不一致 —— `LATEST_LOG_PATH` 配错会换来一次静默的满超时
- 失败路径最多超预算一个 `interval`(生产默认 ~91 秒而不是 90)
- "至少读一次再判超时"这条不变量没有测试锁定(`timeout=0` + marker 已存在 → True 就能锁)
- 文件变短就从 0 重读,理论上给"轮转后的尾巴里还留着旧 marker"开了误判的门(概率极低)

**下载/校验**
- `_fake_response()` 一次性给全部 payload → 所有下载测试都是单 chunk,增量哈希这个性质没被锁
- `test_download_and_extract_returns_false_on_digest_mismatch` 里 `assert _DOWNLOAD_SHA256 in out` 比的是原始常量,而输出打的是归一化后的值 —— 哪天真按 certutil 格式粘贴常量,这条测试会挂(生产行为是对的)
- 校验失败的提示只给两个 digest,没给用户下一步(输入 n 跳过 / 更新本程序)
- 重新构建的 playbook 只写了一半:已经装过的机器压根不会再走 `_download_and_extract()`,只有换 `_INSTALL_DIR_NAME` 才碰得到它们

**其它**
- 已经跑过旧版的机器上,`main.py` 旁边散落的那 ~4500 个文件没有清理路径(不是数据丢失,只是占地方);`skipUpdate: true` 也还留在那份 config 里,我们现在不碰这个键了
- `docs/bilibili/视频2-安装教程-脚本与分镜.md:36` 的"AFK 那个程序还要再占 300M"没改 —— 光 zip 就 347MB,解压后更大,得在 Windows 上量一次才好写准
