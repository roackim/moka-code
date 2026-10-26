"""Tests for the file-based config commands and the external editor helper."""

import asyncio
from pathlib import Path

import pytest

from moka_code.ui.external_editor import edit_file, resolve_editor
from moka_code.ui.commands.core import cmd_edit, cmd_reload
from moka_code.ui.commands.registry import COMMANDS


def cmd_config(ui, args):
    """``/config <args>`` as typed."""
    return COMMANDS["config"].execute(ui, args)


def cmd_role(ui, args):
    """``/role <args>`` as typed."""
    return COMMANDS["role"].execute(ui, args)


class _Panel:
    def __init__(self):
        self.messages = []

    def add_message(self, text, msg_type=None, title=None):
        self.messages.append(text)


class _Terminal:
    def __init__(self):
        self.suspended = 0
        self.resumed = 0

    def suspend(self):
        self.suspended += 1

    def resume(self):
        self.resumed += 1


class _Compositor:
    def __init__(self):
        self.terminal = _Terminal()


class _UI:
    def __init__(self):
        self.chat_history_panel = _Panel()
        self.compositor = _Compositor()
        self.refreshed = 0
        self.popups = []

    def refresh_status_bar(self):
        self.refreshed += 1

    def show_popup(self, title, content, content_padding=1):
        self.popups.append((title, content))


def _recording_editor(opened):
    """Async stand-in for ``open_editor`` that records the opened paths."""
    async def _open(_ui, path):
        opened.append(Path(path))
        return True
    return _open


def test_resolve_editor_prefers_visual_then_editor(monkeypatch):
    monkeypatch.setenv("VISUAL", "my-editor --wait")
    monkeypatch.delenv("EDITOR", raising=False)
    assert resolve_editor() == ["my-editor", "--wait"]

    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "nano")
    assert resolve_editor() == ["nano"]


def test_resolve_editor_never_guesses(monkeypatch):
    """ISSUES U4: with neither variable set, no editor is picked."""
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    assert resolve_editor() == []


def test_edit_file_invokes_editor(monkeypatch, tmp_path):
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "my-editor")
    calls = []
    monkeypatch.setattr("subprocess.call", lambda cmd: calls.append(cmd) or 0)

    assert edit_file(tmp_path / "ui.toml") is True
    assert calls == [["my-editor", str(tmp_path / "ui.toml")]]


def test_config_command_opens_section_and_reloads(monkeypatch, tmp_path):
    import moka_code.settings as cfg_mod
    import moka_code.harness.roles as roles_mod

    monkeypatch.setattr(cfg_mod, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg_mod, "get_state_path", lambda: tmp_path / "state.toml")
    monkeypatch.setattr(roles_mod, "_ROLES_DIR", tmp_path / "roles")
    monkeypatch.setenv("EDITOR", "my-editor")
    ui = _UI()

    opened = []

    async def _fake_open(_ui, path):
        opened.append(Path(path))
        return True

    monkeypatch.setattr("moka_code.ui.external_editor.open_editor", _fake_open)

    asyncio.run(cmd_config(ui, ["servers"]))

    assert opened == [tmp_path / "servers.toml"]
    assert (tmp_path / "servers.toml").exists()
    assert any("Config reloaded" in m for m in ui.chat_history_panel.messages)


def test_config_command_no_args_opens_the_section_picker(monkeypatch, tmp_path):
    """Decided 2026-09-30 (PLAN.md step 0.3): bare /config is the same
    sorted, clickable picker as the other lists; picking opens the section."""
    ui = _UI()
    captured = {}
    ui.show_search_modal = lambda title, items, **k: captured.update(title=title, items=items, **k)

    asyncio.run(cmd_config(ui, []))

    assert captured["title"] == "Config"
    assert {"ui", "servers"} <= set(captured["items"])
    assert not {"sandbox", "role", "theme", "styles"} & set(captured["items"])  # own commands / merged
    assert not captured.get("ordered")                  # the menu sorts it
    assert captured["descriptions"]["ui"] == "Edit ui.toml and reload"

    opened = []
    monkeypatch.setattr(COMMANDS["config"], "execute",
                        lambda ui, args: opened.append(args) or asyncio.sleep(0))

    async def accept():
        captured["on_accept"]("ui")
        await asyncio.sleep(0)
    asyncio.run(accept())
    assert opened == [["ui"]]


def test_config_command_unknown_section_reports_error(monkeypatch):
    ui = _UI()

    asyncio.run(cmd_config(ui, ["nope"]))

    assert any("Unknown section" in m for m in ui.chat_history_panel.messages)


def test_config_command_without_editor_reports_error(monkeypatch):
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    ui = _UI()

    asyncio.run(cmd_config(ui, ["ui"]))

    assert any("No editor" in m for m in ui.chat_history_panel.messages)


# -- /theme edit | create | path | remove: one file per theme (2026-10-26) ---------

