from utils import *
import utils
from overlay import create_overlay
import argparse
import signal
import sys
import threading
import statistics
import cdp_bridge
import time
import random
import afk_watch
import enemy_detect
import app_config
from collections import deque

# ===== 索敌配置 (sszone敌怪检测/追击/规避) =====
ENEMY_MODEL_PATH = "models/desert.pt"
ENEMY_SCAN_INTERVAL = 0.12  # 秒, YOLO扫描节流间隔. 这是"决策新鲜度"的主旋钮:
                              # 追击/规避途中每tick都拿这份决策里的怪坐标去moveTo,
                              # 间隔越大, 中间那几tick就越是照着旧坐标全速走 —— 怪
                              # 早挪窝了, 人一头撞上去. 实测一次推理≈0.1s(Mac MPS;
                              # Windows CUDA更快), 设0.12基本每帧都能重扫. 推理太慢
                              # 的机器上循环会被推理本身卡住, 那也没办法, 至少不比
                              # 大间隔更差. 漫游时每腿路另受move_to_position的
                              # max_attempts限制(见下方wander分支).
AVOID_TRIGGER_PX = 400      # 屏幕像素半径, AVOID怪进入此半径触发逃离
CAUTIOUS_HOLD_PX = 250      # 屏幕像素, CAUTIOUS怪保持的最小距离(不继续贴近)
CHASE_MIN_CONF = 0.55      # 追击目标的最低YOLO置信度(幻影框过滤; 危险怪不受此限)
MYTHIC_LATCH_ENABLED  = True   # 贴脸有 Mythic 怪 → 锁定优先清掉再继续刷 (总开关)
MYTHIC_ENGAGE_PX      = 650    # Mythic 怪进此半径 → 锁定. 实测 --watch: 玩家眼里"贴脸"
                              # 的 Mythic 蝎子/甲虫 中心距其实 450~540px, 旧值 450 全卡在外
MYTHIC_RELEASE_PX     = 850    # 已锁定后, Mythic 出此半径才算脱离 (迟滞)
MYTHIC_RELEASE_MISSES = 3      # 连续多少次扫描没有合格 Mythic 才解锁
MYTHIC_STRAFE_RADIUS  = 180    # 甲虫/火蚁: 环绕它转圈的目标半径 (px)
MYTHIC_CACTUS_HOLD_PX = 220    # 仙人掌: 保持的距离 (px)
MYTHIC_STRAFE_K_RADIAL = 0.8   # 甲虫/火蚁环绕: 径向修正强度 (d 偏离半径时往里/外带多少)
ZOOM_MIN_THICK     = 4    # 血条中位厚度到这个像素数, sample_rarity 才稳 (实测)
ZOOM_MIN_SAMPLES   = 2    # 至少几条可测血条才据此判定 (少于就等 mob 出现)
ZOOM_SCROLL_AMOUNT = -120 # 每次滚轮 deltaY (走 CDP 打进页面). 负=往上滚=florr 拉近;
                          # 一格 ≈120. 方向猜的, 循环里会自翻转
ZOOM_MAX_SCROLLS   = 15   # 滚这么多次还没到就放弃 (可能已是最大 zoom)
ZOOM_WAIT_CAP      = 60   # 周围没 mob 时最多等这么多秒, 之后照常开刷
# 以上数值是没实机测过的占位默认值, 实机跑一遍后再按观察到的效果调.
# ================================================

# ===== 寻路 (移植自 my-auto-pathing-system/pathfinder.py 的高精度 A*) =====
# 核心: 不靠"侵蚀/膨胀墙"硬切(那会把 anthell 这类窄道图切成碎块), 而是给离墙近的
# 格子加动态通行代价(cell_cost), A* 自动沿最宽走廊走, 必要时仍能挤过窄道. 加上对角
# 切角防护(斜步要求两个正交邻格都可走), 路径不再穿墙角 —— 穿墙角正是窄道里反复
# 卡住的根因. 调参是原作者实测过的(2026-09): WALL_KEEPOUT=10, WALL_LAMBDA=8 时
# tight 像素从 15.3% 降到 0%. 见 my-auto-pathing-system/pathfinder.py.
WALL_KEEPOUT = 10             # 距墙 < WALL_KEEPOUT 的格子开始涨价
WALL_LAMBDA = 8.0             # 涨价幅度, 越大越远离墙
PATH_SMOOTH_CLEARANCE = 2     # 抽稀时 LOS 要求途经格子距墙 >= 此像素; 窄道自动保持密集
PATH_SMOOTH_LOOKAHEAD = 40    # 抽稀时一次最多向前看的节点数

# 两次移动指令之间的间隔(秒). 稠密航点下这是每次转向更新的节奏, 调小让移动更顺滑.
MOVE_TICK_INTERVAL = 0.02

# 脱困: 只确定一次方向 —— 沿"离自己最近的墙"的斥力方向顶 ESCAPE_HOLD_SECONDS 秒.
# 不掺目标方向、不反复重算(反复重算在窄道里会让方向来回翻, 顶不出卡点).
ESCAPE_HOLD_SECONDS = 1.0

# 到达判定半径(px): 距航点在这个距离内就算"到了". 太大时在窄道转弯处还没真正
# 到转角就从远处斜着冲下一个点, 切墙角卡住 —— anthell 实测调到 2px 才稳.
ARRIVE_RADIUS = 2.0

# 传送门逃生: 重生后可能被传送门传到 garden(右上角小地图匹配上 garden 模板).
# 切 garden 地图寻路到固定传送门坐标走回去. 详见 recover_from_teleport().
GARDEN_PORTAL_COORD = (133, 233)   # 用户在 maps/garden.png 上量出的传送门坐标
TELEPORT_CHECK_EVERY = 10          # move_to_position 每 N 个 tick 查一次 garden
TELEPORT_RECOVER_TIMEOUT = 15      # 等画面恢复的超时(秒)
PORTAL_WALK_SECONDS = 8            # 走到传送门后等进门/顶门的最长时间(秒)

# ===== 脱困 v2 (沿通道走向 + 验证换向 + 卡点封堵) =====
# 总开关: 置 False 即整体回退到旧版"垂直墙面单发斥力"脱困(_run_escape_v1), 并停止
# 记录/封堵卡点. 新逻辑若在实机翻车, 把这里改成 False 即可回到改动前的行为.
ESCAPE_V2_ENABLED     = True
ESCAPE_PROGRESS_EPS   = 1.5   # 脱困后位移 >= 这个才算"真挪窝了"(minimap 像素)
STUCK_PENALTY_TTL     = 20.0  # 卡点封堵存活时间(秒), 过期自动失效
STUCK_PENALTY_COST    = 60.0  # 追加到 cell_cost 的幅度(基础 ~1, 逼 A* 绕行但不封死)
STUCK_PENALTY_RADIUS  = 3     # 卡点周围涨价半径(px), 防 A* 贴边绕 1px 又钻回去
_ESCAPE_RAYS = [(-1, 0), (1, 0), (0, -1), (0, 1),
                (-1, -1), (-1, 1), (1, -1), (1, 1)]
_ESCAPE_MAX_RUN = 40          # _escape_candidates 发射线的最大净空统计长度(足够长)
_stuck_penalty = {}           # (x, y) -> 最近卡住的 time.time()

_NEIGHBORS = [(-1, 0), (1, 0), (0, -1), (0, 1),
              (-1, -1), (-1, 1), (1, -1), (1, 1)]
_COSTS = [1, 1, 1, 1, math.sqrt(2), math.sqrt(2), math.sqrt(2), math.sqrt(2)]


def _bfs_dist_to_wall(obs):
    """到最近障碍的 8-邻域步数(Chebyshev 距离). 用 cv2.distanceTransform(DIST_C)
    的 C 实现代替纯 Python BFS(300x300 从 ~700ms 降到 <1ms), 数值完全一致.
    全图无墙时 cv2 返回 FLT_MAX —— 任何 >= WALL_KEEPOUT 的距离对 cost 都等价
    (恒为 1), 钳到 1e6 保证 int32 安全又不改变语义."""
    fg = np.where(obs, 0, 255).astype(np.uint8)
    d = cv2.distanceTransform(fg, cv2.DIST_C, 3)
    return np.minimum(d, 10**6).astype(np.int32)


def _snap_to_passable(cost_map, x, y, max_radius):
    """把 (x, y) 吸附到最近的有限 cost 像素. 8 邻域 BFS, 半径上限 max_radius.
    端点常落在玩家/目标图标的边缘像素上, 二值化后是障碍, A* 直接拒收. 吸附让
    端点稳到主通行域; 目标点点在墙上时也能救回来, 不再"路径规划失败"."""
    h, w = cost_map.shape
    ix, iy = int(round(x)), int(round(y))
    if not (0 <= ix < w and 0 <= iy < h):
        return None
    if np.isfinite(cost_map[iy, ix]):
        return (float(ix), float(iy))
    visited = np.zeros((h, w), dtype=bool)
    visited[iy, ix] = True
    q = deque([(iy, ix, 0)])
    while q:
        cy, cx, d = q.popleft()
        if d >= max_radius:
            continue
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0:
                    continue
                ny, nx = cy + dy, cx + dx
                if not (0 <= ny < h and 0 <= nx < w) or visited[ny, nx]:
                    continue
                visited[ny, nx] = True
                if np.isfinite(cost_map[ny, nx]):
                    return (float(nx), float(ny))
                q.append((ny, nx, d + 1))
    return None


