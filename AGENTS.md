# AGENTS.md -- iptv-desktop

## Project purpose

A personal desktop client (forked from `collect-iptv-desktop`)
that fetches IPTV playlists from public m3u feeds, applies 4-layer
filtering (URL blocklist + exclude_non_chinese + exclude_geo_blocked
+ full HEAD probe), and hands the result to PotPlayer.

It is the "lazy" counterpart to the local multi-source aggregator in
`D:\PC-fix\iptv-tool\` -- that tool does local probing; this one
trusts the upstream `Collect-IPTV` and `iptv-org` m3u files plus an
optional `Guovin/iptv-api` fork, and runs a full HEAD probe on every
URL before delivering the final playlist.

The "r3zound" suffix is a personal signature (the GitHub user).

## Design constraints (from user decisions, 2026-09-16 / 2026-09-17)

| Axis | Decision |
|---|---|
| Data flow | Sync fetch on launch; 3 sources (Collect-IPTV primary + iptv-org secondary + optional Guovin fork). Per-source 15s budget. |
| Player | Detect PotPlayer (kept verbatim from collect-iptv-desktop). |
| UI | Console mode -- cmd window shows progress (load config, fetch, filter, probe, save, launch). |
| Cache | exe sibling `cache/`, holds `best_sorted.m3u` + `meta.json` + log file (`iptv-desktop.log`). |
| Channel handling | Tier-based: CCTV (1) -> 31 provinces (2) -> hot local (3) -> HK/MO/TW (4) -> other CN (5) -> foreign (6). Tier 1 sorted by CCTV number. |
| Filter order | URL blocklist -> exclude_non_chinese (default ON) -> exclude_geo_blocked (default ON) -> full HEAD probe. |
| Cache location | exe sibling `cache/`. |
| Source format | Default `.m3u`; `source.ini` overrides url/format/mirrors via `[sources.*]` sections. |

## Win7 SP1 compatibility requirements

> **All code MUST be compatible with Python 3.8.10** (last 3.8 release
> with official Windows binaries, no Win8+ API set dependencies in `python38.dll`).

### Banned on 3.8

- `str.removeprefix()` / `str.removesuffix()` (3.9+)
- `dict1 | dict2` merge (3.9+)
- `isinstance(x, list[int])` generic syntax (3.9+)
- `zoneinfo` (3.9+)
- `functools.cache` (3.9+)
- `match` statement (3.10+)
- `tomllib` (3.11+)
- `typing.TypeAlias` statement form

### Required on 3.8

- `from __future__ import annotations` at top of every module
- `typing.List` / `typing.Dict` / `typing.Optional` / `typing.Union` instead of PEP 585 built-ins

## Build environment

- Python **3.8.10** (embeddable)
- PyInstaller **5.13.2**
- Hidden imports: `encodings.idna`, `stringprep`, `encodings.punycode`, `subprocess` (already standard)

## File layout (per user's 5-class archive convention)

```
iptv-desktop/
|-- AGENTS.md                  <- this file
|-- README.md                  <- user-facing
|-- docs/                      <- long-form docs
|-- tools/                     <- source code (reusable)
|   |-- iptv-desktop.py        <- main entry
|   |-- build.bat              <- PyInstaller wrapper
|   |-- make_portable.py       <- dist -> portable zip
|   |-- source.ini             <- config template (copied to dist)
|   `-- core/
|       |-- __init__.py
|       |-- source.py          <- source.ini parser (multi-source)
|       |-- fetcher.py         <- 3-tier mirror fetch
|       |-- cache.py           <- cache/m3u + meta.json I/O
|       |-- player.py          <- PotPlayer detect + launch
|       |-- playlist.py        <- m3u parse / serialize
|       |-- channel_name.py    <- normalize + 6-tier classify
|       |-- merge.py           <- multi-source dedup + tier sort + cap
|       |-- filters.py         <- URL blocklist + tier probe
|       `-- progress.py        <- ConsoleSink for progress output
|-- cache/                     <- runtime cache (created on first run)
`-- archive/                   <- one-off scripts (per-date)
```

## User preferences (re-stated from memory)

- Pure standard library -- **zero third-party deps** at runtime
- Win7 SP1 must work (this is a HARD requirement, not a nice-to-have)
- Never write tokens / API keys / passwords in chat/docs/logs
- Don't auto-install software -- if a tool is missing, tell the user and
  suggest the install command, then wait for approval

## Naming history

- 2026-09-16 to 2026-09-17: `collect-iptv-desktop` (V1-V6)
- 2026-09-17 onwards: **`iptv-desktop`** (personal signature)

The previous project is kept archived at `D:\PC-fix\collect-iptv-desktop\`
with a README noting it has been superseded.