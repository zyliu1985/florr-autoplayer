"""合成 florr.io 检测训练数据 (copy-paste augmentation).

读 data/anthell/scenes/ (空场景背景) + data/anthell/sprites/<物种>/ (单体精灵贴图),
把每张精灵 flood-fill 抠出前景, 随机 缩放 / 水平·垂直翻转 / 90°旋转 后贴到背景上,
box 直接从贴图位置算 —— 零手工标注. 输出 YOLO 格式数据集 + data.yaml, 可直接喂
ultralytics 训练.

抠图是"从四边 flood-fill 掉背景", 对"怪在中央、背景连到边缘"的干净单体有效.
抠不干净(怪和地面颜色太近被抠缺 / 边缘背景没抠掉)就调 CONFIG["fg_thresh"].

用法:
    venv\\Scripts\\python synthesize_data.py

产物:
    data/anthell/ready/{images,labels}/{train,val}/   (# 合成图 + 标注)
    data/anthell/ready/data.yaml                      (# 训练入口)
    data/anthell/preview/<class>.png                  (# 每类第一张的抠图效果, 先检查)
"""
import os
import glob
import random
import sys

import cv2
import numpy as np

# Windows GBK 控制台打不出 emoji/中文会抛 UnicodeEncodeError, 统一转 UTF-8 输出.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CONFIG = {
    "scenes_dir": "data/anthell/scenes",
    "sprites_dir": "data/anthell/sprites",
    "out_dir": "data/anthell/ready",
    "preview_dir": "data/anthell/preview",

    "val_ratio": 0.15,       # 验证集占比
    "per_scene": 40,         # 每张场景里合成多少张图(场景少, 先给大点; 补图后可调小)
    "min_objs": 1,           # 每张合成图贴几只(含边界)
    "max_objs": 5,
    "empty_ratio": 0.10,     # 其中多少比例是"纯背景无怪"的负样本(压虚警)
    "scale_min": 0.55,       # 贴图缩放范围(相对精灵原生尺寸)
    "scale_max": 1.5,
    "fg_thresh": 22,         # flood-fill 抠图的颜色距离阈值: 抠缺怪身→调大, 抠不掉背景→调小
    "hue_tol": 25,           # 背景色相角距离上限(度): 距边框主色相多少度内算背景
    "sat_tol": 30,           # 背景饱和度距离上限: |sat - 边框中位sat| 在此内算背景
    "val_tol": 40,           # 背景明度距离上限: |val - 边框中位val| 在此内算背景
    "seed": 42,

    # 文件夹名 -> 规范 class 名. 空格统一换下划线; 拼写错的在这里改正.
    "class_aliases": {
        "soildier ant": "soldier_ant",
    },
}


def _norm_class(folder):
    name = CONFIG["class_aliases"].get(folder)
    if name is None:
        name = folder.strip().lower().replace(" ", "_")
    return name


def _load_scenes():
    paths = sorted(glob.glob(os.path.join(CONFIG["scenes_dir"], "*.png"))
                   + glob.glob(os.path.join(CONFIG["scenes_dir"], "*.jpg")))
    return [cv2.imread(p) for p in paths if cv2.imread(p) is not None]


def _load_sprite_pool():
    """返回 (class_names, sprite_pool). sprite_pool[c] 是该类所有贴图的路径列表."""
    root = CONFIG["sprites_dir"]
    folders = sorted(d for d in os.listdir(root)
                     if os.path.isdir(os.path.join(root, d)))
    names, pool = [], []
    for folder in folders:
        paths = sorted(glob.glob(os.path.join(root, folder, "*.png"))
                       + glob.glob(os.path.join(root, folder, "*.jpg")))
        if not paths:
            print(f"⚠️ 物种目录 {folder!r} 里没有图片, 跳过")
            continue
        names.append(_norm_class(folder))
        pool.append(paths)
        print(f"  class {names[-1]!r}: {len(paths)} 张")
    return names, pool


def _crop_border(bgr, r=3):
    """裁掉最外层 r 像素. 精灵贴图四周往往是镇里闪烁/别的怪的边, flood-fill 从
    边缘像散不进来会把它当背景, 裁掉只损失几个像素却解决一大污染源."""
    h, w = bgr.shape[:2]
    if h <= 2 * r or w <= 2 * r:
        return bgr
    return bgr[r:h - r, r:w - r]


