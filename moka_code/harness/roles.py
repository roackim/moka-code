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
from typing import Any

import toml

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

    def enabled_tool_names(self) -> set[str]:
        """Tool names the model is allowed to see (anything but ``no``)."""
        return {name for name, value in self.tools.items() if value != "no"}

    def permission_for(self, tool_name: str) -> str:
        """Approval setting for ``tool_name`` (``no`` when unlisted)."""
        return self.tools.get(tool_name, "no")


def _all_tools_no() -> dict[str, str]:
    from moka_code.harness.tools import registered_tool_names

    return {name: "no" for name in registered_tool_names()}


def agent_role() -> Role:
    """The permissive built-in role: every tool auto-approved."""
    return Role(
        name="agent",
        description="General coding agent (all tools auto-approved)",
        prompt="",
        tools={name: "yes" for name in _all_tools_no()},
    )


def chat_role() -> Role:
    """The pure-chat built-in role: no tools at all."""
    return Role(
        name="chat",
        description="Pure chat (no tools)",
        prompt="",
        tools=_all_tools_no(),
    )


def builtin_roles() -> dict[str, Role]:
    """The built-in roles, used as code fallbacks when files are absent."""
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
        if key in ("description", "prompt", "disabled", "require_sandbox"):
            continue
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
    return Role(
        name=name,
        description=str(data.get("description", "")),
        prompt=str(data.get("prompt", "")),
        tools=tools,
        require_sandbox=bool(data.get("require_sandbox", False)),
    )


def _role_to_dict(role: Role) -> dict[str, Any]:
    data: dict[str, Any] = {
        "description": role.description,
        "prompt": role.prompt,
    }
    if role.require_sandbox:
        data["require_sandbox"] = True
    data.update(role.tools)
    return data


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


def ensure_roles_dir() -> Path:
    """Create the roles directory, seed built-ins, and migrate retired keys."""
    _ROLES_DIR.mkdir(parents=True, exist_ok=True)
    for name, role in builtin_roles().items():
        path = _role_file(name)
        if not path.exists():
            path.write_text(_role_template(role), encoding="utf-8")
    for path in _iter_role_files():
        _migrate_role_file(path)
    return _ROLES_DIR


def _role_template(role: Role) -> str:
    """Render a role file with a short header and one line per tool."""
    import json

    header = (
        f"# Moka role: {role.name}\n"
        f"#   The file name is the role name.\n"
        f"#   Select with: /role {role.name}\n"
        f"#   Edit with:   /config role {role.name}\n"
        f"#\n"
        f"# Tools: no = disabled (hidden from the model) · ask = confirm · yes = auto\n"
        f"# require_sandbox = true locks the conversation unless a sandbox is active.\n\n"
        f"description = {json.dumps(role.description)}\n"
        f"prompt = {json.dumps(role.prompt)}\n"
    )
    if role.require_sandbox:
        header += "require_sandbox = true\n"
    header += "\n# All available tools:\n"
    body = "".join(f'{name} = "{value}"\n' for name, value in role.tools.items())
    return header + body


def create_role(name: str) -> Role:
    """Create a role file from the tool registry (the only programmatic writer).

    A new role has every tool disabled; the user opts tools in explicitly.
    """
    name = _validate_name(name)
    if name in list_roles() or _role_file(name).exists():
        raise ValueError(f"Role already exists: {name}")
    role = Role(name=name, description="", prompt="", tools=_all_tools_no())
    _ROLES_DIR.mkdir(parents=True, exist_ok=True)
    _role_file(name).write_text(_role_template(role), encoding="utf-8")
    return role


def ensure_role_file(name: str) -> Path:
    """Create the role's file if missing (built-ins included); return its path."""
    name = _validate_name(name)
    path = _role_file(name)
    if path.exists():
        return path
    _ROLES_DIR.mkdir(parents=True, exist_ok=True)
    role = builtin_roles().get(name) or Role(name=name, tools=_all_tools_no())
    path.write_text(_role_template(role), encoding="utf-8")
    return path


def delete_role(name: str) -> None:
    """Delete a role file; built-ins are hidden with a tombstone."""
    if name not in list_roles():
        raise KeyError(f"Role not found: {name}")
    if len(list_roles()) <= 1:
        raise ValueError("At least one role must remain")
    path = _role_file(name)
    if name in builtin_roles():
        # Hide the built-in rather than resurrecting it by deleting the file.
        _ROLES_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text("disabled = true\n", encoding="utf-8")
    elif path.exists():
        path.unlink()


def load_role(name: str) -> Role:
    path = _role_file(name)
    if path.exists():
        data = _read_role_file(path)
        if data.get("disabled"):
            raise KeyError(f"Role not found: {name}")
        return _role_from_dict(name, data)
    builtins = builtin_roles()
    if name in builtins:
        return builtins[name]
    raise KeyError(f"Role not found: {name}")


def list_roles() -> list[str]:
    names = set(builtin_roles())
    for path in _iter_role_files():
        data = _read_role_file(path)
        if data.get("disabled"):
            names.discard(path.stem)
        else:
            names.add(path.stem)
    return sorted(names)


def validate_roles() -> list[str]:
    """Return validation errors for every role file (surfaced by /reload)."""
    errors: list[str] = []
    for path in _iter_role_files():
        try:
            data = _read_role_file(path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if data.get("disabled"):
            continue
        try:
            _role_from_dict(path.stem, data)
        except ValueError as exc:
            errors.append(str(exc))
    return errors
