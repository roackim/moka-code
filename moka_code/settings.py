"""moka configuration.

Configuration is split into small, single-concern files under
``~/.config/moka/`` so each one stays focused and easy to edit with
``/config <section>`` or ``/edit``:

- ``ui.toml`` — theme, padding, fps (flat keys)
- ``context.toml`` — context building (flat keys)
- ``debug.toml`` — debug logging (flat keys)
- ``styles.toml`` — ``[markdown_styles.*]`` / ``[syntax_highlight.*]``
- ``servers.toml`` — one ``[servers.<name>]`` table per server
- ``themes.toml`` — one ``[themes.<name>]`` palette table per theme
- ``roles/<name>.toml`` — one file per role
- ``state.toml`` — machine-written, disposable (last server/model, theme, catalog)

Each file is validated independently; errors are collected and reported as
``<file>: ...`` instead of being silently swallowed. There are no
project-local overrides, and reloading is explicit (``/reload``).
"""

from __future__ import annotations

import os
import re
import stat
import tempfile
import toml
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

try:                                    # Linux-only, but moka targets linux-64.
    import fcntl
except ImportError:                     # pragma: no cover - non-POSIX fallback
    fcntl = None                        # type: ignore[assignment]

from moka_code.harness.providers import REGISTRY


class _SyncSkipped(Exception):
    """A config migration was aborted; the file was left byte-identical."""


