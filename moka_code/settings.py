"""moka configuration.

Configuration is split into small, single-concern files under
``~/.config/moka/`` so each one stays focused and easy to edit with
``/config <section>`` or ``/edit``:

- ``ui.toml`` — theme, padding, fps (flat keys), then the
  ``[markdown_styles.*]`` / ``[syntax_highlight.*]`` tables
- ``context.toml`` — context building (flat keys)
- ``debug.toml`` — debug logging (flat keys)
- ``servers.toml`` — one ``[servers.<name>]`` table per server
- ``themes/<name>.toml`` — one palette per theme file
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
# Each key is a highlight type; ``fg`` is a theme color name (follows the
# active theme) or a hex string.
DEFAULT_SYNTAX_HIGHLIGHT_STYLES: Dict[str, Dict[str, str]] = {
    "keyword":   {"fg": "KEYWORD"},
    "function":  {"fg": "FUNCTION"},
    "string":    {"fg": "STRING"},
    "comment":   {"fg": "MUTED"},
    "number":    {"fg": "NUMBER"},     # numbers and constants (true, None, MAX)
    "type":      {"fg": "TYPE"},       # Capitalized names, builtin types, Rust 'a
    "decorator": {"fg": "FUNCTION"},   # @decorator, #[attribute]
    "variable":  {"fg": "TYPE"},       # $name in shell and PHP
    "key":       {"fg": "TYPE"},       # JSON / YAML / TOML keys, CSS properties
    "operator":  {"fg": "KEYWORD"},    # = == -> && | (not punctuation, not a lone :)
    "added":     {"fg": "SUCCESS"},    # diff + lines
    "removed":   {"fg": "ERROR"},      # diff - lines
    "hunk":      {"fg": "TYPE"},       # diff @@ headers
    # "plain" (unset): ordinary code uses the normal text color.
}

CONFIG_DIR_ENV = "MOKA_CONFIG_DIR"
ROLES_DIRNAME = "roles"
THEMES_DIRNAME = "themes"
STATE_FILENAME = "state.toml"

#: Config section -> file name. The section is what ``/config <section>`` takes.
CONFIG_FILES = {
    "ui": "ui.toml",
    "context": "context.toml",
    "debug": "debug.toml",
    "servers": "servers.toml",
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


def _rule(name: str, width: int = 60) -> str:
    """A template section heading: ``## ── Name ────…`` to ``width`` columns."""
    head = f"## ── {name} "
    return head + "─" * max(3, width - len(head))


# Starts the tables part of ``ui.toml``: sync keeps flat keys above it.
_TABLES_HEADING = _rule("Markdown and syntax-highlight styles")

#: The values a ``ui.status_bar`` template can name (``status_presenter`` fills them).
STATUS_BAR_VALUES = ("endpoint", "model", "effort", "role", "context", "cost", "running",
                     "sandbox", "state", "workspace", "spinner")

DEFAULT_STATUS_BAR = ("[{spinner} ]{model}[:{effort}][  mode:{role}][  ctx {context}]"
                      "[  {cost}] | [  shells:{running}][  sandbox:{sandbox}]")

_STATUS_BAR_BLOCK = f'# status_bar = "{DEFAULT_STATUS_BAR}"' + """
## {endpoint} server · {model} model · {effort} reasoning effort · {role} role
## {context} tokens used/window · {cost} $ spent · {sandbox} sandbox (or none)
## {running} bash commands running (empty when none; Enter on it: /running)
## {state} idle/thinking · {workspace} folder · {spinner} while the server is checked
## [ … ] is left out when a value in it is empty (keep its separator inside),
## and drawn in that value's color. | right-aligns everything after it."""

