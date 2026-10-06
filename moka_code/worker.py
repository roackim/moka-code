"""
Stateless tool bodies for moka.

This module is deliberately **stdlib-only** so it can serve two roles:

- the host's source of tool implementations
  (``from moka_code.worker import read, write, edit, bash``), and
- the sandbox worker entrypoint, run as a script
  (``python /opt/worker.py``); because it is executed as a file the
  ``moka_code`` package ``__init__`` is never imported inside the container.

The four verbs are :func:`read`, :func:`write`, :func:`edit` and
:func:`bash`.  They take explicit arguments (a ``cwd`` root instead of a
toolset object) and raise :class:`ToolError` on failure.
"""
from __future__ import annotations

import asyncio
import base64
import inspect
import json
import os
import re
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, TextIO, Tuple


class ToolError(Exception):
    """Base exception for tool errors"""


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _resolve(path: str, cwd: Path | str) -> Path:
    """Resolve ``path`` relative to ``cwd`` (absolute paths pass through)."""
    try:
        target = Path(path)
        if target.is_absolute():
            return target.resolve()
        return (Path(cwd) / path).resolve()
    except Exception as e:
        raise ToolError(f"Invalid path '{path}': {e}")


# ---------------------------------------------------------------------------
# Replace-block parsing (folded from harness/patch_parser.py)
#
# Implements Aider-style search/replace blocks:
#
#     filename.py
#     <<<<<<< SEARCH
#     old code
#     =======
#     new code
#     >>>>>>> REPLACE
# ---------------------------------------------------------------------------

@dataclass
class PatchBlock:
    """Parsed patch block"""
    filename: str
    search_text: str
    replace_text: str


class PatchParseError(Exception):
    """Error parsing patch block"""
    pass


def parse_patch(content: str) -> PatchBlock:
    """
    Parse a replace-block format patch.

    Args:
        content: Patch content with markers

    Returns:
        PatchBlock with filename, search, and replace text

    Raises:
        PatchParseError: If patch format is invalid
    """
    lines = content.strip().split('\n')

    if not lines:
        raise PatchParseError("Empty patch content")

    # Markers
    search_marker = '<<<<<<< SEARCH'
    divider_marker = '======='
    replace_marker = '>>>>>>> REPLACE'

    search_start = None
    divider = None
    replace_end = None

    # 1. Find the SEARCH marker first (it might be indented or have preamble lines before it)
    for i, line in enumerate(lines):
        if search_marker in line:
            search_start = i
            break

    if search_start is None:
        raise PatchParseError(f"Missing '{search_marker}' marker")

    # 2. Extract filename from the line immediately before SEARCH marker
    if search_start == 0:
        raise PatchParseError("Missing filename before SEARCH marker")

    filename = lines[search_start - 1].strip()
    # Basic sanity check for filename (shouldn't have markers or be too long)
    if not filename or any(m in filename for m in [search_marker, divider_marker, replace_marker]):
        raise PatchParseError("Invalid or missing filename immediately before SEARCH marker")

    # 3. Find remaining markers after search_start
    for i in range(search_start + 1, len(lines)):
        line = lines[i]
        if divider_marker in line:
            if divider is not None:
                raise PatchParseError(f"Multiple {divider_marker} markers found")
            divider = i
        elif replace_marker in line:
            if replace_end is not None:
                raise PatchParseError(f"Multiple {replace_marker} markers found")
            replace_end = i
            break  # Stop at first REPLACE marker

    # Validate markers found and order
    if divider is None:
        raise PatchParseError(f"Missing '{divider_marker}' marker")
    if replace_end is None:
        raise PatchParseError(f"Missing '{replace_marker}' marker")

    # 4. Extract content
    search_lines = lines[search_start + 1:divider]
    replace_lines = lines[divider + 1:replace_end]

    search_text = '\n'.join(search_lines)
    replace_text = '\n'.join(replace_lines)

    return PatchBlock(
        filename=filename,
        search_text=search_text,
        replace_text=replace_text
    )