@contextmanager
def _sync_lock(config_dir: Path) -> Iterator[None]:
    """Serialise config migrations between moka instances (advisory).

    Editors do not honour this lock; the stale-file guard and the backup are
    what protect against concurrent edits.
    """
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        lock = os.open(str(config_dir / ".moka-sync.lock"),
                       os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise _SyncSkipped(f"config directory is not writable ({exc})") from exc
    try:
        if fcntl is not None:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise _SyncSkipped("another moka instance is syncing config") from exc
        yield
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(lock, fcntl.LOCK_UN)
        finally:
            os.close(lock)


def _fsync_dir(path: Path) -> None:
    """Best-effort durability for a rename (not all filesystems support it)."""
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write_bytes(path: Path, data: bytes, mode: Optional[int] = None,
                        *, guard_path: Optional[Path] = None,
                        guard_bytes: Optional[bytes] = None) -> None:
    """Replace ``path`` atomically with ``data``, never exposing a partial file.

    The new bytes land in a same-directory temp file, are flushed and fsynced,
    then shown to the guard (if any) and installed with ``os.replace``. On any
    failure the temp file is removed and ``path`` is untouched.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=str(path.parent),
                                     prefix=f".{path.name}.moka-", suffix=".tmp")
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temp, mode)
        if guard_path is not None and guard_bytes is not None:
            try:
                current = guard_path.read_bytes()
            except OSError as exc:
                raise _SyncSkipped(f"file changed on disk ({exc})") from exc
            if current != guard_bytes:
                raise _SyncSkipped("file changed on disk")
        os.replace(temp, path)
        _fsync_dir(path.parent)
    except BaseException:
        try:
            temp.unlink()
        except OSError:
            pass
        raise

# Default markdown element styles.
# Each key is an element name; values are dicts with optional:
#   fg (hex string), bg (hex string), bold (bool), reverse (bool)
DEFAULT_MARKDOWN_STYLES: Dict[str, Dict[str, Any]] = {
    # Color marks structure, weight marks emphasis. ``fg`` is a hex string or
    # a theme color name (``"HEADING"``), which follows the active theme.
    "header1":    {"fg": "HEADING", "bold": True},
    "header2":    {"fg": "HEADING", "bold": True},
    "header3":    {"fg": "HEADING", "bold": True},
    "header4":    {"fg": "HEADING", "bold": True},
    "header5":    {"fg": "HEADING", "bold": True},
    "header6":    {"fg": "HEADING", "bold": True},
    "bold":       {"fg": "EMPHASIS", "bold": True},
    "italic":     {"italic": True},
    "code":       {"fg": "CODE"},
    "code_block": {},
    "quote":      {"fg": "MUTED"},
    "list":       {},
    "link":       {"fg": "#569CD6", "underline": True},
    "hr":         {"fg": "MUTED"},
    "paragraph":  {},
}

# Default syntax highlight element styles.
# Each key is a highlight type; values are dicts with "fg" as a hex string.
DEFAULT_SYNTAX_HIGHLIGHT_STYLES: Dict[str, Dict[str, str]] = {
    "keyword":  {"fg": "#FF6464"},
    "function": {"fg": "#64DC78"},
    "string":   {"fg": "#DCC850"},
    "comment":  {"fg": "#808080"},
    # "plain" (unset): ordinary code uses the normal text color.
}

CONFIG_DIR_ENV = "MOKA_CONFIG_DIR"
ROLES_DIRNAME = "roles"
STATE_FILENAME = "state.toml"

#: Config section -> file name. The section is what ``/config <section>`` takes.
CONFIG_FILES = {
    "ui": "ui.toml",
    "context": "context.toml",
    "debug": "debug.toml",
    "styles": "styles.toml",
    "servers": "servers.toml",
    "theme": "themes.toml",
}


def get_config_dir() -> Path:
    """Directory holding the hand-editable config and disposable state."""
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "moka"


def migrate_legacy_config_dir() -> Optional[str]:
    """Move the pre-rename config dir (``~/.config/pico-chat``) to moka's, once.

    Only when ``MOKA_CONFIG_DIR`` is unset, the new directory does not exist
    yet and the old one does. Returns a notice (or an error) to show the user,
    or None when there was nothing to do.
    """
    if os.environ.get(CONFIG_DIR_ENV):
        return None
    new = get_config_dir()
    old = Path.home() / ".config" / "pico-chat"
    if new.exists() or not old.is_dir():
        return None
    try:
        old.rename(new)
    except OSError as exc:
        return f"Could not move your configuration from {old} to {new}: {exc}"
    return f"pico-chat is now moka: moved your configuration from {old} to {new}."


def get_roles_dir() -> Path:
    """Directory of one-file-per-role definitions."""
    return get_config_dir() / ROLES_DIRNAME


def get_state_path() -> Path:
    """Path to the disposable runtime state file."""
    return get_config_dir() / STATE_FILENAME


# Per-section templates written on first use. Everything is commented out so
# the built-in defaults apply until the user uncomments what they need.
DEFAULT_UI_TOML = """\
## Moka UI settings
## Apply with /reload, or /config ui (reloads when the editor exits).
## A line starting with # is a setting to uncomment; ## is help.

## Look

# theme = "terminal"                    # built-in or [themes.<name>] of themes.toml (/theme)
# use_bg_color = false                  # paint the theme background
# app_global_padding = 0
# msg_h_padding = 1
# msg_v_margin = 1                      # blank lines between turns (assistant output is continuous)
# max_input_height = 8                  # input grows with wrapped lines, then scrolls
# box_style = "square"                  # "square" | "double" | "rounded" | "ascii"
# box_style_focused = "square"

## Scrolling

# scroll_lines_per_notch = 3
# scroll_touchpad_speed = 0.1
# scroll_touchpad_event_threshold = 2
# scroll_alt_multiplier = 3.0

## Live usage

# metrics_refresh_interval = 0.1         # seconds between usage updates while streaming

## Status bar and notices

# status_bar_fields = ["endpoint_model", "role", "context", "cost", "sandbox"]
# status_bar_separator = "  "           # drawn between fields
## Put "|" in status_bar_fields to right-align everything after it.
# notice_lines = 2                      # top band height; past it, "+N more · /activity"

## Model field

# model_glyph = ""                      # before the field; empty hides it (emoji ok)
# model_prefix = ""                     # before provider:model; empty hides it

## Sandbox field

# sandbox_glyph = "⬢"                   # before the field; empty hides it (emoji ok)
# sandbox_prefix = "sandbox:"           # before the sandbox name; empty hides it
# sandbox_active_color = "SUCCESS"      # palette name or #rrggbb
# sandbox_inactive_color = "WARNING"

## Transcript and streaming

# thought_min_tokens = 0                # hide shorter reasoning lines (0 = show all)
# show_banner = true                    # moka art in an empty transcript
# stream_smoothing = true               # reveal streamed text smoothly
# smooth_target_fps = 60                # reveal cadence (independent of render fps)
# target_fps = 60
"""

DEFAULT_CONTEXT_TOML = """\
## Moka context settings
## Apply with /reload, or /config context.
## A line starting with # is a setting to uncomment; ## is help.

## @ file picker

# max_files = 500
# max_depth = 4
# ignore_gitignore = false

## Images and sessions

# max_image_mb = 5                      # largest image a message can attach
# sessions = 10                         # conversations saved per project (/session); 0 = off

## /compact

# compact_filter_thoughts = true        # leave the model's reasoning out of the summary input
# compact_filter_tool_calls = true      # each tool call as one line, without its output
# compact_at = 0.8                      # compact when a turn ends over this share of the context window; 0 = off
# compact_stop_at = 0.85                # ... or pause a running turn over this share, compact, then resume it
# compact_keep = 0.1                    # share of the window kept word for word (the newest turns)

## /diff

# diff_context = 3                      # unchanged lines around each change; "function" = the whole function, "all" = the whole file
"""

DEFAULT_DEBUG_TOML = """\
## Moka debug settings
## Apply with /reload, or /config debug.
## A line starting with # is a setting to uncomment; ## is help.

## Logging

# log_enabled = false                   # write debug_stream.log
"""

DEFAULT_STYLES_TOML = """\
## Moka markdown and syntax-highlight styles
## Apply with /reload, or /config styles.
## A line starting with # is a setting to uncomment; ## is help.
##
## Keys: fg, bg (a hex color, or a theme color name such as "MUTED"), bold,
## italic, underline.

## Markdown

# [markdown_styles.header1]
# fg   = "#FF79C6"
# bold = true

# [markdown_styles.bold]
# fg   = "#FFD700"
# bold = true

# [markdown_styles.code]
# fg = "#9CDCFE"

## Syntax highlighting

# [syntax_highlight.keyword]
# fg = "#FF6464"
"""

# The servers template: a legend, then each provider's own block
# (``Endpoint.template``), in registry order.
DEFAULT_SERVERS_TOML = """\
## Moka servers

## One table per server; the name is yours (shown by /model).
## A table named after its type (openrouter, deepseek) needs no type.

""" + "\n".join(cls.template for cls in REGISTRY.values()) + """
## Technical settings, any server: api_key (or api_key_env), timeout = 30.0,
## retry_attempts = 3, retry_delay = 2.0.
"""

DEFAULT_THEMES_TOML = """\
## Moka color themes
## One [themes.<name>] table per theme; select with /theme. Apply with /reload,
## or /config theme.
## A line starting with # is a setting to uncomment; ## is help.
##
## Each palette entry is a hex RGB string or an ANSI slot table:
##   USER       = "#4EC9B0"
##   MUTED      = { ansi = 90 }            # standard ANSI fg code
##   BACKGROUND = { ansi = 39, bg = 49 }   # fg and/or bg codes
##
## Missing entries inherit from the built-in base of the same name (or terminal).
## Several built-ins ship (terminal is the default; run /theme to list them);
## they are always available and can be overridden here.
##
## Palette keys: BACKGROUND, DEFAULT, MUTED, ERROR, WARNING, SUCCESS,
##               PERMISSION, USER, ASSISTANT, FOCUSED

## Example

# [themes.pastel]
# BACKGROUND = "#1E1E1E"
# DEFAULT    = "#D4D4D4"
# MUTED      = "#808080"
# ERROR      = "#F48771"
# WARNING    = "#CCA700"
# SUCCESS    = "#89D185"
# PERMISSION = "#C586C0"
# USER       = "#4EC9B0"
# ASSISTANT  = "#569CD6"
# FOCUSED    = "#DCDCAA"
"""

DEFAULT_CONFIG_TEMPLATES = {
    "ui": DEFAULT_UI_TOML,
    "context": DEFAULT_CONTEXT_TOML,
    "debug": DEFAULT_DEBUG_TOML,
    "styles": DEFAULT_STYLES_TOML,
    "servers": DEFAULT_SERVERS_TOML,
    "theme": DEFAULT_THEMES_TOML,
}


# --- config schema ----------------------------------------------------------
# Each spec maps a TOML key to (runtime attribute, kind). Kinds drive both
# validation and coercion so a malformed value yields a reported error rather
# than a silently ignored setting.

_UI_SPEC: Dict[str, tuple[str, str]] = {
    "theme": ("ui_theme", "str"),
    "use_bg_color": ("ui_use_bg_color", "bool"),
    "app_global_padding": ("ui_app_global_padding", "int"),
    "msg_h_padding": ("ui_msg_h_padding", "int"),
    "msg_v_margin": ("ui_msg_v_margin", "int"),
    "max_input_height": ("ui_max_input_height", "int"),
    "box_style": ("ui_box_style", "str"),
    "box_style_focused": ("ui_box_style_focused", "str"),
    "scroll_lines_per_notch": ("ui_scroll_lines_per_notch", "int"),
    "scroll_touchpad_speed": ("ui_scroll_touchpad_speed", "float"),
    "scroll_touchpad_event_threshold": ("ui_scroll_touchpad_event_threshold", "int"),
    "scroll_alt_multiplier": ("ui_scroll_alt_multiplier", "float"),
    "metrics_refresh_interval": ("ui_metrics_refresh_interval", "float"),
    "status_bar_fields": ("ui_status_bar_fields", "str_list"),
    "status_bar_separator": ("ui_status_bar_separator", "str"),
    "notice_lines": ("ui_notice_lines", "int"),
    "model_glyph": ("ui_model_glyph", "str"),
    "model_prefix": ("ui_model_prefix", "str"),
    "sandbox_glyph": ("ui_sandbox_glyph", "str"),
    "sandbox_prefix": ("ui_sandbox_prefix", "str"),
    "sandbox_active_color": ("ui_sandbox_active_color", "color"),
    "sandbox_inactive_color": ("ui_sandbox_inactive_color", "color"),
    "thought_min_tokens": ("ui_thought_min_tokens", "int"),
    "show_banner": ("ui_show_banner", "bool"),
    "stream_smoothing": ("ui_stream_smoothing", "bool"),
    "smooth_target_fps": ("ui_smooth_target_fps", "int"),
    "target_fps": ("target_fps", "int"),
}

_CONTEXT_SPEC: Dict[str, tuple[str, str]] = {
    "max_files": ("context_max_files", "int"),
    "max_depth": ("context_max_depth", "int"),
    "ignore_gitignore": ("context_ignore_gitignore", "bool"),
    "max_image_mb": ("context_max_image_mb", "int_or_float"),
    "sessions": ("context_sessions", "int"),
    "compact_filter_thoughts": ("context_compact_filter_thoughts", "bool"),
    "compact_filter_tool_calls": ("context_compact_filter_tool_calls", "bool"),
    "compact_at": ("context_compact_at", "float"),
    "compact_stop_at": ("context_compact_stop_at", "float"),
    "compact_keep": ("context_compact_keep", "float"),
    "diff_context": ("context_diff_context", "diff_context"),
}

_DEBUG_SPEC: Dict[str, tuple[str, str]] = {
    "log_enabled": ("debug_log_enabled", "bool"),
}

# Keys removed from a flat section but kept here so existing user files can be
# cleaned up on startup. Add a key here when you delete it from its ``*_SPEC``
# and ``DEFAULT_*_TOML`` (see "Adding or deprecating a config key" in AGENTS.md).
_RETIRED_UI: set[str] = {  # nothing read these: spinner (elapsed-time labels), console, per-message metrics
    "spinner_fps", "debug_console_height",
    "show_metrics", "metrics_show_tokens", "metrics_show_speed", "metrics_show_ttft",
}
# preserve_reasoning_traces: reasoning is no longer sent back (PLAN.md step 2).
_RETIRED_CONTEXT: set[str] = {"preserve_reasoning_traces", "format"}  # format: the project tree was never sent
_RETIRED_DEBUG: set[str] = set()

# Flat sections that can be synced line-by-line against their template. Each
# maps to ``(template, retired_keys)``. Structured sections (styles, servers,
# themes) are user-authored tables and are deliberately not synced.
_FLAT_SECTION_SYNC: Dict[str, tuple[str, set[str]]] = {
    "ui": (DEFAULT_UI_TOML, _RETIRED_UI),
    "context": (DEFAULT_CONTEXT_TOML, _RETIRED_CONTEXT),
    "debug": (DEFAULT_DEBUG_TOML, _RETIRED_DEBUG),
}

_KEY_LINE_RE = re.compile(r"^\s*#?\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _template_key_lines(template: str) -> "list[tuple[str, str]]":
    """Return ``[(key, line), ...]`` for every key line in a template."""
    entries = []
    for line in template.splitlines():
        match = _KEY_LINE_RE.match(line)
        if match:
            entries.append((match.group(1), line))
    return entries


class _TemplateBlock:
    """A template section: an optional ``##`` heading plus its settings."""

    def __init__(self, lines: "list[str]"):
        self.lines = [line if line.endswith("\n") else line + "\n"
                      for line in lines]
        self.keys = _template_key_lines("".join(self.lines))
        self.heading = (self.lines[0].rstrip("\r\n")
                        if self.lines and self.lines[0].startswith("## ") else "")


def _template_blocks(template: str) -> "list[_TemplateBlock]":
    """Split a template into sections and keep only sections with settings.

    Section headings are ``##`` lines surrounded by blank lines. Inline help
    also starts with ``##`` but is not blank-line-delimited, so it stays inside
    its section instead of opening a new one.
    """
    source = template.splitlines(keepends=True)
    blocks: list[_TemplateBlock] = []
    current: list[str] = []
    for index, line in enumerate(source):
        previous_blank = index == 0 or not source[index - 1].strip()
        next_blank = index + 1 == len(source) or not source[index + 1].strip()
        if line.startswith("## ") and previous_blank and next_blank and current:
            blocks.append(_TemplateBlock(current))
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(_TemplateBlock(current))
    return [block for block in blocks if block.keys]


def _key_positions(lines: "list[str]", known: "dict[str, str]") -> "dict[str, int]":
    """First line index of each template key present in ``lines``."""
    positions: dict[str, int] = {}
    for index, line in enumerate(lines):
        match = _KEY_LINE_RE.match(line)
        if match and match.group(1) in known:
            positions.setdefault(match.group(1), index)
    return positions


def _next_key_position(key: str, order: "list[str]",
                       positions: "dict[str, int]", end: int) -> int:
    """Where a missing key belongs: before the next template key that exists."""
    for following in order[order.index(key) + 1:]:
        if following in positions:
            return positions[following]
    return end


def _next_heading_position(lines: "list[str]", blocks: "list[_TemplateBlock]",
                           current: int, positions: "dict[str, int]") -> Optional[int]:
    """Index of the next following block's heading, when that block exists."""
    for block in blocks[current + 1:]:
        if not block.heading:
            continue
        if not any(key in positions for key, _ in block.keys):
            continue
        for index, line in enumerate(lines):
            if line.rstrip("\r\n") == block.heading:
                return index
    return None


def _key_line_index(lines: "list[str]", key: str) -> Optional[int]:
    for index, line in enumerate(lines):
        match = _KEY_LINE_RE.match(line)
        if match and match.group(1) == key:
            return index
    return None


def _heading_line_index(lines: "list[str]", heading: str) -> Optional[int]:
    for index, line in enumerate(lines):
        if line.rstrip("\r\n") == heading:
            return index
    return None


def _anchor_index(lines: "list[str]", anchor) -> Optional[int]:
    kind, value = anchor
    return (_heading_line_index(lines, value) if kind == "heading"
            else _key_line_index(lines, value))


def _block_header(block: "_TemplateBlock") -> "list[str]":
    """Template lines before a block's first key (heading + any help)."""
    first_key = block.keys[0][1].rstrip("\r\n")
    header: list[str] = []
    for line in block.lines:
        if line.rstrip("\r\n") == first_key:
            break
        header.append(line)
    while header and not header[-1].strip():
        header.pop()
    return header


def _repair_structure(lines: "list[str]", blocks: "list[_TemplateBlock]") -> None:
    """Repair headings and help lines in a file migrated by an older sync.

    Only template-owned documentation moves: settings lines are never removed
    or reordered. A heading that is missing, or that sits above another block's
    keys, is moved directly before its own first key; then any missing ``##``
    help line is inserted at its template-relative position.
    """
    owner = {key: index for index, block in enumerate(blocks)
             for key, _ in block.keys}

    # Pass 1: every block with keys gets its heading immediately above them.
    for index, block in enumerate(blocks):
        if not block.heading:
            continue
        keys = {key for key, _ in block.keys}
        positions = [i for i, line in enumerate(lines)
                     if (match := _KEY_LINE_RE.match(line))
                     and match.group(1) in keys]
        if not positions:
            continue
        first_key = min(positions)
        heading_at = _heading_line_index(lines, block.heading)
        misplaced = heading_at is None or heading_at > first_key
        if heading_at is not None and heading_at < first_key:
            for i in range(heading_at + 1, first_key):
                match = _KEY_LINE_RE.match(lines[i])
                if match and owner.get(match.group(1), index) != index:
                    misplaced = True
                    break
        if not misplaced:
            continue
        if heading_at is not None:
            blank_before = heading_at > 0 and not lines[heading_at - 1].strip()
            del lines[heading_at]
            if (blank_before and heading_at < len(lines)
                    and not lines[heading_at].strip()):
                del lines[heading_at]
        positions = [i for i, line in enumerate(lines)
                     if (match := _KEY_LINE_RE.match(line))
                     and match.group(1) in keys]
        if not positions:
            continue
        first_key = min(positions)
        chunk = [line if line.endswith("\n") else line + "\n"
                 for line in _block_header(block)]
        while chunk and not chunk[-1].strip():
            chunk.pop()
        if chunk:
            chunk.append("\n")
            if first_key > 0 and lines[first_key - 1].strip():
                chunk.insert(0, "\n")
            lines[first_key:first_key] = chunk

    # Pass 2: insert help lines that a partially-synced block is missing.
    for block in blocks:
        items: list[tuple[str, str]] = []
        for index, line in enumerate(block.lines):
            if index == 0 and line.startswith("## "):
                items.append(("heading", line.rstrip("\r\n")))
                continue
            match = _KEY_LINE_RE.match(line)
            if match:
                items.append(("key", match.group(1)))
            elif line.startswith("##"):
                items.append(("help", line.rstrip("\r\n")))
        groups: list[list] = []
        current = None
        for i, item in enumerate(items):
            if item[0] == "help":
                if current is None:
                    continue
                previous, following = current
                if groups and groups[-1][0] == previous and groups[-1][1] == following:
                    groups[-1][2].append(item[1])
                else:
                    groups.append([previous, following, [item[1]]])
                continue
            following = next((other for other in items[i + 1:]
                              if other[0] in ("heading", "key")), None)
            current = (item, following)
        for previous, following, help_lines in groups:
            start = _anchor_index(lines, previous)
            if start is None:
                continue
            end = _anchor_index(lines, following) if following else None
            end = len(lines) if end is None else end
            existing = {line.rstrip("\r\n")
                        for line in lines[start + 1:end]}
            missing = [line for line in help_lines if line not in existing]
            for offset, line in enumerate(missing):
                lines.insert(start + 1 + offset, line + "\n")


def _merge_template(original: str, template: str, retired: "set[str]") -> str:
    """Insert missing template blocks/keys without rewriting user lines."""
    lines = original.splitlines(keepends=True)
    blocks = _template_blocks(template)
    order = [key for block in blocks for key, _ in block.keys]
    known: dict[str, str] = {}
    for block in blocks:
        for key, line in block.keys:
            known.setdefault(key, line)

    # Drop retired keys; every other line is kept byte-for-byte.
    kept: list[str] = []
    for line in lines:
        match = _KEY_LINE_RE.match(line)
        if match and match.group(1) in retired:
            continue
        kept.append(line)

    for block_index, block in enumerate(blocks):
        positions = _key_positions(kept, known)
        has_key = any(key in positions for key, _ in block.keys)
        has_heading = bool(block.heading) and any(
            line.rstrip("\r\n") == block.heading for line in kept)
        if has_key or has_heading:
            # The section exists: add only its missing key lines, in place.
            for key, line in block.keys:
                if key in positions:
                    continue
                insert_at = _next_key_position(key, order, positions, len(kept))
                if insert_at == len(kept) and kept and not kept[-1].endswith("\n"):
                    kept.append("\n")
                    insert_at = len(kept)
                kept.insert(insert_at, line + "\n")
                positions = _key_positions(kept, known)
            continue

        # The whole section is missing: insert heading + help + its keys.
        insert_at = _next_key_position(block.keys[0][0], order, positions, len(kept))
        heading_at = _next_heading_position(kept, blocks, block_index, positions)
        if heading_at is not None:
            insert_at = min(insert_at, heading_at)
        chunk = [line if line.endswith("\n") else line + "\n"
                 for line in block.lines]
        while chunk and not chunk[-1].strip():
            chunk.pop()
        if not chunk:
            continue
        if insert_at == len(kept) and kept and not kept[-1].endswith("\n"):
            kept.append("\n")
            insert_at = len(kept)
        if insert_at > 0 and kept[insert_at - 1].strip() and chunk[0].startswith("## "):
            chunk.insert(0, "\n")
        if insert_at < len(kept):
            chunk.append("\n")
        kept[insert_at:insert_at] = chunk

    _repair_structure(kept, blocks)
    return "".join(kept)


def _template_documentation(template: str) -> "set[str]":
    """Heading/help lines that sync is allowed to move or insert."""
    docs: set[str] = set()
    for block in _template_blocks(template):
        if block.heading:
            docs.add(block.heading)
        for line in block.lines:
            if line.startswith("##"):
                docs.add(line.rstrip("\r\n"))
    return docs


def _is_subsequence(needle: "list[str]", haystack: "list[str]") -> bool:
    """True when every ``needle`` line appears in order in ``haystack``."""
    index = 0
    for line in haystack:
        if index < len(needle) and line.rstrip("\r\n") == needle[index].rstrip("\r\n"):
            index += 1
    return index == len(needle)


def _sync_flat_file(path: Path, template: str, retired: "set[str]") -> bool:
    """Insert missing template blocks/keys and drop retired keys, safely.

    The file is never truncated or written in place. A whole-file merge is
    verified (TOML equality plus line preservation), backed up, then installed
    with an atomic rename guarded against concurrent modification. Returns
    True when the file changed; raises ``_SyncSkipped`` when it was left alone.
    """
    if not path.exists():
        return False
    try:
        target = path.resolve(strict=True)
    except OSError as exc:
        raise _SyncSkipped(f"cannot resolve path ({exc})") from exc
    try:
        before = target.stat()
    except OSError as exc:
        raise _SyncSkipped(f"cannot stat path ({exc})") from exc
    if not stat.S_ISREG(before.st_mode):
        raise _SyncSkipped("not a regular file")

    try:
        original_bytes = target.read_bytes()
        original = original_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise _SyncSkipped(f"cannot read file ({exc})") from exc
    try:
        original_data = toml.loads(original)
    except toml.TomlDecodeError as exc:
        raise _SyncSkipped(f"invalid TOML, not touching it ({exc})") from exc
    if not isinstance(original_data, dict):
        raise _SyncSkipped("top level is not a table, not touching it")

    updated = _merge_template(original, template, retired)
    if updated == original:
        return False

    # Verify the merge before it can reach the disk.
    try:
        new_data = toml.loads(updated)
    except toml.TomlDecodeError as exc:
        raise _SyncSkipped(f"generated content is invalid TOML ({exc})") from exc
    expected = dict(original_data)
    for key in retired:
        expected.pop(key, None)
    if new_data != expected:
        raise _SyncSkipped("verification failed: user values would change")
    original_lines = original.splitlines(keepends=True)
    documentation = _template_documentation(template)
    kept_lines = [line for line in original_lines
                  if (not (match := _KEY_LINE_RE.match(line))
                      or match.group(1) not in retired)
                  and line.rstrip("\r\n") not in documentation]
    if not _is_subsequence(kept_lines, updated.splitlines(keepends=True)):
        raise _SyncSkipped("verification failed: a user line would be lost")

    mode = stat.S_IMODE(before.st_mode)
    backup = target.with_name(target.name + ".bak")
    try:
        _atomic_write_bytes(backup, original_bytes, mode)
        _atomic_write_bytes(target, updated.encode("utf-8"), mode,
                            guard_path=target, guard_bytes=original_bytes)
    except _SyncSkipped:
        raise
    except OSError as exc:
        raise _SyncSkipped(f"could not write safely ({exc})") from exc
    return True


_STYLE_SECTIONS = {"markdown_styles", "syntax_highlight"}

# Keys every server table may have; a provider adds its ``extra_keys``.
_SERVER_KEYS = {
    "type", "base_url", "api_key", "api_key_env",
    "timeout", "retry_attempts", "retry_delay",
}
# Retired keys -> what replaces them (retired types: no replacement). A server still using one is
# reported and skipped (servers.toml is never rewritten by moka).
_RETIRED_SERVER_KEYS = {
    "max_context": "nothing (the context window comes from the server)",
    "provider": "'providers = [...]' (only these, in order)",
    "enabled_models": "'models = [...]'",
    "model_providers": "[servers.<name>.providers_by_model]",
}
_RETIRED_SERVER_TYPES = {"openai"}
_SERVER_STR_KEYS = {"type", "base_url", "api_key", "api_key_env"}
_SERVER_INT_KEYS = {"retry_attempts"}
_SERVER_FLOAT_KEYS = {"timeout", "retry_delay"}

# ``active_model`` (the model is per server, ``last_model``) and
# ``model_catalog`` (discovery is live, in memory) are retired keys, accepted
# and ignored so older state files load without errors; the next save drops them.
_STATE_SECTIONS = {"last_server", "active_model", "last_model", "model_catalog",
                   "active_theme", "effort"}

# Palette keys a ``[themes.<name>]`` table may define.
_THEME_PALETTE = {
    "BACKGROUND", "DEFAULT", "MUTED", "ERROR", "WARNING", "SUCCESS",
    "PERMISSION", "USER", "ASSISTANT", "FOCUSED",
}


def _coerce_theme_color(value: Any) -> Optional[str]:
    """Validate one palette value; return an error string or None.

    Accepted: ``"#RRGGBB"``; an integer ANSI fg code; or a table
    ``{ ansi = <fg>, bg = <bg> }``.
    """
    if isinstance(value, str):
        hex_str = value.lstrip("#")
        if len(hex_str) != 6 or any(c not in "0123456789abcdefABCDEF" for c in hex_str):
            return "must be a '#RRGGBB' hex string, an ANSI code, or a table"
        return None
    if isinstance(value, bool):
        return "must be a hex string, an ANSI code, or a table"
    if isinstance(value, int):
        return None
    if isinstance(value, dict):
        for key in value:
            if key not in ("ansi", "bg"):
                return f"unknown color key '{key}'"
        for key in ("ansi", "bg"):
            if key in value and (isinstance(value[key], bool) or not isinstance(value[key], int)):
                return f"{key} must be an integer"
        if not value:
            return "empty color table"
        return None
    return "must be a hex string, an ANSI code, or a table"


#: Palette names a setting may name instead of a ``#rrggbb`` (the theme's keys).
_COLOR_NAMES = _THEME_PALETTE | {"TOOL", "HEADING", "EMPHASIS", "CODE"}


def _coerce(kind: str, value: Any) -> tuple[Any, Optional[str]]:
    """Return ``(coerced, None)`` or ``(None, error)`` for one config value."""
    if kind == "str":
        return (value, None) if isinstance(value, str) else (None, "must be a string")
    if kind == "color":
        if isinstance(value, str) and (value in _COLOR_NAMES or re.fullmatch(r"#[0-9a-fA-F]{6}", value)):
            return value, None
        return None, f"must be a palette name ({', '.join(sorted(_COLOR_NAMES))}) or #rrggbb"
    if kind == "bool":
        return (value, None) if isinstance(value, bool) else (None, "must be a boolean")
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return None, "must be an integer"
        return value, None
    if kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, "must be a number"
        return float(value), None
    if kind == "int_or_float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, "must be a number"
        return value, None
    if kind == "str_list":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return None, "must be a list of strings"
        return list(value), None
    if kind == "str_or_none":
        if value is None or isinstance(value, str):
            return value, None
        return None, "must be a string or absent"
    if kind == "int_or_none":
        if value is None:
            return None, None
        if isinstance(value, bool) or not isinstance(value, int):
            return None, "must be an integer or absent"
        return value, None
    if kind == "diff_context":
        if value in ("function", "all") or (isinstance(value, int) and not isinstance(value, bool)
                                           and value >= 0):
            return value, None
        return None, "must be a number of lines, 'function' or 'all'"
    return value, None


class Config:
    """Validated view of the split config files plus disposable ``state.toml``."""

    def __init__(self, config_dir: str | Path | None = None,
                 state_path: str | Path | None = None):
        self._config_dir = Path(config_dir) if config_dir else None
        self._state_path = Path(state_path) if state_path else None
        self.load_errors: list[str] = []
        # Sections whose migration was aborted this run, with the reason.
        self.last_sync_skips: list[tuple[str, str]] = []
        # Live discovery results per server, in memory only (never persisted,
        # kept across reloads). Filled by ``endpoint.refresh_catalog``.
        self.models_by_server: Dict[str, list] = {}
        # Servers whose last refresh failed: their entry is the last
        # successful discovery of this session, possibly stale.
        self.stale_servers: set = set()
        # Why the last discovery of a server failed (memory only), by server.
        self.discovery_errors: Dict[str, str] = {}
        self._apply_defaults()
        self.reload()

    # -- defaults ------------------------------------------------------------

    def _apply_defaults(self) -> None:
        """Reset every runtime attribute to its built-in default."""
        # LLM servers (intent) and selection (state).
        self.servers: Dict[str, Dict[str, Any]] = {}
        # None until a first model is selected (auto-selected once one is
        # discovered, see ``commands.base.auto_select``).
        self.active_server: Optional[str] = None
        self.model_selection: Dict[str, str] = {}
        # Reasoning effort per server, per model (``/effort``).
        self.efforts: Dict[str, Dict[str, str]] = {}

        # User color theme definitions (intent) and active selection (state).
        self.themes: Dict[str, Dict[str, Any]] = {}
        self.active_theme: Optional[str] = None

        # UI settings.
        self.ui_max_input_height: int = 8
        self.ui_use_bg_color: bool = False
        self.ui_theme: str = "terminal"
        self.ui_app_global_padding: int = 0
        self.ui_msg_h_padding: int = 1
        self.ui_msg_v_margin: int = 1
        self.ui_box_style: str = "square"
        self.ui_box_style_focused: str = "square"
        self.ui_scroll_lines_per_notch: int = 3
        self.ui_scroll_touchpad_speed: float = 0.1
        self.ui_scroll_touchpad_event_threshold: int = 2
        self.ui_scroll_alt_multiplier: float = 3.0
        self.ui_metrics_refresh_interval: float = 0.1
        self.ui_status_bar_fields: list[str] = ["endpoint_model", "role", "context", "cost", "sandbox"]
        self.ui_status_bar_separator: str = "  "
        self.ui_notice_lines: int = 2
        self.ui_model_glyph: str = ""
        self.ui_model_prefix: str = ""
        self.ui_sandbox_glyph: str = "⬢"
        self.ui_sandbox_prefix: str = "sandbox:"
        self.ui_sandbox_active_color: str = "SUCCESS"
        self.ui_sandbox_inactive_color: str = "WARNING"
        self.ui_thought_min_tokens: int = 0
        self.ui_show_banner: bool = True
        self.ui_stream_smoothing: bool = True
        self.ui_smooth_target_fps: int = 60
        self.target_fps: int = 60

        # Debug / reasoning.
        self.debug_log_enabled: bool = False

        # Context building.
        self.context_max_files: int = 500
        self.context_max_depth: int = 4
        self.context_ignore_gitignore: bool = False
        self.context_max_image_mb: float = 5
        self.context_sessions: int = 10
        self.context_compact_filter_thoughts: bool = True
        self.context_compact_filter_tool_calls: bool = True
        self.context_compact_at: float = 0.8
        self.context_compact_stop_at: float = 0.85
        self.context_compact_keep: float = 0.1
        self.context_diff_context: int | str = 3

        # Style tables.
        self.markdown_styles: Dict[str, Dict[str, Any]] = {
            k: dict(v) for k, v in DEFAULT_MARKDOWN_STYLES.items()
        }
        self.syntax_highlight_styles: Dict[str, Dict[str, str]] = {
            k: dict(v) for k, v in DEFAULT_SYNTAX_HIGHLIGHT_STYLES.items()
        }

    # -- loading -------------------------------------------------------------

    def _dir(self) -> Path:
        return self._config_dir or get_config_dir()

    def section_file(self, section: str) -> Path:
        """Path to a section file within this config's directory."""
        return self._dir() / CONFIG_FILES[section]

    def _state_file(self) -> Path:
        return self._state_path or get_state_path()

    def reload(self) -> list[str]:
        """Reload every section file and state from disk, returning errors.

        Any value that fails validation keeps its default; the rest of the
        file is still applied.
        """
        self._apply_defaults()
        self.load_errors = []
        _load_flat_file(self.section_file("ui"), _UI_SPEC, self, self.load_errors)
        _load_flat_file(self.section_file("context"), _CONTEXT_SPEC, self, self.load_errors)
        _load_flat_file(self.section_file("debug"), _DEBUG_SPEC, self, self.load_errors)
        _load_styles_file(self.section_file("styles"), self, self.load_errors)
        _load_servers_file(self.section_file("servers"), self, self.load_errors)
        _load_themes_file(self.section_file("theme"), self, self.load_errors)
        _load_state_file(self._state_file(), self, self.load_errors)
        return self.load_errors

    # -- state mutations -----------------------------------------------------

    def save_model_selection(self, server: str, model: Optional[str]) -> None:
        """Persist the selected model for a specific server."""
        if model:
            self.model_selection[server] = model
        else:
            self.model_selection.pop(server, None)
        self._save_state()

    def save_effort(self, server: str, model: str, effort: Optional[str]) -> None:
        """Persist the reasoning effort for a server's model (None clears it)."""
        per_model = self.efforts.setdefault(server, {})
        if effort:
            per_model[model] = effort
        else:
            per_model.pop(model, None)
        if not per_model:
            self.efforts.pop(server, None)
        self._save_state()

    def get_effort(self, server: str, model: Optional[str]) -> Optional[str]:
        """Saved effort for a server's model (``None`` with no model)."""
        return self.efforts.get(server, {}).get(model) if model else None

    def save_active_theme(self, name: str) -> None:
        """Persist the selected color theme (state, not intent)."""
        self.active_theme = name
        self._save_state()

    def get_active_theme(self) -> str:
        """Effective theme name: state selection, then ``ui.toml``, else terminal."""
        return self.active_theme or self.ui_theme or "terminal"

    def _save_state(self) -> None:
        path = self._state_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        data: Dict[str, Any] = {}
        if self.active_server:
            data["last_server"] = self.active_server
        if self.model_selection:
            data["last_model"] = dict(self.model_selection)
        if self.active_theme:
            data["active_theme"] = self.active_theme
        if self.efforts:
            data["effort"] = {server: dict(m) for server, m in self.efforts.items()}
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
        _atomic_write_bytes(path, toml.dumps(data).encode("utf-8"), mode)

    def set_active_server(self, name: str) -> None:
        """Make ``name`` the active server (its model is ``last_model``)."""
        if name not in self.servers:
            raise KeyError(name)
        self.active_server = name
        self._save_state()

    def ensure_section_file(self, section: str) -> Path:
        """Create a section file from its commented template if missing.

        Existing flat-section files are also synced (missing keys inserted,
        retired keys removed) so ``/config <section>`` always shows current
        keys.
        """
        if section not in CONFIG_FILES:
            raise KeyError(section)
        path = self.section_file(section)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_bytes(path, DEFAULT_CONFIG_TEMPLATES[section].encode("utf-8"),
                                0o644)
        elif section in _FLAT_SECTION_SYNC:
            template, retired = _FLAT_SECTION_SYNC[section]
            self.last_sync_skips = [
                (s, reason) for s, reason in self.last_sync_skips if s != section]
            try:
                with _sync_lock(self._dir()):
                    _sync_flat_file(path, template, retired)
            except _SyncSkipped as exc:
                self.last_sync_skips.append((section, str(exc)))
            except Exception as exc:        # fail closed, never crash startup
                self.last_sync_skips.append((section, f"unexpected error: {exc}"))
        return path

    def sync_section_files(self) -> list[str]:
        """Sync every existing flat section file; return changed sections.

        Called on startup so keys added (or retired) since a user's config was
        first written appear in their file. Files are replaced atomically and
        abandoned untouched if verification or the concurrent-change guard
        fails; those sections land in ``last_sync_skips``.
        """
        changed: list[str] = []
        self.last_sync_skips = []
        try:
            with _sync_lock(self._dir()):
                for section, (template, retired) in _FLAT_SECTION_SYNC.items():
                    try:
                        if _sync_flat_file(self.section_file(section), template, retired):
                            changed.append(section)
                    except _SyncSkipped as exc:
                        self.last_sync_skips.append((section, str(exc)))
                    except Exception as exc:    # fail closed, never crash startup
                        self.last_sync_skips.append(
                            (section, f"unexpected error: {exc}"))
        except _SyncSkipped as exc:
            self.last_sync_skips.append(("*", str(exc)))
        except Exception as exc:                # fail closed, never crash startup
            self.last_sync_skips.append(("*", f"unexpected error: {exc}"))
        return changed

    # -- read helpers --------------------------------------------------------

    def get_model_for_server(self, server: str) -> Optional[str]:
        """The model last selected on a server (``None`` if never)."""
        return self.model_selection.get(server)



def _read_toml(path: Path, errors: list[str]) -> Optional[dict]:
    """Read a TOML file into a dict, reporting parse/shape errors."""
    if not path.exists():
        return None
    try:
        data = toml.load(path)
    except (toml.TomlDecodeError, OSError) as exc:
        errors.append(f"{path.name}: {exc}")
        return None
    if not isinstance(data, dict):
        errors.append(f"{path.name}: top level must be a table")
        return None
    return data


def _load_flat_file(path: Path, spec: Dict[str, tuple[str, str]],
                    config: Config, errors: list[str]) -> None:
    """Load a whole-file flat key/value section (ui/context/debug)."""
    data = _read_toml(path, errors)
    if data is None:
        return
    for key, value in data.items():
        entry = spec.get(key)
        if entry is None:
            errors.append(f"{path.name}: unknown key '{key}'")
            continue
        attr, kind = entry
        coerced, error = _coerce(kind, value)
        if error:
            errors.append(f"{path.name}: {key} {error}")
            continue
        setattr(config, attr, coerced)


def _load_styles_file(path: Path, config: Config, errors: list[str]) -> None:
    """Load ``styles.toml`` with its two style tables."""
    data = _read_toml(path, errors)
    if data is None:
        return
    for section in data:
        if section not in _STYLE_SECTIONS:
            errors.append(f"{path.name}: unknown section [{section}]")
    _merge_style_table(config, data, path.name, "markdown_styles",
                       config.markdown_styles, errors)
    _merge_style_table(config, data, path.name, "syntax_highlight",
                       config.syntax_highlight_styles, errors)


def _load_servers_file(path: Path, config: Config, errors: list[str]) -> None:
    """Load ``servers.toml`` (one ``[servers.<name>]`` table per server)."""
    data = _read_toml(path, errors)
    if data is None:
        return
    for section in data:
        if section != "servers":
            errors.append(f"{path.name}: unknown section [{section}]")
    _load_servers(config, data, path.name, errors)


def _load_servers(config: Config, data: dict, filename: str,
                  errors: list[str]) -> None:
    table = data.get("servers")
    if table is None:
        return
    if not isinstance(table, dict):
        errors.append(f"{filename}: [servers] must be a table")
        return
    for name, server in table.items():
        where = f"{filename}: [servers.{name}]"
        if not isinstance(server, dict):
            errors.append(f"{where} must be a table")
            continue
        skip = False
        # The type selects the provider. It is only ever implied by a table
        # named after a type (``[servers.openrouter]``); never otherwise guessed.
        if "type" not in server and name in REGISTRY:
            server = {**server, "type": name}
        server_type = server.get("type")
        cls = REGISTRY.get(server_type) if isinstance(server_type, str) else None
        if server_type is None:
            errors.append(f"{where}.type is required unless the table is named after a type "
                          f"({', '.join(REGISTRY)}); server skipped")
        elif server_type in _RETIRED_SERVER_TYPES:
            errors.append(f"{where}.type '{server_type}' is no longer supported "
                          f"({', '.join(REGISTRY)}); server skipped")
        elif cls is None:
            errors.append(f"{where}.type unknown server type '{server_type}'; server skipped")
        for key in server:
            if key in _RETIRED_SERVER_KEYS:
                errors.append(
                    f"{where}.{key} was replaced by {_RETIRED_SERVER_KEYS[key]}; server skipped")
                skip = True
            elif key in _SERVER_KEYS or (cls is not None and key in cls.extra_keys):
                continue
            elif cls is not None and any(key in c.extra_keys for c in REGISTRY.values()):
                owners = [t for t, c in REGISTRY.items() if key in c.extra_keys]
                errors.append(f"{where}.{key} is only supported for {', '.join(owners)} servers")
            else:
                errors.append(f"{where} unknown key '{key}'")
        # No guessed address: only llama.cpp has a default (its own port).
        if (cls is not None and not cls.fixed_url and not cls.default_url
                and "base_url" not in server):
            errors.append(f"{where}.base_url is required for {server_type} servers; "
                          "server skipped")
            skip = True
        if cls is not None and cls.fixed_url and "base_url" in server:
            errors.append(f"{where}.base_url is not used: {server_type} servers always use "
                          f"{cls.fixed_url} (remove the line)")
        for key in _SERVER_STR_KEYS:
            if key in server and not isinstance(server[key], str):
                errors.append(f"{where}.{key} must be a string")
        for key in _SERVER_INT_KEYS:
            if key in server and (isinstance(server[key], bool)
                                  or not isinstance(server[key], int)):
                errors.append(f"{where}.{key} must be an integer")
        for key in _SERVER_FLOAT_KEYS:
            if key in server and (isinstance(server[key], bool)
                                  or not isinstance(server[key], (int, float))):
                errors.append(f"{where}.{key} must be a number")
        if cls is not None and "models" in cls.extra_keys:
            skip = not _valid_routing(server, name, where, errors) or skip
        if cls is None or skip:
            continue
        config.servers[name] = dict(server)


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _valid_routing(server: dict, name: str, where: str, errors: list[str]) -> bool:
    """Check OpenRouter's ``models``, ``providers`` and ``providers_by_model``;
    ``False`` (server skipped) when one is malformed or in the old shape."""
    models = server.get("models", [])
    if isinstance(models, dict):
        errors.append(
            f"{where}.models is now a list: models = [\"<id>\", ...]; per-model "
            f"providers go in [servers.{name}.providers_by_model]; server skipped")
        return False
    if not _is_str_list(models):
        errors.append(f"{where}.models must be a list of model ids; server skipped")
        return False
    if "providers" in server and not _is_str_list(server["providers"]):
        errors.append(f"{where}.providers must be a list of provider slugs; server skipped")
        return False
    by_model = server.get("providers_by_model", {})
    if not isinstance(by_model, dict) or not all(_is_str_list(v) for v in by_model.values()):
        errors.append(f"{where}.providers_by_model must map model ids to lists of "
                      "provider slugs; server skipped")
        return False
    for model_id in by_model:
        if model_id not in models:
            errors.append(f"{where}.providers_by_model: '{model_id}' is not in models "
                          "(entry not used)")
    return True


def _load_themes_file(path: Path, config: Config, errors: list[str]) -> None:
    """Load ``themes.toml`` (one ``[themes.<name>]`` palette table per theme)."""
    data = _read_toml(path, errors)
    if data is None:
        return
    for section in data:
        if section != "themes":
            errors.append(f"{path.name}: unknown section [{section}]")
    table = data.get("themes")
    if table is None:
        return
    if not isinstance(table, dict):
        errors.append(f"{path.name}: [themes] must be a table")
        return
    for name, palette in table.items():
        where = f"{path.name}: [themes.{name}]"
        if not isinstance(palette, dict):
            errors.append(f"{where} must be a table")
            continue
        normalized: Dict[str, Any] = {}
        for key, value in palette.items():
            palette_key = key.upper()
            if palette_key not in _THEME_PALETTE:
                errors.append(f"{where} unknown color '{key}'")
                continue
            error = _coerce_theme_color(value)
            if error:
                errors.append(f"{where}.{key} {error}")
                continue
            normalized[palette_key] = value
        config.themes[name] = normalized


def _merge_style_table(config: Config, data: dict, filename: str, section: str,
                       target: Dict[str, Dict[str, Any]], errors: list[str]) -> None:
    table = data.get(section)
    if table is None:
        return
    if not isinstance(table, dict):
        errors.append(f"{filename}: [{section}] must be a table")
        return
    for element, style in table.items():
        if element not in target:
            errors.append(f"{filename}: [{section}] unknown element '{element}'")
            continue
        if not isinstance(style, dict):
            errors.append(f"{filename}: [{section}].{element} must be a table")
            continue
        target[element].update(style)


def _load_state_file(path: Path, config: Config, errors: list[str]) -> None:
    if not path.exists():
        return
    try:
        data = toml.load(path)
    except (toml.TomlDecodeError, OSError) as exc:
        errors.append(f"{path.name}: {exc}")
        return
    if not isinstance(data, dict):
        errors.append(f"{path.name}: top level must be a table")
        return

    for section in data:
        if section not in _STATE_SECTIONS:
            errors.append(f"{path.name}: unknown key '{section}'")

    last_server = data.get("last_server")
    if last_server is not None:
        if isinstance(last_server, str):
            config.active_server = last_server
        else:
            errors.append(f"{path.name}: last_server must be a string")

    active_theme = data.get("active_theme")
    if active_theme is not None:
        if isinstance(active_theme, str):
            config.active_theme = active_theme
        else:
            errors.append(f"{path.name}: active_theme must be a string")

    last_model = data.get("last_model")
    if last_model is not None:
        if isinstance(last_model, dict) and all(
                isinstance(v, str) for v in last_model.values()):
            config.model_selection = dict(last_model)
        else:
            errors.append(f"{path.name}: last_model must map server names to model ids")

    effort = data.get("effort")
    if effort is not None:
        if isinstance(effort, dict) and all(
                isinstance(m, dict) and all(isinstance(v, str) for v in m.values())
                for m in effort.values()):
            config.efforts = {server: dict(m) for server, m in effort.items()}
        else:
            errors.append(f"{path.name}: effort must map servers to model -> level tables")


# Global config instance, reloadable via reload_config().
config: Config = Config()


def reload_config() -> list[str]:
    """Reload the global config from disk; return validation errors."""
    return config.reload()


def sync_config_files() -> list[str]:
    """Insert new commented keys and drop retired keys in existing flat files.

    Returns the list of changed sections. Safe to call on every startup.
    """
    return config.sync_section_files()
