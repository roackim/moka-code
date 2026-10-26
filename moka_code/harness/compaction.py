"""The conversation as readable text for the compaction summarizer.

The summarizer gets this transcript, never the stored entries: no ids, image
references, ``origin`` or native reasoning blocks. Two filters (``context.toml``):
reasoning is dropped, and each tool call becomes one line without its output.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from moka_code.harness import images

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


def _k(tokens: int) -> str:
    return f"{tokens / 1000:.1f}k" if tokens < 10_000 else f"{tokens // 1000}k"


def compaction_view(history: List[Dict[str, Any]],
                    marker_id: str) -> Optional[Tuple[str, str]]:
    """The transcript's compaction line for the marker ``marker_id``:
    ``(label, text)``. The label is the folded line (``compacted · 42 messages
    · ~85k → ~12k tokens``); the text is what the model now sees instead: the
    summary, then the messages kept word for word. None when there is no such
    marker."""
    index = next((i for i, e in enumerate(history)
                  if e.get("id") == marker_id and e.get("role") == "assistant"
                  and str(e.get("content") or "").startswith(COMPACTION_MARKER_PREFIX)), None)
    if index is None:
        return None
    marker = history[index]
    header, _, summary = marker["content"].partition("\n\n")
    meta = marker.get("compaction") or {}
    count = meta.get("messages")
    if count is None:       # a marker written before the metadata: read its header
        found = re.search(r"compacted_messages=(\d+)", header)
        count = int(found.group(1)) if found else None
    until = next((i for i in range(index, len(history))
                  if history[i].get("id") == meta.get("until")), index)
    kept = [e for e in history[index + 1:until + 1] if e.get("role") != "system"]

    label = ["compacted"]
    if count is not None:
        label.append(f"{count} messages")
    label.append(f"{len(kept)} kept")
    if meta.get("before") is not None and meta.get("after") is not None:
        label.append(f"~{_k(meta['before'])} → ~{_k(meta['after'])} tokens")

    text = summary.strip()
    if kept:
        shown = transcript(kept, filter_thoughts=True, filter_tool_calls=True)
        fence = "````" if "```" in shown else "```"
        text += (f"\n\n---\n\n**Kept word for word** ({len(kept)} messages):\n\n"
                 f"{fence}text\n{shown}\n{fence}")
    return " · ".join(label), text


# Code, JSON and non-English text run near 3 characters per token, prose near
# 4: an estimate must not understate the context, so it takes the lower.
CHARS_PER_TOKEN = 3


def estimate_tokens(entries: List[Dict[str, Any]]) -> int:
    """A rough token count (characters / CHARS_PER_TOKEN) for history entries or API messages.

    Images count by their size (``images.tokens``) from a history entry's
    references; an API message's encoded image parts are skipped, never
    counted as base64 text (that was ~10x the real cost)."""
    chars = 0
    image_tokens = 0
    for entry in entries:
        content = entry.get("content") or ""
        if isinstance(content, list):
            chars += sum(len(part.get("text") or "") for part in content
                         if isinstance(part, dict) and part.get("type") == "text")
        else:
            chars += len(str(content))
        chars += len(str(entry.get("reasoning_content") or ""))     # replayed thinking
        if entry.get("tool_calls"):
            chars += len(json.dumps(entry["tool_calls"], ensure_ascii=False))
        image_tokens += sum(images.tokens(image) for image in entry.get("images") or ())
    return chars // CHARS_PER_TOKEN + image_tokens


def cut_index(history: List[Dict[str, Any]], keep_tokens: int) -> int:
    """Where the verbatim tail of ``history`` starts: the earliest user message
    from which the rest fits in ``keep_tokens`` (always at least the last user
    message). Only user messages are cut points, so a tool call is never parted
    from its results; system notices just before one stay with it. 0 means there
    is nothing before the tail to summarize."""
    starts = []
    for i, entry in enumerate(history):
        if entry.get("role") == "user" and entry.get("source") != "tool":
            while i > 0 and history[i - 1].get("role") == "system":
                i -= 1
            starts.append(i)
    if not starts:
        return 0
    cut = starts[-1]
    for start in reversed(starts[:-1]):
        if estimate_tokens(history[start:]) > keep_tokens:
            break
        cut = start
    return cut
