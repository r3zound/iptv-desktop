# playlist.py -- parse + serialize m3u playlists.
#
# Handles the extended M3U format used by IPTV playlists:
#   #EXTM3U
#   #EXTINF:-1 tvg-id="..." tvg-name="..." tvg-logo="..." group-title="...",Display Name
#   http://stream.example.com/foo.ts
#   ...
#
# Also tolerates:
#   - missing tvg-* attributes (just name + url)
#   - leading whitespace before URL
#   - blank lines / unknown directives
#
# This is intentionally tolerant: a single malformed line must NOT
# abort the whole parse, because a 138k-channel source will have a few
# bad entries.
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Tuple


# Match "#EXTINF:<seconds> [<attrs>,]<name>". We don't trust the seconds
# (sources lie); we only use attrs + name.
_EXTINF_RE = re.compile(
    r'#EXTINF:(?P<seconds>-?\d+)(?P<attrs>\s+.*?)?,(?P<name>.*)$'
)

# Match a single key="value" attribute. tvg-* + group-title can be quoted
# or unquoted in the wild; we only support quoted for robustness.
_ATTR_RE = re.compile(r'(\w[\w-]*?)="([^"]*)"')


class Entry:
    """One #EXTINF + url pair."""

    __slots__ = (
        "tvg_id",
        "tvg_name",
        "tvg_logo",
        "group_title",
        "name",
        "url",
        "source",  # origin label for merge dedup
    )

    def __init__(
        self,
        tvg_id: str = "",
        tvg_name: str = "",
        tvg_logo: str = "",
        group_title: str = "",
        name: str = "",
        url: str = "",
        source: str = "",
    ) -> None:
        self.tvg_id = tvg_id
        self.tvg_name = tvg_name
        self.tvg_logo = tvg_logo
        self.group_title = group_title
        self.name = name
        self.url = url
        self.source = source

    def __repr__(self) -> str:
        return "Entry(name=%r, url=%r, source=%r)" % (
            self.name, self.url[:60], self.source
        )

    @property
    def display_name(self) -> str:
        """Prefer tvg-name, fall back to name."""
        return self.tvg_name or self.name or ""

    @property
    def is_valid(self) -> bool:
        return bool(self.url) and bool(self.display_name)


def _parse_attrs(attr_str: Optional[str]) -> dict:
    if not attr_str:
        return {}
    out = {}
    for m in _ATTR_RE.finditer(attr_str):
        out[m.group(1)] = m.group(2)
    return out


def parse(data: bytes, source: str = "") -> List[Entry]:
    """Parse raw m3u bytes into a list of Entries.

    Empty lines, unknown directives, and entries missing a URL are
    silently skipped.
    """
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        return []

    entries: List[Entry] = []
    pending_extinf: Optional[Entry] = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            pending_extinf = None
            continue

        if line.startswith("#EXTM3U"):
            pending_extinf = None
            continue

        if line.startswith("#EXTINF:"):
            m = _EXTINF_RE.match(line)
            if not m:
                pending_extinf = None
                continue
            attrs = _parse_attrs(m.group("attrs"))
            e = Entry(
                tvg_id=attrs.get("tvg-id", ""),
                tvg_name=attrs.get("tvg-name", ""),
                tvg_logo=attrs.get("tvg-logo", ""),
                group_title=attrs.get("group-title", ""),
                name=m.group("name").strip(),
                url="",
                source=source,
            )
            pending_extinf = e
            continue

        # Other #EXT-* directives: drop the pending extinf (we treat
        # these as alternative metadata lines that some sources put
        # before the URL).
        if line.startswith("#"):
            continue

        # URL line.
        if pending_extinf is None:
            continue
        pending_extinf.url = line
        if pending_extinf.is_valid:
            entries.append(pending_extinf)
        pending_extinf = None

    return entries


def serialize(entries: Iterable[Entry], header: Optional[str] = None) -> bytes:
    """Render entries back to m3u bytes. UTF-8 without BOM.

    ``header`` is an optional line written just after #EXTM3U (rare).
    """
    lines = ["#EXTM3U"]
    if header:
        lines.append(header)

    for e in entries:
        if not e.is_valid:
            continue
        # Build attribute block. Order is significant for some players
        # (PotPlayer is tolerant, but we keep stable order for diff'ing).
        attrs = []
        if e.tvg_id:
            attrs.append('tvg-id="%s"' % e.tvg_id)
        if e.tvg_name:
            attrs.append('tvg-name="%s"' % e.tvg_name)
        if e.tvg_logo:
            attrs.append('tvg-logo="%s"' % e.tvg_logo)
        if e.group_title:
            attrs.append('group-title="%s"' % e.group_title)
        attr_block = (" " + " ".join(attrs)) if attrs else ""
        lines.append("#EXTINF:-1%s,%s" % (attr_block, e.name))
        lines.append(e.url)

    # Trailing newline so PotPlayer doesn't complain.
    return ("\n".join(lines) + "\n").encode("utf-8")


def stat(entries: List[Entry]) -> Tuple[int, int]:
    """Return (total_entries, unique_groups). Cheap summary for logs."""
    groups = set()
    for e in entries:
        if e.group_title:
            groups.add(e.group_title)
    return len(entries), len(groups)