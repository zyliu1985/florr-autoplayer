"""诊断脚本: 把敌人检测层每一步的真实输出摊开, 不靠肉眼看图猜"识别准不准".

查三件事:
  1. desert.pt 的检测框位置/类别/置信度 —— 框到底准不准.
  2. sandstorm.pt 单独跑, 跟 desert.pt 的 class 3 'sandstorm' 并排比 (IoU / 中心距),
     看两个模型是不是在指同一个东西、哪个更靠谱.
  3. sample_rarity() 内部: 每个检测框上方那块采样窗的位置, 以及按 RARITY_COLORS
     每一档数出来的命中像素数 —— 直接看出色表偏了多少、是不是全落回默认 Common.

最后打印 select_action() 在这一帧的决策 (flee / chase / wander), 也就是 bot 本帧
真正会做的动作.

用法:
  python debug_enemy_detect.py                      # 5秒后截当前屏幕
  python debug_enemy_detect.py --image frame.png    # 跑离线帧 (可多张)
  python debug_enemy_detect.py --image a.png b.png --conf 0.25 --tolerance 40
  python debug_enemy_detect.py --watch              # 循环: 每1秒扫一次, 每只怪一行
                                                    #   species conf 血条 稀有度 + 各档命中数
                                                    #   + mythic 候选数 / pick_mythic_target
                                                    # 边玩边看, 站到 Mythic 怪旁边, 复制几行发回
  python debug_enemy_detect.py --watch 0.5          # 间隔 0.5 秒

产物 (每帧一组, <stem> = live 或图片文件名):
  <stem>_desert.png      desert.pt 框 (绿) + 采样窗 (黄) + 判定稀有度
  <stem>_sandstorm.png   sandstorm.pt 框 (品红)
  <stem>_compare.png     两个模型的框叠一起
"""
import argparse
import os
import time

import cv2
import numpy as np
from ultralytics import YOLO

import enemy_detect as ed
from utils import SCREEN_WIDTH, SCREEN_HEIGHT

DESERT_PATH = "models/desert.pt"
SANDSTORM_PATH = "models/sandstorm.pt"


def grab_live(delay):
    import pyautogui
    print(f"检测到分辨率: {SCREEN_WIDTH}x{SCREEN_HEIGHT}")
    print(f"⏳ {delay}秒后截屏, 切到游戏窗口, 保持全屏、画面里有怪...\n")
    for i in range(delay, 0, -1):
        print(f"   {i}...")
        time.sleep(1)
    shot = pyautogui.screenshot(region=[0, 0, SCREEN_WIDTH, SCREEN_HEIGHT])
    return cv2.cvtColor(np.array(shot), cv2.COLOR_RGB2BGR)


def run_model(path, image, conf):
    """直接 new 一个 YOLO —— 不走 ed.load_enemy_model(), 那个是单例, 只留得住
    第一个加载的模型, 两个模型都要跑会拿到同一个."""
    model = YOLO(path)
    res = model.predict(image, conf=conf, verbose=False)
    out = []
    if not res:
        return out
    r = res[0]
    for box in r.boxes:
        x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
        out.append({
            "species": r.names[int(box.cls[0])],
            "confidence": float(box.conf[0]),
            "bbox": (x1, y1, x2, y2),
            "screen_pos": ((x1 + x2) / 2, (y1 + y2) / 2),
        })
    return out