# Per-section templates written on first use. Everything is commented out so
# the built-in defaults apply until the user uncomments what they need.
# Sections run from the most used to the most technical.
DEFAULT_UI_TOML = f"""\
## Moka UI settings
## Apply with /reload, or /config ui (reloads when the editor exits).
## A line starting with # is a setting to uncomment; ## is help.

{_rule("Transcript")}

# thought_min_tokens = 0                # hide shorter reasoning lines (0 = show all)
# thought_color = "MUTED"               # folded thought line and its bar (palette name or #rrggbb)
# thought_expanded_color = "MUTED"      # unfolded thought text and its bar
## Unfocused, a message shows the bullet on its first row (nothing when its
## type's toggle is false) and a muted wall below it; focused, a thick wall.
# bullet = "•"                          # the unfocused marker
# wall_char = "│"                       # muted line below the bullet ("" or " " = none)
# focus_wall_char = "┃"                 # focused: every row, in the focus color
# code_prefix = "│  "                   # muted, before every row of a code block ("" = none)
# user_bullet = true                    # your messages
# answer_bullet = true                  # answers
# thought_bullet = true                 # thoughts
# tool_bullet = true                    # tool lines and their output
# ask_bullet = true                     # questions and permission prompts
# thought_gap = true                    # blank lines between a thought and what follows it
# tool_gap = false                      # blank lines between tool lines
# stack_activity = true                 # with thought_gap: no blank line between a thought and a tool line
# show_banner = true                    # moka art in an empty transcript

{_rule("Look")}

## The theme is chosen with /theme select (kept in state.toml).
# use_bg_color = false                  # paint the theme background
# app_global_padding = 0                # blank cells around the whole screen
# msg_h_padding = 1                     # cells between a message's bar and its text
# msg_v_margin = 1                      # blank lines between turns (assistant output is continuous)
# max_input_height = 8                  # input grows with wrapped lines, then scrolls

{_rule("Status bar")}

{_STATUS_BAR_BLOCK}
# sandbox_active_color = "SUCCESS"      # {{sandbox}} while one is active (palette name or #rrggbb)
# sandbox_inactive_color = "WARNING"    # {{sandbox}} while tools run unsandboxed
# notice_lines = 2                      # top band height; past it, "+N more · /activity"

{_rule("Streaming")}

# stream_smoothing = true               # reveal streamed text smoothly
# smooth_target_fps = 60                # reveal cadence (independent of render fps)

{_rule("Scrolling")}

# scroll_lines_per_notch = 3            # lines per mouse-wheel notch
# scroll_touchpad_speed = 0.1           # multiplier when events come in a burst (touchpad)
# scroll_touchpad_event_threshold = 2   # events per frame that count as a touchpad burst
# scroll_alt_multiplier = 3.0           # multiplier while Alt is held

{_rule("Rendering")}

# target_fps = 60                       # screen refreshes per second at most
# metrics_refresh_interval = 0.1        # seconds between usage updates while streaming

{_TABLES_HEADING}
## Tables come after every setting above. Keys: fg, bg (a hex color, or a
## theme color name such as "MUTED"), bold, italic, underline.

# [markdown_styles.header1]
# fg   = "#FF79C6"
# bold = true

# [markdown_styles.bold]
# fg   = "#FFD700"
# bold = true

# [markdown_styles.code]
# fg = "#9CDCFE"

# [syntax_highlight.keyword]
# fg = "KEYWORD"
"""

DEFAULT_CONTEXT_TOML = f"""\
## Moka context settings
## Apply with /reload, or /config context.
## A line starting with # is a setting to uncomment; ## is help.

{_rule("@ file picker")}

# max_files = 500                       # most entries the picker lists
# max_depth = 4                         # folder levels it walks
# ignore_gitignore = false              # also list what .gitignore hides
# ignore_dotfiles = false               # hide .env, .github/ and other dot names (.git/ always)

{_rule("Diffs")}

# diff_context = 3                      # unchanged lines around each change (/diff and edit lines)
# diff_full_file = false                # /diff shows the whole file, changes marked in place

{_rule("Images and sessions")}

# max_image_mb = 5                      # largest image a message can attach
# sessions = 10                         # conversations saved per project (/session); 0 = off

{_rule("Compaction")}

# compact_filter_thoughts = true        # leave the model's reasoning out of the summary input
# compact_filter_tool_calls = true      # each tool call as one line, without its output
# compact_at = 0.8                      # compact when a turn ends over this share of the context window; 0 = off
# compact_stop_at = 0.85                # ... or pause a running turn over this share, compact, then resume it
# compact_keep = 0.1                    # share of the window kept word for word (the newest turns)
"""

DEFAULT_DEBUG_TOML = f"""\
## Moka debug settings
## Apply with /reload, or /config debug.
## A line starting with # is a setting to uncomment; ## is help.

{_rule("Logging")}

# log_enabled = false                   # write debug_stream.log
"""

