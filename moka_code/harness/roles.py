"""Conversation roles: a prompt plus a per-tool approval setting.

``Role`` is the single source of truth for a conversation's operating mode:
which tools are enabled, what each tool's approval setting is, and the
role-specific prompt.  There is no permission engine and no container code in
moka; isolation is the user's responsibility (see
``plans/containerization.md`` and ``plans/roles_rework.md``).

One role per file at ``<config>/roles/<name>.toml``.  The file name is the role
name; the body is ``description`` / ``prompt`` / technical scalars, then a
``[tools]`` table with one ``<tool> = "no" | "ask" | "yes"`` entry per built-in
tool and a ``[mcps]`` table with one server = value line per MCP server.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import toml

from moka_code.worker import DEFAULT_TOOL_TIMEOUT

#: The only valid per-tool values.
TOOL_VALUES = ("no", "ask", "yes")

#: Files whose stem starts with ``_`` or ``.`` are ignored.
_HIDDEN_PREFIXES = ("_", ".")

#: Tool names retired in the patch/run_command -> edit/bash rework. Old role
#: files are migrated on startup: aliases are renamed, removals are dropped.
_RETIRED_TOOL_ALIASES = {"patch": "edit", "run_command": "bash"}
_RETIRED_TOOLS = frozenset({"subagent", "wait_for_subagents"})


@dataclass
class Role:
    """Complete operating mode for one conversation."""

    name: str
    description: str = ""
    prompt: str = ""
    #: Built-in tool name -> ``no`` / ``ask`` / ``yes`` (the ``[tools]`` table).
    tools: dict[str, str] = field(default_factory=dict)
    #: MCP server name -> ``no`` / ``ask`` / ``yes`` (the ``[mcps]`` table).
    #: All of a server's discovered tools share its setting; unlisted = ``no``.
    mcps: dict[str, str] = field(default_factory=dict)
    #: When True the role only runs while a sandbox is active; otherwise the
    #: conversation is locked (no LLM turns) until one is selected.
    require_sandbox: bool = False
    #: Whether the model's earlier reasoning is sent back with each request:
    #: all of it or none (a window in between moves the start of the reasoning
    #: every turn and so breaks the server's prompt cache). A provider may
    #: require it (``needs_preserved_thinking``).
    preserve_thinking: bool = True
    #: Seconds a ``bash`` command may run before it is killed; 0 or less = no limit.
    #: The harness sends it with every call (the model cannot set it), in bare and
    #: sandbox mode.
    tool_timeout: float = DEFAULT_TOOL_TIMEOUT
    #: Palette name or ``#rrggbb`` coloring the input bars and the status bar's
    #: role field while this role runs; empty keeps the theme's colors.
    color: str = ""

    def enabled_tool_names(self) -> set[str]:
        """Built-in tool names the model is allowed to see (anything but ``no``)."""
        return {name for name, value in self.tools.items() if value != "no"}

    def enabled_mcp_servers(self) -> set[str]:
        """MCP servers whose tools the model may see (anything but ``no``)."""
        return {name for name, value in self.mcps.items() if value != "no"}

    def permission_for(self, tool_name: str) -> str:
        """Approval setting for a tool name.

        Built-ins resolve directly. An MCP tool (``<server>__<tool>``) resolves
        through its server's ``[mcps]`` line; unlisted tools and servers are
        ``no``.
        """
        if tool_name in self.tools:
            return self.tools[tool_name]
        server = tool_name.split("__", 1)[0] if "__" in tool_name else tool_name
        return self.mcps.get(server, "no")


# Prompt a newly created role starts with.
DEFAULT_ROLE_PROMPT = "You are a helpful assistant."


def _all_tools_no() -> dict[str, str]:
    from moka_code.harness.tools import registered_tool_names

    return {name: "no" for name in registered_tool_names()}


def agent_role() -> Role:
    """The permissive built-in role: every tool auto-approved."""
    return Role(
        name="agent",
        description="General coding agent (all tools auto-approved)",
        prompt="You are a helpful assistant that can interact with a computer.",
        tools={name: "yes" for name in _all_tools_no()},
        color="WARNING",
    )


def chat_role() -> Role:
    """The pure-chat built-in role: no tools at all."""
    return Role(
        name="chat",
        description="Pure chat (no tools)",
        prompt=DEFAULT_ROLE_PROMPT,
        tools=_all_tools_no(),
        color="SUCCESS",
    )


#: Name of the placeholder role used when no role file loads (see ``no_role``).
NO_ROLE = "(no role)"


def no_role() -> Role:
    """In-memory placeholder when no role file loads: no tools, no prompt. The
    notice band says to fix it; nothing here comes from a file."""
    return Role(name=NO_ROLE, description="no role file loads", tools=_all_tools_no())


def default_role() -> Role:
    """The role a conversation starts on: the last one used (``state.toml``),
    else ``agent``, else the first role file that loads, else the ``no_role``
    placeholder."""
    from moka_code import settings

    names = list_roles()
    last = settings.config.last_role
    for name in [*([last] if last in names else []), "agent", *names]:
        try:
            return load_role(name)
        except (KeyError, OSError, ValueError):
            continue
    return no_role()


def builtin_roles() -> dict[str, Role]:
    """The built-in roles: the templates seeded into an empty roles folder.
    They are never loaded from code: a role is its file."""
    return {
        "agent": agent_role(),
        "chat": chat_role(),
    }


def _default_roles_dir() -> Path:
    from moka_code import settings

    return settings.get_roles_dir()


# Resolved at import; tests monkeypatch this symbol directly.
_ROLES_DIR = _default_roles_dir()


def role_file(name: str) -> Path:
    return _ROLES_DIR / f"{name}.toml"


def _validate_name(name: str) -> str:
    name = name.strip()
    if not name or name.startswith(".") or any(c in name for c in "[]\\/"):
        raise ValueError("Role name must be non-empty and cannot contain '[', '\\', '/' or start with '.'")
    return name


def _iter_role_files():
    if not _ROLES_DIR.exists():
        return []
    return sorted(
        path for path in _ROLES_DIR.glob("*.toml")
        if not path.stem.startswith(_HIDDEN_PREFIXES)
    )


# Role keys that were replaced: reported, never aliased (a depth of 0 must not
# silently become "all").
_RETIRED_ROLE_KEYS = {"replay_reasoning_depth": "preserve_thinking = true|false"}


def _read_role_file(path: Path) -> dict[str, Any]:
    try:
        return toml.load(path)
    except (toml.TomlDecodeError, OSError) as exc:
        raise ValueError(f"{path.name}: {exc}") from exc


#: Top-level scalar keys of a role file (everything else is a tool table or a
#: legacy flat tool line).
_ROLE_SCALAR_KEYS = ("description", "prompt", "color", "require_sandbox",
                     "preserve_thinking", "tool_timeout")


def _configured_mcp_servers() -> set[str]:
    """The server names declared in ``mcp.toml`` (empty on any load failure)."""
    try:
        from moka_code import settings

        return set(settings.config.mcp_servers)
    except Exception:                   # pragma: no cover - settings always loads
        return set()


def _check_tool_value(where: str, key: str, value: Any) -> str:
    if value not in TOOL_VALUES:
        raise ValueError(f"{where}: {key} must be one of " + " / ".join(TOOL_VALUES))
    return value


def _role_from_dict(name: str, data: dict[str, Any]) -> Role:
    """Build a validated role from a parsed file body.

    Current shape: scalars plus a ``[tools]`` table (built-ins) and a ``[mcps]``
    table (one line per MCP server). Legacy top-level ``<tool> = "..."`` lines
    are still accepted, so an unmigrated file loads before the next startup
    rewrites it.
    """
    from moka_code.harness.tools import registered_tool_names

    registered = set(registered_tool_names())
    tools: dict[str, str] = {}
    mcps: dict[str, str] = {}

    raw_tools = data.get("tools")
    if raw_tools is not None and not isinstance(raw_tools, dict):
        raise ValueError(f"roles/{name}.toml: [tools] must be a table")
    raw_mcps = data.get("mcps")
    if raw_mcps is not None and not isinstance(raw_mcps, dict):
        raise ValueError(f"roles/{name}.toml: [mcps] must be a table")

    def add_tool(key: str, value: Any) -> None:
        key = _RETIRED_TOOL_ALIASES.get(key, key)
        if key in _RETIRED_TOOLS:
            return
        if key not in registered:
            raise ValueError(f"roles/{name}.toml: unknown tool '{key}'")
        tools[key] = _check_tool_value(f"roles/{name}.toml", key, value)

    if raw_tools is not None:
        for key, value in raw_tools.items():
            add_tool(str(key), value)
    for key, value in data.items():
        if key in _ROLE_SCALAR_KEYS or key in ("tools", "mcps"):
            continue
        if key in _RETIRED_ROLE_KEYS:
            raise ValueError(f"roles/{name}.toml: {key} is replaced by {_RETIRED_ROLE_KEYS[key]}")
        add_tool(str(key), value)         # legacy flat tool line

    if raw_mcps is not None:
        configured = _configured_mcp_servers()
        for key, value in raw_mcps.items():
            key = str(key)
            if key not in configured:
                raise ValueError(f"roles/{name}.toml: unknown MCP server '{key}' "
                                 "(declare it in mcp.toml)")
            mcps[key] = _check_tool_value(f"roles/{name}.toml", key, value)

    preserve = data.get("preserve_thinking", True)
    if not isinstance(preserve, bool):
        raise ValueError(f"roles/{name}.toml: preserve_thinking must be true or false")
    timeout = data.get("tool_timeout", DEFAULT_TOOL_TIMEOUT)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError(
            f"roles/{name}.toml: tool_timeout must be a number of seconds (0 or less = no limit)")
    color = data.get("color", "")
    if color != "":
        from moka_code.settings import _coerce

        color, error = _coerce("color", color)
        if error:
            raise ValueError(f"roles/{name}.toml: color {error}")
    return Role(
        name=name,
        description=str(data.get("description", "")),
        prompt=str(data.get("prompt", "")),
        tools=tools,
        mcps=mcps,
        require_sandbox=bool(data.get("require_sandbox", False)),
        preserve_thinking=preserve,
        tool_timeout=timeout,
        color=color,
    )


_KEY_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _tool_key(line: str) -> Optional[str]:
    match = _KEY_LINE_RE.match(line)
    return match.group(1) if match else None


def _mcp_section_lines() -> list[str]:
    """A fresh ``[mcps]`` table, listing configured servers as ``no``."""
    from moka_code.settings import _rule

    configured = sorted(_configured_mcp_servers())
    lines = [
        _rule("MCP servers"),
        "",
        "## One line per server from mcp.toml; unset = no. Every tool of a",
        "## server shares its setting: no = disabled · ask = confirm · yes = auto.",
        "[mcps]",
        "",
    ]
    if configured:
        width = max(len(name) for name in configured)
        lines += [f'{name.ljust(width)} = "no"' for name in configured]
    else:
        lines.append('# github = "ask"')
    return lines


def _migrate_tool_block(block: list[str], registered: list[str]) -> tuple[list[str], bool]:
    """Rename/drop retired tools and add missing ones as ``no`` in one block."""
    result: list[str] = []
    listed: set[str] = set()
    last_tool_line: Optional[int] = None
    changed = False
    registered_set = set(registered)
    for line in block:
        key = _tool_key(line)
        if key in _RETIRED_TOOL_ALIASES:
            line = line.replace(key, _RETIRED_TOOL_ALIASES[key], 1)
            key = _RETIRED_TOOL_ALIASES[key]
            changed = True
        elif key in _RETIRED_TOOLS:
            changed = True
            continue
        if key in registered_set:
            listed.add(key)
            last_tool_line = len(result)
        result.append(line)

    missing = [name for name in registered if name not in listed]
    if missing:
        changed = True
        if last_tool_line is None:
            width = max((len(name) for name in registered), default=0)
            result = [f'{name.ljust(width)} = "no"\n' for name in registered]
        else:
            # Align with the last tool line: its key is padded to the ``=`` column.
            key_part = result[last_tool_line].split("=", 1)[0]
            width = len(key_part) - 1 if key_part.endswith(" ") else len(key_part)
            if not result[last_tool_line].endswith("\n"):
                result[last_tool_line] += "\n"
            added = [f'{name.ljust(width)} = "no"\n' for name in missing]
            result[last_tool_line + 1:last_tool_line + 1] = added
    return result, changed


def _tools_section_lines(tool_lines: list[str]) -> list[str]:
    from moka_code.settings import _rule

    lines = [
        _rule("Tools"),
        "",
        "## Built-in tools: no = disabled (hidden from the model) · ask = confirm · yes = auto",
        "## (ask, the model's questions to you, is never confirmed: ask and yes both enable it)",
        "[tools]",
        "",
    ]
    lines += [line.rstrip("\n") for line in tool_lines]
    return lines


def _migrate_role_file(path: Path) -> bool:
    """Move a role file to the ``[tools]`` / ``[mcps]`` shape.

    Old files had flat top-level ``<tool> = "..."`` lines. TOML cannot return to
    the root after a table header, so those lines are lifted out and appended
    under ``[tools]``; the rest (description/prompt/technical settings, with
    their comments) stays at the root, before the tables. Retired aliases are
    renamed, retired tools dropped, and a registered tool the file does not
    list is added as ``no`` (an upgrade never enables a tool). Already-migrated
    files only get missing tools and a ``[mcps]`` table when absent.
    """
    from moka_code.harness.tools import registered_tool_names

    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return False

    registered = registered_tool_names()
    registered_set = set(registered)
    has_tools = any(line.strip() == "[tools]" for line in lines)
    has_mcps = any(line.strip() == "[mcps]" for line in lines)

    if has_tools:
        start = next(i for i, line in enumerate(lines) if line.strip() == "[tools]")
        end = start + 1
        while end < len(lines) and not lines[end].strip().startswith("["):
            end += 1
        block, block_changed = _migrate_tool_block(lines[start + 1:end], registered)
        changed = block_changed
        if block_changed:
            lines = lines[:start + 1] + block + lines[end:]
        if not has_mcps:
            lines = [line if line.endswith("\n") else line + "\n" for line in lines]
            lines += [line + "\n" for line in _mcp_section_lines()]
            changed = True
        if changed:
            path.write_text("".join(lines), encoding="utf-8")
        return changed

    # Legacy flat format: find top-level tool lines (before any table header).
    indices: list[int] = []
    in_table = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("["):
            in_table = True
        key = _tool_key(line)
        if (not in_table and key
                and (key in registered_set or key in _RETIRED_TOOL_ALIASES
                     or key in _RETIRED_TOOLS)):
            indices.append(index)

    if indices:
        first, last = indices[0], indices[-1]
        # Pull in the old Tools heading/help comments directly above the block.
        while first > 0 and (not lines[first - 1].strip()
                             or lines[first - 1].lstrip().startswith("##")):
            first -= 1
        head, tail = lines[:first], lines[last + 1:]
        tool_lines = [lines[index] for index in indices]
    else:
        head, tail, tool_lines = list(lines), [], []

    processed, _ = _migrate_tool_block(tool_lines, registered)
    root = "".join(head + tail).rstrip("\n")
    parts: list[str] = []
    if root:
        parts += [root, ""]
    parts += _tools_section_lines(processed)
    if not has_mcps:
        parts += [""] + _mcp_section_lines()
    text = "\n".join(parts).rstrip("\n") + "\n"
    path.write_text(text, encoding="utf-8")
    return text != "".join(lines)


def _is_tombstone(path: Path) -> bool:
    """The old marker of a deleted built-in: a file holding only ``disabled = true``."""
    try:
        return toml.load(path) == {"disabled": True}
    except (toml.TomlDecodeError, OSError):
        return False


def ensure_roles_dir() -> Path:
    """Create the roles directory, seed the built-ins when it holds no role
    (first run), and migrate retired keys. A deleted role stays deleted."""
    _ROLES_DIR.mkdir(parents=True, exist_ok=True)
    for path in _iter_role_files():
        if _is_tombstone(path):          # deleting a role now just removes its file
            path.unlink()
    if not _iter_role_files():
        for name, role in builtin_roles().items():
            role_file(name).write_text(_role_template(role), encoding="utf-8")
    for path in _iter_role_files():
        _migrate_role_file(path)
    return _ROLES_DIR


def _role_template(role: Role) -> str:
    """Render a role file: scalars first, then the ``[tools]`` and ``[mcps]``
    tables. ``##`` is help; a single ``#`` is a setting to uncomment."""
    import json
    from moka_code.settings import _rule

    width = max((len(name) for name in role.tools), default=0)
    tools = "".join(f'{name.ljust(width)} = "{value}"\n' for name, value in role.tools.items())

    mcp_names = list(role.mcps)
    for name in sorted(_configured_mcp_servers()):
        if name not in mcp_names:
            mcp_names.append(name)
    mcp_width = max((len(name) for name in mcp_names), default=0)
    if mcp_names:
        mcps = "".join(f'{name.ljust(mcp_width)} = "{role.mcps.get(name, "no")}"\n'
                       for name in mcp_names)
    else:
        mcps = '# github = "ask"\n'

    sandbox = ("require_sandbox = true\n" if role.require_sandbox
               else "# require_sandbox = true\n")
    color = (f"color = {json.dumps(role.color)}\n" if role.color
             else '# color = "USER"\n')
    return (
        f"## Moka role: {role.name}\n"
        f"## Apply with /reload, or /role edit.\n"
        f"## A line starting with # is a setting to uncomment; ## is help.\n\n"
        f"{_rule('Role')}\n\n"
        f"description = {json.dumps(role.description)}\n"
        f"prompt = {json.dumps(role.prompt)}\n"
        f"## color: input bars and status bar role field (palette name or #rrggbb)\n"
        f"{color}\n"
        f"{_rule('Technical settings')}\n\n"
        f"## require_sandbox: true locks the conversation unless a sandbox is active.\n"
        f"{sandbox}\n"
        f"## preserve_thinking: send the model's earlier reasoning back with each request\n"
        f"## (true), or none of it (false). Nothing in between: a window changes the start\n"
        f"## of the prompt every turn and defeats the server's cache.\n"
        f"preserve_thinking = true\n\n"
        f"## tool_timeout: seconds a bash command may run before it is killed (bare and\n"
        f"## sandbox alike); 0 or -1 = no limit. The model cannot change it.\n"
        f"# tool_timeout = {DEFAULT_TOOL_TIMEOUT}\n\n"
        f"{_rule('Tools')}\n\n"
        f"## Built-in tools: no = disabled (hidden from the model) · ask = confirm · yes = auto\n"
        f"## (ask, the model's questions to you, is never confirmed: ask and yes both enable it)\n"
        f"[tools]\n\n"
        f"{tools}\n"
        f"{_rule('MCP servers')}\n\n"
        f"## One line per server from mcp.toml; unset = no. Every tool of a\n"
        f"## server shares its setting: no = disabled · ask = confirm · yes = auto.\n"
        f"[mcps]\n\n"
        f"{mcps}"
    )