def find_path(cost_map, d_wall, start, goal, extra_cost=None):
    """A* 8 邻域寻路. 返回路径 list[(x, y)] 或 None.

    cost_map: (h, w) float32, np.inf=不可走, 有限值=通行. 只用作通行性掩码,
    实际 cell_cost 由到墙距离动态计算:
        cell_cost = 1 + WALL_LAMBDA * max(0, WALL_KEEPOUT - d)^2 / WALL_KEEPOUT^2
    离墙越近代价越高, A* 自然沿最宽走廊走. 斜向移动要求两个正交邻格都可走
    (不切墙角). d_wall 由调用方算出(抽稀还要复用), 不在此重算.
    extra_cost: 可选, 与 cost_map 同形状的网格, 叠加到 cell_cost 上(卡点封堵用)."""
    h, w = cost_map.shape
    sx, sy = int(round(start[0])), int(round(start[1]))
    gx, gy = int(round(goal[0])), int(round(goal[1]))

    snapped_start = _snap_to_passable(cost_map, sx, sy, max_radius=30)
    if snapped_start is None:
        return None
    sx, sy = int(round(snapped_start[0])), int(round(snapped_start[1]))
    snapped_goal = _snap_to_passable(cost_map, gx, gy, max_radius=30)
    if snapped_goal is None:
        return None
    gx, gy = int(round(snapped_goal[0])), int(round(snapped_goal[1]))

    if not np.isfinite(cost_map[sy, sx]) or not np.isfinite(cost_map[gy, gx]):
        return None

    keepout = max(1, WALL_KEEPOUT)
    inv_keepout2 = 1.0 / (keepout * keepout)

    # 扁平索引 + numpy 数组代替 dict/set, 去掉元组哈希开销(纯 Python 下这是
    # 本函数的主要耗时, 300x300 全图搜索从 ~1.2s 降到 ~0.3s).
    start_i = sy * w + sx
    goal_i = gy * w + gx
    if start_i == goal_i:
        return [(sx, sy)]
    passable = np.isfinite(cost_map)
    cost_grid = np.where(
        passable,
        1.0 + WALL_LAMBDA * np.maximum(keepout - d_wall, 0) ** 2 * inv_keepout2,
        0.0,
    )
    if extra_cost is not None:
        cost_grid = cost_grid + extra_cost
    g_score = np.full(w * h, float("inf"))
    closed = np.zeros(w * h, dtype=bool)
    g_score[start_i] = 0.0
    open_heap = [(0.0, start_i)]
    came = {}

    while open_heap:
        f, current_i = heapq.heappop(open_heap)
        if closed[current_i]:
            continue
        if current_i == goal_i:
            path = [current_i]
            while current_i in came:
                current_i = came[current_i]
                path.append(current_i)
            path.reverse()
            return [(i % w, i // w) for i in path]
        closed[current_i] = True
        cx = current_i % w
        cy = current_i // w
        for (dx, dy), move_cost in zip(_NEIGHBORS, _COSTS):
            nx, ny = cx + dx, cy + dy
            if not (0 <= nx < w and 0 <= ny < h):
                continue
            ni = ny * w + nx
            if closed[ni]:
                continue
            if not passable[ny, nx]:
                continue
            if dx != 0 and dy != 0:
                if not passable[cy, nx] or not passable[ny, cx]:
                    continue
            tentative = g_score[current_i] + move_cost * cost_grid[ny, nx]
            if tentative < g_score[ni]:
                came[ni] = current_i
                g_score[ni] = tentative
                heapq.heappush(open_heap, (tentative + math.hypot(nx - gx, ny - gy), ni))
    return None


def line_of_sight_clear(binary_map, a, b):
    """Bresenham 直线可见性 + 切角拒绝. binary_map 上 255=可走, 0=障碍.
    斜步跨过墙角时, 两个正交角格任一为墙 → 返回 False(这条直线会切角)."""
    x0, y0 = a[0], a[1]
    x1, y1 = b[0], b[1]
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy

    while True:
        if binary_map[y0, x0] == 0:
            return False
        if x0 == x1 and y0 == y1:
            return True
        e2 = err * 2
        moved_x = moved_y = False
        if e2 > -dy:
            err -= dy
            x0 += sx
            moved_x = True
        if e2 < dx:
            err += dx
            y0 += sy
            moved_y = True
        if moved_x and moved_y:
            if binary_map[y0 - sy, x0] == 0 or binary_map[y0, x0 - sx] == 0:
                return False
    return True


def _smooth_path(safe_map, path):
    """贪心拉绳抽稀: 从当前航点向后最多看 PATH_SMOOTH_LOOKAHEAD 个节点, 取最远
    的 LOS 可达点作下一航点. safe_map 上 LOS 要求途经格子全部可走 —— 开阔区
    (距墙 >= PATH_SMOOTH_CLEARANCE)自动拉成稀疏直线, 窄道里 safe_map 全是障碍,
    LOS 全失败, 航点保持逐像素密集(精确爬行)."""
    if not path:
        return path
    out = [path[0]]
    i = 0
    n = len(path)
    while i < n - 1:
        j = min(i + PATH_SMOOTH_LOOKAHEAD, n - 1)
        while j > i + 1 and not line_of_sight_clear(safe_map, path[i], path[j]):
            j -= 1
        out.append(path[j])
        i = j
    return out


def find_walkable_path(binary_map, start, goal, extra_cost=None):
    """高层入口: 0/255 二值图 → 高精度 A* 路径(已抽稀). extra_cost 是可选的同形状
    代价网格(卡点封堵用), 传入则叠加到 A* 的 cell_cost 上. 返回 list[(x,y)] 或 None."""
    if binary_map is None or start is None or goal is None:
        return None
    cost = np.where(binary_map == 255, 1.0, np.inf).astype(np.float32)
    d_wall = _bfs_dist_to_wall(~np.isfinite(cost))
    dense = find_path(cost, d_wall, start, goal, extra_cost=extra_cost)
    if dense is None:
        return None
    safe = np.where(d_wall >= PATH_SMOOTH_CLEARANCE, 255, 0).astype(np.uint8)
    return _smooth_path(safe, dense)


def _nearest_wall_direction(pos, binary_map, max_radius=6):
    """找离玩家最近的一个墙像素, 返回 玩家→墙 的单位方向 (dx, dy). 半径内没墙 → None.
    BFS 从玩家向外扩, 遇到的第一个墙像素就是最近的(8邻域步数)."""
    px, py = pos
    h, w = binary_map.shape
    visited = np.zeros((h, w), dtype=bool)
    visited[py, px] = True
    q = deque([(py, px, 0)])
    while q:
        cy, cx, depth = q.popleft()
        if depth > max_radius:
            continue
        if depth > 0 and binary_map[cy, cx] == 0:
            dx, dy = cx - px, cy - py
            n = math.hypot(dx, dy)
            if n < 1e-6:
                return None
            return (dx / n, dy / n)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0:
                    continue
                ny, nx = cy + dy, cx + dx
                if not (0 <= ny < h and 0 <= nx < w) or visited[ny, nx]:
                    continue
                visited[ny, nx] = True
                q.append((ny, nx, depth + 1))
    return None


def _run_escape(pos, target, binary_map):
    """卡住脱困总入口. ESCAPE_V2_ENABLED 决定走新版还是旧版.

    旧版(v1): 只确定一次方向 —— 沿"离自己最近的墙"的斥力方向顶 ESCAPE_HOLD_SECONDS
    秒. 窄道里这个方向≈垂直墙面, 会顶进对侧墙(净位移≈0).
    新版(v2): 沿通道走向(净空最长方向)逐个尝试, 顶完验证真挪窝了没, 没动就换下一个."""
    if ESCAPE_V2_ENABLED:
        _run_escape_v2(pos, target, binary_map)
    else:
        _run_escape_v1(pos, target, binary_map)


def _run_escape_v1(pos, target, binary_map):
    """旧版脱困(回退用): 沿"离自己最近的墙"的斥力方向单发顶 ESCAPE_HOLD_SECONDS 秒.
    不掺目标方向、不反复重算. 位置/地图拿不到或附近没墙就退回墙色随机脱困."""
    if pos is None or binary_map is None:
        execute_anti_stuck()
        return
    wall_dir = _nearest_wall_direction(pos, binary_map)
    if wall_dir is None:
        execute_anti_stuck()
        return
    extend = 250 * mouse_scale()
    mouse = clamp_to_screen(
        SCREEN_WIDTH / 2 - wall_dir[0] * extend,
        SCREEN_HEIGHT / 2 - wall_dir[1] * extend,
    )
    execute_anti_stuck(mouse_target=mouse, duration=ESCAPE_HOLD_SECONDS)


def _escape_candidates(pos, binary_map, target=None):
    """脱困候选方向, 按优先级排序. 从玩家位置向 8 个方向发射线, 数每个方向连续可走
    (binary_map==255)的像素数作为"净空长度": 净空越长的方向越优先(窄道里就是通道
    轴向), 长度相近时更顺目标方向的那个排前面. 返回单位方向 [(dx, dy), ...];
    四周全走不动 -> 空列表."""
    px, py = int(round(pos[0])), int(round(pos[1]))
    h, w = binary_map.shape
    if not (0 <= py < h and 0 <= px < w):
        return []
    target_dx = target_dy = None
    if target is not None:
        target_dx, target_dy = target[0] - px, target[1] - py
    scored = []
    for (dx, dy) in _ESCAPE_RAYS:
        run = 0
        cx, cy = px, py
        while run < _ESCAPE_MAX_RUN:
            cx += dx
            cy += dy
            if not (0 <= cy < h and 0 <= cx < w):
                break
            if binary_map[cy, cx] != 255:
                break
            run += 1
        if run <= 0:
            continue
        n = math.hypot(dx, dy)
        ux, uy = dx / n, dy / n
        align = 0.0
        if target_dx is not None:
            tn = math.hypot(target_dx, target_dy)
            if tn > 1e-6:
                align = (ux * target_dx + uy * target_dy) / tn
        scored.append((run, align, (ux, uy)))
    # 净空长度降序为主, 目标对齐度降序为次.
    scored.sort(key=lambda t: (-t[0], -t[1]))
    return [d for (_, _, d) in scored]


def _push_and_verify(pos, dir_vec):
    """朝 dir_vec 顶 ESCAPE_HOLD_SECONDS 秒(满速)后回中, 读一次位置, 位移 >=
    ESCAPE_PROGRESS_EPS 算"真挪窝了". 读完位置是 None 当挪了处理(别在读数失败上
    死转). 返回是否成功脱身."""
    extend = 250 * mouse_scale()
    mouse = clamp_to_screen(
        SCREEN_WIDTH / 2 + dir_vec[0] * extend,
        SCREEN_HEIGHT / 2 + dir_vec[1] * extend,
    )
    execute_anti_stuck(mouse_target=mouse, duration=ESCAPE_HOLD_SECONDS)
    new_pos = get_player_position()
    if new_pos is None:
        return True
    return distance(pos, new_pos) >= ESCAPE_PROGRESS_EPS


def _run_escape_v2(pos, target, binary_map):
    """新版脱困: 沿通道走向(净空最长方向)逐个尝试, 顶完验证, 没挪窝就换下一个候选
    方向; 所有方向都失败才退回 execute_anti_stuck 的墙色/随机硬闯."""
    if pos is None or binary_map is None:
        execute_anti_stuck()
        return
    for dir_vec in _escape_candidates(pos, binary_map, target):
        if _push_and_verify(pos, dir_vec):
            return
    execute_anti_stuck()


def _mark_stuck(pos):
    """记录一个刚卡住的点(卡点封堵), 让重寻路绕开它. 仅在 ESCAPE_V2_ENABLED 下生效."""
    if ESCAPE_V2_ENABLED and pos is not None:
        _stuck_penalty[(int(round(pos[0])), int(round(pos[1])))] = time.time()


def _build_stuck_cost(shape):
    """把 _stuck_penalty 里还没过期的卡点摊成一个 extra_cost 网格(过期的顺手清掉).
    没有生效记录或开关关闭 -> None(不改变寻路). 卡点周围 STUCK_PENALTY_RADIUS 半径内
    按 Chebyshev 距离线性衰减涨价 —— 只是抬代价不封死, 万一那是唯一通道仍能通过."""
    if not ESCAPE_V2_ENABLED or not _stuck_penalty:
        return None
    now = time.time()
    active = {p: t for p, t in _stuck_penalty.items() if now - t < STUCK_PENALTY_TTL}
    _stuck_penalty.clear()
    _stuck_penalty.update(active)
    if not active:
        return None
    h, w = shape
    grid = np.zeros((h, w), dtype=np.float32)
    r = STUCK_PENALTY_RADIUS
    denom = r + 1
    for (px, py) in active:
        x0, x1 = max(0, px - r), min(w, px + r + 1)
        y0, y1 = max(0, py - r), min(h, py + r + 1)
        for yy in range(y0, y1):
            for xx in range(x0, x1):
                d = max(abs(xx - px), abs(yy - py))
                grid[yy, xx] = max(grid[yy, xx], STUCK_PENALTY_COST * (1.0 - d / denom))
    return grid


def _wait_for_screen_recovery(timeout=TELEPORT_RECOVER_TIMEOUT):
    """等传送黑屏结束. 返回 True=已恢复, False=超时."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not screen_is_black():
            return True
        time.sleep(0.5)
    return False


_teleport_recovery_active = False   # 防递归: 恢复期间 garden 检测不再触发


def _garden_escape_needed():
    """在 garden 上且刷图目标不是 garden → 需要传送逃生回原图.
    刷图目标本身是 garden 时 is_on_garden() 恒真, 绝不能触发逃生(否则 bot
    永远往传送门跑, 刷不了). 用 utils.MAP 而不是 from utils import * 带入的
    MAP 快照 —— 调用时才读当前值(跟 can_switch_server 同一告诫)."""
    return not _teleport_recovery_active and utils.MAP != "garden" and is_on_garden()


def _enter_portal_and_wait(timeout=PORTAL_WALK_SECONDS):
    """已到传送门附近(坐标不精确, 实际门在坐标点右下方) → 朝右下小幅走, 监测
    全屏黑屏(进入传送门的信号). 幅度小 + 检查密: 幅度大角色走得快, 会冲过门的小
    碰撞区; 检查疏会漏掉黑屏那一瞬 —— 两个都是"有概率进不去"的原因."""
    deadline = time.time() + timeout
    step = 40 * mouse_scale()   # 朝右下的屏幕偏移量(小, 走慢点)
    while time.time() < deadline:
        if screen_is_black():
            if _wait_for_screen_recovery():
                print("✅ 传送完成, 回到 anthell")
                return True
            return False
        pyautogui.moveTo(clamp_to_screen(SCREEN_WIDTH // 2 + step, SCREEN_HEIGHT // 2 + step))
        time.sleep(0.15)
    return False


def _precise_approach(location, max_attempts=60):
    """目标点必须精确到达(传送门入口对 1px 偏差都敏感). 从当前点朝目标精确靠拢,
    直到 get_player_position() == location(玩家标记真正踩到目标格). 返回是否到达."""
    for _ in range(max_attempts):
        pos = get_player_position()
        if pos is None:
            time.sleep(0.05)
            continue
        if pos == location:
            return True
        dx = location[0] - pos[0]
        dy = location[1] - pos[1]
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            time.sleep(0.05)
            continue
        extend = max(min(dist * 45, 200), 30) * mouse_scale()
        mouse = clamp_to_screen(SCREEN_WIDTH / 2 + dx / dist * extend,
                                SCREEN_HEIGHT / 2 + dy / dist * extend)
        pyautogui.moveTo(mouse)
        time.sleep(0.05)
    return False


def recover_from_teleport():
    """传送门逃生: 小地图检测到 garden(被传送) → 切 garden 地图寻路到固定传送门
    坐标 → 走进去回 anthell. 返回 True=已回 anthell, False=失败(外层 garden 检测
    会自愈重试). 不用 get_player_position() 当判据(错误地图上它也能测出垃圾位置)."""
    global _teleport_recovery_active
    _teleport_recovery_active = True
    try:
        print("🗺️ 检测到 garden 小地图, 前往传送门回 anthell...")
        overlay.update(state="传送中", message="检测到 garden, 前往传送门")
        original_map = utils.MAP or "anthell"
        apply_map("garden")
        try:
            if not lazy_theta_pathing(GARDEN_PORTAL_COORD, []):
                # 寻路中途全屏黑屏(已走进门) 或小地图不再是 garden(已回 anthell)
                # → 恢复完成. 绝不能当"失败"处理: 否则 MAP 会留着 garden, bot 拿
                # garden 坐标在 anthell 上乱跑(用户实测的"黑屏后保留在 garden").
                if screen_is_black() or not is_on_garden():
                    print("✅ 已触发传送/离开 garden, 恢复完成")
                    return True
                print("❌ 未能走到 garden 传送门")
                overlay.update(state="出错", message="未能走到传送门")
                return False
            entered = _enter_portal_and_wait()
            print(f"🔁 recover_from_teleport 结果: {'✅ 已回 anthell' if entered else '❌ 进门失败'}")
            return entered
        finally:
            apply_map(original_map)   # 无论成败都恢复原地图(anthell), 黑屏之后必须切回
    finally:
        _teleport_recovery_active = False


def reset_keyboard():
    pyautogui.keyUp("space")
    keyup("w")
    keyup("a")
    keyup("s")
    keyup("d")


def move_to_position(current_pos, target_pos, max_attempts=200, stall_limit=13,
                     progress_epsilon=1.5, on_tick=None):
    """移动到目标位置.

    on_tick: 可选回调, 每个内循环 tick(moveTo 之后、sleep 之前)调一次, 传入当前
    minimap 坐标. 返回真值 → 立刻收手, move_to_position 把那个真值原样返回给调用方
    (约定用短字符串, 比如 "enemy"). 给 auto_farming 的 wander 腿用: 这函数是阻塞的,
    整段(max_attempts×0.05s)期间外层拿不回控制权、跑不了索敌, 快怪冲过来就撞死 ——
    钩子让 wander 途中也能触发一次索敌、需要接战/规避时中断这条腿. on_tick=None
    (execute_path / lazy_theta_pathing 那些纯赶路调用)时行为跟以前完全一样.

    跟原版(github.com/Shiny-Ladybug/florr-auto-pathing)的go_direction比对后, 补回了
    两条它有而我们这版"简化版本"漏掉的关键判定 —— 之前只看"到没到5px内", 不看有
    没有在朝目标靠近, 导致过头或者原地打转都要死等到max_attempts才认卡住:
      - 冲过头(这次比上次离目标还远) —— 已经很接近了, 直接算到达, 别死磕这一段.
      - 连续stall_limit次距离都没缩短(原地打转) —— 才真正判定为卡住, 不是简单数
        循环次数. max_attempts只是保底上限, 防止极端情况死循环, 平时基本不会撞到.

    原版(以及我们最早抄过来那版)这两条判定都是用"距离完全相等"(dist == last_dist)
    做比较 —— 实测位置检测本身有量化噪声, 连续两帧distance几乎不可能位级精确相等,
    导致卡在死角/洞里时stall_count永远攒不起来, "卡住"判定形同虚设, 角色能在原地
    干耗到天荒地老。改用progress_epsilon容差带: 只要没有明显缩短(缩短量小于
    progress_epsilon)就算一次停滞, 不再要求毫厘不差.
    """
    if current_pos is None or target_pos is None:
        return "stuck"

    last_dist = None
    stall_count = 0
    attempts = 0
    tick_count = 0
    while attempts < max_attempts:
        if afk_watch.poll_afk_pause():
            overlay.update(state="AFK弹窗处理中", message="等待florr-auto-afk解题")
            time.sleep(0.2)
            # 暂停期间角色可能被上一次鼠标指令继续带着走(这游戏靠鼠标位置转向,
            # 不是靠按键状态) —— 暂停12秒后dist跟last_dist已经没有可比性了,
            # 不清零的话很容易被误判成"冲过头"直接算到达. 清成跟函数开头一样的
            # 初始状态, 让暂停后第一个真实tick当"刚开始移动"处理.
            last_dist = None
            stall_count = 0
            continue

        tick_count += 1
        if (tick_count % TELEPORT_CHECK_EVERY == 0 and _garden_escape_needed()):
            # 被传送到 garden: 导航回 anthell. 恢复后角色挪了窝, 旧停滞状态作废
            # (跟 AFK 暂停同一套重置). 恢复失败也往下走 —— 正常定位测不到会返回
            # stuck, 交外层处理.
            if recover_from_teleport():
                last_dist = None
                stall_count = 0

        current_pos = get_player_position()
        if current_pos is None:
            overlay.update(state="无法检测位置", message="移动中丢失玩家位置")
            return "stuck"

        # 计算方向
        dx = target_pos[0] - current_pos[0]
        dy = target_pos[1] - current_pos[1]
        dist = math.sqrt(dx**2 + dy**2)

        overlay.update(state="移动中", pos=current_pos, target=target_pos)

        # 如果已到达目标 (半径调小: 太大时窄道转弯处会提前转向切墙角)
        if dist < ARRIVE_RADIUS:
            reset_keyboard()
            return True

        if last_dist is not None:
            if dist > last_dist + progress_epsilon:
                # 明显冲过头了, 已经足够接近, 当作到达, 不继续死磕这一段.
                reset_keyboard()
                return True
            elif dist < last_dist - progress_epsilon:
                # 明显缩短了, 真有进展, 停滞计数清零.
                stall_count = 0
            else:
                # 在容差带内(包括原来要求毫厘不差才算的"完全相等") —— 没有实质进展.
                stall_count += 1
        last_dist = dist

        if stall_count > stall_limit:
            reset_keyboard()
            overlay.update(state="卡住", message=f"原地打转{stall_count}次")
            return "stuck"

        # 移动鼠标指向目标
        extend = max(min(dist * 45, 500), 50) * mouse_scale()
        if dist > 0:
            extend_x = extend * dx / dist
            extend_y = extend * dy / dist
        else:
            extend_x = extend_y = 0

        mouse_pos = clamp_to_screen(SCREEN_WIDTH // 2 + extend_x, SCREEN_HEIGHT // 2 + extend_y)
        pyautogui.moveTo(mouse_pos)

        # 检查游戏状态 —— 不用check_stage(): 它的in_game_dead/in_menu判定靠
        # 探测固定像素点是不是某个精确RGB, 连1920x1080参照分辨率下都从没真正
        # 触发过(见on_death_screen()的注释), 2026-08-26在1280x923上实测过更是
        # 直接把"正常游戏中"误判成"in_game_dead"(探测点landed在别的白色UI上)。
        # on_death_screen()/on_start_screen()是靠采样一块区域算颜色占比, 已经
        # 经过缩放+实机验证, 顶层重试循环(lazy_theta_pathing)一直用的就是这套,
        # 这里跟着统一, 不再有两条不一致的死亡检测逻辑.
        if on_death_screen():
            reset_keyboard()
            overlay.update(state="已死亡")
            return "in_game_dead"
        elif on_start_screen():
            reset_keyboard()
            overlay.update(state="菜单中")
            return "in_menu"

        if on_tick is not None:
            signal = on_tick(current_pos)
            if signal:
                reset_keyboard()
                return signal

        attempts += 1
        time.sleep(MOVE_TICK_INTERVAL)

    reset_keyboard()
    overlay.update(state="卡住", message=f"{max_attempts}次尝试后仍未到达")
    return "stuck"


def _same_direction(a, b, c):
    """a→b 与 b→c 是否同向(允许步长不同): 叉积=0 且点积>0(共线且同向).
    稠密航点(1px 一格)在直走廊里会逐格停顿, 用这个把同向直线段合并成一条腿."""
    dx1, dy1 = b[0] - a[0], b[1] - a[1]
    dx2, dy2 = c[0] - b[0], c[1] - b[1]
    return dx1 * dy2 == dy1 * dx2 and dx1 * dx2 + dy1 * dy2 > 0


def execute_path(path):
    """执行路径"""
    if path is None or len(path) == 0:
        return "stuck"

    print(f"🗺️  执行路径，共 {len(path)} 个节点...")

    # 稠密航点在窄道里一格一格走, 每次 move_to_position 到达都回中鼠标停一下 ——
    # 直线段(同向连续节点)合并成一条腿一次走完, 转弯处才单独处理(不切角).
    i = 0
    n = len(path)
    while i < n - 1:
        j = i + 1
        while j < n - 1 and _same_direction(path[i], path[j], path[j + 1]):
            j += 1
        current, next_point = path[i], path[j]

        print(f"   [{i+1}/{n-1}] 移动到 {next_point}")
        result = move_to_position(current, next_point)

        if result == "stuck":
            print(f"   ⚠️ 在 {next_point} 卡住了")
            return "stuck"
        elif result in ["in_game_dead", "in_menu"]:
            return result
        i = j

    print("✅ 路径执行完成")
    return True


def lazy_theta_pathing(location, area=[]):
    """寻路到目标区域. 检测不到位置、或者移动卡住, 都不放弃, 一直重试
    (脱困后重新规划路径)直到真的到达/玩家死亡/进了菜单为止。
    """
    retry_count = 0

    while True:
        if afk_watch.poll_afk_pause():
            overlay.update(state="AFK弹窗处理中", message="等待florr-auto-afk解题")
            time.sleep(0.2)
            continue

        if _teleport_recovery_active:
            # 恢复期间: 全屏黑屏(已走进传送门, 不要求精确到达坐标) 或小地图不再是
            # garden(已回 anthell) → 交回 recover 收尾, 别拿 garden 地图乱导航.
            if screen_is_black() or not is_on_garden():
                print("🌀 恢复期间触发传送/离开 garden, 交回 recover 处理")
                return False
        elif _garden_escape_needed():
            # 被传送门传到 garden(小地图匹配上 garden 模板, 且刷图目标不是
            # garden): 导航回原图.
            recover_from_teleport()
            continue

        # 死亡/开局画面的检查必须放在最前面、且不能只在"pos is None"分支里做 ——
        # 实机踩过坑: 死亡结算画面上凑巧有个像素跟玩家标记色对上了, 稳定测出一个
        # 假位置(不是None!), 导致下面那个"pos is None才查死亡画面"的分支永远
        # 进不去, 角色明明卡在死亡画面上, 脚本还在拿假坐标一遍遍重新规划路径。
        # 不管这轮测没测到位置, 每次循环开头都先确认没有落在这两个画面上.
        if on_death_screen() or on_start_screen():
            print("🔁 检测到落在死亡/开局画面上, 交回上层处理")
            overlay.update(state="出错", message="落在死亡/开局画面, 交回上层重开")
            return False

        pos = get_player_position()

        if pos is None:
            # 死亡/开局画面已经在循环开头查过了, 到这里还是None就是真的暂时没
            # 认出玩家标记(截图抖动之类), 单纯重试.
            retry_count += 1
            print(f"⚠️ 无法检测玩家位置，持续重试中 (第{retry_count}次)...")
            # 只在小状态框 + 控制台/GUI 日志里提示 —— 以前 retry_count>7 会弹一个
            # 屏幕正中央的大号黄色警告窗, 结果那个窗盖住了小地图, get_player_position()
            # 截图截到的是警告窗本身, 位置永远认不回来, 警告窗也就再也不消失. 死循环.
            hint = ("持续重试中 (第{}次)".format(retry_count) if retry_count <= 7
                    else "第{}次仍测不到 —— 检查地图是否被放大(M键) / 窗口是否全屏(F11)".format(retry_count))
            overlay.update(state="无法检测位置", message=hint)
            time.sleep(1)
            continue

        retry_count = 0
        print(f"\n📍 寻路: {pos} -> {location}")
        overlay.update(state="寻路中", pos=pos, target=location, message="规划路径...")
        time_now = time.time()

        binary_map = load_binary_map()
        if binary_map is None:
            print("❌ 地图加载失败")
            overlay.update(state="出错", message="地图加载失败")
            return False

        path = find_walkable_path(binary_map, pos, location,
                                  extra_cost=_build_stuck_cost(binary_map.shape))
        print(f"⏱️  寻路耗时: {time.time() - time_now:.2f}秒")

        if path is None:
            print("❌ 路径规划失败")
            overlay.update(state="出错", message="路径规划失败")
            return False

        print(f"✅ 找到路径，共 {len(path)} 个点")
        overlay.update(message=f"找到路径, 共{len(path)}个点")
        stat = execute_path(path)

        if stat == "stuck":
            # 卡住时 if_in_area / ==location 不可能成立, 别白读一次位置(截图+全图
            # 校准 ~0.2s), 直接脱困后重寻路, 缩短"卡住→重新开走"的间隔.
            print("🔄 检测到卡住, 脱困后重新规划路径...")
            overlay.update(state="卡住", message="脱困中, 稍后重新寻路")
            _run_escape(pos, location, binary_map)
            _mark_stuck(pos)   # 记卡点, 下次规划绕开这个瓶颈(别再原路冲回去)
            continue

        # 检查是否到达目标区域
        current_pos = get_player_position()
        if current_pos and if_in_area(area, current_pos):
            print(f"✅ 已到达目标区域！位置: {current_pos}\n")
            overlay.update(state="完成", pos=current_pos, message="已到达目标区域")
            return True

        if current_pos == location:
            print(f"✅ 已到达目标位置！\n")
            overlay.update(state="完成", pos=current_pos, message="已到达目标位置")
            return True

        # 目标必须精确到达(传送门入口对 1px 偏差都敏感): 已到附近(≤ARRIVE_RADIUS)
        # 但没到精确格 → 继续精确靠拢. 否则 move_to_position 在 2px 内就算"到了",
        # 而这里要求精确相等, 会停在 1-2px 外反复重规划死循环, 永远到不了终点.
        if current_pos and location and distance(current_pos, location) <= ARRIVE_RADIUS:
            if _precise_approach(location):
                print(f"✅ 已到达目标位置！位置: {location}\n")
                overlay.update(state="完成", pos=location, message="已到达目标位置")
                return True

        if on_death_screen():
            print("💀 玩家已死亡")
            overlay.update(state="已死亡")
            return False
        elif on_start_screen():
            print("📋 玩家在菜单中")
            overlay.update(state="菜单中")
            return False


def random_walkable_point(area, binary_map, max_tries=20):
    """在矩形区域内随机采样一个可走点(binary_map里=255的), 而不是纯瞎猜坐标.

    之前是在整个矩形里直接randint, 完全不管地图形状 —— 采样到墙里/区域外形状
    (不是每个刷怪区域都是实心矩形)的点很常见, 角色会直接顶着墙走不过去。
    这里改成拒绝采样: 采到墙就重来, max_tries次都不行就退回原来的随机点
    (兜底, 不会因为极端形状的区域卡死采样).
    """
    (x1, y1), (x2, y2) = area
    for _ in range(max_tries):
        x = random.randint(x1, x2)
        y = random.randint(y1, y2)
        if binary_map is not None and 0 <= y < binary_map.shape[0] and 0 <= x < binary_map.shape[1]:
            if binary_map[y, x] == 255:
                return x, y
    return random.randint(x1, x2), random.randint(y1, y2)


def _maybe_scan_enemies(enemy_ai_enabled, now, last_enemy_scan, prev_decision, prev_detections):
    """索敌节流 + 总开关. 返回 (decision, detections, last_enemy_scan, scanned).

    - enemy_ai_enabled=False: 永远返回漫游决策 + 空检测列表, 一次都不碰 enemy_detect.
    - 距上次扫描不到 ENEMY_SCAN_INTERVAL: 沿用上一轮的 decision 和 detections.
    - 到点了: 跑一次 scan_enemies + select_action; 任何异常 → 漫游 + 空列表.
    detections 单独回传是给 Mythic 近身锁定用的 (select_action 不看这个).
    scanned 只在真跑了一次 scan_enemies 的分支为 True (含扫描抛错 —— 尝试过一次
    观测就算数); 关掉索敌 / 节流跳过的 tick 是 False. 调用方靠这个只在新鲜扫描上
    推进 Mythic miss 计数, 别让节流 tick 拿同一份缓存检测重复扣数.
    """
    if not enemy_ai_enabled:
        return ("wander", None), [], last_enemy_scan, False
    if now - last_enemy_scan < ENEMY_SCAN_INTERVAL:
        return prev_decision, prev_detections, last_enemy_scan, False
    last_enemy_scan = now
    try:
        detections = enemy_detect.scan_enemies(model_path=ENEMY_MODEL_PATH)
        decision = enemy_detect.select_action(
            detections,
            avoid_trigger_px=AVOID_TRIGGER_PX,
            cautious_hold_px=CAUTIOUS_HOLD_PX,
            chase_min_conf=CHASE_MIN_CONF,
        )
    except Exception as e:
        print(f"⚠️ 索敌出错, 本轮当漫游处理: {e}")
        decision, detections = ("wander", None), []
    return decision, detections, last_enemy_scan, True


def _update_mythic_latch(latched, misses, has_target, release_misses):
    """Mythic 近身锁定的状态机 (纯函数). has_target = 这次扫描有没有合格的近身
    Mythic. 有 → 锁定, misses 清零. 没有且已锁定 → misses+1, 攒够 release_misses
    就解锁 (迟滞, 扛检测闪烁). 返回 (latched, misses)."""
    if has_target:
        return True, 0
    if not latched:
        return False, 0
    misses += 1
    if misses >= release_misses:
        return False, 0
    return True, misses


def _drive_and_check_stall(mouse_target, current_pos, chase_pos_history, state, message):
    """chase / flee / 清青怪 三条分支共用的"卡住检测 + 出手"收尾.

    mouse_target == enemy_detect.SCREEN_CENTER 是"刻意停在这" (保持距离 / 合力抵消),
    不算移动 —— 这种 tick 不往 history 塞样本 (留给下一个真在动的 tick). 其余情况:
    攒近期 minimap 坐标, 时间窗内净位移不足 → execute_anti_stuck() 接管这一 tick、
    返回 "stuck"; 否则 overlay 更新 + moveTo + sleep, 返回 "moved"."""
    if mouse_target != enemy_detect.SCREEN_CENTER:
        chase_pos_history.append(current_pos)
        if len(chase_pos_history) > enemy_detect.CHASE_STALL_WINDOW:
            chase_pos_history.pop(0)
        if enemy_detect.chase_is_stalled(chase_pos_history):
            print(f"⚠️ {state}途中卡住, 脱困一下...")
            overlay.update(state="卡住", message=f"{state}卡住, 脱困中")
            execute_anti_stuck()
            chase_pos_history.clear()
            return "stuck"
    overlay.update(state=state, pos=current_pos, message=message)
    pyautogui.moveTo(clamp_to_screen(*mouse_target))
    time.sleep(0.05)
    return "moved"


def ensure_zoom_for_rarity(enemy_ai_enabled):
    """开刷前把相机滚轮拉近到"血条中位厚度 >= ZOOM_MIN_THICK" —— 低于这个厚度
    sample_rarity 读不出稀有度词 (实测 厚<4 时 Mythic 名牌就几个青像素, 全读
    Common, Mythic 锁定永不触发). best-effort:
      - enemy_ai_enabled=False → 直接返回 False (zoom 只影响稀有度, 索敌关了不用管)
      - 进来先把鼠标挪回屏幕中心 —— florr 靠鼠标位置操纵角色, 不居中的话等待/AFK
        分支里角色会一直往边上走
      - 够不到 ZOOM_MIN_SAMPLES 条可测血条 (周围没 mob) → 不滚, 等
      - 任何放弃路径 (超时 / 滚满 ZOOM_MAX_SCROLLS / 两个方向都没改善) 都先把已经
        滚掉的量 scroll(-applied) 还原, 别把 zoom 留在半路比进来时还糟
      - 滚一下中位厚度反而变小 → 方向反了, 翻转一次 ZOOM_SCROLL_AMOUNT 符号;
        翻转后还在变小 → 撤销并放弃 (别顺着噪声一路滚到 cap)
      - 任何异常 → 打一行警告返回 False
    返回是否达到目标厚度; 调用方 (run_worker) 只打日志, 不管返回值都照常开刷."""
    if not enemy_ai_enabled:
        return False
    overlay.update(state="调整视角", message="拉近相机以便读稀有度...")
    scroll_amount = ZOOM_SCROLL_AMOUNT
    scroll_count = 0
    prev_median = None
    applied = 0        # 已经滚掉的净 deltaY (传给 cdp_bridge.scroll_wheel 的和), 放弃时 scroll_wheel(-applied) 还原
    flipped = False    # 方向只翻转一次; 翻转后还变糟就撤销走人
    start = time.time()
    try:
        pyautogui.moveTo(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)  # 居中鼠标, 别让角色在等待里瞎走
        while True:
            if time.time() - start >= ZOOM_WAIT_CAP:
                print("⚠️ 视角调整: 超时未完成, 照常开刷")
                if applied != 0:
                    cdp_bridge.scroll_wheel(-applied)   # 撤销已滚的, 别把 zoom 留在半路
                return False

            if afk_watch.poll_afk_pause():
                overlay.update(state="AFK弹窗处理中", message="等待florr-auto-afk解题")
                time.sleep(0.2)
                continue

            thicks = enemy_detect.scan_bar_thickness(model_path=ENEMY_MODEL_PATH)

            if len(thicks) < ZOOM_MIN_SAMPLES:
                time.sleep(2)
                continue

            median = statistics.median(thicks)
            if median >= ZOOM_MIN_THICK:
                print(f"✅ 视角OK (血条中位厚度 {median})")
                return True

            if prev_median is not None and median < prev_median - 0.5:
                if not flipped:
                    scroll_amount = -scroll_amount
                    flipped = True
                    print("↔️ 视角: 滚轮方向反了, 已翻转")
                else:
                    print("⚠️ 视角调整: 两个方向都没改善, 撤销并放弃")
                    if applied != 0:
                        cdp_bridge.scroll_wheel(-applied)
                    return False
            prev_median = median

            if scroll_count >= ZOOM_MAX_SCROLLS:
                print(f"⚠️ 视角调整: 滚了 {scroll_count} 次仍没到目标厚度 "
                      f"(可能已最大 zoom), 照常开刷")
                if applied != 0:
                    cdp_bridge.scroll_wheel(-applied)
                return False

            cdp_bridge.scroll_wheel(scroll_amount)   # 走 CDP 打进页面, 不看窗口焦点
            applied += scroll_amount
            scroll_count += 1
            time.sleep(0.4)
    except Exception as e:
        print(f"⚠️ 视角调整出错, 照常开刷: {e}")
        return False


def auto_farming(farming_area, duration=300, *, enemy_ai_enabled=True,
                 farming_path=None):
    """自动刷怪逻辑（依赖一直攻击按钮）—— 连续走动, 不停下站桩.

    区域模式(farming_path=None, 默认): 在 farming_area 内随机选可走点走动.
    固定路径模式(farming_path 给定时): 依次走路径点, 到终点后循环回起点
    (不走反向折返). 两种模式都不主动暂停, 靠外部"一直攻击"按钮持续输出.
    """
    x1, y1 = farming_area[0]
    x2, y2 = farming_area[1]

    min_x, max_x = min(x1, x2), max(x1, x2)
    min_y, max_y = min(y1, y2), max(y1, y2)
    farming_area = [(min_x, min_y), (max_x, max_y)]
    binary_map = load_binary_map()

    if farming_path:
        # 路径坐标是从 GUI/worker 来的, 可能含越界值, 钳进地图范围再用.
        farming_path = [(
            min(max(int(px), 0), 299),
            min(max(int(py), 0), 299),
        ) for (px, py) in farming_path]
        print(f"\n🎮 开始沿固定路径刷怪: {farming_path}")
        overlay.update(state="刷怪中", message=f"固定路径 {len(farming_path)} 点")
    else:
        print(f"\n🎮 开始在区域 {farming_area} 进行自动刷怪...")
        overlay.update(state="刷怪中", message=f"区域 {farming_area}")
    print(f"⏱️  刷怪时长: {duration}秒（持续走动模式）\n")

    start_time = time.time()
    move_count = 0
    exit_reason = "timeout"
    last_enemy_scan = 0.0
    enemy_decision = ("wander", None)
    detections = []
    chase_pos_history = []   # 近期minimap坐标, 供enemy_detect.chase_is_stalled()看净位移
    mythic_latch = False
    mythic_misses = 0
    mythic_target_pos = None   # 上一 tick 锁定 Mythic 的屏幕坐标, 给 pick_mythic_target 做连续性
    path_index = 0   # 固定路径模式: 下一个要走的路径点下标

    def _wander_enemy_watch(_pos):
        """move_to_position 的 on_tick 钩子: wander 腿途中做一次(节流的)索敌, 需要
        规避/接战/锁 Mythic 时返回 "enemy" 中断这条腿, 外层下个 tick 就按刚更新的
        enemy_decision 处理. 只更新扫描状态、不推进 mythic miss 计数(那个归外层
        mythic 分支的 scanned 门管)."""
        nonlocal enemy_decision, detections, last_enemy_scan
        enemy_decision, detections, last_enemy_scan, _scanned = _maybe_scan_enemies(
            enemy_ai_enabled, time.time(), last_enemy_scan, enemy_decision, detections)
        if enemy_decision[0] in ("flee", "chase"):
            return "enemy"
        if (MYTHIC_LATCH_ENABLED and enemy_ai_enabled
                and enemy_detect.pick_mythic_target(
                    detections, center=enemy_detect.SCREEN_CENTER, latched=mythic_latch,
                    engage_px=MYTHIC_ENGAGE_PX, release_px=MYTHIC_RELEASE_PX,
                    chase_min_conf=CHASE_MIN_CONF, prev_pos=mythic_target_pos) is not None):
            return "enemy"
        return None

    while time.time() - start_time < duration:
        if afk_watch.poll_afk_pause():
            overlay.update(state="AFK弹窗处理中", message="等待florr-auto-afk解题")
            time.sleep(0.2)
            # 暂停/丢位置/出区一圈回来后场景可能全变了 —— 别带着旧锁定用 600 释放
            # 半径, 让下一 tick 重新过 450 接战门槛.
            mythic_latch, mythic_misses, mythic_target_pos = False, 0, None
            continue

        # 被传送到 garden: 导航回原图, 回来后重新从刷怪区域走起.
        if _garden_escape_needed():
            recover_from_teleport()
            mythic_latch, mythic_misses, mythic_target_pos = False, 0, None
            continue

        # 死亡/开局画面检查放在循环最前面、不依赖"位置测不到" —— 死亡结算画面上
        # 曾经实测出过稳定的假位置(不是None), 只在current_pos is None分支里查
        # 会被这种假阳性绕过去, 角色明明已经死了脚本还在拿假坐标继续瞎刷.
        if on_death_screen() or on_start_screen():
            print("🔁 检测到落在死亡/开局画面上, 交回上层处理")
            overlay.update(state="出错", message="落在死亡/开局画面, 交回上层重开")
            exit_reason = "break"
            break

        current_pos = get_player_position()

        if current_pos is None:
            print("⚠️ 无法检测玩家位置")
            overlay.update(state="无法检测位置")
            time.sleep(1)
            mythic_latch, mythic_misses, mythic_target_pos = False, 0, None
            continue

        # 检查是否还在刷怪区域 —— 只在区域随机模式有效: 固定路径模式下没有
        # "区域"语义(路径点可能超出任何单个矩形), 走到路径点本身就是在刷怪,
        # 不能因为路径点落在一个假想的矩形外就判定"离开区域"去寻路回去.
        if farming_path is None and not if_in_area([farming_area], current_pos):
            print(f"⚠️ 离开刷怪区域 (当前: {current_pos})，重新寻路回去")
            overlay.update(state="离开刷怪区域", pos=current_pos, message="重新寻路回去")
            target_x = (farming_area[0][0] + farming_area[1][0]) // 2
            target_y = (farming_area[0][1] + farming_area[1][1]) // 2
            if not lazy_theta_pathing((target_x, target_y), [farming_area]):
                print("❌ 无法回到刷怪区域")
                overlay.update(state="出错", message="无法回到刷怪区域")
                exit_reason = "break"
                break
            mythic_latch, mythic_misses, mythic_target_pos = False, 0, None
            continue

        # 索敌: 按ENEMY_SCAN_INTERVAL节流跑YOLO(不是每tick都跑, 推理有开销).
        # 索敌是附加功能, 任何异常都退化成"漫游", 不能让它打断刷怪主循环.
        now = time.time()
        enemy_decision, detections, last_enemy_scan, scanned = _maybe_scan_enemies(
            enemy_ai_enabled, now, last_enemy_scan, enemy_decision, detections)
        enemy_action = enemy_decision[0]

        # 1) flee 最优先 —— 且立刻放掉 Mythic 锁定 (躲优先, 不为打 Mythic 送死).
        if enemy_action == "flee":
            mythic_latch, mythic_misses = False, 0
            mouse_target = enemy_detect.flee_mouse_target(enemy_decision[1])
            _drive_and_check_stall(mouse_target, current_pos, chase_pos_history,
                                   "规避中", "附近有危险稀有怪, 拉开距离")
            continue

        # 2) Mythic 近身锁定 —— flee 之外, 贴脸有合格 Mythic 就锁定按物种走位磨掉.
        if MYTHIC_LATCH_ENABLED and enemy_ai_enabled:
            mtarget = enemy_detect.pick_mythic_target(
                detections, center=enemy_detect.SCREEN_CENTER, latched=mythic_latch,
                engage_px=MYTHIC_ENGAGE_PX, release_px=MYTHIC_RELEASE_PX,
                chase_min_conf=CHASE_MIN_CONF, prev_pos=mythic_target_pos)
            # miss 计数只在真跑过扫描的 tick 推进 —— 节流 tick 拿的是同一份缓存
            # 检测, 再扣一次等于把同一帧证据数两遍, 3-miss 释放在快机器上缩成 ~2.
            if scanned:
                mythic_latch, mythic_misses = _update_mythic_latch(
                    mythic_latch, mythic_misses, mtarget is not None, MYTHIC_RELEASE_MISSES)
            if mythic_latch and mtarget is not None:
                mythic_target_pos = mtarget["screen_pos"]
                repel = enemy_decision[3] if enemy_action == "chase" else []
                mouse_target = enemy_detect.mythic_move_target(
                    mtarget, enemy_detect.SCREEN_CENTER,
                    strafe_radius=MYTHIC_STRAFE_RADIUS,
                    cactus_hold_px=MYTHIC_CACTUS_HOLD_PX,
                    repel_positions=repel, k_radial=MYTHIC_STRAFE_K_RADIAL)
                policy = enemy_detect.MYTHIC_KITE_SPECIES[mtarget["species"]]
                _drive_and_check_stall(mouse_target, current_pos, chase_pos_history,
                                       "清青怪", f"遛 {mtarget['species']}({policy})")
                continue
            # 没锁定 / 这 tick 没目标 —— 放掉连续性锚点, 别让下次锁定拿旧坐标.
            mythic_target_pos = None

        # 3) 普通追击 —— 不 fleeing 也没锁定 Mythic.
        if enemy_action == "chase":
            target, hold_px, repel = enemy_decision[1], enemy_decision[2], enemy_decision[3]
            mouse_target = enemy_detect.aim_mouse_target(
                target["screen_pos"], hold_px=hold_px, repel_positions=repel)
            _drive_and_check_stall(mouse_target, current_pos, chase_pos_history,
                                   "索敌中", f"追击 {target['species']}({target['rarity']})")
            continue

        # 4) enemy_action == "wander": 没有可打/需规避的目标, 移动.
        chase_pos_history.clear()
        if farming_path:
            # 固定路径: 走下一个路径点. 到终点(最后一个点)后循环回起点 ——
            # 下个 tick 从 index 0 重新走, 不是原路折返(用户选的"循环").
            target = farming_path[path_index]
            print(f"🚶 沿路径移动到 {target} (点 {path_index + 1}/{len(farming_path)})")
            overlay.update(state="刷怪中", pos=current_pos, target=target,
                           message=f"固定路径 (点 {path_index + 1}/{len(farming_path)})")
        else:
            random_x, random_y = random_walkable_point(farming_area, binary_map)
            target = (random_x, random_y)
            print(f"🚶 移动到 ({random_x}, {random_y})")
            overlay.update(state="刷怪中", pos=current_pos, target=target,
                           message=f"持续走动中 (第{move_count + 1}次)")

        # 移动到目标点 —— 到了立刻挑下一个点接着走, 不暂停.
        # max_attempts=20 (≈1s worst case at time.sleep(0.05)每tick) 而不是默认的
        # 200(≈10s) —— 让外层循环更频繁拿回控制权重新索敌扫描, 见下面ENEMY_SCAN_INTERVAL
        # 的注释.
        move_result = move_to_position(current_pos, target,
                                       max_attempts=20, on_tick=_wander_enemy_watch)

        if move_result == "enemy":
            # 路途中扫到怪(该 flee/chase/锁 Mythic) —— 立刻回外层, 下个 tick 用
            # _wander_enemy_watch 刚更新的 enemy_decision 处理, 不算走完一趟.
            continue
        if move_result == "stuck":
            print("⚠️ 移动受阻, 脱困一下...")
            overlay.update(state="卡住", message="脱困中")
            _run_escape(current_pos, target, binary_map)
        elif move_result in ["in_game_dead", "in_menu"]:
            print(f"⚠️ 游戏状态变化: {move_result}")
            exit_reason = "break"
            break
        else:
            # 只有真正走到点上才计入移动次数, "受阻"那次不算.
            move_count += 1
            # 固定路径: 走到当前点才算数, 推进到下一个; 到终点就循环回起点.
            if farming_path:
                path_index = (path_index + 1) % len(farming_path)

        # 检查游戏状态
        if on_death_screen():
            print("💀 玩家已死亡")
            overlay.update(state="已死亡")
            exit_reason = "break"
            break
        elif on_start_screen():
            print("📋 玩家在菜单中")
            overlay.update(state="菜单中")
            exit_reason = "break"
            break

    elapsed = time.time() - start_time
    print(f"\n" + "="*50)
    print(f"✅ 刷怪完成！")
    print(f"   实际耗时: {elapsed:.1f}秒")
    print(f"   移动次数: {move_count}")
    print(f"="*50)
    if exit_reason == "timeout":
        overlay.update(state="完成", message=f"刷怪结束, 共移动{move_count}次")
    else:
        overlay.update(message=f"刷怪结束, 共移动{move_count}次")

    # 是不是刷满了整个duration —— 给调用方(主循环)判断"这轮算不算刷够时长"用,
    # 不刷满(死亡/被踢/卡死放弃)的连续出现太多次, 说明这个服务器可能有问题
    # (比如刷怪区域被占、或者哪里持续卡关), 值得换个服务器而不是死磕.
    return exit_reason == "timeout"


def _apply_worker_config(cfg):
    """把 config.json 的值应用/摊平成 run_worker 主循环要用的局部值.
    apply_map() 必须在这里就调 —— utils 的 MAP 是模块级全局, load_binary_map()
    等一堆函数都读它."""
    apply_map(cfg["map"])
    farming_path = cfg.get("farming_path")
    if farming_path:
        # GUI 落盘前已 clamp, 这里再兜底一次(手改 config 的越界点会坏寻路).
        farming_path = [
            (min(max(int(px), 0), 299), min(max(int(py), 0), 299))
            for (px, py) in farming_path
        ]
    location = tuple(cfg["location"])
    if farming_path:
        # 路径起点即目标点: 就算 config 里 location 是旧的, 也以路径起点为准.
        location = farming_path[0]
    return {
        "location": location,
        "farming_area": [tuple(p) for p in cfg["farming_area"]],
        "farming_path": farming_path,
        "farming_duration": cfg["farming_duration"],
        "short_round_limit": cfg["consecutive_short_round_limit"],
        "enemy_ai_enabled": cfg["enemy_ai_enabled"],
        "auto_switch_server": cfg["auto_switch_server"],
    }


def _auto_switch_step(round_completed_full, consecutive_short_rounds, auto_switch, limit):
    """run_worker 每轮结尾的自动换服决策(纯函数). 返回 (action, count).

    count 是"本轮没刷满时递增后的值"(刷满时为 0); action:
      "none"    —— 刷满 / 没到 limit / 开关没开: 不换
      "switch"  —— 触发换服, 调用方负责成功后把 count 清零
      "blocked" —— 该图禁用换服(ant_hell 实机换服会卡退, 见 utils.can_switch_server):
                   调用方打印跳过提示并清零 count, 否则下轮 count 又到 limit,
                   每条短局都刷同一条警告."""
    if round_completed_full:
        return "none", 0
    count = consecutive_short_rounds + 1
    if auto_switch and count >= limit:
        if not can_switch_server():
            return "blocked", count
        return "switch", count
    return "none", count


def run_worker(cfg):
    """刷怪 worker: 由 GUI 以 `main.py --worker` 子进程拉起. 掉线/死亡后自动点
    开始重来, 不主动停(沿用改造前 __main__ 的行为)."""
    # 直接 python main.py --worker 调试时给个清楚的报错 —— 交互式 Chrome 引导
    # 已经搬进 GUI, 这条路不再自己拉 Chrome.
    if not cdp_bridge.is_dedicated_chrome_ready():
        print("❌ 专用 Chrome 未就绪. 请从 GUI 启动(GUI 会引导你准备 Chrome).")
        sys.exit(1)

    # florr-auto-afk 的生命周期整个归 GUI 管(gui_app._ensure_afk / _on_afk_toggle,
    # 两条路都先看 AFK 开关). worker 这边只 poll_afk_pause() 读它的日志, 绝不自己
    # 去拉起它 —— 以前无条件 ensure_florr_auto_afk_running() 有两个真实后果:
    # (1) exe 不在时它会走 input() 问要不要下载, 而 console=False 的打包 exe 里
    #     worker 的 stdin 是死的, 直接 RuntimeError 把 worker 撂倒;
    # (2) 用户刚在界面上关掉 AFK 开关(GUI 已 stop_florr_auto_afk), worker 一起来
    #     又给它拉回去.
    global overlay
    overlay = create_overlay()

    w = _apply_worker_config(cfg)
    location = w["location"]
    farming_area = w["farming_area"]
    farming_path = w["farming_path"]
    farming_duration = w["farming_duration"]
    CONSECUTIVE_SHORT_ROUND_LIMIT = w["short_round_limit"]

    # 索敌 AI 只有 desert 一张图有 YOLO 模型, 而且那个 .pt 不随仓库发布(第三方
    # pickle 权重, 见 README), 得用户自己放进 models/. 开着但用不了的话
    # _maybe_scan_enemies 每 0.12 秒抛一次异常刷屏 —— 这里一次性查清楚, 用不了
    # 就本次按关闭处理, 只提示一行.
    if w["enemy_ai_enabled"]:
        if cfg["map"] != "desert":
            print(f"⚠️ 索敌 AI 目前只有 desert 图有模型, 当前是 {cfg['map']} 图 —— 本次按关闭处理")
            w["enemy_ai_enabled"] = False
        elif not os.path.isfile(ENEMY_MODEL_PATH):
            print(f"⚠️ 索敌 AI 已开, 但模型文件不在: {ENEMY_MODEL_PATH} —— 本次按关闭处理"
                  " (需自己把 desert.pt 放进 models/, 见 README)")
            w["enemy_ai_enabled"] = False

    print("🎮 开始自动寻路+刷怪 (掉线/死亡后自动点开始重来, 不主动停)\n")
    consecutive_short_rounds = 0
    round_count = 0
    while True:
        round_count += 1
        round_start_time = time.time()
        print(f"\n{'='*50}\n第 {round_count} 轮\n{'='*50}")

        # 重生后可能直接落在传送门附近被传到 garden —— 先处理传送再判死亡/菜单.
        if _garden_escape_needed():
            recover_from_teleport()

        if on_death_screen():
            print("💀 检测到死亡结算画面, 点击继续...")
            overlay.update(state="重新开始", message="死亡, 点击继续...")
            click_continue_after_death()
            time.sleep(2)
        if on_start_screen():
            print("🔁 检测到开局菜单, 点击开始按钮进入游戏...")
            overlay.update(state="重新开始", message="点击开始按钮...")
            click_start_game()
            time.sleep(3)

        print(f"📍 目标区域: {farming_area}\n")
        overlay.update(state="启动", target=location,
                       message=f"第{round_count}轮: 开始自动寻路到刷怪区域")

        if lazy_theta_pathing(location, [] if farming_path else [farming_area]):
            print("✅ 到达刷怪起点！")
            zoom_ok = ensure_zoom_for_rarity(w["enemy_ai_enabled"])
            if w["enemy_ai_enabled"] and not zoom_ok:
                print("⚠️ 视角未调到位, 本轮稀有度识别可能不准 (Mythic 锁定可能不触发)")
            auto_farming(farming_area, farming_duration,
                         enemy_ai_enabled=w["enemy_ai_enabled"],
                         farming_path=farming_path)
        else:
            print("❌ 本轮未能到达目标区域")
            overlay.update(message="本轮未能到达目标区域")
            time.sleep(1)

        round_elapsed = time.time() - round_start_time
        completed_full_duration = round_elapsed >= farming_duration
        action, consecutive_short_rounds = _auto_switch_step(
            completed_full_duration, consecutive_short_rounds,
            w["auto_switch_server"], CONSECUTIVE_SHORT_ROUND_LIMIT)
        if not completed_full_duration:
            print(f"⚠️ 这条命只撑了{round_elapsed:.0f}秒, 没到{farming_duration}秒 "
                  f"(连续{consecutive_short_rounds}次)")
        if action == "blocked":
            print("⚠️ 当前地图不支持自动换服务器, 已跳过 (该图换服会卡退)")
            overlay.update(message="该图不支持自动换服务器, 已跳过")
            consecutive_short_rounds = 0
        elif action == "switch":
            print(f"🌐 连续{consecutive_short_rounds}轮没刷满, 换个服务器...")
            overlay.update(state="换服务器",
                           message=f"连续{consecutive_short_rounds}轮没刷满, 切换中")
            try:
                switch_server()
                consecutive_short_rounds = 0
                time.sleep(2)
            except Exception as e:
                print(f"⚠️ 换服务器失败, 先用当前服务器继续刷 (下轮再重试): {e}")
                overlay.update(message=f"换服务器失败(下轮重试): {e}")


def _worker_graceful_exit(signum, frame):
    """GUI 点"停止"时给 worker 发的信号处理: 先把按住的方向键/空格松开, 再退出 ——
    直接 kill 的话这些键会一直是按下状态."""
    try:
        reset_keyboard()
    finally:
        sys.exit(0)


def _install_worker_signal_handlers():
    # POSIX 上 GUI 的"停止"会补一发 SIGTERM; Windows 上 SIGBREAK 只有从真控制台
    # (开发时 `python main.py --worker`)按 Ctrl+Break 才会来 —— 打包成
    # console=False 的 exe 之后两边都不保险, 真正的停止信号走 stdin EOF, 见
    # _install_worker_stdin_watcher().
    signal.signal(signal.SIGTERM, _worker_graceful_exit)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _worker_graceful_exit)


def _worker_stdin_watch():
    """阻塞读 stdin 直到对端关掉管道(EOF), 然后松开按住的键再退出."""
    try:
        sys.stdin.read()      # 阻塞到对端关闭管道 / 手动 Ctrl-D
    except Exception:
        pass
    try:
        reset_keyboard()
    finally:
        os._exit(0)


def _install_worker_stdin_watcher():
    """GUI 关闭 worker 的 stdin 管道 = 请求停止. 起一个守护线程阻塞读 stdin, 读到
    EOF(管道被关)就先松开按住的键再退出 —— 打包成 console=False 的 exe 后
    CTRL_BREAK / SIGTERM 都不一定送得到, stdin EOF 是唯一跨平台可靠的信号."""
    if sys.stdin is None:
        return
    t = threading.Thread(target=_worker_stdin_watch, daemon=True)
    t.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="florr-auto-pathing")
    parser.add_argument("--worker", action="store_true",
                        help="内部用: 跑刷怪循环子进程(由 GUI 拉起, 不要手动加)")
    args = parser.parse_args()

    if args.worker:
        _install_worker_signal_handlers()
        _install_worker_stdin_watcher()
        run_worker(app_config.load_config())
    else:
        from gui_app import main as gui_main  # 惰性 import: 不让 `import main` 拖进 GUI 依赖
        gui_main()
