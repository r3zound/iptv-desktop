# -*- coding: utf-8 -*-
"""
build_icons.py -- 一次性生成 10 个 ICO 图标供 iptv-desktop.exe 选用.

主题: IPTV / 直播 / 信号 / 雷达 / 卫星 / 频道聚合 / 过滤 (科技范).
输出: assets/icons/<NN>-<name>.png + .ico (16/32/48/64/128/256 多尺寸).

用法:
    python tools/build_icons.py
"""
from __future__ import annotations

import math
import os
import random

from PIL import Image, ImageDraw, ImageFont

# ---- 颜色 ----
BG_DARK = (10, 25, 41, 255)        # 深蓝
BG_DARKER = (13, 13, 13, 255)      # 近黑
CYAN = (0, 229, 255, 255)          # 青色
CYAN_BRIGHT = (0, 217, 255, 255)
PURPLE = (139, 92, 246, 255)
GREEN = (0, 255, 136, 255)
GREEN_DARK = (0, 180, 90, 255)
WHITE = (255, 255, 255, 255)
GRAY = (176, 190, 197, 255)
GRAY_DIM = (60, 80, 100, 255)

W = H = 256
SIZES = [16, 32, 48, 64, 128, 256]

ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets', 'icons')


# ---- 工具 ----
def make_base(bg_color=BG_DARK, radius=44):
    """统一圆角方形背景."""
    img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=radius, fill=bg_color)
    return img, d


def save_icon(name, img_256):
    """保存 PNG + 多尺寸 ICO."""
    base = os.path.join(ICON_DIR, name)
    img_256.save(base + '.png')
    img_256.save(base + '.ico', format='ICO', sizes=[(s, s) for s in SIZES])
    print(f'  -> {name}.ico ({SIZES})')


# ---- 图标 01: 雷达扫描 ----
def icon_01_radar():
    img, d = make_base(BG_DARK)
    cx, cy = 128, 128
    # 同心圆
    for r, alpha in [(95, 70), (70, 110), (45, 160), (22, 220)]:
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(0, 229, 255, alpha), width=2)
    # 十字线
    d.line([cx - 100, cy, cx + 100, cy], fill=(0, 200, 255, 100), width=1)
    d.line([cx, cy - 100, cx, cy + 100], fill=(0, 200, 255, 100), width=1)
    # 扫描扇形 (从 12 点顺时针扫 140 度) -- PIL pieslice 是顺时针
    sweep_start = -100  # 左下偏上
    sweep_end = -100 + 140
    d.pieslice([cx - 95, cy - 95, cx + 95, cy + 95], sweep_start, sweep_end, fill=(0, 229, 255, 60))
    d.pieslice([cx - 70, cy - 70, cx + 70, cy + 70], sweep_start, sweep_end, fill=(0, 229, 255, 90))
    d.pieslice([cx - 45, cy - 45, cx + 45, cy + 45], sweep_start, sweep_end, fill=(0, 229, 255, 130))
    # 扫描线 (扇形前缘)
    a = math.radians(sweep_end)
    d.line([cx, cy, cx + 95 * math.cos(a), cy + 95 * math.sin(a)], fill=CYAN_BRIGHT, width=4)
    # 中心点
    d.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], fill=CYAN_BRIGHT)
    # 一个被扫到的"目标"
    tx, ty = 180, 100
    d.ellipse([tx - 5, ty - 5, tx + 5, ty + 5], fill=GREEN)
    return img


# ---- 图标 02: 卫星 + 地球 ----
def icon_02_satellite():
    img, d = make_base(BG_DARKER)
    # 地球
    cx, cy = 128, 145
    r = 78
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(0, 60, 100, 230), outline=CYAN, width=4)
    # 经线 (竖)
    for offset in [-55, -28, 0, 28, 55]:
        d.arc([cx - r + abs(offset), cy - r, cx + r - abs(offset), cy + r], 0, 360, fill=(0, 200, 255, 130), width=1)
    # 纬线 (横)
    for offset in [-50, -25, 0, 25, 50]:
        d.arc([cx - r, cy - r + offset, cx + r, cy + r + offset], 0, 360, fill=(0, 200, 255, 130), width=1)
    # 卫星
    sx, sy = 200, 50
    d.rectangle([sx - 16, sy - 8, sx + 16, sy + 8], fill=GRAY, outline=WHITE, width=1)
    d.rectangle([sx - 36, sy - 5, sx - 18, sy + 5], fill=(60, 120, 170, 255), outline=WHITE, width=1)
    d.rectangle([sx + 18, sy - 5, sx + 36, sy + 5], fill=(60, 120, 170, 255), outline=WHITE, width=1)
    d.line([sx - 18, sy, sx - 18, sy], fill=WHITE)
    d.line([sx, sy - 8, sx, sy + 8], fill=WHITE, width=1)
    # 卫星信号波 (向下指向地球)
    for r2 in [10, 22, 34]:
        d.arc([sx - r2, sy + 8, sx + r2, sy + 8 + 2 * r2], 200, 340, fill=CYAN, width=2)
    # 卫星本体中央信号
    d.ellipse([sx - 3, sy - 3, sx + 3, sy + 3], fill=CYAN_BRIGHT)
    return img


