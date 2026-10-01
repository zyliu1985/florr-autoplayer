"""GUI 与 worker 共用的配置读写. main.py 以前把这些值硬编码在 __main__ 里,
现在集中到 exe 同级的 config.json —— GUI 写, worker(`main.py --worker`)读.

设计要点跟 afk_watch._write_afk_config() 一致: 读不出来/键坏了不抛, 回落到
DEFAULTS(= 以前硬编码那套) + print 一句警告. 这里只读不写用户那份坏文件,
所以不需要像 afk_watch 那样备份 .bak.
"""
import copy
import json
import os
import sys

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(sys.argv[0])), "config.json"
)

# 每个值 = 改造前 main.py __main__ 里的硬编码值.
DEFAULTS = {
    "map": "desert",
    "location": [22, 32],
    "farming_area": [[9, 8], [51, 56]],
    "farming_path": None,  # 固定路径刷怪(替代 farming_area): 用户依次点的路径点(≥2).
                           # None = 走区域随机模式(旧行为).
    "farming_duration": 300,
    "consecutive_short_round_limit": 2,
    # 默认关: 索敌要 models/desert.pt, 那个权重文件没进仓库也没进发布包. 默认开
    # 的话全新安装一跑就是每 ENEMY_SCAN_INTERVAL(0.3s) 一条"索敌出错"刷屏.
    # README / PACKAGING 里写的也是"默认关闭, 要自己放权重", 这里跟文档对齐.
    "enemy_ai_enabled": False,
    "auto_switch_server": True,
    "afk_enabled": False,
}

# maps/ 下的 png(去扩展名). 新增地图要同步这里 —— 跟 utils.check_map_border()
# 那种"就地写死一小张表"的仓库既有做法一致, 不在 import 时去 listdir.
# garden 也在这: 它同时是"传送门逃生"的过渡地图, 但现在正式允许作为刷图目标.
_VALID_MAPS = ("desert", "ocean", "anthell", "garden")


def _is_int_pair(v):
    return (
        isinstance(v, (list, tuple))
        and len(v) == 2
        and all(isinstance(n, int) and not isinstance(n, bool) for n in v)
    )


def _coerce(raw):
    """拿 DEFAULTS 当底, 逐键把 raw 里合法的值覆盖上去; 不合法的键留默认 + 警告.
    未知键直接丢掉(worker 只认 DEFAULTS 里那几个键)."""
    cfg = copy.deepcopy(DEFAULTS)
    if not isinstance(raw, dict):
        print(f"⚠️ config.json 顶层不是对象(是 {type(raw).__name__}), 全部用默认值")
        return cfg

    for key, default in DEFAULTS.items():
        if key not in raw:
            continue
        val = raw[key]
        ok = False
        if key == "map":
            ok = isinstance(val, str) and val in _VALID_MAPS
        elif key == "location":
            ok = _is_int_pair(val)
            if ok:
                val = [int(val[0]), int(val[1])]
        elif key == "farming_area":
            ok = (
                isinstance(val, (list, tuple))
                and len(val) == 2
                and all(_is_int_pair(corner) for corner in val)
            )
            if ok:
                val = [[int(c[0]), int(c[1])] for c in val]
        elif key == "farming_path":
            # 固定路径刷怪: 合法值 = None(区域模式) 或 ≥2 个 int 坐标对. 跟
            # farming_area 同一套校验风格, 但允许 None 且要求点数≥2(单点没意义).
            ok = val is None or (
                isinstance(val, (list, tuple))
                and len(val) >= 2
                and all(_is_int_pair(p) for p in val)
            )
            if ok and val is not None:
                val = [[int(p[0]), int(p[1])] for p in val]
        elif key == "farming_duration":
            ok = isinstance(val, int) and not isinstance(val, bool) and val > 0
        elif key == "consecutive_short_round_limit":
            ok = isinstance(val, int) and not isinstance(val, bool) and val >= 1
        else:  # enemy_ai_enabled / auto_switch_server / afk_enabled
            ok = isinstance(val, bool)

        if ok:
            cfg[key] = val
        else:
            print(f"⚠️ config.json 的 {key} 值不合法({val!r}), 用默认 {default!r}")

    return cfg


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return copy.deepcopy(DEFAULTS)
    except Exception as e:
        print(f"⚠️ 读 config.json 失败, 全部用默认值: {e}")
        return copy.deepcopy(DEFAULTS)
    return _coerce(raw)


def save_config(cfg):
    """写之前先 _coerce, GUI 传进来的东西也不例外 —— 别让界面 bug 写出一份坏配置."""
    cleaned = _coerce(cfg)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2, ensure_ascii=False)