# The servers template: a legend, then one section per provider
# (``Endpoint.label`` and ``Endpoint.template``), in registry order.
DEFAULT_SERVERS_TOML = """\
## Moka servers
## Apply with /reload, or /config servers.
## A line starting with # is a setting to uncomment; ## is help.
## One table per server; the name is yours (shown by /model).
## A table named after its type (openrouter, deepseek) needs no type.

""" + "".join(f"{_rule(cls.label)}\n\n{cls.template}\n" for cls in REGISTRY.values()) + f"""\
{_rule("Technical settings")}

## Any server: api_key ("$NAME" reads the NAME environment variable,
## anything else is the key itself), timeout = 30.0, retry_attempts = 3,
## retry_delay = 2.0.
"""

# Help heading a ``themes/<name>.toml`` file (written by ``/theme create`` and
# ``/theme edit``, followed by the palette lines).
THEME_FILE_HELP = f"""\
## Moka color theme; the file name is the theme name.
## Select with /theme select; apply edits with /reload (or /theme edit).

{_rule("Palette")}

## Each palette entry is a hex RGB string or an ANSI slot table:
##   USER       = "#4EC9B0"
##   MUTED      = {{ ansi = 90 }}            # standard ANSI fg code
##   BACKGROUND = {{ ansi = 39, bg = 49 }}   # fg and/or bg codes
##
## Missing entries inherit from the built-in of the same name (or terminal);
## USER_TEXT and ASSISTANT_TEXT (message text) default to this file's DEFAULT.
"""

DEFAULT_CONFIG_TEMPLATES = {
    "ui": DEFAULT_UI_TOML,
    "context": DEFAULT_CONTEXT_TOML,
    "debug": DEFAULT_DEBUG_TOML,
    "servers": DEFAULT_SERVERS_TOML,
}


# --- config schema ----------------------------------------------------------
# Each spec maps a TOML key to (runtime attribute, kind, default). Kinds drive
# both validation and coercion so a malformed value yields a reported error
# rather than a silently ignored setting. The default is the single source: a
# guard test checks the template's commented value against it.

_UI_SPEC: Dict[str, tuple[str, str, Any]] = {
    # Transcript
    "thought_min_tokens": ("ui_thought_min_tokens", "int", 0),
    "thought_color": ("ui_thought_color", "color", "MUTED"),
    "thought_expanded_color": ("ui_thought_expanded_color", "color", "MUTED"),
    "bullet": ("ui_bullet", "str", "•"),
    "wall_char": ("ui_wall_char", "str", "│"),
    "focus_wall_char": ("ui_focus_wall_char", "str", "┃"),
    "code_prefix": ("ui_code_prefix", "str", "│  "),
    "user_bullet": ("ui_user_bullet", "bool", True),
    "answer_bullet": ("ui_answer_bullet", "bool", True),
    "thought_bullet": ("ui_thought_bullet", "bool", True),
    "tool_bullet": ("ui_tool_bullet", "bool", True),
    "ask_bullet": ("ui_ask_bullet", "bool", True),
    "thought_gap": ("ui_thought_gap", "bool", True),
    "tool_gap": ("ui_tool_gap", "bool", False),
    "stack_activity": ("ui_stack_activity", "bool", True),
    "show_banner": ("ui_show_banner", "bool", True),
    # Look
    "use_bg_color": ("ui_use_bg_color", "bool", False),
    "app_global_padding": ("ui_app_global_padding", "int", 0),
    "msg_h_padding": ("ui_msg_h_padding", "int", 1),
    "msg_v_margin": ("ui_msg_v_margin", "int", 1),
    "max_input_height": ("ui_max_input_height", "int", 8),
    # Status bar
    "status_bar": ("ui_status_bar", "status_bar", DEFAULT_STATUS_BAR),
    "sandbox_active_color": ("ui_sandbox_active_color", "color", "SUCCESS"),
    "sandbox_inactive_color": ("ui_sandbox_inactive_color", "color", "WARNING"),
    "notice_lines": ("ui_notice_lines", "int", 2),
    # Streaming
    "stream_smoothing": ("ui_stream_smoothing", "bool", True),
    "smooth_target_fps": ("ui_smooth_target_fps", "int", 60),
    # Scrolling
    "scroll_lines_per_notch": ("ui_scroll_lines_per_notch", "int", 3),
    "scroll_touchpad_speed": ("ui_scroll_touchpad_speed", "float", 0.1),
    "scroll_touchpad_event_threshold": ("ui_scroll_touchpad_event_threshold", "int", 2),
    "scroll_alt_multiplier": ("ui_scroll_alt_multiplier", "float", 3.0),
    # Rendering
    "target_fps": ("ui_target_fps", "int", 60),
    "metrics_refresh_interval": ("ui_metrics_refresh_interval", "float", 0.1),
}

