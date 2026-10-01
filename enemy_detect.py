import math
import os

# ultralytics 每次加载模型都会去 api.github.com 查资产版本/更新, 未认证请求很容易
# 403 "rate limit exceeded" 刷屏 —— 本地模型完好也照查. 在 import ultralytics 之前
# 关掉它的联网检查. 这个知道就行: 我们只用本地的 models/desert.pt, 不需要它联网.
os.environ.setdefault("YOLO_OFFLINE", "1")
os.environ.setdefault("ULTRALYTICS_OFFLINE", "1")

import cv2
import numpy as np
import pyautogui
from ultralytics import YOLO

import utils

# florr.io稀有度色表(低到高). 来源: 一个公开的florr.io稀有度检测油猴脚本, 缺
# Eternal档(脚本没更新到那个档, 借用Super当占位) —— 跟这个项目其它靠实测校准出来
# 的颜色(比如玩家标记的f8de60)一样, 这张表投入实机使用前需要拿真实截图校准一遍,
# 别直接信.
RARITY_COLORS = {
    "Common": "7EEF6D",
    "Unusual": "FFE65D",
    "Rare": "4D52E3",
    "Epic": "861FDE",
    "Legendary": "DE1F1F",
    "Mythic": "1FDBDE",
    "Ultra": "FF2B75",
    "Super": "2BFFA3",
    "Eternal": "2BFFA3",  # 占位, 未校准 —— 借用Super的颜色, 见上面注释
    "Unique": "555555",
}

RARITY_ORDER = [
    "Common", "Unusual", "Rare", "Epic", "Legendary",
    "Mythic", "Ultra", "Super", "Eternal", "Unique",
]
RARITY_RANK = {name: i for i, name in enumerate(RARITY_ORDER)}


def _hex_to_bgr(hex_color):
    """'RRGGBB' → (B, G, R), 跟cv2的通道顺序一致. 复用utils.py里同样的
    (4, 2, 0)切片写法(那边给玩家标记色用的同一个手法)."""
    return tuple(int(hex_color[i:i + 2], 16) for i in (4, 2, 0))


MIN_RARITY_PIXEL_RATIO = 0.06  # 稀有度词是描边方块字, 笔画占比本来就不高; 采样窗
                                 # 已经靠血条锚点定位到词上之后, 6%命中就采信这一档.
                                 # 低于此 → 背景主导 → 默认Common.


def _find_hp_bar(image, bbox):
    """在怪的下半身~框底偏下一带找那条绿色血条. florr.io每只怪脚下都挂一条亮饱和
    绿的横条, 是整个名牌区里最好认的锚点 —— 怪名(白字, 没有稀有度信息)在血条正
    上方, 稀有度词(带稀有度颜色, 要采的就是它)在血条正下方、右对齐血条右端.
    返回(bar_x0, bar_y, bar_x1, bar_thick), 找不到 → None."""
    x1, y1, x2, y2 = [int(v) for v in bbox]
    h = max(1, y2 - y1)
    H, W = image.shape[:2]
    # 上边到框内15%处就开始找 —— 框松时名牌会落在框内靠下, 不能只从框底往下看.
    ys0 = max(0, y1 + int(0.15 * h))
    ys1 = min(H, y2 + max(22, int(0.60 * h)) + 8)
    xs0 = max(0, x1 - 12)
    xs1 = min(W, x2 + 18)
    if ys1 - ys0 < 3 or xs1 - xs0 < 15:
        return None

    band = image[ys0:ys1, xs0:xs1].astype(int)
    b, g, r = band[..., 0], band[..., 1], band[..., 2]
    green = ((g > 130) & (g < 245) & (r < 205) & (b < 135)
             & (g - r > 18) & (g - b > 50)).astype(np.uint8)
    if int(green.sum()) < 12:
        return None
    rowsum = green.sum(axis=1)
    row = int(np.argmax(rowsum))
    if int(rowsum[row]) < 15:
        return None
    cols = np.where(green[row] > 0)[0]
    run = int(cols.max() - cols.min())
    if run < 15:
        return None

    # 血条厚度: 从峰值行往上下扩, 命中数还有峰值40%的行都算进去.
    thr = max(1, int(0.4 * rowsum[row]))
    thick, ri = 1, row - 1
    while ri >= 0 and rowsum[ri] >= thr:
        thick, ri = thick + 1, ri - 1
    ri = row + 1
    while ri < len(rowsum) and rowsum[ri] >= thr:
        thick, ri = thick + 1, ri + 1

    # 血条是"长而薄"的; 绿色仙人掌/沙尘暴半透明身体是一大坨绿 —— 用长宽比 + 厚度
    # 上限把那种一坨的绿挡掉, 别把怪身当血条.
    if thick > 12 or run < 4 * thick:
        return None
    return (xs0 + int(cols.min()), ys0 + row, xs0 + int(cols.max()), thick)


