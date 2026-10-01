"""小工具: 自动学习任意地图小地图上没有的捷径.

地图里有些捷径(隐藏通道)不画在小地图上. 玩家手动走进这些捷径时, 角色在小地图
模板上落在"墙"里. 本工具轮询玩家位置, 发现玩家在墙里(且离最近可走区够远)就沿玩家
走过的路径精确地把那段墙刷成可走并保存到 maps/<地图>.png —— 之后寻路就能用上.

精度: 记录连续的"在墙里"采样点, 用 Bresenham 直线把相邻点连起来刷, 半径默认 1,
还原捷径真实走向; 而不是在每个采样点糊一个大圆盘(那会把周边不是通道的墙也刷掉).
用法(游戏开着, 地图全屏, 在项目目录下):
    python learn_shortcuts.py            # 默认 anthell(向后兼容)
    python learn_shortcuts.py desert     # 或任何 maps/ 下的地图名
然后手动沿着捷径走一遍, 工具会把走过的墙段精确刷成可走. Ctrl+C 退出.
"""
import os
import sys
import time

import cv2
import numpy as np

import utils

CARVE_RADIUS = 1           # 沿路径每个点刷成可走的半径(px). 通道窄就保持 1, 宽就调大
MIN_WALL_DISTANCE = 2      # 玩家离最近可走区超过这个距离才算"在墙里"(防边界抖动误判)
POLL_INTERVAL = 1.0


def _raw_player_position():
    """玩家标记在 minimap 上的原始中心(不吸附到可走区).

    判断"是否在墙里"必须用原始位置: get_player_position() 会 calibrate 到最近
    可走格, 永远返回不了墙里的坐标."""
    image = utils.get_map()
    binary_map = utils.load_binary_map()
    for color in utils.PLAYER_MARKER_COLORS.get(utils.MAP, ["f8de60"]):
        pos = utils.get_player_location_on_map(image, color, binary_map, precise=True)
        if pos is not None:
            return round(pos[0]), round(pos[1])
    return None


def _dist_to_walkable(binary, cx, cy):
    """该像素到最近可走像素的 8 邻域步数(cv2 距离变换). 出界返回大值.

    cv2 距离变换量的是"前景(255)到最近背景(0)的距离" —— 所以把可走当背景(0)、
    墙当前景(255), 墙像素拿到的就是它到最近可走区的距离."""
    fg = np.where(binary == 255, 0, 255).astype(np.uint8)
    dist = cv2.distanceTransform(fg, cv2.DIST_C, 3)
    h, w = fg.shape
    if not (0 <= cx < w and 0 <= cy < h):
        return 999
    return int(dist[cy, cx])


def _carve_disk(binary, cx, cy, radius):
    """把 (cx, cy) 周围半径内的墙(0)刷成可走(255)."""
    h, w = binary.shape
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx * dx + dy * dy <= radius * radius:
                x, y = cx + dx, cy + dy
                if 0 <= x < w and 0 <= y < h:
                    binary[y, x] = 255


def _bresenham(x0, y0, x1, y1):
    """两个采样点之间的直线像素(Bresenham), 把离散采样点连成完整路径."""
    pts = []
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        pts.append((x0, y0))
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy
    return pts


def _carve_line(binary, p1, p2, radius):
    """把 p1→p2 连线上每个像素(含半径)的墙刷成可走 —— 精确还原捷径走向,
    而不是在每个采样点糊一个大圆盘."""
    for x, y in _bresenham(p1[0], p1[1], p2[0], p2[1]):
        _carve_disk(binary, x, y, radius)


def main():
    # 支持 python learn_shortcuts.py <地图名>, 默认 anthell(向后兼容).
    map_name = sys.argv[1] if len(sys.argv) > 1 else "anthell"
    map_path = f"./maps/{map_name}.png"
    assert map_name in [path.removesuffix(".png") for path in os.listdir("./maps")], \
        f"{map_name} 不是 maps/ 下的一张地图"

    utils.MAP = map_name
    binary = utils.load_binary_map()
    if binary is None:
        print(f"❌ 读不到 {map_path}, 确认在项目目录下运行")
        return
    print(f"已加载 {map_name} 地图(可走 {100 * (binary == 255).mean():.0f}%). 轮询玩家位置...")
    print("手动沿着捷径走一遍, 工具会把走过的墙段精确刷成可走并保存. Ctrl+C 退出.")
    patched = 0
    last_wall_pos = None   # 上一个"在墙里"的采样点, 用于把路径连起来
    while True:
        try:
            raw = _raw_player_position()
            if raw is None:
                last_wall_pos = None
                time.sleep(POLL_INTERVAL)
                continue
            x, y = raw
            if binary[y, x] == 255:
                last_wall_pos = None   # 回到可走区, 一段捷径结束
                time.sleep(POLL_INTERVAL)
                continue
            d = _dist_to_walkable(binary, x, y)
            if d <= MIN_WALL_DISTANCE:
                last_wall_pos = None
                time.sleep(POLL_INTERVAL)
                continue
            # 玩家在墙里(走捷径) → 和上一个采样点连起来精确刷
            if last_wall_pos is not None:
                _carve_line(binary, last_wall_pos, raw, CARVE_RADIUS)
            else:
                _carve_disk(binary, x, y, CARVE_RADIUS)
            last_wall_pos = raw
            patched += 1
            cv2.imwrite(map_path, binary)
            print(f"🔧 刷捷径: ({x},{y}) 离最近可走{d}px —— 已保存 "
                  f"(可走 {100 * (binary == 255).mean():.0f}%, 采样 {patched} 次)")
        except KeyboardInterrupt:
            print(f"\n退出. 已保存的地图改动保留在 {map_path}.")
            return
        except Exception as e:
            print(f"⚠️ {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
