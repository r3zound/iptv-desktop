# progress.py -- console progress reporter for collect-iptv-desktop.
#
# Why console (not Tkinter):
#   - Win7 SP1 + embeddable Python 3.8.10 has NO tkinter module
#     (embeddable distributions strip Tcl/Tk).
#   - Installing a full Python would violate the "Win7 SP1 + zero
#     setup" portability goal.
#   - Pure-stdlib console output works on every Windows version.
#
# Output format (after each event):
#   [HH:MM:SS] STATUS: <message>
#   then per-source lines:
#     [OK ] collect-iptv    782 channels   in 5.6s
#     [ERR] guovin-fork     -              timeout: ...
#   then a summary box:
#     ==================================================================
#     merged: 10289 channels
#       tier 1 (CCTV)        49
#       tier 2 (Satellite)   41
#       tier 4 (HK/MO/TW)   52
#       tier 5 (Other CN)  10143
#     ==================================================================
#
# Threading: stdout writes are serialized through a single lock so
# the worker pool's events stay in order.
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Dict, Optional


# ---------------- availability check ---------------------------------

def _has_console() -> bool:
    """Return True if stdout is attached to a real console (cmd.exe).

    PyInstaller --windowed builds have no console; --console builds
    do. Used to decide whether the console sink should print anything.
    """
    try:
        # sys.stdout.isatty() returns False when redirected to a file
        # (which is the case in the smoke tests). We still want to
        # print in that mode, so only skip if sys.stdout is None.
        return sys.stdout is not None
    except Exception:
        return False


# ---------------- the sink ---------------------------------------------

class ConsoleSink:
    """Sink that prints events to stdout, one line per event.

    Thread-safe via a lock so worker threads don't garble output.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._t0 = time.time()
        self._lines: Dict[str, dict] = {}  # source_name -> info
        self._expected_sources = 0
        self._status = ""
        self._merged_count = 0
        self._tier_counts: Dict[int, int] = {}
        self._closed = False
        self._ok_count = 0

    def set_expected_sources(self, n: int) -> None:
        with self._lock:
            self._expected_sources = max(0, n)

    def emit(self, event) -> None:
        kind = event[0]
        with self._lock:
            if kind == "status":
                self._status = event[1]
                self._print_status_line()
            elif kind == "source_start":
                name = event[1]
                self._lines[name] = {
                    "status": "fetching",
                    "count": "",
                    "ms": "",
                    "err": "",
                }
                self._print_source_line(name)
            elif kind == "source_done":
                name, count, ms = event[1], event[2], event[3]
                self._lines[name] = {
                    "status": "ok",
                    "count": "%d channels" % count,
                    "ms": "in %.1fs" % (ms / 1000.0),
                    "err": "",
                }
                self._ok_count += 1
                self._print_source_line(name)
            elif kind == "source_fail":
                name, err = event[1], event[2]
                self._lines[name] = {
                    "status": "fail",
                    "count": "-",
                    "ms": "",
                    "err": (err or "")[:40],
                }
                self._print_source_line(name)
            elif kind == "merge":
                self._merged_count = event[1]
                print("    merged: %d channels so far" % self._merged_count)
            elif kind == "tier":
                tier, count = event[1], event[2]
                self._tier_counts[tier] = count
            elif kind == "done":
                _, m3u_path, count = event[:3]
                self._merged_count = count
                self._status = "DONE: %d channels -> %s" % (
                    count, os.path.basename(m3u_path)
                )
                self._print_status_line()
                # Print the tier breakdown exactly once, at the end --
                # per-tier events arrive incrementally, so printing on
                # each of them would repeat the whole box N times.
                self._print_summary()
            elif kind == "error":
                _, title, body = event
                self._status = "ERROR: " + title
                self._print_status_line()
                # Print body to stderr so it's visible even if user
                # only looks at the success lines.
                print("\n" + body + "\n", file=sys.stderr)
            elif kind == "warning":
                _, title, body = event
                print("\n[WARN] " + title + "\n" + body + "\n")
            elif kind == "close":
                self._closed = True
                # Final blank line so the next shell prompt is clean.
                print()

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def wait(self) -> None:
        pass

    # ---- internal pretty-printers (caller holds the lock) -----

    def _print_status_line(self) -> None:
        ts = time.strftime("%H:%M:%S")
        print("[%s] %s" % (ts, self._status))

    def _print_source_line(self, name: str) -> None:
        info = self._lines.get(name)
        if not info:
            return
        if info["status"] == "fetching":
            tag = "..."
            tail = ""
        elif info["status"] == "ok":
            tag = " OK"
            tail = "%s  %s" % (info["count"].ljust(14), info["ms"])
        elif info["status"] == "fail":
            tag = "ERR"
            tail = "%s  %s" % (info["count"].ljust(14), info["err"])
        else:
            tag = "?? "
            tail = ""
        print("    [%s] %-20s %s" % (tag, name, tail))

    def _print_summary(self) -> None:
        if not self._tier_counts and not self._merged_count:
            return
        tier_labels = {
            1: "CCTV",
            2: "Satellite",
            3: "Hot local",
            4: "HK/MO/TW",
            5: "Other CN",
            6: "Foreign",
        }
        bar = "=" * 60
        print(bar)
        print("merged: %d channels" % self._merged_count)
        for t in sorted(self._tier_counts.keys()):
            label = tier_labels.get(t, "tier %d" % t)
            print("  tier %d (%-12s): %5d" % (
                t, label, self._tier_counts[t]
            ))
        print(bar)


# ---------------- factory ---------------------------------------------

def make_sink(use_console: bool):
    """Return a sink instance based on whether console is requested.

    If ``use_console`` is False, returns NullSink (silent).
    If ``use_console`` is True but stdout is not attached to a real
    console, also returns NullSink.
    """
    if not use_console:
        from . import _NullSink  # local import to avoid circular
        return _NullSink()
    if not _has_console():
        from . import _NullSink
        return _NullSink()
    return ConsoleSink()


# NullSink is defined here (instead of collect-iptv.py) so the
# factory can import it without circular references.
class _NullSink:
    def emit(self, _event):
        pass

    def close(self):
        pass

    def wait(self):
        pass

    def set_expected_sources(self, n):
        pass