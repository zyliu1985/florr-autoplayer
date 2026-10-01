"""诊断脚本: 精确复现get_player_location_on_map()内部逻辑, 不靠肉眼看图/看颜色排行榜猜.

跟debug.py的区别: debug.py列"出现次数最多的颜色" —— 玩家标记只是个小圆点, 在
300x300=90000像素里占比太小, 根本进不了前15名, 所以那份颜色表看不出标记在不在.
这份直接用跟get_player_location_on_map()一模一样的±20容差去数f8de60范围内的像素
有多少个、在哪, 不靠猜.

用法: python debug_position_diag.py
"""
import time
import cv2
import numpy as np
from utils import get_map, load_binary_map, MAP, minimap_capture_region, SCREEN_WIDTH, SCREEN_HEIGHT


def main():
    print(f"检测到的分辨率: {SCREEN_WIDTH}x{SCREEN_HEIGHT}")
    print(f"当前MAP变量: {MAP!r} (空字符串说明还没调用过apply_map(), load_binary_map会失败, 不影响这次诊断)")
    print(f"get_map()理论截图区域(minimap_capture_region()): {minimap_capture_region()}")
    print("\n⏳ 5秒后截屏, 这段时间切到游戏窗口, 保持全屏、角色在场景里能看见小地图...\n")
    for i in range(5, 0, -1):
        print(f"   {i}...")
        time.sleep(1)

    image = get_map()  # 已经resize回300x300了
    h, w = image.shape[:2]
    print(f"\n✅ get_map()返回图像尺寸: {w}x{h} (应该恒为300x300, 不管什么分辨率)")
    cv2.imwrite("./debug_position_diag_raw.png", image)
    print("✅ 已保存 debug_position_diag_raw.png (get_map()截到的原始画面, 可以自己打开看是不是小地图)")

    target_color = "f8de60"
    target_bgr = tuple(int(target_color[i:i + 2], 16) for i in (4, 2, 0))
    lower = np.array([max(0, c - 20) for c in target_bgr])
    upper = np.array([min(255, c + 20) for c in target_bgr])
    mask = cv2.inRange(image, lower, upper)
    match_count = int(np.count_nonzero(mask))
    print(f"\n🎯 跟玩家标记颜色f8de60(±20容差)匹配的像素数: {match_count} / {w*h}")

    if match_count == 0:
        print("❌ 一个匹配的像素都没有 —— 说明get_map()截到的画面里根本没有这个黄色标记.")
        print("   可能原因: 1) 截图区域压根没对准小地图  2) 对准了小地图但分辨率太小/太靠边缘被裁掉了")
        print("   看一眼 debug_position_diag_raw.png 能立刻分清是哪种.")
    else:
        contours, _ = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        print(f"   匹配像素聚成了 {len(contours)} 个连通块, 逐个看半径(get_player_location_on_map要求radius>2才算数):")
        found_valid = False
        marked = image.copy()
        for c in contours:
            (x, y), radius = cv2.minEnclosingCircle(c)
            flag = "✅ 达标(radius>2)" if radius > 2 else "❌ 太小(radius<=2, 会被判定为噪声忽略)"
            if radius > 2:
                found_valid = True
            print(f"   - 位置({x:.1f}, {y:.1f}), 半径{radius:.2f}px  {flag}")
            cv2.circle(marked, (int(x), int(y)), max(3, int(radius) + 2), (0, 0, 255), 1)
        cv2.imwrite("./debug_position_diag_marked.png", marked)
        print("✅ 已保存 debug_position_diag_marked.png (红圈标出了每个候选点)")
        if found_valid:
            print("\n✅ 至少有一个候选点半径>2, 理论上get_player_position()应该能返回结果 —— 如果实机还是卡住, 问题可能在calibrate_player或更上游, 需要再往下查.")
        else:
            print("\n❌ 匹配到像素了, 但每个连通块半径都<=2 —— 标记点太小太碎, 被当成噪声过滤掉了. 这种情况通常是分辨率太小导致标记只剩1-2个像素, 是真实的检测极限问题.")


if __name__ == "__main__":
    main()