_CONTEXT_SPEC: Dict[str, tuple[str, str, Any]] = {
    # @ file picker
    "max_files": ("context_max_files", "int", 500),
    "max_depth": ("context_max_depth", "int", 4),
    "ignore_gitignore": ("context_ignore_gitignore", "bool", False),
    "ignore_dotfiles": ("context_ignore_dotfiles", "bool", False),
    # Diffs
    "diff_context": ("context_diff_context", "lines", 3),
    "diff_full_file": ("context_diff_full_file", "bool", False),
    # Images and sessions
    "max_image_mb": ("context_max_image_mb", "int_or_float", 5),
    "sessions": ("context_sessions", "int", 10),
    # Compaction
    "compact_filter_thoughts": ("context_compact_filter_thoughts", "bool", True),
    "compact_filter_tool_calls": ("context_compact_filter_tool_calls", "bool", True),
    "compact_at": ("context_compact_at", "float", 0.8),
    "compact_stop_at": ("context_compact_stop_at", "float", 0.85),
    "compact_keep": ("context_compact_keep", "float", 0.1),
}

_DEBUG_SPEC: Dict[str, tuple[str, str, Any]] = {
    "log_enabled": ("debug_log_enabled", "bool", False),
}

# Keys removed from a flat section but kept here so existing user files can be
# cleaned up on startup. Add a key here when you delete it from its ``*_SPEC``
# and ``DEFAULT_*_TOML`` (see "Adding or deprecating a config key" in AGENTS.md).
_RETIRED_UI: set[str] = {  # nothing read these: spinner (elapsed-time labels), console, per-message metrics
    "spinner_fps", "debug_console_height",
    "show_metrics", "metrics_show_tokens", "metrics_show_speed", "metrics_show_ttft",
    # Superseded by show_provider/provider_separator/show_effort/effort_separator.
    "model_glyph", "model_prefix",
    # Only the popups had a border style left; every frame is square.
    "box_style", "box_style_focused",
    # The theme is state (/theme select); a ui.toml value moves there once.
    "theme",
    # Replaced by the status_bar template.
    "status_bar_fields", "status_bar_separator", "show_provider", "provider_separator",
    "show_effort", "effort_separator", "sandbox_glyph", "sandbox_prefix",
    # Replaced by bullet and the per-type *_bullet toggles.
    "thought_bar", "answer_bar",
}
# preserve_reasoning_traces: reasoning is no longer sent back (PLAN.md step 2).
_RETIRED_CONTEXT: set[str] = {"preserve_reasoning_traces", "format"}  # format: the project tree was never sent
_RETIRED_DEBUG: set[str] = set()

# Flat sections that can be synced line-by-line against their template. Each
# maps to ``(template, retired_keys)``. Structured files (servers, themes) and
# the tables part of ``ui.toml`` are user-authored and deliberately not synced.
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
            # Each missing line goes after the template line before it.
            at = start + 1
            for line in help_lines:
                found = next((i for i in range(at, end)
                              if lines[i].rstrip("\r\n") == line), None)
                if found is None:
                    lines.insert(at, line + "\n")
                    end += 1
                    at += 1
                else:
                    at = found + 1


_TABLE_LINE_RE = re.compile(r"^\s*#?\s*\[")


def _split_tables(text: str) -> "tuple[str, str]":
    """Split a file into its flat keys and its tables part, which starts at
    ``_TABLES_HEADING`` or the first (possibly commented) table header."""
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.rstrip("\r\n") == _TABLES_HEADING or _TABLE_LINE_RE.match(line):
            return "".join(lines[:index]), "".join(lines[index:])
    return text, ""


def _merge_template(original: str, template: str, retired: "set[str]") -> str:
    """Insert missing template blocks/keys without rewriting user lines.

    Only the flat part is merged; the tables part is kept as written (a file
    without one gets the template's commented tables)."""
    head, tail = _split_tables(original)
    template, template_tail = _split_tables(template)
    added_tail = not tail and bool(template_tail)
    if added_tail:
        tail = template_tail
    lines = head.splitlines(keepends=True)
    blocks = _template_blocks(template)
    order = [key for block in blocks for key, _ in block.keys]
    known: dict[str, str] = {}
    for block in blocks:
        for key, line in block.keys:
            known.setdefault(key, line)

    # Drop retired keys and refresh commented template keys (they only show
    # the default, which may have changed); set lines are kept byte-for-byte.
    kept: list[str] = []
    for line in lines:
        match = _KEY_LINE_RE.match(line)
        if match and match.group(1) in retired:
            continue
        if match and _is_commented_key(line) and match.group(1) in known:
            line = known[match.group(1)] + "\n"
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
    merged = "".join(kept)
    if tail and (merged != head or added_tail) and merged.strip():
        merged = merged.rstrip("\n") + "\n\n"
    return merged + tail