def _to_hue_sat_val(bgr):
    """BGR -> (色相 0-358, 饱和度 0-255, 明度 0-255). 色相翻倍对齐到度."""
    hsv = cv2.cvtColor(bgr[..., :3], cv2.COLOR_BGR2HSV).astype(np.int16)
    return hsv[..., 0] * 2, hsv[..., 1], hsv[..., 2]


def _hue_dist(hue, base):
    """色相角距离, 结果 0..180."""
    d = np.abs(hue - base)
    return np.minimum(d, 360 - d)


def _flood_bg(bgr3, thresh):
    """区域 flood-fill: 从四边沿颜色爬, 返回连到边界、颜色一致的背景掩码(255=背景)."""
    h, w = bgr3.shape[:2]
    tmp = bgr3.copy()
    mask = np.zeros((h + 2, w + 2), np.uint8)
    flags = 8 | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE
    lo = up = (thresh, thresh, thresh)
    xs = range(0, w, max(1, w // 40))
    ys = range(0, h, max(1, h // 40))
    for x in xs:
        cv2.floodFill(tmp, mask, (x, 0), 0, lo, up, flags)
        cv2.floodFill(tmp, mask, (x, h - 1), 0, lo, up, flags)
    for y in ys:
        cv2.floodFill(tmp, mask, (0, y), 0, lo, up, flags)
        cv2.floodFill(tmp, mask, (w - 1, y), 0, lo, up, flags)
    return (mask[1:-1, 1:-1] > 0).astype(np.uint8) * 255


def _hue_bg(bgr3):
    """色相抠图: 用"边框一圈"估计地面主色(hue/sat/val), 找出三通道都贴近地色的
    像素, 再只保留"同色连通到边界"的区域(connectedComponents 保证不吃掉孤立的怪).
    注意: 这里的"色相抠图"实际是 HSV 三通道判据 —— 地色是低饱和棕, sat/val 才是
    决定性信息. 之前只用 hue + remaining_hue_quant 展开误判为"取非", 故失效."""
    h, w = bgr3.shape[:2]
    hue, sat, val = _to_hue_sat_val(bgr3)
    ring = np.zeros((h, w), np.uint8)
    ring[0, :] = ring[-1, :] = ring[:, 0] = ring[:, -1] = 255
    m = ring > 0
    if m.sum() < 20:
        return np.zeros((h, w), np.uint8)
    base_hue = float(np.median(hue[m]))
    base_sat = float(np.median(sat[m]))
    base_val = float(np.median(val[m]))

    hue_near = _hue_dist(hue, base_hue) <= CONFIG["hue_tol"]
    sat_near = np.abs(sat - base_sat) <= CONFIG["sat_tol"]
    val_near = np.abs(val - base_val) <= CONFIG["val_tol"]
    bgcolor = (hue_near & sat_near & val_near).astype(np.uint8)

    n, labels = cv2.connectedComponents(bgcolor, connectivity=8)
    border = set(np.unique(np.concatenate(
        [labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]])).tolist())
    border.discard(0)
    keep = [i for i in range(1, n) if i in border]
    return np.isin(labels, keep).astype(np.uint8) * 255


def _extract_fg(bgr, thresh):
    """两路抠图, 合并取**交集**(背景交集 = 前景并集):
      1) 区域 flood-fill —— 连边、RGB 一致的背景;
      2) 色相抠图   —— 颜色像地面主色、且同色连边的背景.
    合并取**背景并集**(bitwise_or 背景掩码): 任一路判为背景的像素就删除,
    等价于主体取**交集**(两路都判前景才保留) —— 最激进地抠干净地面,
    flood 或 hue 任何一路漏抠的都由另一路补掉.
    返回 (RGBA 贴图, 二值前景掩码 fg). 自带 alpha 通道时直接信任 alpha."""
    bgr = _crop_border(bgr)
    h, w = bgr.shape[:2]
    if bgr.shape[2] == 4:
        alpha = bgr[..., 3]
        if (alpha < 250).any():
            return bgr.copy(), alpha.copy()
    bgr3 = bgr[..., :3].astype(np.uint8)

    bg = cv2.bitwise_or(_flood_bg(bgr3, thresh), _hue_bg(bgr3))
    fg = (bg == 0).astype(np.uint8) * 255
    rgba = cv2.cvtColor(bgr3, cv2.COLOR_BGR2BGRA)
    rgba[..., 3] = fg
    return rgba, fg


def _transform(rgba, fg):
    """随机 水平/垂直翻转 + 0/90/180/270 旋转. rgba 和 fg 同步变换, 保持 alpha 对齐."""
    if random.random() < 0.5:
        rgba, fg = cv2.flip(rgba, 1), cv2.flip(fg, 1)
    if random.random() < 0.5:
        rgba, fg = cv2.flip(rgba, 0), cv2.flip(fg, 0)
    k = random.randint(0, 3)
    for _ in range(k):
        rgba = cv2.rotate(rgba, cv2.ROTATE_90_CLOCKWISE)
        fg = cv2.rotate(fg, cv2.ROTATE_90_CLOCKWISE)
    return rgba, fg


def _compose_one(bg, pool):
    """往 bg 上贴 0~max_objs 只随机精灵, 返回 (合成图, labels). labels = [(cls, cx, cy, w, h)]."""
    H, W = bg.shape[:2]
    out = bg.copy()
    labels = []
    if random.random() < CONFIG["empty_ratio"]:
        n = 0
    else:
        n = random.randint(CONFIG["min_objs"], CONFIG["max_objs"])

    for _ in range(n):
        ci = random.randrange(len(pool))
        path = random.choice(pool[ci])
        sprite = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        if sprite is None:
            continue
        if sprite.ndim == 2:
            sprite = cv2.cvtColor(sprite, cv2.COLOR_GRAY2BGRA)
        elif sprite.shape[2] == 3:
            sprite = cv2.cvtColor(sprite, cv2.COLOR_BGR2BGRA)

        rgba, fg = _extract_fg(sprite, CONFIG["fg_thresh"])
        rgba, fg = _transform(rgba, fg)
        sh, sw = rgba.shape[:2]

        scale = random.uniform(CONFIG["scale_min"], CONFIG["scale_max"])
        scale = min(scale, (W * 0.85) / max(1, sw), (H * 0.85) / max(1, sh))
        if scale < 0.1:
            continue
        nw, nh = max(1, int(round(sw * scale))), max(1, int(round(sh * scale)))
        rgba = cv2.resize(rgba, (nw, nh), interpolation=cv2.INTER_LINEAR)
        fg = cv2.resize(fg, (nw, nh), interpolation=cv2.INTER_AREA)
        fg_bin = (fg > 128).astype(np.uint8) * 255

        ys, xs = np.where(fg_bin > 0)
        if len(xs) < 6:  # 前景太小 = 抠图失败/图太空, 丢弃
            continue
        x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()

        px = random.randint(0, max(1, W - nw))
        py = random.randint(0, max(1, H - nh))

        # alpha 羽化贴图
        alpha = (cv2.GaussianBlur(fg_bin.astype(np.float32), (5, 5), 0) / 255.0).clip(0, 1)
        alpha = alpha[..., None]
        roi = out[py:py + nh, px:px + nw].astype(np.float32)
        sprite_rgb = rgba[..., :3].astype(np.float32)
        out[py:py + nh, px:px + nw] = (sprite_rgb * alpha + roi * (1 - alpha)).astype(np.uint8)

        labels.append((ci,
                       (px + (x0 + x1) / 2) / W,
                       (py + (y0 + y1) / 2) / H,
                       (x1 - x0 + 1) / W,
                       (y1 - y0 + 1) / H))
    return out, labels


def _write_preview(pool, names):
    """每个物种抽一张, 存三种抠图效果白底图供人工对比:
      <class>.png      两路合并后的最终前景
      <class>_hue.png  仅色相抠图
      <class>_flood.png 仅区域 flood-fill
    看哪个更接近"干净怪"来反推要调哪个参数."""
    os.makedirs(CONFIG["preview_dir"], exist_ok=True)
    for ci, paths in enumerate(pool):
        sprite = cv2.imread(paths[0], cv2.IMREAD_UNCHANGED)
        if sprite is None:
            continue
        if sprite.ndim == 2:
            sprite = cv2.cvtColor(sprite, cv2.COLOR_GRAY2BGRA)
        elif sprite.shape[2] == 3:
            sprite = cv2.cvtColor(sprite, cv2.COLOR_BGR2BGRA)

        base = _crop_border(sprite)
        h, w = base.shape[:2]
        bgr3 = base[..., :3].astype(np.uint8)

        def _on_white(fg):
            canvas = np.full((h, w, 3), 255, np.uint8)
            a = (fg.astype(np.float32) / 255.0)[..., None]
            return (bgr3 * a + canvas * (1 - a)).astype(np.uint8)

        # 自带 alpha 就直接用原始 alpha
        if base.shape[2] == 4 and (base[..., 3] < 250).any():
            alpha = base[..., 3]
            cv2.imwrite(os.path.join(CONFIG["preview_dir"], f"{names[ci]}.png"),
                        _on_white(alpha))
            print(f"  preview -> {names[ci]}.png (原生 alpha)")
            continue

        # flood / hue 存的是"该路径认为的前景"(255=前景), 单独存两张便于对比.
        # merged = 主体**交集** = 两路都判前景才保留 = 背景并集 = 与 _extract_fg 一致.
        flood_fg = (_flood_bg(bgr3, CONFIG["fg_thresh"]) == 0).astype(np.uint8) * 255
        hue_fg = (_hue_bg(bgr3) == 0).astype(np.uint8) * 255
        merged = cv2.bitwise_and(flood_fg, hue_fg)
        cv2.imwrite(os.path.join(CONFIG["preview_dir"], f"{names[ci]}.png"), _on_white(merged))
        cv2.imwrite(os.path.join(CONFIG["preview_dir"], f"{names[ci]}_flood.png"), _on_white(flood_fg))
        cv2.imwrite(os.path.join(CONFIG["preview_dir"], f"{names[ci]}_hue.png"), _on_white(hue_fg))
        print(f"  preview -> {names[ci]}.png (+_flood / +_hue)")


def main():
    random.seed(CONFIG["seed"])
    np.random.seed(CONFIG["seed"])

    scenes = _load_scenes()
    if not scenes:
        print(f"❌ 没有读到场景图, 检查 {CONFIG['scenes_dir']}")
        return
    names, pool = _load_sprite_pool()
    if not names:
        print(f"❌ 没有读到精灵贴图, 检查 {CONFIG['sprites_dir']}")
        return

    print(f"\n合成前先出抠图预览到 {CONFIG['preview_dir']} — 先去看这批图抠得干不干净")
    _write_preview(pool, names)

    normal = names  # class 名露出以便下面的字符串里引用
    out = CONFIG["out_dir"]
    for sub in ("images", "labels"):
        for split in ("train", "val"):
            d = os.path.join(out, sub, split)
            if os.path.exists(d):
                import shutil
                shutil.rmtree(d)
            os.makedirs(d, exist_ok=True)

    total = len(scenes) * CONFIG["per_scene"]
    written = 0
    for si, bg in enumerate(scenes):
        for k in range(CONFIG["per_scene"]):
            img, labels = _compose_one(bg, pool)
            split = "val" if random.random() < CONFIG["val_ratio"] else "train"
            stem = f"synth_{si:03d}_{k:03d}"
            cv2.imwrite(os.path.join(out, "images", split, f"{stem}.jpg"),
                        img, [cv2.IMWRITE_JPEG_QUALITY, 95])
            with open(os.path.join(out, "labels", split, f"{stem}.txt"), "w") as f:
                for (ci, cx, cy, w, h) in labels:
                    f.write(f"{ci} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n")
            written += 1

    # data.yaml
    yaml = (
        f"path: {os.path.abspath(out)}\n"
        f"train: images/train\n"
        f"val: images/val\n"
        f"names:\n" + "".join(f"  {i}: {n}\n" for i, n in enumerate(names))
    )
    with open(os.path.join(out, "data.yaml"), "w", encoding="utf-8") as f:
        f.write(yaml)

    print(f"\n✅ 合成完成: 共 {written} 张 -> {out}")
    print(f"   classes: {names}")
    print("   先用数据跑一版 YOLOv8n 看 mAP, 不行就回来调 fg_thresh / 补场景。")


if __name__ == "__main__":
    main()