def ensure_role_file(name: str) -> Path:
    """Create the role's file if missing (built-ins included); return its path."""
    name = _validate_name(name)
    path = role_file(name)
    if path.exists():
        return path
    _ROLES_DIR.mkdir(parents=True, exist_ok=True)
    role = builtin_roles().get(name) or Role(
        name=name, prompt=DEFAULT_ROLE_PROMPT, tools=_all_tools_no())
    path.write_text(_role_template(role), encoding="utf-8")
    return path


def delete_role(name: str) -> None:
    """Delete a role file (a running conversation keeps its role in memory)."""
    if name not in list_roles():
        raise KeyError(f"Role not found: {name}")
    if len(list_roles()) <= 1:
        raise ValueError("At least one role must remain")
    role_file(name).unlink(missing_ok=True)


def load_role(name: str) -> Role:
    """The role in ``roles/<name>.toml``; ``KeyError`` when there is no such file."""
    path = role_file(name)
    if not path.exists():
        raise KeyError(f"Role not found: {name}")
    return _role_from_dict(name, _read_role_file(path))


def role_file_stat(name: str) -> Optional[tuple[int, int]]:
    """``(mtime_ns, size)`` of the role's file, ``None`` when it is gone: the
    cheap check that tells whether a running role may differ from its file."""
    try:
        stat = role_file(name).stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def list_roles() -> list[str]:
    """The names of the role files."""
    return [path.stem for path in _iter_role_files()]


def validate_roles() -> list[str]:
    """Return validation errors for every role file (surfaced by /reload)."""
    errors: list[str] = []
    for path in _iter_role_files():
        try:
            data = _read_role_file(path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        try:
            _role_from_dict(path.stem, data)
        except ValueError as exc:
            errors.append(str(exc))
    return errors