def apply_patch(file_content: str, patch: PatchBlock) -> Tuple[str, str]:
    """
    Apply a patch to file content.

    Args:
        file_content: Original file content
        patch: Parsed patch block

    Returns:
        Tuple of (new_content, message)
        - new_content: Modified content (or original if no match)
        - message: Success or error message

    Examples:
        >>> content = "def foo():\\n    pass"
        >>> patch = PatchBlock("test.py", "def foo():\\n    pass", "def foo():\\n    return 42")
        >>> new, msg = apply_patch(content, patch)
        >>> "return 42" in new
        True
    """
    def _exact_positions(content: str, needle: str) -> list[tuple[int, int]]:
        if not needle:
            return []
        positions: list[tuple[int, int]] = []
        start = 0
        while True:
            idx = content.find(needle, start)
            if idx == -1:
                break
            positions.append((idx, idx + len(needle)))
            start = idx + 1
        return positions

    def _line_offsets(text: str) -> list[int]:
        offsets = [0]
        for i, ch in enumerate(text):
            if ch == '\n':
                offsets.append(i + 1)
        return offsets

    def _line_spans(
        content: str,
        search_text: str,
        normalizer: Callable[[str], str],
    ) -> list[tuple[int, int]]:
        content_lines = content.split('\n')
        search_lines = search_text.split('\n')
        if not search_lines:
            return []

        n = len(search_lines)
        if n > len(content_lines):
            return []

        normalized_search = [normalizer(line) for line in search_lines]
        offsets = _line_offsets(content)
        spans: list[tuple[int, int]] = []

        for i in range(0, len(content_lines) - n + 1):
            window = content_lines[i:i + n]
            normalized_window = [normalizer(line) for line in window]
            if normalized_window == normalized_search:
                start_idx = offsets[i]
                end_line = i + n
                end_idx = offsets[end_line] if end_line < len(offsets) else len(content)
                spans.append((start_idx, end_idx))
        return spans

    def _norm_whitespace(line: str) -> str:
        return re.sub(r'\s+', ' ', line).strip()

    def _norm_indentation(line: str) -> str:
        return line.lstrip().rstrip()

    modes: list[tuple[str, list[tuple[int, int]]]] = [
        ("exact", _exact_positions(file_content, patch.search_text)),
        ("whitespace-normalized", _line_spans(file_content, patch.search_text, _norm_whitespace)),
        ("indentation-normalized", _line_spans(file_content, patch.search_text, _norm_indentation)),
    ]

    for mode, spans in modes:
        if not spans:
            continue

        if len(spans) > 1:
            return file_content, (
                f"[ERROR] Search block is ambiguous in {mode} mode "
                f"({len(spans)} matches). Add more unique context."
            )

        start_idx, end_idx = spans[0]
        new_content = file_content[:start_idx] + patch.replace_text + file_content[end_idx:]
        return new_content, f"[OK] Applied edit to {patch.filename} (1 replacement, mode={mode})"

    search_lines = patch.search_text.split('\n')
    if search_lines:
        first_line = search_lines[0].strip()
        if first_line:
            for i, line in enumerate(file_content.split('\n'), start=1):
                if first_line in line:
                    return file_content, f"[ERROR] Search block not found. Similar content at line {i}: '{line.strip()}'"

    return file_content, "[ERROR] Search block not found in file. Tried exact, whitespace-normalized, indentation-normalized."


# ---------------------------------------------------------------------------
# File tools
# ---------------------------------------------------------------------------

MAX_PATCH_REPLACEMENT_CHARS = 100_000
MAX_PATCH_LINE_DELTA = 500


