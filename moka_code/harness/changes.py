"""The workspace's uncommitted changes, read from git (read-only).

What ``/diff`` lists and shows: the working tree against ``HEAD``, untracked
files included. Nothing is tracked or recorded by moka, and git is never asked
to change anything. Renames are shown as a deletion plus an addition.
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

_TIMEOUT = 5.0
_MAX_COUNTED_BYTES = 1_000_000      # larger untracked files are listed without a line count
_CACHE_SECONDS = 3.0


@dataclass(frozen=True)
class Change:
    path: str
    status: str                     # "M" modified, "A" added, "D" deleted, "??" untracked
    added: Optional[int] = None     # None: binary or too large to count
    removed: Optional[int] = None

    def stats(self) -> str:
        if self.added is None or self.removed is None:
            return "binary"
        return f"+{self.added} −{self.removed}"


def _git(workspace: str, *args: str) -> Optional[subprocess.CompletedProcess]:
    """Run git in ``workspace``; ``None`` when git is missing or too slow."""
    try:
        return subprocess.run(
            ["git", "-c", "core.quotepath=false", *args], cwd=workspace, capture_output=True,
            text=True, errors="replace", timeout=_TIMEOUT,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except (OSError, subprocess.SubprocessError):
        return None


def _base(workspace: str) -> str:
    """What the working tree is compared with: ``HEAD``, or the empty tree in a
    repository without commits."""
    if (head := _git(workspace, "rev-parse", "--verify", "-q", "HEAD")) and head.returncode == 0:
        return "HEAD"
    empty = _git(workspace, "hash-object", "-t", "tree", os.devnull)
    return empty.stdout.strip() if empty and empty.returncode == 0 else "HEAD"


def _count_lines(workspace: str, path: str) -> Optional[int]:
    full = os.path.join(workspace, path)
    try:
        if os.path.getsize(full) > _MAX_COUNTED_BYTES:
            return None
        with open(full, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    if b"\0" in data:
        return None
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def _status_letter(code: str) -> str:
    if code == "??":
        return "??"
    return "D" if "D" in code else "A" if "A" in code else "M"


def changes(workspace: str) -> Optional[List[Change]]:
    """The changed files, by path; ``None`` outside a git repository (or without git)."""
    status = _git(workspace, "status", "--porcelain=v1", "-z", "-uall", "--no-renames")
    if status is None or status.returncode != 0:
        return None
    # ``status`` names paths from the repository root; the workspace may be a
    # folder inside it, and only that folder's changes are ours.
    prefix_run = _git(workspace, "rev-parse", "--show-prefix")
    prefix = prefix_run.stdout.strip() if prefix_run and prefix_run.returncode == 0 else ""
    base = _base(workspace)
    counted: Dict[str, Tuple[Optional[int], Optional[int]]] = {}
    numstat = _git(workspace, "diff", base, "--numstat", "--no-renames", "--relative")
    for line in (numstat.stdout.splitlines() if numstat and numstat.returncode == 0 else []):
        added, removed, path = line.split("\t", 2)
        counted[path] = (None, None) if added == "-" else (int(added), int(removed))
    found = []
    for entry in status.stdout.split("\0"):
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if not path.startswith(prefix):
            continue
        path = path[len(prefix):]
        if code == "??":
            lines = _count_lines(workspace, path)
            found.append(Change(path, "??", lines, 0 if lines is not None else None))
        else:
            added, removed = counted.get(path, (0, 0))
            found.append(Change(path, _status_letter(code), added, removed))
    return sorted(found, key=lambda change: change.path)


_cache: Dict[str, Tuple[float, Optional[List[Change]]]] = {}


def cached_changes(workspace: str) -> Optional[List[Change]]:
    """``changes`` for places that ask often (the ``/`` menu): a few seconds old at most."""
    now = time.monotonic()
    stamp, found = _cache.get(workspace, (0.0, None))
    if stamp and now - stamp < _CACHE_SECONDS:
        return found
    found = changes(workspace)
    _cache[workspace] = (now, found)
    return found


def _context_flag(context) -> str:
    """git's option for ``diff_context``: N lines, the enclosing function, or the file."""
    if context == "function":
        return "--function-context"
    return f"-U{1_000_000 if context == 'all' else context}"


def diff_text(workspace: str, path: Optional[str] = None, context=3) -> str:
    """The unified diff of one file (or of everything) against ``HEAD``, untracked
    files included, without colour. ``context``: lines around each change (an int),
    ``"function"`` for the whole enclosing function, ``"all"`` for the whole file."""
    untracked = [c.path for c in (changes(workspace) or []) if c.status == "??"]
    parts = []
    if path is None or path not in untracked:
        tracked = _git(workspace, "diff", _base(workspace), "--no-renames", "--no-color",
                       "--relative", "--no-ext-diff", _context_flag(context), *(["--", path] if path else []))
        if tracked and tracked.returncode == 0:
            parts.append(tracked.stdout)
    for new in untracked if path is None else ([path] if path in untracked else []):
        added = _git(workspace, "diff", "--no-index", "--no-color", "--no-ext-diff",
                     "--", os.devnull, new)
        if added and added.stdout:           # exit status 1 only means "they differ"
            parts.append(added.stdout)
    return "".join(parts)
