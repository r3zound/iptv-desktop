# iptv-desktop

轻量级、单用途、命令行模式、绿色版 Windows 客户端 — 抓取公共 m3u IPTV
播放列表,做 4 层过滤(URL 黑名单 + 排除非中文 + 排除地域屏蔽 + 全量
HEAD 探活),交给 PotPlayer 播放。

> 工作流:启动 → 拉源 → 过滤 → 探活 → 合并 → 写 m3u → 拉起 PotPlayer
> UI 默认走控制台,跑完自动关闭;失败有提示。

## 它做什么

1. 读取 `source.ini` 获取上游源 + 过滤规则。
2. **并发**拉取每个启用的源(`Collect-IPTV` + `iptv-org` + 可选 Guovin fork)。
3. 解析每个源的 m3u 为频道列表。
4. 按顺序应用过滤器:
   - **URL 黑名单**:正则匹配已知死链主机(`jmp2.uk`、
     `cdn-globecast.akamaized.net`、`streamlock.net`、`39.134.*.*:8080` 等)。
   - **exclude_non_chinese**:剔除分类为第 6 层(纯外语)的频道。
   - **exclude_geo_blocked**:剔除频道名含 `[Geo-blocked]` 的(已被上游标不可达)。
   - **full HEAD probe**:对每条 URL 用 1.5s 超时 / 16-way 并发做 GET-Range
     探测,丢掉非 200/206 的。
5. 按 URL 和归一化频道名去重。
6. 按层级排序:
   - **CCTV**(tier 1)按数字顺序(CCTV-4K → CCTV1..17 → 变体)。
   - **31 个省级卫视**(tier 2)按省份字母。
   - **热门地方台**(tier 3)字母序。
   - **港澳台**(tier 4)字母序。
   - **其他中文**(tier 5)字母序。
   - **外语**(tier 6)默认剔除。
7. 每个频道最多保留 3 个 URL(可配)。
8. 写 `cache/best_sorted.m3u` + `meta.json`。
9. 拉起 PotPlayer 加载合并后的播放列表。

## 使用方法

1. 把 `iptv-desktop-portable\` 文件夹复制到任意位置。
2. 双击 `iptv-desktop.exe`。
3. 等 ~1-2 分钟(全量探活约 1 万频道)。
4. PotPlayer 自动打开过滤后的播放列表。

CLI 标志:

- `iptv-desktop.exe` — 默认,显示控制台进度。
- `iptv-desktop.exe --silent` — 不显示控制台,不写日志。

## 便携目录布局

```
iptv-desktop-portable\
  iptv-desktop.exe       <- 双击启动
  *.dll / *.pyd / *.zip  <- Python 3.8 + PyInstaller 运行时
  source.ini             <- 可编辑配置
  使用说明.txt            <- 终端用户说明(中文)
  cache\                 <- 首次运行后生成:
    best_sorted.m3u      <- 过滤后的播放列表
    meta.json            <- {saved_at, source_url, channel_count, by_tier}
    iptv-desktop.log     <- 最近一次运行日志(仅错误)
```

## 配置(`source.ini`)

```ini
[sources.collect-iptv]
url = https://cdn.jsdelivr.net/gh/zilong7728/Collect-IPTV@main/best_sorted.{ext}
format = m3u
mirrors = https://raw.githubusercontent.com/zilong7728/Collect-IPTV/main/best_sorted.{ext}|https://zilong7728.github.io/Collect-IPTV/best_sorted.{ext}
enabled = 1

[sources.iptv-org]
url = https://iptv-org.github.io/iptv/index.m3u
enabled = 1

; 选填: 自己 fork Guovin/iptv-api 跑 Actions 后填 release URL
; 留空则该源自动跳过
[sources.guovin-fork]
url =
format = m3u
mirrors =
enabled = 0

[filters]
exclude_url_pattern =
exclude_non_chinese = 1
exclude_geo_blocked = 1
probe_mode = full
probe_timeout = 1.5
probe_concurrency = 16
probe_min_success_rate = 0.20

[merge]
per_channel_cap = 3

[player]
path =
```

完整选项(中英对照说明)直接写在 `source.ini` 注释里 — 每个
`key = value` 后面跟 `; 中文说明`。

## 构建(从源码)

```cmd
cd tools
build.bat
python make_portable.py
```

输出:`dist\iptv-desktop-portable\` + `dist\iptv-desktop-portable.zip`。

依赖:

- Python **3.8.10**(embeddable,Win7 兼容) — 编辑 `tools\build.bat`
  顶部的 `set PY=` 行指向你的 Python 3.8 安装目录。
- PyInstaller **5.13.2**。

目标平台:Win7 SP1 x64 / Win10 / Win11。

## 故障排查

| 症状 | 原因 | 解决 |
|---|---|---|
| "All mirrors failed" 弹窗 | 网络不通 / GitHub + CDN 全被封 | 看 `cache\iptv-desktop.log`;PotPlayer 仍会用上次缓存打开 |
| "PotPlayer not found" | 没装或装在不常见路径 | 编辑 `source.ini` `[player] path=` 写完整 exe 路径 |
| Win7 上静默崩溃 | 缺少 Win7 兼容 API | 用自带的 portable 目录(已用 Python 3.8.10 编译) |
| 全量探活太慢 | 正常 — 11000 频道 @ 1.5s / 16-way 最坏 1-2 分钟 | 编辑 `source.ini` `[filters] probe_mode = sample` 改 ~5s 抽样 |
| 没有日志文件 | `cache\` 不存在或只读 | 给 exe 同目录写权限后重试 |

## 性能说明

100 频道抽样:**47.9% 可播,46.9% 可达,5.2% 死链**(共 94.8% 干净)。

未过滤上游 ~53% 死链 — 过滤后减少 90% 死链。

## 性能优化

2026-09-17 加入两阶段探活(host-aware dedup):先按 host 去重(死 host
整组跳过),再对 live host 下的 URL 逐个测。生产场景 1.06-2.5x 提速,
精度 ≥ 旧版。

## 许可

本工程代码:AGPL-3.0-or-later(沿用上游精神,本工具为个人使用)。
上游播放列表内容:Apache-2.0(Collect-IPTV)。