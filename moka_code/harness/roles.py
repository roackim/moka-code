"""Conversation roles: a prompt plus a per-tool approval setting.

``Role`` is the single source of truth for a conversation's operating mode:
which tools are enabled, what each tool's approval setting is, and the
role-specific prompt.  There is no permission engine and no container code in
moka; isolation is the user's responsibility (see
``plans/containerization.md`` and ``plans/roles_rework.md``).

One role per file at ``<config>/roles/<name>.toml``.  The file name is the role
name; the body is ``description`` / ``prompt`` and one ``<tool> = "no" | "ask"
| "yes"`` entry per registered tool.
"""

from __future__ import annotations

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
    tools: dict[str, str] = field(default_factory=dict)
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

    def enabled_tool_names(self) -> set[str]:
        """Tool names the model is allowed to see (anything but ``no``)."""
        return {name for name, value in self.tools.items() if value != "no"}

    def permission_for(self, tool_name: str) -> str:
        """Approval setting for ``tool_name`` (``no`` when unlisted)."""
        return self.tools.get(tool_name, "no")


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
    )


def chat_role() -> Role:
    """The pure-chat built-in role: no tools at all."""
    return Role(
        name="chat",
        description="Pure chat (no tools)",
        prompt=DEFAULT_ROLE_PROMPT,
        tools=_all_tools_no(),
    )


#: Name of the placeholder role used when no role file loads (see ``no_role``).
NO_ROLE = "(no role)"


def no_role() -> Role:
    """In-memory placeholder when no role file loads: no tools, no prompt. The
    notice band says to fix it; nothing here comes from a file."""
    return Role(name=NO_ROLE, description="no role file loads", tools=_all_tools_no())


def default_role() -> Role:
    """The role a conversation starts on: ``agent``, else the first role file
    that loads, else the ``no_role`` placeholder."""
    for name in ["agent", *list_roles()]:
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


def _role_file(name: str) -> Path:
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


def _role_from_dict(name: str, data: dict[str, Any]) -> Role:
    """Build a validated role from a parsed file body."""
    from moka_code.harness.tools import registered_tool_names

    registered = set(registered_tool_names())
    tools: dict[str, str] = {}
    for key, value in data.items():
        if key in ("description", "prompt", "require_sandbox", "preserve_thinking",
                   "tool_timeout"):
            continue
        if key in _RETIRED_ROLE_KEYS:
            raise ValueError(f"roles/{name}.toml: {key} is replaced by {_RETIRED_ROLE_KEYS[key]}")
        key = _RETIRED_TOOL_ALIASES.get(key, key)
        if key in _RETIRED_TOOLS:
            continue
        if key not in registered:
            raise ValueError(f"roles/{name}.toml: unknown tool '{key}'")
        if value not in TOOL_VALUES:
            raise ValueError(
                f"roles/{name}.toml: {key} must be one of "
                + " / ".join(TOOL_VALUES)
            )
        tools[key] = value
    preserve = data.get("preserve_thinking", True)
    if not isinstance(preserve, bool):
        raise ValueError(f"roles/{name}.toml: preserve_thinking must be true or false")
    timeout = data.get("tool_timeout", DEFAULT_TOOL_TIMEOUT)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError(
            f"roles/{name}.toml: tool_timeout must be a number of seconds (0 or less = no limit)")
    return Role(
        name=name,
        description=str(data.get("description", "")),
        prompt=str(data.get("prompt", "")),
        tools=tools,
        require_sandbox=bool(data.get("require_sandbox", False)),
        preserve_thinking=preserve,
        tool_timeout=timeout,
    )


def _migrate_role_file(path: Path) -> bool:
    """Rename/drop retired tool keys in a role file, preserving everything else.

    Only top-level ``<tool> = "..."`` lines are touched, and only keys in
    ``_RETIRED_TOOL_ALIASES`` / ``_RETIRED_TOOLS``. Comments, ordering,
    ``description``/``prompt`` and every other key are left intact; unknown
    keys still surface through validation. Returns True when the file changed.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return False

    changed = False
    result: list[str] = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            in_table = True
        if not in_table and stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in _RETIRED_TOOL_ALIASES:
                result.append(line.replace(key, _RETIRED_TOOL_ALIASES[key], 1))
                changed = True
                continue
            if key in _RETIRED_TOOLS:
                changed = True
                continue
        result.append(line)

    if changed:
        path.write_text("".join(result), encoding="utf-8")
    return changed


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
            _role_file(name).write_text(_role_template(role), encoding="utf-8")
    for path in _iter_role_files():
        _migrate_role_file(path)
    return _ROLES_DIR


def _role_template(role: Role) -> str:
    """Render a role file: description, prompt, one line per tool, then the
    technical settings. ``##`` is help; a single ``#`` is a setting to uncomment."""
    import json

    width = max((len(name) for name in role.tools), default=0)
    tools = "".join(f'{name.ljust(width)} = "{value}"\n' for name, value in role.tools.items())
    sandbox = ("require_sandbox = true\n" if role.require_sandbox
               else "# require_sandbox = true\n")
    return (
        f"## Moka role: {role.name}\n\n"
        f"description = {json.dumps(role.description)}\n"
        f"prompt = {json.dumps(role.prompt)}\n\n"
        f"## All available tools: no = disabled (hidden from the model) · ask = confirm · yes = auto\n"
        f"{tools}\n"
        f"## Technical settings\n\n"
        f"## require_sandbox: true locks the conversation unless a sandbox is active.\n"
        f"{sandbox}\n"
        f"## preserve_thinking: send the model's earlier reasoning back with each request\n"
        f"## (true), or none of it (false). Nothing in between: a window changes the start\n"
        f"## of the prompt every turn and defeats the server's cache.\n"
        f"preserve_thinking = true\n\n"
        f"## tool_timeout: seconds a bash command may run before it is killed (bare and\n"
        f"## sandbox alike); 0 or -1 = no limit. The model cannot change it.\n"
        f"# tool_timeout = {DEFAULT_TOOL_TIMEOUT}\n"
    )


def ensure_role_file(name: str) -> Path:
    """Create the role's file if missing (built-ins included); return its path."""
    name = _validate_name(name)
    path = _role_file(name)
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
    _role_file(name).unlink(missing_ok=True)


def load_role(name: str) -> Role:
    """The role in ``roles/<name>.toml``; ``KeyError`` when there is no such file."""
    path = _role_file(name)
    if not path.exists():
        raise KeyError(f"Role not found: {name}")
    return _role_from_dict(name, _read_role_file(path))


def role_file_stat(name: str) -> Optional[tuple[int, int]]:
    """``(mtime_ns, size)`` of the role's file, ``None`` when it is gone: the
    cheap check that tells whether a running role may differ from its file."""
    try:
        stat = _role_file(name).stat()
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
