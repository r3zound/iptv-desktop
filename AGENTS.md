# AGENTS.md -- iptv-desktop

## Project purpose

一个 Windows 桌面客户端,功能:

- 从公共 m3u 源(Collect-IPTV + iptv-org + 可选 Guovin fork)拉取 IPTV 播放列表
- 4 层过滤:URL 黑名单 + 排除非中文 + 排除地域屏蔽 + 全量 HEAD 探活
- 过滤后的结果交给 PotPlayer 播放

不依赖任何第三方库 — 纯 Python 3.8.10 标准库。

## 设计约束

| 维度 | 决策 |
|---|---|
| 数据流 | 启动时同步拉取;3 个源(Collect-IPTV 主 + iptv-org 备 + 可选 Guovin fork)。每源 15s 预算。 |
| 播放器 | 自动检测 PotPlayer(继承自上游设计)。 |
| UI | 命令行模式 — cmd 窗口显示进度(加载配置 → 拉源 → 过滤 → 探活 → 保存 → 启动)。 |
| 缓存 | exe 同目录 `cache/`,存放 `best_sorted.m3u` + `meta.json` + 日志(`iptv-desktop.log`)。 |
| 频道分层 | 6 层:CCTV (1) → 31 省卫视 (2) → 热门地方 (3) → 港/澳/台 (4) → 其他中文 (5) → 外语 (6)。第 1 层按 CCTV 编号排。 |
| 过滤顺序 | URL 黑名单 → exclude_non_chinese(默认开)→ exclude_geo_blocked(默认开)→ full HEAD probe。 |
| 缓存位置 | exe 同目录 `cache/`。 |
| 源格式 | 默认 `.m3u`;`source.ini` 的 `[sources.*]` 段可覆盖 url/format/mirrors。 |

## Win7 SP1 兼容要求

> **所有代码必须兼容 Python 3.8.10**(3.8 最后版本,Windows binaries 官方支持,`python38.dll` 不依赖 Win8+ API set)。

### 3.8 不可用

- `str.removeprefix()` / `str.removesuffix()`(3.9+)
- `dict1 | dict2` 合并(3.9+)
- `isinstance(x, list[int])` 泛型语法(3.9+)
- `zoneinfo`(3.9+)
- `functools.cache`(3.9+)
- `match` 语句(3.10+)
- `tomllib`(3.11+)
- `typing.TypeAlias` 语句形式

### 3.8 必须

- 每个模块顶部 `from __future__ import annotations`
- 用 `typing.List` / `typing.Dict` / `typing.Optional` / `typing.Union`,不用 PEP 585 内建泛型

## 构建环境

- Python **3.8.10**(embeddable)
- PyInstaller **5.13.2**
- Hidden imports:`encodings.idna`、`stringprep`、`encodings.punycode`、`subprocess`(其中大部分为标准库)

## 目录布局(按"五类归档"约定)

```
iptv-desktop/
|-- AGENTS.md                  <- 本文件
|-- README.md                  <- 面向用户
|-- tools/                     <- 源码(可复用)
|   |-- iptv-desktop.py        <- 主入口
|   |-- build.bat              <- PyInstaller 封装
|   |-- make_portable.py       <- dist -> portable zip
|   |-- source.ini             <- 配置模板(复制到 dist)
|   `-- core/
|       |-- __init__.py
|       |-- source.py          <- source.ini 解析(多源)
|       |-- fetcher.py         <- 3-tier mirror 拉取
|       |-- cache.py           <- cache/m3u + meta.json I/O
|       |-- player.py          <- PotPlayer 检测 + 启动
|       |-- playlist.py        <- m3u 解析 / 序列化
|       |-- channel_name.py    <- 归一化 + 6 层分类
|       |-- merge.py           <- 多源去重 + 分层排序 + cap
|       |-- filters.py         <- URL 黑名单 + 分层探活(host-aware dedup)
|       `-- progress.py        <- ConsoleSink 进度输出
|-- assets/
|   `-- icons/
|       `-- 10-globe.ico       <- 主图标,16/32/48/64/128/256 多尺寸
|-- cache/                     <- 运行时缓存(首次运行后生成)
|-- archive/                   <- 一次性脚本(按日期归档)
`-- dist/                      <- 构建产物(被 .gitignore 排除)
```

## 用户偏好

- 纯标准库 — 运行时**零第三方依赖**
- Win7 SP1 必须工作(硬要求,不是 nice-to-have)
- 不在聊天/文档/日志里写 token、API key、密码
- 不自动装软件 — 工具缺失时先告诉用户,给安装命令,等用户确认再装

## 图标选择

`assets/icons/10-globe.ico` — 数字地球节点风格(经纬网格 + 节点连线),
呼应工具"聚合多源数据"的核心能力。
其他 9 个候选已删除,不再保留。