def _is_image(head: bytes) -> bool:
    """PNG, JPEG, GIF or WebP, by signature (``harness.images`` parses them)."""
    return (head.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8", b"GIF87a", b"GIF89a"))
            or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"))


def read(
    path: str,
    *,
    cwd: Path | str,
    offset: int = 0,
    limit: int | None = None,
    max_chars: int | None = None,
    include_line_numbers: bool = False,
    max_image_bytes: int | None = None,
) -> str | dict:
    """
    Read file content.

    With ``max_image_bytes`` set (by the harness, never the model), an image
    file is returned as ``{"image": {"path": ..., "data": <base64>}}``;
    without it an image is refused like any non-text file.

    Args:
        path: File path relative to ``cwd`` or absolute
        cwd: Workspace root for relative paths
        offset: Zero-based first line to return
        limit: Optional number of lines to return
        max_chars: Optional maximum size of the returned content
        include_line_numbers: Prefix each returned line with its source line
            number

    Returns:
        File content as string

    Raises:
        ToolError: If the path is invalid or the file cannot be read
    """
    for name, value in (("offset", offset), ("limit", limit), ("max_chars", max_chars)):
        minimum = 0 if name == "offset" else 1
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < minimum):
            expectation = "a non-negative integer" if name == "offset" else "a positive integer"
            raise ToolError(f"Invalid {name}: expected {expectation}")

    target = _resolve(path, cwd)

    if not target.exists():
        raise ToolError(f"File not found: {path}")

    if not target.is_file():
        raise ToolError(f"Not a file: {path}")

    if max_image_bytes is not None:
        try:
            with open(target, "rb") as stream:
                head = stream.read(12)
        except OSError as e:
            raise ToolError(f"Error reading file: {e}")
        if _is_image(head):
            size = target.stat().st_size
            if size > max_image_bytes:
                raise ToolError(
                    f"Image too large: {path} is {size / 1048576:.1f} MB, "
                    f"over the {max_image_bytes / 1048576:.1f} MB limit"
                )
            data = base64.b64encode(target.read_bytes()).decode("ascii")
            return {"image": {"path": str(target), "data": data}}

    try:
        content = target.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        raise ToolError(f"File is not UTF-8 text: {path}")
    except Exception as e:
        raise ToolError(f"Error reading file: {e}")

    # Keep line endings while slicing so a selected block can be copied
    # directly into the edit tool.
    lines = content.splitlines(keepends=True)
    first = offset
    last = offset + limit if limit is not None else len(lines)
    selected = lines[first:last]

    if include_line_numbers:
        selected = [f"{number:>6}\t{line}" for number, line in zip(range(first + 1, last + 1), selected)]

    result = "".join(selected)
    if max_chars is not None and len(result) > max_chars:
        result = result[:max_chars] + f"\n[truncated: showing {max_chars} of {len(result)} characters]"
    return result


def write(path: str, content: str, *, cwd: Path | str) -> str:
    """
    Write file content (creates or overwrites).

    Args:
        path: File path relative to ``cwd`` or absolute
        content: Content to write
        cwd: Workspace root for relative paths

    Returns:
        Success message

    Raises:
        ToolError: If the file cannot be written
    """
    target = _resolve(path, cwd)

    # Create parent directories if needed
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        target.write_text(content, encoding='utf-8')
        byte_count = len(content.encode('utf-8'))
        return f"[OK] Wrote {byte_count} bytes to {path}"
    except Exception as e:
        raise ToolError(f"Error writing file: {e}")


def edit(path: str, search: str, replace: str, *, cwd: Path | str) -> str:
    """
    Replace one exact text block in a file.

    Args:
        path: File path relative to ``cwd`` or absolute
        search: Exact existing text block to replace (include enough context
            to be unique)
        replace: Replacement text block
        cwd: Workspace root for relative paths

    Returns:
        Success or error message

    Raises:
        ToolError: If the block cannot be applied
    """
    if search is None:
        raise ToolError("Invalid edit arguments: missing 'search'")
    if replace is None:
        raise ToolError("Invalid edit arguments: missing 'replace'")

    try:
        patch = parse_patch(
            f"{path}\n"
            "<<<<<<< SEARCH\n"
            f"{search}\n"
            "=======\n"
            f"{replace}\n"
            ">>>>>>> REPLACE"
        )
    except PatchParseError as e:
        raise ToolError(f"Invalid edit: {e}")

    # Guardrails: replacement size and line delta constraints
    replacement_chars = len(patch.replace_text)
    if replacement_chars > MAX_PATCH_REPLACEMENT_CHARS:
        raise ToolError(
            f"Edit rejected: replacement too large ({replacement_chars} chars > {MAX_PATCH_REPLACEMENT_CHARS})"
        )

    search_line_count = patch.search_text.count('\n') + 1 if patch.search_text else 0
    replace_line_count = patch.replace_text.count('\n') + 1 if patch.replace_text else 0
    line_delta = abs(replace_line_count - search_line_count)
    if line_delta > MAX_PATCH_LINE_DELTA:
        raise ToolError(
            f"Edit rejected: line delta too large ({line_delta} lines > {MAX_PATCH_LINE_DELTA})"
        )

    # Read current file
    try:
        current_content = read(patch.filename, cwd=cwd)
    except ToolError as e:
        raise ToolError(f"Cannot read file for editing: {e}")

    # Apply patch
    new_content, message = apply_patch(current_content, patch)

    # If successful, write back
    if message.startswith('[OK]'):
        write(patch.filename, new_content, cwd=cwd)

    return message


