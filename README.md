# iptv-desktop

A personal **single-purpose**, **console-mode**, **portable** Windows
client that fetches IPTV playlists from public m3u feeds, applies
4-layer filtering (URL blocklist + non-Chinese filter + geo-blocked
filter + full HEAD probe), and hands the result to PotPlayer.

The "r3zound" suffix is a personal signature.

This is a fork of `collect-iptv-desktop` (which is now archived at
`D:\PC-fix\collect-iptv-desktop\`). The renaming preserves the
feature set but adds a clear personal-ownership marker.

## What it does

1. Reads `source.ini` for upstream sources + filter rules.
2. Fetches each enabled source IN PARALLEL (`Collect-IPTV` +
   `iptv-org` + optional `Guovin/iptv-api` fork).
3. Parses each source's m3u into a list of channels.
4. Applies filters IN ORDER:
   - **URL blocklist**: regex against known-dead hosts (`jmp2.uk`,
     `cdn-globecast.akamaized.net`, `streamlock.net`,
     `39.134.*.*:8080`, etc.).
   - **exclude_non_chinese**: drops any channel classified as tier 6
     (pure foreign) by `core/channel_name.py`.
   - **exclude_geo_blocked**: drops channels whose name contains
     `[Geo-blocked]` (iptv-org marks these as unreachable).
   - **full HEAD probe**: HEAD every URL with 1.5s timeout / 16-way
     concurrency. Drop any that don't return 200/206.
5. Deduplicates by URL and by normalized channel name.
6. Sorts by tier:
   - **CCTV** (tier 1) sorted by **numeric** order (CCTV-4K first,
     then CCTV1..17, then variants).
   - **31 province satellites** (tier 2) alphabetical by province.
   - **Hot local** (tier 3) alphabetical.
   - **HK / Macau / TW** (tier 4) alphabetical.
   - **Other Chinese** (tier 5) alphabetical.
   - **Foreign** (tier 6) -- dropped by default.
7. Caps at 3 URLs per channel (configurable).
8. Writes `cache/best_sorted.m3u` + `meta.json`.
9. Launches PotPlayer with the merged file.

UI is **silent on the GUI side** -- no cmd window, no MessageBox --
but the tool prints a small progress log to its console window while
running. Errors are surfaced via MessageBox.

## Measured quality (V6 of the archived V1-V6 lineage)

| Metric | Value (100-channel probe) |
|---|---|
| playable | **47.9%** |
| reachable | **46.9%** |
| dead | **5.2%** |
| playable + reachable | **94.8%** |

(vs. **53% dead** for the unfiltered upstream -- a 90% reduction in
dead links.)

## Use

1. Copy `dist/iptv-desktop-portable\` anywhere on your machine.
2. Double-click `iptv-desktop.exe`.
3. Wait ~1-2 minutes (full probe on ~10k channels).
4. PotPlayer opens with the filtered playlist.

CLI flags:
- `iptv-desktop.exe` -- default, console progress shown
- `iptv-desktop.exe --silent` -- no console, no log

## File layout (portable folder)

```
iptv-desktop-portable\
  iptv-desktop.exe       <- double-click
  *.dll / *.pyd / *.zip  <- Python 3.8 + PyInstaller runtime
  source.ini             <- editable config
  使用说明.txt            <- end-user readme (in Chinese)
  cache\                 <- populated on first run:
    best_sorted.m3u      <- filtered, tier-sorted playlist
    meta.json            <- {saved_at, source_url, channel_count, by_tier}
    iptv-desktop.log     <- last-run log (errors only)
```

## Configuration (`source.ini`)

```ini
[sources.collect-iptv]
url = https://cdn.jsdelivr.net/gh/zilong7728/Collect-IPTV@main/best_sorted.{ext}
format = m3u
mirrors = https://raw.githubusercontent.com/zilong7728/Collect-IPTV/main/best_sorted.{ext}|https://zilong7728.github.io/Collect-IPTV/best_sorted.{ext}
enabled = 1

[sources.iptv-org]
url = https://iptv-org.github.io/iptv/index.m3u
enabled = 1

[sources.guovin-fork]
url = https://github.com/r3zound/iptv-api/releases/download/playlist-latest/result.m3u
enabled = 1

[filters]
exclude_url_pattern =
exclude_non_chinese = 1
exclude_geo_blocked = 1
probe_mode = full
probe_timeout = 1.5

[merge]
per_channel_cap = 3

[player]
path =
```

See `source.ini` comments for the full set of options.

## Build (from source)

```cmd
cd tools
build.bat
python make_portable.py
```

Output: `dist\iptv-desktop-portable\` + `dist\iptv-desktop-portable.zip`.

Requires Python 3.8.10 (embeddable) + PyInstaller 5.13.2 at
`C:\Python38\`. Targets Win7 SP1 x64.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| "All mirrors failed" dialog | Internet down or GitHub + CDN both blocked | Check `cache\iptv-desktop.log`; PotPlayer still opens with last cached playlist |
| "PotPlayer not found" | Not installed or unusual install path | Edit `source.ini` `[player] path=` to the explicit exe |
| exe crashes silently on Win7 | Python runtime missing Win7-compatible APIs | Use the bundled portable folder (already built on Python 3.8.10) |
| Full probe takes too long | Normal -- 11000 channels @ 1.5s / 16-way = 1-2 min worst case | Edit `source.ini` `[filters] probe_mode = sample` for ~5s sampling |
| No log file | `cache\` doesn't exist or is read-only | Run the exe once with write permission to its own folder |

## License

Project layout and source code: AGPL-3.0-or-later (mirrors the
upstream spirit; this is a personal-use tool).
Upstream playlist content: Apache-2.0 (Collect-IPTV).