def _theme_ui(monkeypatch, tmp_path, opened):
    import moka_code.settings as cfg_mod

    config = cfg_mod.Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")
    monkeypatch.setattr(cfg_mod, "config", config)
    monkeypatch.setattr(cfg_mod, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg_mod, "get_state_path", lambda: tmp_path / "state.toml")
    monkeypatch.setenv("EDITOR", "my-editor")
    monkeypatch.setattr("moka_code.ui.external_editor.open_editor", _recording_editor(opened))
    return _UI()


def cmd_theme(ui, args):
    return COMMANDS["theme"].execute(ui, args)


def test_theme_edit_writes_a_builtin_out_as_its_own_file(monkeypatch, tmp_path):
    import moka_code.settings as cfg_mod

    opened = []
    ui = _theme_ui(monkeypatch, tmp_path, opened)

    asyncio.run(cmd_theme(ui, ["edit", "nord"]))

    path = tmp_path / "themes" / "nord.toml"
    assert opened == [path]
    assert "TOOL" in path.read_text(encoding="utf-8")     # the whole palette
    assert not any("error" in m for m in ui.chat_history_panel.messages)
    assert cfg_mod.config.load_errors == [] and "nord" in cfg_mod.config.themes


def test_theme_edit_unknown_creates_nothing(monkeypatch, tmp_path):
    ui = _theme_ui(monkeypatch, tmp_path, [])

    asyncio.run(cmd_theme(ui, ["edit", "nope"]))

    assert any("/theme create nope" in m for m in ui.chat_history_panel.messages)
    assert not (tmp_path / "themes").exists()


def test_theme_create_copies_the_active_palette_and_refuses_builtins(monkeypatch, tmp_path):
    import moka_code.settings as cfg_mod

    opened = []
    ui = _theme_ui(monkeypatch, tmp_path, opened)

    asyncio.run(cmd_theme(ui, ["create", "mine"]))
    assert opened == [tmp_path / "themes" / "mine.toml"]
    assert "mine" in cfg_mod.config.themes

    asyncio.run(cmd_theme(ui, ["create", "mine"]))
    assert any("already exists" in m for m in ui.chat_history_panel.messages)
    asyncio.run(cmd_theme(ui, ["create", "nord"]))
    assert any("/theme edit nord" in m for m in ui.chat_history_panel.messages)
    assert not (tmp_path / "themes" / "nord.toml").exists()


def test_theme_path_and_remove_need_a_file(monkeypatch, tmp_path):
    ui = _theme_ui(monkeypatch, tmp_path, [])
    monkeypatch.setattr("moka_code.ui.clipboard.copy_to_clipboard", lambda text: None)

    asyncio.run(cmd_theme(ui, ["path", "nord"]))
    assert any("built-in and has no file" in m for m in ui.chat_history_panel.messages)

    asyncio.run(cmd_theme(ui, ["create", "mine"]))
    asyncio.run(cmd_theme(ui, ["path", "mine"]))
    path = tmp_path / "themes" / "mine.toml"
    assert any(str(path) in m for m in ui.chat_history_panel.messages)

    asked = {}
    ui.show_search_modal = lambda title, items, **k: asked.update(title=title, items=items, **k)
    async def remove():
        await cmd_theme(ui, ["remove", "mine"])
        asked["on_accept"]("Remove")
    asyncio.run(remove())
    assert not path.exists()


def test_edit_command_opens_requested_file(monkeypatch, tmp_path):
    monkeypatch.setenv("EDITOR", "my-editor")
    ui = _UI()
    opened = []
    monkeypatch.setattr(
        "moka_code.ui.external_editor.open_editor",
        _recording_editor(opened),
    )

    target = tmp_path / "notes.txt"
    asyncio.run(cmd_edit(ui, [str(target)]))

    assert opened == [target]


def test_reload_command_success(monkeypatch, tmp_path):
    import moka_code.settings as cfg_mod
    import moka_code.harness.roles as roles_mod

    monkeypatch.setattr(cfg_mod, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg_mod, "get_state_path", lambda: tmp_path / "state.toml")
    monkeypatch.setattr(roles_mod, "_ROLES_DIR", tmp_path / "roles")
    ui = _UI()

    asyncio.run(cmd_reload(ui, []))

    assert any("Config reloaded" in m for m in ui.chat_history_panel.messages)
    assert ui.refreshed >= 1        # the background catalog refresh may add one


# -- a reload applies the edited roles too (2026-10-01) -------------------------------

def _role_ui(monkeypatch, tmp_path, generating=False):
    import moka_code.settings as cfg_mod
    import moka_code.harness.roles as roles_mod
    from types import SimpleNamespace

    monkeypatch.setattr(cfg_mod, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg_mod, "get_state_path", lambda: tmp_path / "state.toml")
    monkeypatch.setattr(roles_mod, "_ROLES_DIR", tmp_path / "roles")
    monkeypatch.setenv("EDITOR", "my-editor")
    roles_mod.ensure_roles_dir()
    ui = _UI()
    ui.agent = SimpleNamespace(role=roles_mod.load_role("agent"), endpoint=None)
    ui.agent.set_role = lambda role: setattr(ui.agent, "role", role)
    ui.is_generating = lambda: generating
    return ui, tmp_path / "roles" / "agent.toml"