# ---------------------------------------------------------------------------
# Shell
# ---------------------------------------------------------------------------

def _format_output(stdout: str, stderr: str, returncode: int) -> str:
    output_parts = []
    if stdout:
        output_parts.append(f"[stdout]\n{stdout}")
    if stderr:
        output_parts.append(f"[stderr]\n{stderr}")
    output_parts.append(f"[exit:{returncode}]")
    return '\n'.join(output_parts) if output_parts else "[exit:0]"


def kill_process_group(proc) -> None:
    """Kill a subprocess and its entire process group (best effort)."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


#: Seconds a ``bash`` command may run when the caller states no limit. The
#: harness always states one: the role's ``tool_timeout`` (same default). Zero or
#: less means no limit.
DEFAULT_TOOL_TIMEOUT = 300


def limit_seconds(timeout: float) -> Optional[float]:
    """``timeout`` as a wait limit: ``None`` (wait for ever) when it is 0 or less."""
    return timeout if timeout > 0 else None


def bash_sync(command: str, *, cwd: Path | str, timeout: float = DEFAULT_TOOL_TIMEOUT) -> str:
    """Execute a shell command synchronously (blocking)."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=limit_seconds(timeout),
        )
        return _format_output(
            result.stdout.rstrip() if result.stdout else "",
            result.stderr.rstrip() if result.stderr else "",
            result.returncode,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"Command timed out after {timeout:g}s (the role's tool_timeout)")
    except Exception as e:
        raise ToolError(f"Command execution failed: {e}")


#: Seconds a finished command's output pumps get to drain (see :func:`bash`).
_DRAIN_GRACE = 1.0


async def bash(
    command: str,
    *,
    cwd: Path | str,
    timeout: float = DEFAULT_TOOL_TIMEOUT,
    on_spawn: Optional[Callable[[object], None]] = None,
    on_output: Optional[Callable[[str, str], None]] = None,
) -> str:
    """Execute a shell command asynchronously.

    Runs the command in its own process group so the whole tree can be killed
    on timeout or cancellation.  ``on_spawn`` (if given) is called with the
    live process right after spawn and with ``None`` when it finishes, which
    lets the in-process caller cancel it.  ``on_output(stream, chunk)`` (if
    given) is called for every stdout/stderr chunk as it arrives, so long
    commands stream live; the same callback is used bare and sandboxed.
    """
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            shell=True,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,  # own process group so stop kills children
        )
        if on_spawn is not None:
            on_spawn(proc)

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        def _sink(stream: str, chunk: str) -> None:
            (stdout_chunks if stream == "stdout" else stderr_chunks).append(chunk)
            if on_output is not None:
                on_output(stream, chunk)

        async def _pump(reader, stream: str) -> None:
            while True:
                line = await reader.readline()
                if not line:
                    break
                _sink(stream, line.decode("utf-8", errors="replace"))

        pumps = [
            asyncio.create_task(_pump(proc.stdout, "stdout")),
            asyncio.create_task(_pump(proc.stderr, "stderr")),
        ]

        try:
            await asyncio.wait_for(proc.wait(), timeout=limit_seconds(timeout))
            # wait() can return before the pumps have consumed what is already
            # buffered: let them finish, or the last output is dropped. The
            # grace only bounds a pump that never ends.
            await asyncio.wait(pumps, timeout=_DRAIN_GRACE)
        except asyncio.TimeoutError:
            kill_process_group(proc)
            await proc.wait()
            raise ToolError(f"Command timed out after {timeout:g}s (the role's tool_timeout)")
        except asyncio.CancelledError:
            kill_process_group(proc)
            raise
        finally:
            if on_spawn is not None:
                on_spawn(None)
            for pump in pumps:
                pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)

        stdout_text = "".join(stdout_chunks).rstrip()
        stderr_text = "".join(stderr_chunks).rstrip()
        return _format_output(stdout_text, stderr_text, proc.returncode)
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(f"Command execution failed: {e}")