def _is_commented_key(line: str) -> bool:
    """A ``# key = ...`` line (a default to uncomment), not ``##`` help."""
    stripped = line.lstrip()
    return stripped.startswith("#") and not stripped.startswith("##")


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
    refreshed = {key for key, _ in _template_key_lines(template)}
    kept_lines = [line for line in original_lines
                  if (not (match := _KEY_LINE_RE.match(line))
                      or (match.group(1) not in retired
                          and not (_is_commented_key(line)
                                   and match.group(1) in refreshed)))
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
    "type", "base_url", "api_key",
    "timeout", "retry_attempts", "retry_delay",
}
# Retired keys -> what replaces them (retired types: no replacement). A server still using one is
# reported and skipped (servers.toml is never rewritten by moka).
_RETIRED_SERVER_KEYS = {
    "max_context": "nothing (the context window comes from the server)",
    "provider": "'providers = [...]' (only these, in order)",
    "enabled_models": "'models = [...]'",
    "model_providers": "[servers.<name>.providers_by_model]",
    "api_key_env": "'api_key = \"$NAME\"'",
}
_RETIRED_SERVER_TYPES = {"openai"}
_SERVER_STR_KEYS = {"type", "base_url", "api_key"}
_SERVER_INT_KEYS = {"retry_attempts"}
_SERVER_FLOAT_KEYS = {"timeout", "retry_delay"}

# ``active_model`` (the model is per server, ``last_model``) and
# ``model_catalog`` (discovery is live, in memory) are retired keys, accepted
# and ignored so older state files load without errors; the next save drops them.
_STATE_SECTIONS = {"last_server", "active_model", "last_model", "model_catalog",
                   "active_theme", "effort", "last_role"}

