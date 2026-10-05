"""Tests for conversation roles: model, files, and the approval gate."""

import pytest

from moka_code.harness.permissions import PermissionGate
from moka_code.harness.roles import (
    Role,
    agent_role,
    builtin_roles,
    chat_role,
    ensure_role_file,
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


def test_new_role_file_defaults_to_all_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(roles_module, "_ROLES_DIR", tmp_path / "roles")

    path = ensure_role_file("scratch")

    assert path == tmp_path / "roles" / "scratch.toml"
    assert load_role("scratch").enabled_tool_names() == set()
    assert "scratch" in list_roles()
    assert ensure_role_file("scratch") == path      # an existing file is left alone


def test_role_lifecycle_and_last_role_is_kept(tmp_path, monkeypatch):
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


def test_replay_reasoning_depth(tmp_path, monkeypatch):
    """2b (2026-10-01): default 999 ("all"), a file can set it, bad values are
    errors, and the template shows the default as a commented line."""
    monkeypatch.setattr(roles_module, "_ROLES_DIR", tmp_path / "roles")
    (tmp_path / "roles").mkdir()
    assert Role(name="x").replay_reasoning_depth == 999
    template = roles_module._role_template(Role(name="t", tools={"read": "yes"}))
    assert "\n# replay_reasoning_depth = 999\n" in template
    (tmp_path / "roles" / "t.toml").write_text(template, encoding="utf-8")
    assert load_role("t").replay_reasoning_depth == 999

    (tmp_path / "roles" / "t.toml").write_text(
        template + "replay_reasoning_depth = 3\n", encoding="utf-8")
    loaded = load_role("t")
    assert loaded.replay_reasoning_depth == 3
    assert "replay_reasoning_depth" not in loaded.tools
    assert validate_roles() == []

    for bad in ("-1", "true", '"all"', "1.5"):
        (tmp_path / "roles" / "t.toml").write_text(
            template + f"replay_reasoning_depth = {bad}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="replay_reasoning_depth"):
            load_role("t")


def test_role_template_layout_and_uncommentable_settings():
    """Layout (2026-10-01): ``##`` is help, a single ``#`` is a setting that
    works when uncommented; tools are aligned, technical settings come last."""
    import re
    import toml

    template = roles_module._role_template(
        Role(name="agent", description="d", prompt="p", tools={"read": "yes", "bash": "ask"}))

    assert template.startswith("## Moka role: agent\n\ndescription = ")
    assert 'read = "yes"\nbash = "ask"\n' in template
    assert template.index("## Technical settings") > template.index('bash = "ask"')
    assert [l for l in template.splitlines() if l.startswith("# ")] == [
        "# require_sandbox = true", "# replay_reasoning_depth = 999"]
    uncommented = re.sub(r"^# ", "", template, flags=re.M)
    loaded = toml.loads(uncommented)
    assert loaded["require_sandbox"] is True and loaded["replay_reasoning_depth"] == 999
    assert "require_sandbox = true\n" in roles_module._role_template(
        Role(name="x", require_sandbox=True))


# --- roles are files; the running one lives in memory ----------------------

def _roles_in(tmp_path, monkeypatch):
    monkeypatch.setattr(roles_module, "_ROLES_DIR", tmp_path / "roles")
    return tmp_path / "roles"


def test_a_missing_role_file_is_not_replaced_by_a_built_in(tmp_path, monkeypatch):
    """The built-in ``agent`` (every tool ``yes``) never comes back from code."""
    folder = _roles_in(tmp_path, monkeypatch)
    ensure_roles_dir()
    (folder / "agent.toml").unlink()

    with pytest.raises(KeyError):
        load_role("agent")
    assert "agent" not in list_roles()
    ensure_roles_dir()                      # another startup: still deleted
    assert not (folder / "agent.toml").exists() and list_roles() == ["chat"]


def test_built_ins_are_seeded_only_into_an_empty_folder(tmp_path, monkeypatch):
    folder = _roles_in(tmp_path, monkeypatch)
    ensure_roles_dir()
    assert sorted(list_roles()) == ["agent", "chat"]

    for path in folder.glob("*.toml"):
        path.unlink()
    ensure_roles_dir()
    assert sorted(list_roles()) == ["agent", "chat"]


def test_an_old_deleted_built_in_marker_is_cleaned_up(tmp_path, monkeypatch):
    folder = _roles_in(tmp_path, monkeypatch)
    folder.mkdir()
    (folder / "agent.toml").write_text("disabled = true\n", encoding="utf-8")
    (folder / "mine.toml").write_text('description = "m"\nprompt = ""\n', encoding="utf-8")

    ensure_roles_dir()

    assert list_roles() == ["mine"]


def test_default_role_is_agent_else_the_first_that_loads_else_a_placeholder(tmp_path, monkeypatch):
    folder = _roles_in(tmp_path, monkeypatch)
    ensure_roles_dir()
    assert roles_module.default_role().name == "agent"

    (folder / "agent.toml").unlink()
    (folder / "aaa.toml").write_text('read = "maybe"\n', encoding="utf-8")     # does not load
    (folder / "bbb.toml").write_text('description = "b"\nprompt = ""\n', encoding="utf-8")
    assert roles_module.default_role().name == "bbb"

    for path in folder.glob("*.toml"):
        path.unlink()
    placeholder = roles_module.default_role()
    assert placeholder.name == roles_module.NO_ROLE
    assert placeholder.enabled_tool_names() == set()


def _harness_on(tmp_path, monkeypatch):
    from moka_code.harness.harness import Harness

    folder = _roles_in(tmp_path, monkeypatch)
    ensure_roles_dir()
    harness = Harness(workspace_path=str(tmp_path))
    return harness, folder


def test_running_role_reports_how_it_differs_from_its_file(tmp_path, monkeypatch):
    harness, folder = _harness_on(tmp_path, monkeypatch)
    assert harness.role.name == "agent" and harness.role_problem() is None

    path = folder / "agent.toml"
    path.write_text(path.read_text(encoding="utf-8") + "\n# a comment\n", encoding="utf-8")
    assert harness.role_problem() is None           # touched, same role: no warning

    path.write_text(path.read_text(encoding="utf-8").replace('bash  = "yes"', 'bash  = "no"'),
                    encoding="utf-8")
    assert harness.role_problem() == "changed"
    assert harness.role.permission_for("bash") == "yes"     # memory is untouched

    path.unlink()
    assert harness.role_problem() == "gone"
    assert harness.role.permission_for("bash") == "yes"     # still running from memory


def test_the_band_names_a_gone_or_changed_role(tmp_path, monkeypatch):
    from moka_code.ui.status_presenter import notices

    harness, folder = _harness_on(tmp_path, monkeypatch)
    texts = lambda: [t for _, t in notices(harness)]
    assert not any("role agent" in t for t in texts())

    path = folder / "agent.toml"
    path.write_text('description = "other"\nprompt = ""\n', encoding="utf-8")
    assert "role agent changed on disk → /reload" in texts()

    path.unlink()
    assert any("role agent: its file is gone, running from memory" in t for t in texts())


def test_reload_leaves_the_placeholder_once_a_role_loads(tmp_path, monkeypatch):
    from moka_code.ui.commands.core import _reapply_role
    from moka_code.ui.status_presenter import notices

    folder = _roles_in(tmp_path, monkeypatch)
    folder.mkdir()
    from moka_code.harness.harness import Harness
    harness = Harness(workspace_path=str(tmp_path))
    assert harness.role.name == roles_module.NO_ROLE
    assert ("error", "no role file loads → /config role agent") in notices(harness)

    ensure_roles_dir()
    ui = type("UI", (), {"agent": harness})()
    assert _reapply_role(ui) == [] and harness.role.name == "agent"


def test_reload_says_when_the_running_roles_file_is_gone(tmp_path, monkeypatch):
    from moka_code.ui.commands.core import _reapply_role

    harness, folder = _harness_on(tmp_path, monkeypatch)
    (folder / "agent.toml").unlink()
    ui = type("UI", (), {"agent": harness})()

    assert "roles/agent.toml is gone" in _reapply_role(ui)[0]
    assert harness.role.name == "agent"