def rarity_breakdown(image, bbox, tolerance):
    """复刻 ed.sample_rarity() 的定位 + 计数, 额外吐出每一档的命中像素数和血条
    锚点位置. 逻辑跟 enemy_detect 里保持一致: 先 _find_hp_bar() 找绿血条, 再在
    血条右下方那块数颜色, 只扫 Common..Ultra."""
    H, W = image.shape[:2]
    bar = ed._find_hp_bar(image, bbox)
    if bar is None:
        return None, None, [], 0.0, "Common (没找到绿血条锚点)"

    bar_x0, bar_y, bar_x1, bar_thick = bar
    bar_len = bar_x1 - bar_x0
    word_h = max(12, 3 * bar_thick)
    word_w = max(40, int(0.55 * bar_len))
    ry0 = min(H, bar_y + bar_thick // 2 + 1)
    ry1 = min(H, ry0 + word_h)
    rx0 = max(0, bar_x1 - word_w)
    rx1 = min(W, bar_x1 + max(4, bar_thick))
    win = (rx0, ry0, rx1, ry1)
    region = image[ry0:ry1, rx0:rx1]
    if region.size == 0:
        return bar, win, [], 0.0, "Common (采样窗越界, 空)"

    total = region.shape[0] * region.shape[1]
    scan = ed.RARITY_ORDER[:ed.RARITY_RANK["Ultra"] + 1]
    counts = []
    for name in scan:
        b, g, r = ed._hex_to_bgr(ed.RARITY_COLORS[name])
        lower = np.array([max(0, b - tolerance), max(0, g - tolerance), max(0, r - tolerance)])
        upper = np.array([min(255, b + tolerance), min(255, g + tolerance), min(255, r + tolerance)])
        c = int(np.count_nonzero(cv2.inRange(region, lower, upper)))
        counts.append((name, c))
    counts.sort(key=lambda t: t[1], reverse=True)
    top_name, top_c = counts[0]
    ratio = top_c / total if total else 0.0
    verdict = top_name if ratio >= ed.MIN_RARITY_PIXEL_RATIO else "Common (最高档占比 %.3f < %.2f 阈值)" % (
        ratio, ed.MIN_RARITY_PIXEL_RATIO)
    return bar, win, counts, ratio, verdict


def iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / ua if ua > 0 else 0.0


def draw_box(img, bbox, color, label):
    x1, y1, x2, y2 = [int(v) for v in bbox]
    cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
    cv2.putText(img, label, (x1, max(12, y1 - 4)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)


def process(image, stem, conf, tolerance):
    print("=" * 72)
    print(f"帧: {stem}   尺寸: {image.shape[1]}x{image.shape[0]}")

    cv2.imwrite(f"{stem}_raw.png", image)  # 未标注原始帧, 用来采样真实名牌颜色

    desert = run_model(DESERT_PATH, image, conf)
    storm = run_model(SANDSTORM_PATH, image, conf)

    # --- desert.pt ---
    print(f"\n[desert.pt]  conf>={conf}  检测数: {len(desert)}")
    di = image.copy()
    for i, d in enumerate(desert):
        bar, win, counts, ratio, verdict = rarity_breakdown(image, d["bbox"], tolerance)
        real = ed.sample_rarity(image, d["bbox"], tolerance=tolerance)
        bucket = ed.classify_action(d["species"], real)
        sx, sy = d["screen_pos"]
        print(f"  #{i} {d['species']:<16} conf={d['confidence']:.3f} "
              f"screen_pos=({sx:.0f},{sy:.0f}) bbox=({','.join(f'{v:.0f}' for v in d['bbox'])})")
        if bar is None:
            print(f"      绿血条锚点: 没找到 -> 默认 Common")
        else:
            print(f"      绿血条锚点: y={bar[1]} x=[{bar[0]}..{bar[2]}] 厚={bar[3]}   "
                  f"采样窗 x[{win[0]}:{win[2]}] y[{win[1]}:{win[3]}]")
            print(f"      命中像素/档: " + ", ".join(f"{n}={c}" for n, c in counts))
        print(f"      -> rarity_breakdown 判定: {verdict}")
        print(f"      -> ed.sample_rarity(): {real}   classify_action(): {bucket}")
        if real != verdict.split()[0]:
            print(f"      ⚠️ 复刻结果与 ed.sample_rarity() 不一致, 检查脚本是否跟实现漂移")
        draw_box(di, d["bbox"], (0, 200, 0), f"#{i} {d['species']} {d['confidence']:.2f} {real}")
        if bar is not None:
            cv2.line(di, (bar[0], bar[1]), (bar[2], bar[1]), (255, 0, 0), 1)      # 蓝 = 绿血条锚点
            cv2.rectangle(di, (win[0], win[1]), (win[2], win[3]), (0, 0, 255), 1)  # 红 = 稀有度采样窗
    cv2.imwrite(f"{stem}_desert.png", di)

    # --- sandstorm.pt ---
    print(f"\n[sandstorm.pt]  conf>={conf}  检测数: {len(storm)}")
    si = image.copy()
    for i, s in enumerate(storm):
        sx, sy = s["screen_pos"]
        print(f"  #{i} {s['species']:<16} conf={s['confidence']:.3f} "
              f"screen_pos=({sx:.0f},{sy:.0f}) bbox=({','.join(f'{v:.0f}' for v in s['bbox'])})")
        draw_box(si, s["bbox"], (220, 0, 220), f"#{i} {s['species']} {s['confidence']:.2f}")
    cv2.imwrite(f"{stem}_sandstorm.png", si)

    # --- 两模型对 sandstorm 的一致性 ---
    d_storm = [d for d in desert if d["species"] == "sandstorm"]
    if d_storm or storm:
        print(f"\n[sandstorm 一致性]  desert.pt class3: {len(d_storm)}  vs  sandstorm.pt: {len(storm)}")
        for i, d in enumerate(d_storm):
            if not storm:
                print(f"  desert#{i} 无 sandstorm.pt 对应框")
                continue
            j = max(range(len(storm)), key=lambda k: iou(d["bbox"], storm[k]["bbox"]))
            best = iou(d["bbox"], storm[j]["bbox"])
            dc = np.hypot(d["screen_pos"][0] - storm[j]["screen_pos"][0],
                          d["screen_pos"][1] - storm[j]["screen_pos"][1])
            print(f"  desert#{i} <-> sandstorm#{j}  IoU={best:.2f}  中心距={dc:.0f}px")

    ci = image.copy()
    for d in desert:
        draw_box(ci, d["bbox"], (0, 200, 0), d["species"])
    for s in storm:
        draw_box(ci, s["bbox"], (220, 0, 220), "storm")
    cv2.imwrite(f"{stem}_compare.png", ci)

    # --- 本帧 bot 实际决策 ---
    detections = [{
        "species": d["species"],
        "rarity": ed.sample_rarity(image, d["bbox"], tolerance=tolerance),
        "screen_pos": d["screen_pos"],
        "bbox": d["bbox"],
        "confidence": d["confidence"],
    } for d in desert]
    action = ed.select_action(detections)
    print(f"\n[select_action 本帧决策] {action[0]}")
    if action[0] == "chase":
        tgt, hold = action[1], action[2]
        print(f"  追击 {tgt['species']}({tgt['rarity']})  hold_px={hold}  "
              f"({'贴脸' if hold is None else '保持距离'})")
    elif action[0] == "flee":
        print(f"  规避, 触发半径内 AVOID 怪: {len(action[1])} 个")
    print(f"  产物: {stem}_raw.png  {stem}_desert.png  {stem}_sandstorm.png  {stem}_compare.png")


def watch_loop(interval, conf, tolerance):
    """循环: 每 interval 秒截屏 + desert.pt + 逐怪 rarity_breakdown, 每只怪一行.
    专门查"为什么 Mythic 怪没被读成 Mythic": 看血条找没找到、各稀有度档命中多少
    像素(尤其 Mythic 的青色 1FDBDE)、以及 pick_mythic_target 返回啥. Ctrl+C 退出."""
    import pyautogui
    model = YOLO(DESERT_PATH)
    cx, cy = ed.SCREEN_CENTER
    interesting = {"beetle", "scorpion", "cactus"}   # 会走位磨的那几种 (排除 sandstorm/蜈蚣)
    saved = 0
    print(f"分辨率 {SCREEN_WIDTH}x{SCREEN_HEIGHT}  间隔 {interval}s  conf>={conf}  Ctrl+C 退出")
    print(f"(看到 beetle/scorpion/cactus 就存一张 debug_watch_NN.png, 最多 12 张)\n")
    t0 = time.time()
    while True:
        shot = pyautogui.screenshot(region=[0, 0, SCREEN_WIDTH, SCREEN_HEIGHT])
        img = cv2.cvtColor(np.array(shot), cv2.COLOR_RGB2BGR)
        res = model.predict(img, conf=conf, verbose=False)
        boxes = res[0].boxes if res else []
        names = res[0].names if res else {}
        dets = []
        vis = img.copy()
        hit_interesting = False
        print(f"─ scan @ {time.time() - t0:5.1f}s  dets={len(boxes)}")
        for box in boxes:
            sp = names[int(box.cls[0])]
            cf = float(box.conf[0])
            x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
            bbox = (x1, y1, x2, y2)
            spos = ((x1 + x2) / 2, (y1 + y2) / 2)
            dcen = ((spos[0] - cx) ** 2 + (spos[1] - cy) ** 2) ** 0.5
            bar, win, counts, ratio, verdict = rarity_breakdown(img, bbox, tolerance)
            rar = ed.sample_rarity(img, bbox, tolerance=tolerance)
            dets.append({"species": sp, "rarity": rar, "screen_pos": spos,
                         "bbox": bbox, "confidence": cf})
            if bar is None:
                barinfo = "血条=无"
                cnts = ""
            else:
                barinfo = f"血条y={bar[1]} 厚={bar[3]}"
                cnts = "  " + " ".join(f"{n}={c}" for n, c in counts[:4])
            print(f"    {sp:16} conf={cf:.2f}  中心距={dcen:4.0f}px  {barinfo}  -> {rar}{cnts}")
            col = (0, 200, 0) if sp in interesting else (160, 160, 160)
            cv2.rectangle(vis, (int(x1), int(y1)), (int(x2), int(y2)), col, 2)
            cv2.putText(vis, f"{sp} {cf:.2f} {rar}", (int(x1), max(12, int(y1) - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1, cv2.LINE_AA)
            if bar is not None:
                cv2.line(vis, (bar[0], bar[1]), (bar[2], bar[1]), (255, 0, 0), 1)
                cv2.rectangle(vis, (win[0], win[1]), (win[2], win[3]), (0, 0, 255), 1)
            if sp in interesting:
                hit_interesting = True
        if hit_interesting and saved < 12:
            fn = f"debug_watch_{saved:02d}.png"
            cv2.imwrite(fn, vis)
            print(f"    [存图 {fn}]")
            saved += 1
        mc = ed.mythic_candidates(dets)
        pick = ed.pick_mythic_target(dets, ed.SCREEN_CENTER)
        pick_s = None if pick is None else f"{pick['species']}@{tuple(round(v) for v in pick['screen_pos'])}"
        act = ed.select_action(dets)
        print(f"    => mythic候选={len(mc)}  pick_mythic_target={pick_s}  select_action={act[0]}"
              + (f"({act[1]['species']}/{act[1]['rarity']})" if act[0] == 'chase' else ""))
        time.sleep(interval)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", nargs="+", help="离线帧, 不给则截当前屏幕")
    ap.add_argument("--conf", type=float, default=0.4, help="YOLO 置信度阈值 (scan_enemies 默认 0.4)")
    ap.add_argument("--tolerance", type=int, default=40, help="sample_rarity 颜色容差 (默认 40)")
    ap.add_argument("--delay", type=int, default=5, help="实时截屏倒计时秒数")
    ap.add_argument("--watch", nargs="?", type=float, const=1.0, default=None,
                    metavar="SEC", help="循环监视模式, 每 SEC 秒一次 (默认 1.0)")
    args = ap.parse_args()

    for p in (DESERT_PATH, SANDSTORM_PATH):
        if not os.path.exists(p):
            print(f"❌ 缺模型文件: {p}")
            return

    if args.watch is not None:
        try:
            watch_loop(args.watch, args.conf, args.tolerance)
        except KeyboardInterrupt:
            print("\n退出")
        return

    if args.image:
        for path in args.image:
            img = cv2.imread(path)
            if img is None:
                print(f"❌ 读不了: {path}")
                continue
            process(img, os.path.splitext(os.path.basename(path))[0] + "_diag", args.conf, args.tolerance)
    else:
        process(grab_live(args.delay), "debug_enemy_live", args.conf, args.tolerance)


if __name__ == "__main__":
    main()
