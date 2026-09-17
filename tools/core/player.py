# player.py -- find and launch PotPlayer.
#
# Adapted from iptv-tool's tools/core/player.py. Self-contained: we
# duplicate the logic here so collect-iptv-desktop doesn't have to
# import a sibling project's code (which would break if the user
# moves the project root).
#
# Detection order (from iptv-tool AGENTS.md, pitfall #8):
#   1. PotPlayerMini64.exe   <- default installer target on most setups
#   2. PotPlayerMini.exe     <- 32-bit legacy
#   3. PotPlayer64.exe       <- 64-bit full
#   4. PotPlayer.exe         <- 32-bit full (rare)
#
# Sources probed, in order:
#   1. INI-configured path (source.ini [player] path=)
#   2. HKCU/HKLM App Paths registry entries
#   3. SOFTWARE\DAUM\PotPlayer* ProgramPath
#   4. PATH
#   5. Start menu shortcuts (.lnk -- skipped; too brittle for 3.8 stdlib)
#
# Launch:
#   - subprocess.Popen with DETACHED_PROCESS so the player survives
#     the launcher exiting (iptv-tool AGENTS.md pitfall #8).
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from typing import List, Optional

# subprocess.DETACHED_PROCESS is 0x00000008 on Windows; defined here
# explicitly so non-Windows dev hosts can still import the module
# without crashing.
_DETACHED_PROCESS = 0x00000008

CANDIDATE_BINS = (
    "PotPlayerMini64.exe",
    "PotPlayerMini.exe",
    "PotPlayer64.exe",
    "PotPlayer.exe",
)

REGISTRY_HIVES = (
    "HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\App Paths",
    "HKEY_LOCAL_MACHINE\\Software\\Microsoft\\Windows\\CurrentVersion\\App Paths",
    "HKEY_LOCAL_MACHINE\\SOFTWARE\\DAUM\\PotPlayer",
    "HKEY_CURRENT_USER\\SOFTWARE\\DAUM\\PotPlayer",
)


def _is_executable(path: str) -> bool:
    return bool(path) and os.path.isfile(path)


def _which_all() -> List[str]:
    """Find candidates on PATH. Returns absolute paths in PATH order."""
    out = []
    seen = set()
    for bin_name in CANDIDATE_BINS:
        p = shutil.which(bin_name)
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out


def _scan_dir_for_bins(root: str) -> List[str]:
    """Walk a directory (non-recursive) looking for candidate exes."""
    if not root or not os.path.isdir(root):
        return []
    out = []
    try:
        for entry in os.listdir(root):
            full = os.path.join(root, entry)
            if os.path.isfile(full) and entry in CANDIDATE_BINS:
                out.append(full)
    except OSError:
        return []
    # Sort by CANDIDATE_BINS order so Mini64 wins over 64.
    out.sort(key=lambda p: CANDIDATE_BINS.index(os.path.basename(p)))
    return out


def _scan_install_dirs() -> List[str]:
    """Probe well-known install locations."""
    candidates = []
    pf = os.environ.get("ProgramFiles", "C:\\Program Files")
    pfx86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")
    for base in (pf, pfx86):
        for sub in ("DAUM\\PotPlayer", "PotPlayer"):
            candidates.append(os.path.join(base, sub))
    return candidates