# Palette keys a ``themes/<name>.toml`` file may define (``colors.PALETTE_FIELDS``).
_THEME_PALETTE = {
    "BACKGROUND", "DEFAULT", "MUTED", "ERROR", "WARNING", "SUCCESS",
    "PERMISSION", "TOOL", "USER", "ASSISTANT", "FOCUSED", "USER_TEXT", "ASSISTANT_TEXT",
    "HEADING", "EMPHASIS", "CODE", "KEYWORD", "FUNCTION", "STRING", "NUMBER", "TYPE",
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
_COLOR_NAMES = _THEME_PALETTE


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
    if kind == "status_bar":
        if not isinstance(value, str):
            return None, "must be a string"
        unknown = sorted(set(re.findall(r"\{([A-Za-z_]+)\}", value)) - set(STATUS_BAR_VALUES))
        if unknown:
            return None, (f"names unknown values {', '.join('{' + n + '}' for n in unknown)} "
                          f"(known: {', '.join(STATUS_BAR_VALUES)})")
        depth = 0
        for char in value:
            depth += {"[": 1, "]": -1}.get(char, 0)
            if depth < 0:
                break
        if depth:
            return None, "has unbalanced [ ]"
        return value, None
    if kind == "lines":
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value, None
        return None, "must be a number of lines (0 or more)"
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
        # Why a server cannot be joined (memory only), by server: the latest
        # failed probe (model listing, connection check or chat request),
        # cleared by the next one that succeeds.
        self.server_errors: Dict[str, str] = {}
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
        # Role of the last conversation (state), tried first at startup.
        self.last_role: Optional[str] = None

        # The flat section settings (``ui_*``, ``context_*``, ``debug_*``):
        # their defaults live in the specs.
        for spec in (_UI_SPEC, _CONTEXT_SPEC, _DEBUG_SPEC):
            for attr, _kind, default in spec.values():
                setattr(self, attr, list(default) if isinstance(default, list) else default)

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

    def themes_dir(self) -> Path:
        """Directory of one-file-per-theme palettes."""
        return self._dir() / THEMES_DIRNAME

    def theme_file(self, name: str) -> Path:
        return self.themes_dir() / f"{name}.toml"

    def _state_file(self) -> Path:
        return self._state_path or get_state_path()

    def reload(self) -> list[str]:
        """Reload every section file and state from disk, returning errors.

        Any value that fails validation keeps its default; the rest of the
        file is still applied.
        """
        self._apply_defaults()
        self.load_errors = []
        ui = _load_flat_file(self.section_file("ui"), _UI_SPEC, self, self.load_errors,
                             tables=_STYLE_SECTIONS, retired=_RETIRED_UI)
        _load_styles(ui or {}, CONFIG_FILES["ui"], self, self.load_errors)
        _load_flat_file(self.section_file("context"), _CONTEXT_SPEC, self, self.load_errors,
                        retired=_RETIRED_CONTEXT)
        _load_flat_file(self.section_file("debug"), _DEBUG_SPEC, self, self.load_errors,
                        retired=_RETIRED_DEBUG)
        _load_servers_file(self.section_file("servers"), self, self.load_errors)
        _load_themes_dir(self.themes_dir(), self, self.load_errors)
        _load_state_file(self._state_file(), self, self.load_errors)
        # ``theme`` left ui.toml for state: a value chosen there (and never
        # with /theme select) carries over before the sync removes the line.
        legacy_theme = (ui or {}).get("theme")
        if isinstance(legacy_theme, str) and legacy_theme and not self.active_theme:
            try:
                self.save_active_theme(legacy_theme)
            except OSError:
                self.active_theme = legacy_theme
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

    def save_last_role(self, name: str) -> None:
        """Persist the role a conversation switched to (the next start uses it)."""
        if name == self.last_role:
            return
        self.last_role = name
        self._save_state()

    def get_active_theme(self) -> str:
        """Effective theme name: the ``/theme select`` choice (state), else terminal."""
        return self.active_theme or "terminal"

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
        if self.last_role:
            data["last_role"] = self.last_role
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



def _read_toml(path: Path, errors: list[str], where: Optional[str] = None) -> Optional[dict]:
    """Read a TOML file into a dict, reporting parse/shape errors as ``where``
    (the file name by default)."""
    if not path.exists():
        return None
    where = where or path.name
    try:
        data = toml.load(path)
    except (toml.TomlDecodeError, OSError) as exc:
        errors.append(f"{where}: {exc}")
        return None
    if not isinstance(data, dict):
        errors.append(f"{where}: top level must be a table")
        return None
    return data


def _load_flat_file(path: Path, spec: Dict[str, tuple[str, str, Any]],
                    config: Config, errors: list[str],
                    tables: "set[str]" = frozenset(),
                    retired: "set[str]" = frozenset()) -> Optional[dict]:
    """Load a whole-file flat key/value section (ui/context/debug); keys in
    ``tables`` are left to the caller, ``retired`` keys are ignored (the sync
    removes them). Returns the parsed file."""
    data = _read_toml(path, errors)
    if data is None:
        return None
    for key, value in data.items():
        if key in tables or key in retired:
            continue
        entry = spec.get(key)
        if entry is None:
            errors.append(f"{path.name}: unknown key '{key}'")
            continue
        attr, kind, _default = entry
        coerced, error = _coerce(kind, value)
        if error:
            errors.append(f"{path.name}: {key} {error}")
            continue
        setattr(config, attr, coerced)
    return data


def _load_styles(data: dict, filename: str, config: Config, errors: list[str]) -> None:
    """Apply the ``[markdown_styles.*]`` / ``[syntax_highlight.*]`` tables of ``ui.toml``."""
    _merge_style_table(config, data, filename, "markdown_styles",
                       config.markdown_styles, errors)
    _merge_style_table(config, data, filename, "syntax_highlight",
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


def _load_themes_dir(directory: Path, config: Config, errors: list[str]) -> None:
    """Load ``themes/<name>.toml`` (one palette per file; the file name is the theme)."""
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.toml")):
        if path.stem.startswith("."):
            continue
        where = f"{THEMES_DIRNAME}/{path.name}"
        palette = _read_toml(path, errors, where)
        if palette is None:
            continue
        normalized: Dict[str, Any] = {}
        for key, value in palette.items():
            palette_key = key.upper()
            if palette_key not in _THEME_PALETTE:
                errors.append(f"{where}: unknown color '{key}'")
                continue
            error = _coerce_theme_color(value)
            if error:
                errors.append(f"{where}: {key} {error}")
                continue
            normalized[palette_key] = value
        config.themes[path.stem] = normalized


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

    last_role = data.get("last_role")
    if last_role is not None:
        if isinstance(last_role, str):
            config.last_role = last_role
        else:
            errors.append(f"{path.name}: last_role must be a string")

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