def sample_rarity(image, bbox, tolerance=40, min_pixel_ratio=MIN_RARITY_PIXEL_RATIO):
    """读一只怪的稀有度. 先用_find_hp_bar()找到怪脚下那条绿血条当锚点, 再在血条
    正下方、右对齐血条右端的那一小块(florr.io稀有度词的固定位置)按每种稀有度颜色
    数命中像素、取最多那档(数像素、不取均值 —— 描边文字取均值会被背景冲淡, 跟
    utils.get_player_location_on_map同手法). 找不到血条、采样窗越界、或最高档占比
    < min_pixel_ratio → 默认Common(最宽松那档, 读失败不会误触发规避).

    旧实现往框顶上方14px采样 —— 实测名牌整个在怪下方, 那位置永远是空地, 每只怪
    都读成Common → 全部ENGAGE、该躲的强怪也直接撞上去(见
    docs/superpowers/specs/2026-08-16-sszone-enemy-detection-design.md稀有度校准
    遗留项)."""
    bar = _find_hp_bar(image, bbox)
    if bar is None:
        return "Common"
    bar_x0, bar_y, bar_x1, bar_thick = bar
    H, W = image.shape[:2]
    bar_len = bar_x1 - bar_x0

    # 稀有度词: 血条下方(跳过半根血条厚度, 别采到血条本身), 高度~3倍血条厚, 宽度
    # 取血条右端往左一段(词右对齐血条右端, 2个方块字), 略越过右端好收住边缘.
    word_h = max(12, 3 * bar_thick)
    word_w = max(40, int(0.55 * bar_len))
    ry0 = min(H, bar_y + bar_thick // 2 + 1)
    ry1 = min(H, ry0 + word_h)
    rx0 = max(0, bar_x1 - word_w)
    rx1 = min(W, bar_x1 + max(4, bar_thick))
    region = image[ry0:ry1, rx0:rx1]
    if region.size == 0:
        return "Common"

    # 只扫Common..Ultra. Super/Eternal/Unique这仨这个刷怪区实测不刷(见design doc),
    # 而且它们的色(2BFFA3绿/555555灰)最容易被血条绿、描边黑误命中 —— 扫了只会
    # 制造假的高稀有度读数, 反而把本该正常打的怪误判成规避.
    scan = RARITY_ORDER[:RARITY_RANK["Ultra"] + 1]
    total_px = region.shape[0] * region.shape[1]
    best_name, best_count = "Common", 0
    for name in scan:
        b, g, r = _hex_to_bgr(RARITY_COLORS[name])
        lower = np.array([max(0, b - tolerance), max(0, g - tolerance), max(0, r - tolerance)])
        upper = np.array([min(255, b + tolerance), min(255, g + tolerance), min(255, r + tolerance)])
        count = int(np.count_nonzero(cv2.inRange(region, lower, upper)))
        if count > best_count:
            best_name, best_count = name, count

    if best_count / total_px < min_pixel_ratio:
        return "Common"
    return best_name


def measure_hp_bar_thickness(detections, image):
    """每个检测框跑 _find_hp_bar, 收集找到的血条厚度 (第4个返回值), 跳过 None.
    顺序跟 detections 一致, 纯函数无 I/O. 给 ensure_zoom_for_rarity 判相机 zoom
    够不够 —— 实测血条 厚<4 时 sample_rarity 的稀有度词像素太少, 全读 Common."""
    out = []
    for d in detections:
        bar = _find_hp_bar(image, d["bbox"])
        if bar is not None:
            out.append(bar[3])
    return out


# 数值越大优先级越高(故意跟RARITY_RANK同方向, 好用max()一起挑目标).
# sandstorm > cactus > beetle > scorpion > {sand_centipede, soldier_fire_ant}(并列最低)
SPECIES_RANK = {
    "sandstorm": 5,
    "cactus": 4,
    "beetle": 3,
    "scorpion": 2,
    "sand_centipede": 1,
    "soldier_fire_ant": 1,
}

_AVOID_PAIRS = {("scorpion", "Ultra"), ("beetle", "Ultra")}
_CAUTIOUS_PAIRS = {
    ("sandstorm", "Ultra"), ("cactus", "Ultra"),
    ("sand_centipede", "Ultra"), ("soldier_fire_ant", "Ultra"),
}


def classify_action(species, rarity):
    """按(物种, 稀有度)分档: ENGAGE(正常接战)/CAUTIOUS(可打但保持距离)/
    AVOID(不打, 触发规避). Mythic及以下全ENGAGE; Ultra档蝎子/甲虫AVOID,
    沙尘暴/仙人掌/沙蜈蚣/火蚁CAUTIOUS; 比Ultra还稀有(Super/Eternal/Unique, 实测
    这个刷怪区不会刷新这个档位)没规则覆盖时兜底AVOID —— 失败方向选"别惹", 不选
    "谨慎打": 比已经判AVOID的Ultra蝎子/甲虫还稀有的东西没理由更弱。"""
    if RARITY_RANK[rarity] < RARITY_RANK["Ultra"]:
        return "ENGAGE"
    if (species, rarity) in _AVOID_PAIRS:
        return "AVOID"
    if (species, rarity) in _CAUTIOUS_PAIRS:
        return "CAUTIOUS"
    return "AVOID"


def priority_score(species, rarity):
    """排序键, 数值越大优先级越高. 稀有度档位是第一比较项(碾压式), 物种优先级
    只在同稀有度档位时当平手规则."""
    return (RARITY_RANK[rarity], SPECIES_RANK[species])


# ── Mythic 近身处理 ("先清青怪") ──────────────────────────────────────────
# 青怪 = Mythic 档 (青 = Mythic 名牌的青色). desert.pt 的 6 类里, sandstorm 是刷怪
# 目标本身, 不进这套 —— 其余 5 种每种按下面的策略走位磨死.
MYTHIC_KITE_SPECIES = {
    "beetle": "strafe",           # 直冲型, 垂直环绕让它打空
    "soldier_fire_ant": "strafe",
    "scorpion": "ram",            # 直接撞
    "sand_centipede": "ram",
    "cactus": "hold",             # 站桩带刺, 保持距离在旁边
}

# 多个 Mythic 同时在场时先处理谁 (用户给的顺序, 只用于 Mythic 锁定, 跟 SPECIES_RANK
# 那套普通追击优先级无关).
MYTHIC_TARGET_RANK = {
    "beetle": 5,
    "soldier_fire_ant": 4,
    "scorpion": 3,
    "sand_centipede": 2,
    "cactus": 1,
}


def mythic_candidates(detections, chase_min_conf=None):
    """从 detections 里挑出够格进 Mythic 锁定池的: rarity 是 Mythic、species 在
    MYTHIC_KITE_SPECIES (sandstorm 排除)、置信度过 chase_min_conf (同追击的幻影框
    过滤). 返回列表, 可能为空."""
    if chase_min_conf is None:
        chase_min_conf = CHASE_MIN_CONF
    return [
        d for d in detections
        if d.get("rarity") == "Mythic"
        and d.get("species") in MYTHIC_KITE_SPECIES
        and d.get("confidence", 1.0) >= chase_min_conf
    ]


SCREEN_CENTER = (utils.SCREEN_WIDTH / 2, utils.SCREEN_HEIGHT / 2)  # 屏幕中心, 同时也是
                              # "停止移动"的鼠标位置约定(见utils.keyup()) ——
                              # aim_mouse_target/flee_mouse_target在"保持距离"/
                              # "没有明确方向"时都退回这个值, 调用方(main.py)靠跟这个
                              # 常量比较来判断"这tick是不是故意停住"。跟utils.py共用
                              # 同一份SCREEN_WIDTH/SCREEN_HEIGHT, 不再自己独立写死一份.


def pick_mythic_target(detections, center=SCREEN_CENTER, latched=False,
                       engage_px=450, release_px=600, chase_min_conf=None,
                       prev_pos=None):
    """挑这一 tick 要处理的那只 Mythic. 搜索半径: 已锁定用 release_px (放宽, 迟滞),
    没锁定用 engage_px. 半径内没有合格 Mythic → None.

    没锁定 / 没给 prev_pos: 按 (MYTHIC_TARGET_RANK, 离屏幕中心近) 取最高.
    已锁定且给了 prev_pos: 目标位置连续性优先 —— 取离 prev_pos 最近的候选, 只有当
    另一个候选 MYTHIC_TARGET_RANK 严格更高 (真来了更值得打的) 才切过去, 同 rank
    之间仍按离 prev_pos 近取. 免得两只同 rank Mythic 因亚像素抖动每 tick 翻 180°."""
    radius = release_px if latched else engage_px
    cx, cy = center

    def dist(d):
        px, py = d["screen_pos"]
        return math.hypot(px - cx, py - cy)

    in_range = [d for d in mythic_candidates(detections, chase_min_conf=chase_min_conf)
                if dist(d) <= radius]
    if not in_range:
        return None

    if latched and prev_pos is not None:
        ppx, ppy = prev_pos

        def dist_prev(d):
            px, py = d["screen_pos"]
            return math.hypot(px - ppx, py - ppy)

        nearest = min(in_range, key=dist_prev)
        best_rank = max(MYTHIC_TARGET_RANK[d["species"]] for d in in_range)
        if best_rank > MYTHIC_TARGET_RANK[nearest["species"]]:
            better = [d for d in in_range
                      if MYTHIC_TARGET_RANK[d["species"]] == best_rank]
            return min(better, key=dist_prev)
        return nearest

    return max(in_range, key=lambda d: (MYTHIC_TARGET_RANK[d["species"]], -dist(d)))


def mythic_move_target(target, center=SCREEN_CENTER, *, strafe_radius, cactus_hold_px,
                       max_extend=None, repel_positions=None, k_radial=0.8):
    """按 target 的物种策略算这一 tick 鼠标该移到哪:
      ram   (蝎子/蜈蚣)   —— 直接朝目标全速贴, 等同 aim_mouse_target(hold_px=None)
      hold  (仙人掌)       —— 远于 hold*1.15 逼近; 近于 hold*0.85 沿 -u 后撤;
                             中间沿垂直方向 perp 绕圈
      strafe(甲虫/火蚁)    —— 垂直环绕 perp + 朝 strafe_radius 的径向修正
                             (d>r 往里带, d<r 往外推), 归一化后 ×max_extend
    perp 取固定一侧 (-u_y, u_x). d==0 无方向 → 返回 center."""
    if max_extend is None:
        max_extend = 500 * utils.mouse_scale()
    policy = MYTHIC_KITE_SPECIES.get(target["species"], "ram")
    px, py = target["screen_pos"]
    cx, cy = center
    vx, vy = px - cx, py - cy
    d = math.hypot(vx, vy)
    if d == 0:
        return center
    ux, uy = vx / d, vy / d
    perp = (-uy, ux)

    if policy == "ram":
        return aim_mouse_target(target["screen_pos"], hold_px=None, center=center,
                                max_extend=max_extend, repel_positions=repel_positions)

    if policy == "hold":
        if d > cactus_hold_px * 1.15:
            return aim_mouse_target(target["screen_pos"], hold_px=None, center=center,
                                    max_extend=max_extend, repel_positions=repel_positions)
        if d < cactus_hold_px * 0.85:
            dx, dy = -ux, -uy            # 后撤
        else:
            dx, dy = perp               # 绕圈
        return (cx + dx * max_extend, cy + dy * max_extend)

    # policy == "strafe"
    radial = (d - strafe_radius) / strafe_radius * k_radial
    dx = perp[0] + ux * radial
    dy = perp[1] + uy * radial
    m = math.hypot(dx, dy)
    if m < 1e-6:
        return center
    return (cx + dx / m * max_extend, cy + dy / m * max_extend)


def aim_mouse_target(target_pos, hold_px=None, center=SCREEN_CENTER, max_extend=None,
                     repel_positions=None, repel_px=None, repel_gain=1.6):
    """把目标的屏幕坐标换算成鼠标该移到的位置 —— 纯屏幕坐标系计算, 跟
    move_to_position()那套小地图坐标系是两套独立空间, 不能互相传参数。
    hold_px设了值时, 一旦已经进到这个距离内就不再继续靠近(退回屏幕中心, 停止
    输出"继续接近"的方向), 给CAUTIOUS档的怪用; hold_px=None时无视距离上限一直
    往目标方向贴(只按max_extend限速度), 给ENGAGE档用。

    repel_positions给了值时(一串危险怪的屏幕坐标), 会往"远离它们"的方向叠一个
    排斥分量到追击方向上 —— 追归追, 但路径绕开半路的危险怪, 不是直直怼过去。
    只有危险怪进到repel_px以内才起作用, 越近推得越狠(线性衰减×repel_gain);
    repel_positions为空/None时行为跟以前完全一样。合成方向被排斥力抵消到约0 →
    这一tick退回屏幕中心(停一下), 等下一帧重新算。

    max_extend默认None时按1920x1080参照值500乘utils.mouse_scale()换算 —— 跟
    utils.keydown()的delta是同一种"1920x1080量出来的屏幕转向距离"，同样需要
    按分辨率缩放。repel_px默认None时同理按参照值450换算。显式传值(比如测试里传
    500)会跳过默认换算, 保持既有调用点行为不变。"""
    if max_extend is None:
        max_extend = 500 * utils.mouse_scale()
    tx, ty = target_pos
    cx, cy = center
    dx, dy = tx - cx, ty - cy
    dist = math.hypot(dx, dy)
    if dist == 0:
        return center

    rx, ry = 0.0, 0.0
    if repel_positions:
        if repel_px is None:
            repel_px = 450 * utils.mouse_scale()
        for px, py in repel_positions:
            adx, ady = cx - px, cy - py
            ad = math.hypot(adx, ady)
            if ad == 0 or ad >= repel_px:
                continue
            w = (1.0 - ad / repel_px) * repel_gain
            rx += adx / ad * w
            ry += ady / ad * w

    if hold_px is not None and dist <= hold_px:
        # 已经进到CAUTIOUS保持距离内: 平时就停(退回中心), 但半路有危险怪在推 →
        # 这一tick还是往远离危险的方向挪一下, 别傻站着被撞。
        if rx == 0.0 and ry == 0.0:
            return center
        rmag = math.hypot(rx, ry)
        step = min(max_extend, repel_px)
        return (cx + rx / rmag * step, cy + ry / rmag * step)

    ux, uy = dx / dist + rx, dy / dist + ry
    umag = math.hypot(ux, uy)
    if umag < 1e-6:
        return center
    extend = min(dist, max_extend)
    return (cx + ux / umag * extend, cy + uy / umag * extend)


def flee_mouse_target(avoid_positions, center=SCREEN_CENTER, extend=None):
    """算所有AVOID怪的排斥力合向量, 换算成鼠标该移到的位置(往远离它们的方向)。
    合力互相抵消成约0向量(比如两个AVOID怪分别在玩家两侧)时没有明确逃离方向,
    退回屏幕中心 —— 等同于"停止移动", 跟utils.keyup()把鼠标收回中心停止移动是
    同一个约定。

    extend默认None时按1920x1080参照值400乘utils.mouse_scale()换算, 理由同
    aim_mouse_target的max_extend。"""
    if extend is None:
        extend = 400 * utils.mouse_scale()
    cx, cy = center
    fx, fy = 0.0, 0.0
    for px, py in avoid_positions:
        dx, dy = cx - px, cy - py
        dist = math.hypot(dx, dy)
        if dist == 0:
            continue
        fx += dx / dist
        fy += dy / dist
    mag = math.hypot(fx, fy)
    if mag < 0.05:
        return center
    return (cx + fx / mag * extend, cy + fy / mag * extend)


CHASE_STALL_WINDOW = 25  # tick, ≈1.25s @ time.sleep(0.05); 判"追击途中卡住"看的时间窗


def chase_is_stalled(pos_history, min_progress=4.0, window=CHASE_STALL_WINDOW):
    """追击/规避途中判断是否真的卡住了 —— 看整段时间窗内玩家的**净位移**, 不是
    看相邻两tick挪了多少。追一个会走位的目标时, 相邻tick位移小是常态(绕圈、
    微调), 旧写法(相邻tick差<1.5就+1, 连续15次就脱困)会把正常追击误判成卡住、
    半路触发execute_anti_stuck()把玩家怼向目标。改成: 攒满一个window的位置样本
    后, 窗口首尾净位移 < min_progress(minimap坐标单位)才算卡住 —— 贴墙被顶住
    净位移≈0, 正常追击哪怕绕圈净位移也会累积过阈值。

    pos_history: 调用方维护的近期minimap坐标列表(get_player_position()的返回值,
    不是屏幕坐标), 最新的在末尾。样本不足一个window → 返回False(还没攒够, 不判)。
    只返回bool(该不该让步脱困), 不再回传计数 —— 状态在调用方那个列表里。"""
    if pos_history is None or len(pos_history) < window:
        return False
    x0, y0 = pos_history[-window]
    x1, y1 = pos_history[-1]
    return math.hypot(x1 - x0, y1 - y0) < min_progress


CHASE_MIN_CONF = 0.55  # 只有YOLO置信度到这个数的检测框才够格当"追击目标". 0.4~0.55
                        # 那档框经常是幻影(半透明沙尘暴边缘、影子), 拿它当目标就是
                        # 朝空气全速冲. 危险怪(AVOID/CAUTIOUS)不受此限 —— 宁可对着
                        # 一个可能不存在的强怪多绕一下, 不能漏躲。


def select_action(detections, avoid_trigger_px=400, cautious_hold_px=250,
                  center=SCREEN_CENTER, chase_min_conf=CHASE_MIN_CONF):
    """每tick的索敌决策入口. detections是scan_enemies()给的检测列表(或测试里
    手搭的同结构字典列表). 返回三选一:
      ("flee", avoid_positions)             —— 触发半径内有AVOID怪, 优先规避
      ("chase", target, hold_px, repel)     —— 有值得专门追的目标(稀有度>=Mythic,
                                               或Ultra档CAUTIOUS怪); repel是半路要
                                               绕开的危险怪坐标(AVOID全部 + 除目标外
                                               的CAUTIOUS), 传给aim_mouse_target当
                                               排斥源
      ("wander", None)                      —— 没有到Mythic档的目标, 交回随机漫游
    AVOID怪永远进不了"chase"候选池, 哪怕它稀有度算下来优先级最高。追击目标还要
    过chase_min_conf置信度关; 没过关的ENGAGE直接丢, 没过关的AVOID/CAUTIOUS仍算
    危险源(进flee判定/repel), 只是不当追击目标。

    ★ chase只留给Mythic及以上(和Ultra CAUTIOUS): 密集刷怪区(比如沙尘暴区)每tick
    都有Common/传奇沙尘暴当最高分候选, 早先版本每tick都返回chase去追它 —— 结果
    auto_farming永远走chase分支、从不进wander, 对着一个被打死/新刷/乱动的目标
    原地微振, chase_is_stalled误判"卡住"触发execute_anti_stuck乱跳, 整轮
    move_count=0一点没刷. Common..传奇这些交回wander撞怪 + 外部"一直攻击"就够了,
    不值得专门追。"""
    avoid_positions = []
    cautious_dets = []
    candidates = []
    for d in detections:
        bucket = classify_action(d["species"], d["rarity"])
        conf = d.get("confidence", 1.0)
        if bucket == "AVOID":
            avoid_positions.append(d["screen_pos"])
            continue
        if bucket == "CAUTIOUS":
            cautious_dets.append(d)
        if conf >= chase_min_conf:
            candidates.append((d, bucket))

    if avoid_positions:
        cx, cy = center
        in_range = [
            p for p in avoid_positions
            if math.hypot(p[0] - cx, p[1] - cy) <= avoid_trigger_px
        ]
        if in_range:
            return ("flee", in_range)

    if candidates:
        best, best_bucket = max(
            candidates,
            key=lambda pair: priority_score(pair[0]["species"], pair[0]["rarity"]))
        # max()按稀有度档优先, 所以best没到Mythic档 == 所有候选都没到. 没到就不追,
        # 交回wander(见docstring里的"密集刷怪区死循环"). Ultra CAUTIOUS(rank 6)恒
        # >= Mythic, 保留"对Ultra沙尘暴/仙人掌保持距离接战"这条.
        if RARITY_RANK[best["rarity"]] >= RARITY_RANK["Mythic"]:
            hold_px = cautious_hold_px if best_bucket == "CAUTIOUS" else None
            # 半路危险源: 所有AVOID怪(不管在不在flee触发半径内 —— 402px的Ultra蝎子
            # 不该触发flee, 但追别的怪时也不能直直穿过它) + 除目标外的CAUTIOUS怪。
            repel = list(avoid_positions)
            repel += [d["screen_pos"] for d in cautious_dets if d is not best]
            return ("chase", best, hold_px, repel)

    return ("wander", None)


_model = None


def load_enemy_model(path="models/desert.pt"):
    """加载一次desert.pt, 模块级单例缓存. 只走ultralytics.YOLO()的安全加载
    路径(底层是torch的weights_only安全反序列化), 不直接用不设限的
    torch.load(..., weights_only=False) —— 见
    docs/superpowers/specs/2026-08-16-sszone-enemy-detection-design.md的
    "模型来源"说明。"""
    global _model
    if _model is None:
        _model = YOLO(path)
    return _model


def scan_enemies(image=None, conf=0.4, model_path="models/desert.pt"):
    """跑一次YOLO检测, 返回屏幕坐标系(不是小地图坐标系!)下的检测列表.
    image=None时截一次全屏游戏画面; 传image是为了测试时能喂合成图片, 不用依赖
    真实截屏(pyautogui.screenshot()在没有真实显示器的环境里跑不了)。model_path
    转手传给load_enemy_model() —— 不在这里写死, 让调用方(main.py)的配置常量
    真正管用, 不是摆设。"""
    if image is None:
        screenshot = pyautogui.screenshot(region=[0, 0, utils.SCREEN_WIDTH, utils.SCREEN_HEIGHT])
        image = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

    model = load_enemy_model(model_path)
    results = model.predict(image, conf=conf, verbose=False)
    if not results:
        return []

    result = results[0]
    names = result.names
    detections = []
    for box in result.boxes:
        species = names[int(box.cls[0])]
        confidence = float(box.conf[0])
        x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
        bbox = (x1, y1, x2, y2)
        screen_pos = ((x1 + x2) / 2, (y1 + y2) / 2)
        detections.append({
            "species": species,
            "rarity": sample_rarity(image, bbox),
            "screen_pos": screen_pos,
            "bbox": bbox,
            "confidence": confidence,
        })
    return detections


def scan_bar_thickness(image=None, conf=0.4, model_path="models/desert.pt"):
    """截一帧 + YOLO + measure_hp_bar_thickness, 返回这一帧里能定位到的血条厚度
    列表. 跟 scan_enemies 平行, 但只关心血条粗细 (给 ensure_zoom_for_rarity 判
    zoom 够不够), 不算 screen_pos / rarity, 也就省掉每框一次 sample_rarity."""
    if image is None:
        screenshot = pyautogui.screenshot(region=[0, 0, utils.SCREEN_WIDTH, utils.SCREEN_HEIGHT])
        image = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

    model = load_enemy_model(model_path)
    results = model.predict(image, conf=conf, verbose=False)
    if not results:
        return []
    dets = [{"bbox": tuple(float(v) for v in box.xyxy[0])} for box in results[0].boxes]
    return measure_hp_bar_thickness(dets, image)