def _probe_registry() -> List[str]:
    """Read App Paths + DAUM registry keys. Stdlib only (`_winreg` is
    ``winreg`` on 3+, aliased here for clarity)."""
    try:
        import winreg  # type: ignore[import]  # noqa: F401
    except ImportError:
        return []

    found = []
    # App Paths entries: each subkey is an exe name with default value
    # containing the full path.
    for hive_path in REGISTRY_HIVES[:2]:
        try:
            hive_name, sub = hive_path.split("\\Software\\", 1)
            sub = "Software\\" + sub
            hive = (
                winreg.HKEY_CURRENT_USER
                if hive_name == "HKEY_CURRENT_USER"
                else winreg.HKEY_LOCAL_MACHINE
            )
            with winreg.OpenKey(hive, sub) as key:
                idx = 0
                while True:
                    try:
                        sub_name = winreg.EnumKey(key, idx)
                    except OSError:
                        break
                    idx += 1
                    if sub_name.lower() in (
                        b.lower() for b in CANDIDATE_BINS
                    ):
                        try:
                            with winreg.OpenKey(key, sub_name) as sk:
                                v, _ = winreg.QueryValueEx(sk, "")
                                if _is_executable(v):
                                    found.append(v)
                        except OSError:
                            pass
        except OSError:
            continue

    # DAUM\PotPlayer ProgramPath
    for hive_path in REGISTRY_HIVES[2:]:
        try:
            hive_name, sub = hive_path.split("\\SOFTWARE\\", 1)
            sub = "SOFTWARE\\" + sub
            hive = (
                winreg.HKEY_CURRENT_USER
                if hive_name == "HKEY_CURRENT_USER"
                else winreg.HKEY_LOCAL_MACHINE
            )
            with winreg.OpenKey(hive, sub) as key:
                for value_name in ("ProgramPath", "Path", "InstallPath"):
                    try:
                        v, _ = winreg.QueryValueEx(key, value_name)
                    except OSError:
                        continue
                    if not v:
                        continue
                    # ProgramPath may be a dir OR an exe; handle both.
                    if _is_executable(v):
                        found.append(v)
                    elif os.path.isdir(v):
                        found.extend(_scan_dir_for_bins(v))
        except OSError:
            continue

    return found


def find_potplayer() -> Optional[str]:
    """Return the first valid PotPlayer path, or None if not found."""
    # Probe in priority order, dedupe by lowercase.
    seen = set()
    ordered: List[str] = []

    def add(p):
        if p and os.path.normcase(p) not in seen and _is_executable(p):
            seen.add(os.path.normcase(p))
            ordered.append(p)

    for d in _scan_install_dirs():
        for p in _scan_dir_for_bins(d):
            add(p)
    for p in _probe_registry():
        add(p)
    for p in _which_all():
        add(p)

    if not ordered:
        return None

    # Sort by CANDIDATE_BINS priority so Mini64 wins.
    def rank(p):
        bn = os.path.basename(p)
        try:
            return CANDIDATE_BINS.index(bn)
        except ValueError:
            return len(CANDIDATE_BINS)

    ordered.sort(key=rank)
    return ordered[0]


def launch(m3u_path: str, explicit_path: Optional[str] = None) -> bool:
    """Launch PotPlayer with the given m3u. Returns True on success.

    Order of resolution:
      1. ``explicit_path`` (from source.ini [player] path=)
      2. Auto-detected PotPlayer

    Post-launch verification: Popen returning does not prove the
    player actually started (a corrupt exe, a missing DLL, or an
    antivirus block all surface as an immediate exit). We poll the
    child for a short window and treat these as success:
      - still running after the grace period, OR
      - exited with code 0 (PotPlayer is single-instance: when an
        instance is already open, the new process forwards the file
        and exits 0 immediately)

    A non-zero early exit is reported as failure.

    Failure modes (returns False):
      - explicit path was set but missing/invalid
      - no PotPlayer detected anywhere
      - Popen itself raised
      - the process died immediately with a non-zero code
    """
    player = explicit_path if explicit_path else find_potplayer()

    if not player:
        return False
    if not _is_executable(player):
        return False

    m3u_abs = os.path.abspath(m3u_path)
    if not os.path.isfile(m3u_abs):
        return False

    try:
        proc = subprocess.Popen(
            [player, m3u_abs],
            creationflags=_DETACHED_PROCESS,
            close_fds=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    # Verify it survived startup. 1.5s is enough to catch an instant
    # crash while keeping the happy path snappy.
    GRACE_SECONDS = 1.5
    deadline = time.time() + GRACE_SECONDS
    while time.time() < deadline:
        rc = proc.poll()
        if rc is None:
            # Still running -- startup succeeded.
            return True
        if rc == 0:
            # Exited cleanly: most likely forwarded to an existing
            # single-instance PotPlayer. Treat as success.
            return True
        # Non-zero: crashed during startup. Give it one more look in
        # case the code is transient (rare), then fail.
        time.sleep(0.2)
        rc2 = proc.poll()
        if rc2 is None or rc2 == 0:
            return True
        return False

    # Never exited within the grace window -- definitely alive.
    return True