def _editing(monkeypatch, path, text, replace=None):
    """The editor appends ``text``, or with ``replace=(old, new)`` swaps a line."""
    async def _edit(_ui, opened):
        opened.write_text(opened.read_text().replace(*replace) if replace
                          else opened.read_text() + text)
        return True
    monkeypatch.setattr("moka_code.ui.external_editor.open_editor", _edit)


def test_editing_the_active_role_applies_when_the_editor_closes(monkeypatch, tmp_path):
    ui, path = _role_ui(monkeypatch, tmp_path)
    assert ui.agent.role.preserve_thinking is True
    _editing(monkeypatch, path, "", ("preserve_thinking = true", "preserve_thinking = false"))

    asyncio.run(cmd_role(ui, ["edit", "agent"]))

    assert ui.agent.role.preserve_thinking is False
    assert any("Config reloaded." in m for m in ui.chat_history_panel.messages)


def test_reload_applies_a_role_edited_by_hand(monkeypatch, tmp_path):
    ui, path = _role_ui(monkeypatch, tmp_path)
    path.write_text(path.read_text().replace("preserve_thinking = true", "preserve_thinking = false"))

    asyncio.run(cmd_reload(ui, []))

    assert ui.agent.role.preserve_thinking is False


def test_a_role_file_that_no_longer_loads_keeps_the_running_role(monkeypatch, tmp_path):
    ui, path = _role_ui(monkeypatch, tmp_path)
    _editing(monkeypatch, path, "\nreplay_reasoning_depth = 3\n")

    asyncio.run(cmd_role(ui, ["edit", "agent"]))

    assert ui.agent.role.preserve_thinking is True
    assert any("preserve_thinking" in m for m in ui.chat_history_panel.messages)


def test_a_role_change_waits_for_the_running_response_and_says_so(monkeypatch, tmp_path):
    ui, path = _role_ui(monkeypatch, tmp_path, generating=True)
    _editing(monkeypatch, path, "", ("preserve_thinking = true", "preserve_thinking = false"))

    asyncio.run(cmd_role(ui, ["edit", "agent"]))

    assert ui.agent.role.preserve_thinking is True
    assert any("run /reload once the response is done" in m for m in ui.chat_history_panel.messages)


# -- /role edit | create | path | remove (2026-10-22) -----------------------------

def test_role_edit_never_creates_a_file(monkeypatch, tmp_path):
    ui, _ = _role_ui(monkeypatch, tmp_path)
    _editing(monkeypatch, None, "")

    asyncio.run(cmd_role(ui, ["edit", "reveiw"]))

    assert not (tmp_path / "roles" / "reveiw.toml").exists()
    assert any("/role create reveiw" in m for m in ui.chat_history_panel.messages)


def test_role_create_writes_a_starter_and_refuses_an_existing_name(monkeypatch, tmp_path):
    ui, _ = _role_ui(monkeypatch, tmp_path)
    _editing(monkeypatch, None, "")

    asyncio.run(cmd_role(ui, ["create", "review"]))
    assert (tmp_path / "roles" / "review.toml").exists()

    asyncio.run(cmd_role(ui, ["create", "agent"]))
    assert any("already exists" in m for m in ui.chat_history_panel.messages)

    # Names no longer sit beside the verbs (/role select <name>), so a verb is a fine name.
    asyncio.run(cmd_role(ui, ["create", "path"]))
    assert (tmp_path / "roles" / "path.toml").exists()


def test_role_path_defaults_to_the_active_role(monkeypatch, tmp_path):
    ui, path = _role_ui(monkeypatch, tmp_path)
    monkeypatch.setattr("moka_code.ui.clipboard.copy_to_clipboard", lambda text: None)

    asyncio.run(cmd_role(ui, ["path"]))

    assert any(str(path) in m for m in ui.chat_history_panel.messages)


def test_role_remove_asks_then_deletes_the_file(monkeypatch, tmp_path):
    ui, _ = _role_ui(monkeypatch, tmp_path)
    asked = {}
    ui.show_search_modal = lambda title, items, **k: asked.update(title=title, items=items, **k)

    asyncio.run(cmd_role(ui, ["remove", "chat"]))
    assert asked["items"] == ["Remove", "Cancel"]
    asked["on_accept"]("Cancel")
    assert (tmp_path / "roles" / "chat.toml").exists()

    asked["on_accept"]("Remove")
    assert not (tmp_path / "roles" / "chat.toml").exists()


def test_role_remove_without_a_picker_removes_nothing(monkeypatch, tmp_path):
    ui, _ = _role_ui(monkeypatch, tmp_path)

    asyncio.run(cmd_role(ui, ["remove", "chat"]))

    assert (tmp_path / "roles" / "chat.toml").exists()
    assert any("nothing removed" in m for m in ui.chat_history_panel.messages)
