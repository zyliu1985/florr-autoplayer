"""采集 florr.io 训练数据(路线 B: copy-paste 合成用).

用法(Windows, 用项目 venv):
    venv\\Scripts\\python collect_data.py

开始前: 先把游戏 zoom 锁死到"开着 bot 刷怪时的那个视野", F11 全屏, 全程别动 —
否则空场景和单体精灵的尺度对不上, 训练集/推理域错配。

   F6    截"全屏空场景"    -> data/anthell/scenes/    (画面里尽量别留怪)
   F7    截"鼠标周围方形单体精灵" -> data/anthell/sprites/  (框要紧凑, 少带背景)
   Esc   退出

产出全部纯 ASCII、自增命名, 后面合成脚本直接读这两个目录.
"""
import ctypes
import os
import time

import pyautogui

SCENES_DIR = "data/anthell/scenes"
SPRITES_DIR = "data/anthell/sprites"
SPRITE_HALF = 128   # F7 裁剪框的半边长(像素): 以鼠标为中心截 SPRITE_HALF*2 见方.
                    # 怪太小就把数值调小、太大调大, 目标是"正好包住一只怪 + 一点边".

_F6 = 0x75
_F7 = 0x76
_ESC = 0x1B


def _pressed(vk):
    """读 Win32 按键状态(GetAsyncKeyState), 全局有效 —— 游戏全屏时也读得到, 零额外依赖."""
    return (ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000) != 0


def _wait_release(vk):
    """等按键放开, 防一次按住触发多张."""
    while _pressed(vk):
        time.sleep(0.02)


def _next_path(directory, prefix):
    os.makedirs(directory, exist_ok=True)
    n = len(os.listdir(directory))
    while True:
        n += 1
        path = os.path.join(directory, f"{prefix}_{n:04d}.png")
        if not os.path.exists(path):
            return path


def main():
    for d in (SCENES_DIR, SPRITES_DIR):
        os.makedirs(d, exist_ok=True)
    print("采集脚本运行中 (游戏窗口保持活动即可, 脚本在后台听按键):")
    print(f"  F6 = 全屏空场景 -> {SCENES_DIR}")
    print(f"  F7 = 单体精灵   -> {SPRITES_DIR} (鼠标周围 {SPRITE_HALF * 2}x{SPRITE_HALF * 2})")
    print("  Esc = 退出\n")
    while True:
        if _pressed(_F6):
            path = _next_path(SCENES_DIR, "scene")
            pyautogui.screenshot().save(path)
            print(f"已存场景: {path}")
            _wait_release(_F6)
        elif _pressed(_F7):
            x, y = pyautogui.position()
            region = [x - SPRITE_HALF, y - SPRITE_HALF, SPRITE_HALF * 2, SPRITE_HALF * 2]
            path = _next_path(SPRITES_DIR, "sprite")
            pyautogui.screenshot(region=region).save(path)
            print(f"已存精灵: {path}")
            _wait_release(_F7)
        elif _pressed(_ESC):
            print("退出采集.")
            return
        time.sleep(0.05)


if __name__ == "__main__":
    main()