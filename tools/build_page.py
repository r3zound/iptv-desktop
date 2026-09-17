# -*- coding: utf-8 -*-
"""
build_page.py -- 生成 10 个图标的对比 HTML 页面, 供预览/选择.

用法:
    python tools/build_icons.py    # 先生成 .ico
    python tools/build_page.py     # 再生成对比页
    start preview.html             # 浏览器打开
"""
from __future__ import annotations

import base64
import os
import sys

ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets', 'icons')
PAGE = os.path.join(ICON_DIR, 'preview.html')

ICONS = [
    ('01-radar', '雷达扫描', '同心圆 + 扫描扇形 + 目标点'),
    ('02-satellite', '卫星 + 地球', '卫星信号覆盖蓝色地球'),
    ('03-spectrum', '频谱 + 播放', '对称频谱柱 + 中央播放按钮'),
    ('04-tv-signal', 'TV + 信号波', '复古 TV 框 + 弧形广播波'),
    ('05-honeycomb', '蜂巢频道', '六边形蜂巢代表频道聚合'),
    ('06-circuit', '电路 + TV', 'PCB 走线连接到 TV 屏幕'),
    ('07-funnel', '过滤漏斗', '5 路频道输入 → 1 路干净输出'),
    ('08-wifi-play', 'WiFi + 播放', 'WiFi 信号波 + 播放按钮融合'),
    ('09-binary', '二进制 + TV', '0/1 数据流落到 TV'),
    ('10-globe', '数字地球节点', '经纬网格 + 节点连线'),
]


def main():
    cards = []
    for i, (slug, cn, desc) in enumerate(ICONS, 1):
        ico = os.path.join(ICON_DIR, slug + '.ico')
        png = os.path.join(ICON_DIR, slug + '.png')
        if not os.path.exists(png):
            print(f'[WARN] {slug}.png missing -- run build_icons.py first')
            continue
        # 用 PNG 做 base64 内嵌, 浏览器直接显示大图
        with open(png, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode('ascii')
        # ICO 给文件链接 (Windows 资源管理器图标就是它)
        rel_ico = slug + '.ico'
        cards.append(f"""
        <div class="card">
            <div class="big"><img src="data:image/png;base64,{b64}" alt="{slug}"></div>
            <div class="ico-row">
                <img class="ico" src="{rel_ico}" width="16" height="16" alt="16">
                <img class="ico" src="{rel_ico}" width="32" height="32" alt="32">
                <img class="ico" src="{rel_ico}" width="48" height="48" alt="48">
                <img class="ico" src="{rel_ico}" width="64" height="64" alt="64">
                <img class="ico" src="{rel_ico}" width="128" height="128" alt="128">
            </div>
            <div class="meta">
                <div class="num">No.{i:02d}</div>
                <div class="cn">{cn}</div>
                <div class="desc">{desc}</div>
                <div class="file">{rel_ico}</div>
            </div>
        </div>
        """)

    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>iptv-desktop 图标候选</title>
<style>
  body {
    background: #0a1929;
    color: #e3f2fd;
    font-family: -apple-system, "Microsoft YaHei", "Segoe UI", sans-serif;
    margin: 0;
    padding: 32px;
  }
  h1 {
    margin: 0 0 8px;
    font-size: 22px;
    font-weight: 600;
    color: #00e5ff;
  }
  .sub {
    color: #80cbc4;
    font-size: 13px;
    margin-bottom: 28px;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 24px;
  }
  .card {
    background: #102a43;
    border: 1px solid #1e3a5f;
    border-radius: 12px;
    padding: 20px;
    display: flex;
    flex-direction: column;
    align-items: center;
    transition: transform .15s, border-color .15s;
  }
  .card:hover {
    transform: translateY(-2px);
    border-color: #00e5ff;
  }
  .big img {
    width: 180px;
    height: 180px;
    image-rendering: -webkit-optimize-contrast;
    border-radius: 8px;
  }
  .ico-row {
    margin-top: 14px;
    display: flex;
    align-items: end;
    gap: 10px;
    padding: 10px 14px;
    background: #061321;
    border-radius: 6px;
  }
  .ico { image-rendering: pixelated; }
  .meta {
    margin-top: 14px;
    text-align: center;
    width: 100%;
  }
  .num {
    color: #00e5ff;
    font-weight: 700;
    font-size: 12px;
    letter-spacing: 2px;
  }
  .cn {
    color: #fff;
    font-size: 15px;
    font-weight: 600;
    margin-top: 4px;
  }
  .desc {
    color: #b0bec5;
    font-size: 12px;
    margin-top: 6px;
    line-height: 1.4;
  }
  .file {
    margin-top: 10px;
    color: #607d8b;
    font-family: "Cascadia Code", Consolas, monospace;
    font-size: 11px;
  }
</style>
</head>
<body>
  <h1>iptv-desktop 图标候选</h1>
  <div class="sub">10 个科技范 ICO (256x256 + 16/32/48/64/128 多尺寸) — 选好后告诉我编号即可</div>
  <div class="grid">
""" + ''.join(cards) + """
  </div>
</body>
</html>
"""
    with open(PAGE, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'Wrote {PAGE}')
    print(f'Open in browser: file:///{PAGE.replace(chr(92), "/")}')


if __name__ == '__main__':
    main()