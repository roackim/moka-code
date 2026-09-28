"""Tests for conversation roles: model, files, and the approval gate."""

import pytest

from moka_code.harness.permissions import PermissionGate
from moka_code.harness.roles import (
    Role,
    agent_role,
    builtin_roles,
    chat_role,
    create_role,
    delete_role,
    ensure_roles_dir,
    load_role,
    list_roles,
    validate_roles,
)
import moka_code.harness.roles as roles_module


def test_agent_role_enables_everything():
    role = agent_role()

    assert role.name == "agent"
    assert role.enabled_tool_names() == set(role.tools)
    assert role.permission_for("write") == "yes"


def test_chat_role_has_no_tools():
    role = chat_role()

    assert role.name == "chat"
    assert role.enabled_tool_names() == set()
    assert role.permission_for("read") == "no"


def test_disabled_tool_is_denied_before_permission_prompt():
    role = Role(name="read-only", tools={"read": "yes", "write": "no"})
    gate = PermissionGate(role=role)

    assert gate.check("read", {"path": "README.md"}) == "allow"
    assert gate.check("write", {"path": "README.md"}) == "deny"


def test_gate_maps_ask_and_unknown_tools():
    role = Role(name="mixed", tools={"bash": "ask"})
    gate = PermissionGate(role=role)

    assert gate.check("bash", {"command": "ls"}) == "ask"
    assert gate.check("write", {}) == "deny"


def test_saved_role_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(roles_module, "_ROLES_DIR", tmp_path / "roles")
    role = Role(
        name="custom",
        description="A focused role",
        prompt="Only inspect the workspace.",
        tools={"read": "yes", "write": "no"},
    )

    roles_module._ROLES_DIR.mkdir(parents=True)
    (tmp_path / "roles" / "custom.toml").write_text(
        roles_module._role_template(role), encoding="utf-8")

    loaded = load_role("custom")

    assert loaded.description == role.description
    assert loaded.prompt == role.prompt
    assert loaded.tools == role.tools


def test_create_role_defaults_to_all_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(roles_module, "_ROLES_DIR", tmp_path / "roles")

    role = create_role("scratch")

    assert role.enabled_tool_names() == set()
    assert (tmp_path / "roles" / "scratch.toml").exists()
    assert "scratch" in list_roles()
    with pytest.raises(ValueError):
        create_role("scratch")


def test_role_lifecycle_and_tombstones(tmp_path, monkeypatch):
    monkeypatch.setattr(roles_module, "_ROLES_DIR", tmp_path / "roles")

    ensure_roles_dir()
    assert set(list_roles()) == {"agent", "chat"}

    delete_role("chat")
    assert "chat" not in list_roles()
    with pytest.raises(KeyError):
        load_role("chat")

    try:
        delete_role("agent")
    except ValueError as exc:
        assert "At least one role" in str(exc)
    else:
        raise AssertionError("last role was deleted")


def test_unknown_tool_and_bad_value_are_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(roles_module, "_ROLES_DIR", tmp_path / "roles")
    (tmp_path / "roles").mkdir()
    (tmp_path / "roles" / "bad.toml").write_text(
        'description = "x"\nnot_a_tool = "yes"\n', encoding="utf-8")
    (tmp_path / "roles" / "bad2.toml").write_text(
        'read = "maybe"\n', encoding="utf-8")

    errors = validate_roles()

    assert any("unknown tool 'not_a_tool'" in e for e in errors)
    assert any("bad2.toml: read must be one of" in e for e in errors)


def test_ensure_roles_dir_seeds_builtins(tmp_path, monkeypatch):
    monkeypatch.setattr(roles_module, "_ROLES_DIR", tmp_path / "roles")

    ensure_roles_dir()

    assert (tmp_path / "roles" / "agent.toml").exists()
    assert (tmp_path / "roles" / "chat.toml").exists()
    assert set(builtin_roles()) == {"agent", "chat"}


def test_retired_tool_keys_are_migrated_on_startup(tmp_path, monkeypatch):
    monkeypatch.setattr(roles_module, "_ROLES_DIR", tmp_path / "roles")
    (tmp_path / "roles").mkdir()
    (tmp_path / "roles" / "agent.toml").write_text(
        "# keep me\n"
        'description = "agent"\n'
        'prompt = ""\n'
        'read = "yes"\n'
        'patch = "ask"\n'
        'run_command = "yes"\n'
        'subagent = "yes"\n',
        encoding="utf-8",
    )

    ensure_roles_dir()

    text = (tmp_path / "roles" / "agent.toml").read_text(encoding="utf-8")
    assert "# keep me" in text
    assert "patch =" not in text
    assert "run_command =" not in text
    assert "subagent" not in text
    assert 'edit = "ask"' in text
    assert 'bash = "yes"' in text

    role = load_role("agent")
    assert role.permission_for("edit") == "ask"
    assert role.permission_for("bash") == "yes"
    assert validate_roles() == []


def test_old_tool_aliases_load_without_migration(tmp_path, monkeypatch):
    monkeypatch.setattr(roles_module, "_ROLES_DIR", tmp_path / "roles")
    (tmp_path / "roles").mkdir()
    (tmp_path / "roles" / "legacy.toml").write_text(
        'patch = "yes"\nrun_command = "no"\n', encoding="utf-8")

    role = load_role("legacy")

    assert role.permission_for("edit") == "yes"
    assert role.permission_for("bash") == "no"
    assert validate_roles() == []


def test_role_file_overrides_builtin(tmp_path, monkeypatch):
    monkeypatch.setattr(roles_module, "_ROLES_DIR", tmp_path / "roles")
    (tmp_path / "roles").mkdir()
    (tmp_path / "roles" / "agent.toml").write_text(
        'description = "Custom agent"\nprompt = ""\nread = "ask"\n', encoding="utf-8")

    assert load_role("agent").description == "Custom agent"
    assert load_role("agent").permission_for("read") == "ask"


def test_require_sandbox_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(roles_module, "_ROLES_DIR", tmp_path / "roles")
    (tmp_path / "roles").mkdir()
    role = Role(name="locked", tools={"read": "yes"}, require_sandbox=True)

    (tmp_path / "roles" / "locked.toml").write_text(
        roles_module._role_template(role), encoding="utf-8")

    loaded = load_role("locked")

    assert loaded.require_sandbox is True
    assert "require_sandbox" not in loaded.tools  # reserved, not a tool
    assert validate_roles() == []


def test_require_sandbox_defaults_false():
    assert agent_role().require_sandbox is False
    assert Role(name="x").require_sandbox is False