# ---------------------------------------------------------------------------
# JSONL protocol (container entrypoint)
#
# Requests arrive one per line on stdin, responses go one per line on stdout
# (stdout is protocol-only; logs go to stderr).  Each request carries an
# integer ``id`` that is echoed back:
#
#     {"id": 7, "tool": "bash", "args": {"command": "pytest -q"}}
#     {"id": 7, "stream": "stdout", "data": "collected 42 items\n"}   // interim
#     {"id": 7, "ok": true, "result": "..."}
#     {"id": 7, "ok": false, "error": "timeout after 120s"}
#
# A bare ``{"op": "shutdown"}`` stops the loop.  Execution is serialized (one
# request at a time) for v1.
# ---------------------------------------------------------------------------

_TOOL_TABLE: dict[str, Callable] = {
    "read": read,
    "write": write,
    "edit": edit,
    "bash": bash,
}


async def _dispatch(
    name: str,
    args: dict,
    cwd: Path | str,
    on_output: Optional[Callable[[str, str], None]] = None,
):
    func = _TOOL_TABLE.get(name)
    if func is None:
        raise ToolError(f"Unknown tool: {name}")
    if on_output is not None and name == "bash":
        args = {**args, "on_output": on_output}
    result = func(cwd=cwd, **args)
    if inspect.isawaitable(result):
        result = await result
    return result


async def handle_request(
    request: dict,
    cwd: Path | str,
    on_output: Optional[Callable[[str, str], None]] = None,
) -> dict:
    """Execute one protocol request and return its response frame.

    ``on_output(stream, chunk)`` (if given) receives interim bash output; it is
    usually wired to emit ``{"stream": ...}`` frames on the wire.
    """
    if not isinstance(request, dict):
        return {"id": None, "ok": False, "error": "request must be a JSON object"}

    req_id = request.get("id")
    tool = request.get("tool")
    args = request.get("args") or {}
    if not isinstance(args, dict):
        return {"id": req_id, "ok": False, "error": "args must be a JSON object"}

    try:
        result = await _dispatch(tool, args, cwd, on_output=on_output)
        return {"id": req_id, "ok": True, "result": result}
    except ToolError as e:
        return {"id": req_id, "ok": False, "error": str(e)}
    except Exception as e:  # pragma: no cover - defensive
        return {"id": req_id, "ok": False, "error": f"{type(e).__name__}: {e}"}


def _write_frame(stream: TextIO, frame: dict) -> None:
    stream.write(json.dumps(frame) + "\n")
    stream.flush()


async def serve(
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    cwd: Path | str | None = None,
) -> None:
    """Read JSONL requests from ``stdin`` and answer on ``stdout``.

    Defaults to the process stdio and current working directory when run as a
    script.  Returns when stdin reaches EOF or a shutdown op is received.
    """
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    root = Path(cwd) if cwd is not None else Path.cwd()

    loop = asyncio.get_event_loop()
    while True:
        line = await loop.run_in_executor(None, stdin.readline)
        if not line:
            break

        line = line.rstrip("\r\n")
        if not line.strip():
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            _write_frame(stdout, {"id": None, "ok": False, "error": f"invalid JSON: {e}"})
            continue

        if isinstance(request, dict) and request.get("op") == "shutdown":
            _write_frame(stdout, {"id": request.get("id"), "ok": True, "result": ""})
            break

        request_id = request.get("id") if isinstance(request, dict) else None

        def emit(stream: str, chunk: str, _id=request_id) -> None:
            _write_frame(stdout, {"id": _id, "stream": stream, "data": chunk})

        _write_frame(stdout, await handle_request(request, root, on_output=emit))


def main() -> None:
    """Entrypoint when the module is run as a script."""
    asyncio.run(serve())


if __name__ == "__main__":
    main()


__all__ = [
    "ToolError",
    "PatchBlock",
    "PatchParseError",
    "parse_patch",
    "apply_patch",
    "read",
    "write",
    "edit",
    "bash",
    "bash_sync",
    "DEFAULT_TOOL_TIMEOUT",
    "limit_seconds",
    "kill_process_group",
    "handle_request",
    "serve",
    "main",
]
