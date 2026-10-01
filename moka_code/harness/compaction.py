"""The conversation as readable text for the compaction summarizer.

The summarizer gets this transcript, never the stored entries: no ids, image
references, ``origin`` or native reasoning blocks. Two filters (``context.toml``):
reasoning is dropped, and each tool call becomes one line without its output.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

COMPACTION_MARKER_PREFIX = "[COMPACTION_SUMMARY]"

_COMMAND_CHARS = 120
_FAILURE_CHARS = 160
_FAILURES = ("Error:", "[TOOL DENIED]")


def _args(call: Dict[str, Any]) -> Dict[str, Any]:
    try:
        args = json.loads((call.get("function") or {}).get("arguments") or "{}")
    except (TypeError, ValueError):
        return {}
    return args if isinstance(args, dict) else {}


def _first_line(text: str, limit: int) -> str:
    line = (text.strip().splitlines() or [""])[0]
    return line if len(line) <= limit else line[:limit - 1] + "…"


def _one_line(name: str, args: Dict[str, Any], result: str) -> str:
    """One call as one line: its key arguments, and why it failed if it did."""
    path = args.get("path", "")
    if name == "read":
        offset, limit = args.get("offset") or 0, args.get("limit")
        span = (f" lines {offset + 1}–{offset + limit}" if limit
                else f" from line {offset + 1}" if offset else "")
        line = f"read {path}{span}"
    elif name == "write":
        content = args.get("content") or ""
        line = f"write {path} ({content.count(chr(10)) + 1 if content else 0} lines)"
    elif name == "edit":
        line = f"edit {path}"
    elif name == "bash":
        line = f"bash: {_first_line(str(args.get('command', '')), _COMMAND_CHARS)}"
        exit_code = re.search(r"\[exit:(-?\d+)\]\s*$", result)
        if exit_code:
            line += f" → exit {exit_code.group(1)}"
    else:
        line = f"{name} {_first_line(json.dumps(args, ensure_ascii=False), _COMMAND_CHARS)}"
    if result.startswith(_FAILURES):
        line += f" → {_first_line(result, _FAILURE_CHARS)}"
    return line


def transcript(history: List[Dict[str, Any]], *, filter_thoughts: bool,
               filter_tool_calls: bool) -> str:
    """``history`` as text for the summarizer (see the module docstring)."""
    results = {m.get("tool_call_id"): str(m.get("content") or "")
               for m in history if m.get("role") == "tool"}
    lines: List[str] = []
    for entry in history:
        role, content = entry.get("role"), entry.get("content") or ""
        if role == "user":
            lines.append(content if entry.get("source") == "tool" else f"User: {content}")
        elif role == "system":
            lines.append(f"Note: {content}")
        elif role == "assistant" and content.startswith(COMPACTION_MARKER_PREFIX):
            lines.append("Earlier summary:\n" + content.split("\n\n", 1)[-1])
        elif role == "assistant":
            if entry.get("reasoning") and not filter_thoughts:
                lines.append(f"[thinking] {entry['reasoning']}")
            if content:
                lines.append(f"Assistant: {content}")
            for call in entry.get("tool_calls") or []:
                name = (call.get("function") or {}).get("name", "?")
                result = results.get(call.get("id"), "")
                if filter_tool_calls:
                    lines.append("  " + _one_line(name, _args(call), result))
                else:
                    lines.append(f"  [tool call] {name} "
                                 f"{(call.get('function') or {}).get('arguments', '')}")
                    lines.append(f"  [tool result] {result}")
    return "\n".join(lines)
