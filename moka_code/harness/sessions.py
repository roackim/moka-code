"""Saved conversations: the conversation file format and per-project sessions.

A conversation file is ``{"role": ..., "model": ..., "history": [...]}`` —
what ``/export`` writes and ``/import`` reads, and what every session is.
Sessions are saved automatically under
``~/.local/state/moka/sessions/<project>/`` (state, not cache: they are the
user's data, and cache folders may be wiped). Image references stay references
in a session (the image cache holds the bytes); ``/export`` embeds them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

History = List[Dict[str, Any]]


# -- conversation files --------------------------------------------------

def write(path: str | os.PathLike, role: str, history: History,
          model: Optional[str] = None) -> None:
    """Write a conversation file atomically (a crash never leaves half a file)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: Dict[str, Any] = {"role": role, "history": history}
    if model:
        data["model"] = model
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read(path: str | os.PathLike) -> Tuple[Optional[str], History]:
    """Read a conversation file: ``(role name or None, history)``.

    Also accepts a bare history array (old exports). Raises ``ValueError``
    with a user-facing reason when the file is not a conversation.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    role = data.get("role") if isinstance(data, dict) else None
    history = data.get("history") if isinstance(data, dict) else data
    if not isinstance(history, list):
        raise ValueError("expected a history array or conversation object")
    if role is not None and not isinstance(role, str):
        raise ValueError("role must be a string")
    for index, message in enumerate(history):
        if not isinstance(message, dict):
            raise ValueError(f"message {index}: expected an object")
        if "role" not in message:
            raise ValueError(f"message {index}: missing 'role' field")
    return role, history


# -- sessions ------------------------------------------------------------

def sessions_dir(workspace: str) -> Path:
    """This project's session folder: ``<name>-<hash of its path>``."""
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    resolved = str(Path(workspace).resolve())
    name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(resolved).name) or "root"
    digest = hashlib.sha256(resolved.encode()).hexdigest()[:8]
    return Path(base) / "moka" / "sessions" / f"{name}-{digest}"


def new_path(workspace: str) -> Path:
    """Where a new session of this project will be saved (not created yet)."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return sessions_dir(workspace) / f"{stamp}-{os.urandom(2).hex()}.json"


@dataclass
class Session:
    path: Path
    title: str          # the first thing the user asked
    messages: int
    model: Optional[str]
    modified: float


def _title(history: History) -> str:
    for message in history:
        if message.get("role") == "user" and message.get("source") != "tool":
            text = " ".join(str(message.get("content") or "").split())
            if text:
                return text
    return "(no message)"


def list_sessions(workspace: str) -> List[Session]:
    """This project's saved sessions, most recent first (unreadable ones skipped)."""
    folder = sessions_dir(workspace)
    if not folder.is_dir():
        return []
    found = []
    for path in folder.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            history = data["history"]
            found.append(Session(path, _title(history),
                                 sum(1 for m in history if m.get("role") in ("user", "assistant")),
                                 data.get("model"), path.stat().st_mtime))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return sorted(found, key=lambda s: s.modified, reverse=True)


def prune(workspace: str, keep: int) -> None:
    """Delete this project's sessions beyond the ``keep`` most recent."""
    for session in list_sessions(workspace)[max(0, keep):]:
        try:
            session.path.unlink()
        except OSError:
            pass
