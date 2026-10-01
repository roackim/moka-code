"""moka configuration.

Configuration is split into small, single-concern files under
``~/.config/moka/`` so each one stays focused and easy to edit with
``/config <section>`` or ``/edit``:

- ``ui.toml`` — theme, padding, metrics, fps (flat keys)
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
import toml
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from moka_code.harness.providers import REGISTRY

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


def get_section_path(section: str) -> Path:
    """Path to one config section file (e.g. ``ui`` -> ``ui.toml``)."""
    return get_config_dir() / CONFIG_FILES[section]


def get_roles_dir() -> Path:
    """Directory of one-file-per-role definitions."""
    return get_config_dir() / ROLES_DIRNAME


def get_role_path(name: str) -> Path:
    """Path to ``roles/<name>.toml``."""
    return get_roles_dir() / f"{name}.toml"


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

# theme = "terminal"                    # "terminal" | "pastel"
# use_bg_color = false                  # paint the theme background
# app_global_padding = 0
# msg_h_padding = 1
# msg_v_margin = 1                      # blank lines between turns (assistant output is continuous)
# debug_console_height = 10
# max_input_height = 8                  # input grows with wrapped lines, then scrolls
# box_style = "square"                  # "square" | "double" | "rounded" | "ascii"
# box_style_focused = "square"

## Scrolling

# scroll_lines_per_notch = 3
# scroll_touchpad_speed = 0.1
# scroll_touchpad_event_threshold = 2
# scroll_alt_multiplier = 3.0

## Metrics under the answer

# show_metrics = true
# metrics_show_tokens = false
# metrics_show_speed = true
# metrics_show_ttft = false
# metrics_refresh_interval = 0.1

## Status bar and notices

# status_bar_fields = ["endpoint_model", "role", "context", "cost", "sandbox"]
# notice_lines = 2                      # top band height; past it, "+N more · /activity"

## Sandbox field

# sandbox_glyph = "⬢"                   # before the field; empty hides it (emoji ok)
# sandbox_prefix = "sandbox:"           # before the runtime name; empty hides it
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

## Project context

# format = "tree"                       # "tree" (token-saving) | "flat"
# max_files = 500
# max_depth = 4
# ignore_gitignore = false

## Images and sessions

# max_image_mb = 5                      # largest image a message can attach
# sessions = 10                         # conversations saved per project (/session); 0 = off

## /compact

# compact_filter_thoughts = true        # leave the model's reasoning out of the summary input
# compact_filter_tool_calls = true      # each tool call as one line, without its output

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
    "debug_console_height": ("ui_debug_console_height", "int"),
    "max_input_height": ("ui_max_input_height", "int"),
    "box_style": ("ui_box_style", "str"),
    "box_style_focused": ("ui_box_style_focused", "str"),
    "scroll_lines_per_notch": ("ui_scroll_lines_per_notch", "int"),
    "scroll_touchpad_speed": ("ui_scroll_touchpad_speed", "float"),
    "scroll_touchpad_event_threshold": ("ui_scroll_touchpad_event_threshold", "int"),
    "scroll_alt_multiplier": ("ui_scroll_alt_multiplier", "float"),
    "show_metrics": ("ui_show_metrics", "bool"),
    "metrics_show_tokens": ("ui_metrics_show_tokens", "bool"),
    "metrics_show_speed": ("ui_metrics_show_speed", "bool"),
    "metrics_show_ttft": ("ui_metrics_show_ttft", "bool"),
    "metrics_refresh_interval": ("ui_metrics_refresh_interval", "float"),
    "status_bar_fields": ("ui_status_bar_fields", "str_list"),
    "notice_lines": ("ui_notice_lines", "int"),
    "sandbox_glyph": ("ui_sandbox_glyph", "str"),
    "sandbox_prefix": ("ui_sandbox_prefix", "str"),
    "sandbox_active_color": ("ui_sandbox_active_color", "str"),
    "sandbox_inactive_color": ("ui_sandbox_inactive_color", "str"),
    "thought_min_tokens": ("ui_thought_min_tokens", "int"),
    "show_banner": ("ui_show_banner", "bool"),
    "stream_smoothing": ("ui_stream_smoothing", "bool"),
    "smooth_target_fps": ("ui_smooth_target_fps", "int"),
    "target_fps": ("target_fps", "int"),
}

_CONTEXT_SPEC: Dict[str, tuple[str, str]] = {
    "format": ("context_format", "context_format"),
    "max_files": ("context_max_files", "int"),
    "max_depth": ("context_max_depth", "int"),
    "ignore_gitignore": ("context_ignore_gitignore", "bool"),
    "max_image_mb": ("context_max_image_mb", "int_or_float"),
    "sessions": ("context_sessions", "int"),
    "compact_filter_thoughts": ("context_compact_filter_thoughts", "bool"),
    "compact_filter_tool_calls": ("context_compact_filter_tool_calls", "bool"),
    "diff_context": ("context_diff_context", "diff_context"),
}

_DEBUG_SPEC: Dict[str, tuple[str, str]] = {
    "log_enabled": ("debug_log_enabled", "bool"),
}

# Keys removed from a flat section but kept here so existing user files can be
# cleaned up on startup. Add a key here when you delete it from its ``*_SPEC``
# and ``DEFAULT_*_TOML`` (see "Adding or deprecating a config key" in AGENTS.md).
_RETIRED_UI: set[str] = {"spinner_fps"}  # spinner replaced by elapsed-time labels
# preserve_reasoning_traces: reasoning is no longer sent back (PLAN.md step 2).
_RETIRED_CONTEXT: set[str] = {"preserve_reasoning_traces"}
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


def _sync_flat_file(path: Path, template: str, retired: "set[str]") -> bool:
    """Insert missing commented keys and drop retired ones.

    Line-preserving: user values, comments, and ordering are left alone. Only
    keys named by the template or the ``retired`` set are touched. Returns True
    when the file changed.
    """
    if not path.exists():
        return False
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)

    known = {}
    for key, line in _template_key_lines(template):
        known.setdefault(key, line)

    kept = []
    present: set[str] = set()
    for line in lines:
        match = _KEY_LINE_RE.match(line)
        key = match.group(1) if match else None
        if key is not None:
            if key in retired:
                continue  # drop deprecated key
            present.add(key)
        kept.append(line)

    missing = [key for key in known if key not in present]
    if missing:
        if kept and not kept[-1].endswith("\n"):
            kept[-1] += "\n"
        order = list(known)
        template_index = {key: i for i, key in enumerate(order)}
        positions: dict[str, int] = {}
        for index, line in enumerate(kept):
            match = _KEY_LINE_RE.match(line)
            if match and match.group(1) in known:
                positions.setdefault(match.group(1), index)
        for key in missing:
            insert_at = len(kept)
            for following in order[template_index[key] + 1:]:
                if following in positions:
                    insert_at = positions[following]
                    break
            kept.insert(insert_at, known[key] + "\n")
            positions = {
                pk: (pv + 1 if pv >= insert_at else pv)
                for pk, pv in positions.items()
            }
            positions[key] = insert_at

    updated = "".join(kept)
    if updated == original:
        return False
    path.write_text(updated, encoding="utf-8")
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
_RETIRED_SERVER_TYPES = {"openai", "openai-compatible"}
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


def _coerce(kind: str, value: Any) -> tuple[Any, Optional[str]]:
    """Return ``(coerced, None)`` or ``(None, error)`` for one config value."""
    if kind == "str":
        return (value, None) if isinstance(value, str) else (None, "must be a string")
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
    if kind == "context_format":
        if value not in ("tree", "flat"):
            return None, "must be 'tree' or 'flat'"
        return value, None
    return value, None


class Config:
    """Validated view of the split config files plus disposable ``state.toml``."""

    def __init__(self, config_dir: str | Path | None = None,
                 state_path: str | Path | None = None):
        self._config_dir = Path(config_dir) if config_dir else None
        self._state_path = Path(state_path) if state_path else None
        self.load_errors: list[str] = []
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
        self.ui_debug_console_height: int = 10
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
        self.ui_show_metrics: bool = True
        self.ui_metrics_show_tokens: bool = False
        self.ui_metrics_show_speed: bool = True
        self.ui_metrics_show_ttft: bool = False
        self.ui_metrics_refresh_interval: float = 0.1
        self.ui_status_bar_fields: list[str] = ["endpoint_model", "role", "context", "cost", "sandbox"]
        self.ui_notice_lines: int = 2
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
        self.context_format: Literal["tree", "flat"] = "tree"
        self.context_max_files: int = 500
        self.context_max_depth: int = 4
        self.context_ignore_gitignore: bool = False
        self.context_max_image_mb: float = 5
        self.context_sessions: int = 10
        self.context_compact_filter_thoughts: bool = True
        self.context_compact_filter_tool_calls: bool = True
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
        path.write_text(toml.dumps(data), encoding="utf-8")

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
            path.write_text(DEFAULT_CONFIG_TEMPLATES[section], encoding="utf-8")
        elif section in _FLAT_SECTION_SYNC:
            template, retired = _FLAT_SECTION_SYNC[section]
            _sync_flat_file(path, template, retired)
        return path

    def sync_section_files(self) -> list[str]:
        """Sync every existing flat section file; return changed sections.

        Called on startup so keys added (or retired) since a user's config was
        first written appear in their file.
        """
        changed = []
        for section, (template, retired) in _FLAT_SECTION_SYNC.items():
            if _sync_flat_file(self.section_file(section), template, retired):
                changed.append(section)
        return changed

    def ensure_config_files(self) -> list[Path]:
        """Create every missing section file from its commented template."""
        return [self.ensure_section_file(section) for section in CONFIG_FILES]

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