# ---- 图标 03: 频谱 + 播放 ----
def icon_03_spectrum_play():
    img, d = make_base(BG_DARK)
    cx = 128
    bar_w = 14
    gap = 8
    heights_left = [50, 90, 130, 110, 80, 55, 35]
    heights_right = [35, 55, 80, 110, 130, 90, 50]
    # 左
    n = len(heights_left)
    total_w = n * bar_w + (n - 1) * gap
    start_x = cx - 50 - total_w
    for i, h in enumerate(heights_left):
        x = start_x + i * (bar_w + gap)
        y_top = 128 - h // 2
        d.rounded_rectangle([x, y_top, x + bar_w, 128 + h // 2], radius=2, fill=CYAN)
    # 右
    for i, h in enumerate(heights_right):
        x = cx + 50 + i * (bar_w + gap)
        y_top = 128 - h // 2
        d.rounded_rectangle([x, y_top, x + bar_w, 128 + h // 2], radius=2, fill=PURPLE)
    # 中央播放按钮
    d.ellipse([cx - 36, 92, cx + 36, 164], fill=CYAN_BRIGHT, outline=WHITE, width=3)
    d.polygon([(cx - 8, 110), (cx - 8, 146), (cx + 18, 128)], fill=BG_DARK)
    return img


# ---- 图标 04: TV + 信号波 ----
def icon_04_tv_signal():
    img, d = make_base(BG_DARK)
    # 信号弧线 (从 TV 右上角辐射出去)
    sx, sy = 210, 70
    for r, alpha in [(28, 240), (48, 200), (70, 150), (95, 100), (120, 60)]:
        d.arc([sx - r, sy - r, sx + r, sy + r], 220, 320, fill=(0, 229, 255, alpha), width=4)
    # 信号源点
    d.ellipse([sx - 5, sy - 5, sx + 5, sy + 5], fill=CYAN_BRIGHT)
    # TV 主体
    tx, ty = 50, 105
    tw_, th_ = 156, 105
    d.rounded_rectangle([tx, ty, tx + tw_, ty + th_], radius=10, fill=(25, 45, 80, 255), outline=CYAN, width=4)
    # 屏幕内框
    d.rounded_rectangle([tx + 10, ty + 10, tx + tw_ - 10, ty + th_ - 10], radius=4, fill=(8, 18, 30, 255))
    # 屏幕里的播放三角
    ptx = tx + tw_ // 2
    pty = ty + th_ // 2
    d.polygon([(ptx - 18, pty - 22), (ptx - 18, pty + 22), (ptx + 22, pty)], fill=CYAN_BRIGHT)
    # 底座
    d.rounded_rectangle([tx + 50, ty + th_, tx + tw_ - 50, ty + th_ + 8], fill=GRAY)
    d.rounded_rectangle([tx + 25, ty + th_ + 5, tx + tw_ - 25, ty + th_ + 14], radius=3, fill=GRAY)
    return img


# ---- 图标 05: 蜂巢频道 ----
def icon_05_honeycomb():
    img, d = make_base(BG_DARK)
    cx, cy = 128, 128
    r = 30  # 六边形外接圆半径

    def hex_pts(cx, cy, r):
        return [(cx + r * math.cos(math.radians(60 * i - 30)), cy + r * math.sin(math.radians(60 * i - 30))) for i in range(6)]

    # 外圈 6 个
    outer_centers = []
    for i in range(6):
        ang = math.radians(60 * i - 30)
        nx = cx + r * 1.8 * math.cos(ang)
        ny = cy + r * 1.8 * math.sin(ang)
        outer_centers.append((nx, ny))
        p = hex_pts(nx, ny, r * 0.85)
        d.polygon(p, outline=(0, 200, 255, 220), fill=(0, 60, 110, 230), width=2)
        # 连线到中心
        d.line([cx, cy, nx, ny], fill=(0, 180, 220, 140), width=2)
    # 中心六边形 (最大)
    p_center = hex_pts(cx, cy, r)
    d.polygon(p_center, outline=CYAN_BRIGHT, fill=(0, 90, 150, 255), width=3)
    # 中心一个"播放"三角
    d.polygon([(cx - 6, cy - 12), (cx - 6, cy + 12), (cx + 12, cy)], fill=WHITE)
    # 外圈更外的小六边形 (代表更多频道)
    for i, (ox, oy) in enumerate(outer_centers):
        ang = math.radians(60 * i - 30)
        nx = ox + r * 1.7 * math.cos(ang)
        ny = oy + r * 1.7 * math.sin(ang)
        p = hex_pts(nx, ny, r * 0.55)
        d.polygon(p, outline=(139, 92, 246, 200), fill=(50, 30, 80, 220), width=1)
    return img


# ---- 图标 06: 电路 + TV ----
def icon_06_circuit_tv():
    img, d = make_base(BG_DARKER)
    cx, cy = 128, 128
    # PCB 走线 (从四角到 TV)
    paths = [
        [(20, 35), (75, 35), (75, 100)],
        [(236, 50), (180, 50), (180, 100)],
        [(20, 220), (90, 220), (90, 160)],
        [(236, 210), (170, 210), (170, 160)],
    ]
    for path in paths:
        d.line(path, fill=GREEN, width=3)
        # 端点焊盘
        ex, ey = path[0]
        d.ellipse([ex - 6, ey - 6, ex + 6, ey + 6], fill=GREEN, outline=WHITE, width=1)
        # 折点焊盘
        for px, py in path[1:-1]:
            d.ellipse([px - 4, py - 4, px + 4, py + 4], fill=GREEN_DARK)
    # 走线上的电阻 (小矩形)
    for x, y in [(75, 60), (180, 75), (90, 200), (170, 190)]:
        d.rounded_rectangle([x - 8, y - 3, x + 8, y + 3], fill=GREEN, outline=WHITE, width=1)
    # 中央 TV 屏幕
    d.rounded_rectangle([cx - 55, cy - 38, cx + 55, cy + 38], radius=8, outline=GREEN, width=4)
    d.rounded_rectangle([cx - 47, cy - 30, cx + 47, cy + 30], fill=(0, 30, 20, 255))
    # 屏幕里的波形
    pts = []
    for i in range(20):
        x = cx - 42 + i * 4.4
        y = cy + 12 * math.sin(i * 0.6)
        pts.append((x, y))
    d.line(pts, fill=GREEN, width=2)
    # 屏幕里的播放按钮
    d.polygon([(cx - 10, cy - 12), (cx - 10, cy + 12), (cx + 12, cy)], fill=GREEN)
    return img


# ---- 图标 07: 过滤漏斗 ----
def icon_07_filter_funnel():
    img, d = make_base(BG_DARK)
    # 输入端: 多个频道矩形 (顶部)
    for i, x in enumerate([55, 90, 125, 160, 195]):
        color = CYAN if i % 2 == 0 else PURPLE
        d.rounded_rectangle([x - 18, 30, x + 18, 48], radius=3, fill=color)
    # 漏斗本体 (倒梯形 + 颈)
    d.polygon([(50, 60), (206, 60), (148, 145), (148, 205), (108, 205), (108, 145)], fill=CYAN_BRIGHT)
    # 漏斗内部高亮
    d.polygon([(70, 75), (186, 75), (143, 130), (113, 130)], fill=(255, 255, 255, 90))
    # 输出端单线 (底部,代表过滤后单一干净流)
    d.rounded_rectangle([115, 205, 141, 225], radius=3, fill=GREEN)
    d.rounded_rectangle([110, 222, 146, 230], radius=3, fill=GREEN)
    # 左侧箭头 (代表"过滤")
    d.line([(15, 128), (40, 128)], fill=WHITE, width=3)
    d.polygon([(35, 120), (45, 128), (35, 136)], fill=WHITE)
    # 右侧小过滤符号 (×)
    d.line([(215, 122), (235, 142)], fill=WHITE, width=3)
    d.line([(235, 122), (215, 142)], fill=WHITE, width=3)
    return img


# ---- 图标 08: WiFi + 播放 ----
def icon_08_wifi_play():
    img, d = make_base(BG_DARK)
    cx, cy = 128, 150
    # 底部信号点
    d.ellipse([cx - 9, cy + 50, cx + 9, cy + 68], fill=CYAN_BRIGHT)
    # 三层弧
    for r, alpha in [(35, 240), (60, 190), (88, 130), (118, 70)]:
        d.arc([cx - r, cy - r, cx + r, cy + r], 220, 320, fill=(0, 229, 255, alpha), width=5)
    # 中央播放按钮 (浮在上方)
    d.ellipse([cx - 32, cy - 80, cx + 32, cy - 16], fill=(10, 25, 41, 255), outline=CYAN_BRIGHT, width=4)
    d.polygon([(cx - 10, cy - 65), (cx - 10, cy - 30), (cx + 14, cy - 48)], fill=CYAN_BRIGHT)
    return img


# ---- 图标 09: 二进制流 + TV ----
def icon_09_binary_tv():
    img, d = make_base(BG_DARKER)
    # 二进制数据流 (3 行 0/1)
    random.seed(9)
    font = None
    for f in [r'C:\Windows\Fonts\consola.ttf', r'C:\Windows\Fonts\arial.ttf']:
        if os.path.exists(f):
            try:
                font = ImageFont.truetype(f, 13)
                break
            except Exception:
                pass
    if font is None:
        font = ImageFont.load_default()
    row_colors = [(GREEN, 20), (CYAN, 42), (PURPLE, 100), (GREEN, 220)]
    for color, y in row_colors:
        bits = ''.join(random.choice(['0', '1']) for _ in range(22))
        for i, b in enumerate(bits):
            x = 8 + i * 11
            # 透明度随距离渐变
            alpha = 255 if 6 <= i <= 16 else 140
            c = color[:3] + (alpha,)
            d.text((x, y), b, fill=c, font=font)
    # 中央 TV
    d.rounded_rectangle([60, 130, 196, 210], radius=8, outline=GREEN, width=4)
    d.rounded_rectangle([68, 138, 188, 202], fill=(0, 30, 20, 255))
    # 屏幕里波形
    pts = []
    for i in range(24):
        x = 76 + i * 4.5
        y = 170 + 10 * math.sin(i * 0.7)
        pts.append((x, y))
    d.line(pts, fill=GREEN, width=2)
    # 底台
    d.rectangle([112, 210, 175, 218], fill=GRAY)
    d.rounded_rectangle([88, 216, 199, 226], radius=3, fill=GRAY)
    return img


# ---- 图标 10: 数字地球节点 ----
def icon_10_globe_network():
    img, d = make_base(BG_DARK)
    cx, cy = 128, 128
    r = 96
    # 经纬网
    for offset in range(-80, 81, 16):
        d.arc([cx - r + abs(offset), cy - r, cx + r - abs(offset), cy + r], 0, 360, fill=(0, 150, 200, 110), width=1)
        d.arc([cx - r, cy - r + abs(offset), cx + r, cy + r - abs(offset)], 0, 360, fill=(0, 150, 200, 110), width=1)
    # 外圈
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=CYAN, width=3)
    # 节点
    random.seed(42)
    nodes = []
    n_nodes = 10
    for i in range(n_nodes):
        ang = math.radians(i * 360 / n_nodes + 18)
        nx = cx + r * 0.82 * math.cos(ang)
        ny = cy + r * 0.82 * math.sin(ang)
        nodes.append((nx, ny))
    # 连线
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            if random.random() < 0.35:
                d.line([nodes[i], nodes[j]], fill=(0, 200, 255, 110), width=2)
    # 节点圆
    for i, (nx, ny) in enumerate(nodes):
        c = GREEN if i % 3 == 0 else CYAN
        d.ellipse([nx - 5, ny - 5, nx + 5, ny + 5], fill=c, outline=WHITE, width=1)
    # 中心节点 (更大)
    d.ellipse([cx - 10, cy - 10, cx + 10, cy + 10], fill=CYAN_BRIGHT, outline=WHITE, width=2)
    return img


# ---- 入口 ----
ICONS = [
    ('01-radar', 'Radar Sweep -- radar scan with concentric circles', icon_01_radar),
    ('02-satellite', 'Satellite + Earth -- satellite broadcasting to earth', icon_02_satellite),
    ('03-spectrum', 'Spectrum + Play -- audio bars flanking play button', icon_03_spectrum_play),
    ('04-tv-signal', 'TV + Signal -- retro TV with broadcast waves', icon_04_tv_signal),
    ('05-honeycomb', 'Honeycomb -- 6 surrounding hexes (channel cluster)', icon_05_honeycomb),
    ('06-circuit', 'PCB + TV -- circuit board feeding TV screen', icon_06_circuit_tv),
    ('07-funnel', 'Filter Funnel -- 5 channels in, 1 clean stream out', icon_07_filter_funnel),
    ('08-wifi-play', 'WiFi + Play -- wifi arcs over play button', icon_08_wifi_play),
    ('09-binary', 'Binary + TV -- 0/1 stream falling into TV', icon_09_binary_tv),
    ('10-globe', 'Digital Globe -- networked nodes on lat/lon grid', icon_10_globe_network),
]


def main():
    os.makedirs(ICON_DIR, exist_ok=True)
    print(f'Output: {ICON_DIR}')
    for name, desc, fn in ICONS:
        print(f'[{name}] {desc}')
        img = fn()
        save_icon(name, img)
    print(f'\nDone. {len(ICONS)} icons written.')
    print('Preview HTML: see tools/build_page.py')


if __name__ == '__main__':
    main()