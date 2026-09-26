"""Tests for the file-based config commands and the external editor helper."""

import asyncio
from pathlib import Path

import pytest

from pico_chat.ui.external_editor import edit_file, resolve_editor
from pico_chat.ui.commands.core import cmd_config, cmd_edit, cmd_reload


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


def test_edit_file_invokes_editor(monkeypatch, tmp_path):
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "my-editor")
    calls = []
    monkeypatch.setattr("subprocess.call", lambda cmd: calls.append(cmd) or 0)

    assert edit_file(tmp_path / "ui.toml") is True
    assert calls == [["my-editor", str(tmp_path / "ui.toml")]]


def test_config_command_opens_section_and_reloads(monkeypatch, tmp_path):
    import pico_chat.pico_cfg as cfg_mod
    import pico_chat.harness.roles as roles_mod

    monkeypatch.setattr(cfg_mod, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg_mod, "get_state_path", lambda: tmp_path / "state.toml")
    monkeypatch.setattr(roles_mod, "_ROLES_DIR", tmp_path / "roles")
    monkeypatch.setenv("EDITOR", "my-editor")
    ui = _UI()

    opened = []

    async def _fake_open(_ui, path):
        opened.append(Path(path))
        return True

    monkeypatch.setattr("pico_chat.ui.external_editor.open_editor", _fake_open)

    asyncio.run(cmd_config(ui, ["servers"]))

    assert opened == [tmp_path / "servers.toml"]
    assert (tmp_path / "servers.toml").exists()
    assert any("Config reloaded" in m for m in ui.chat_history_panel.messages)


def test_config_command_no_args_lists_sections(monkeypatch):
    ui = _UI()

    asyncio.run(cmd_config(ui, []))

    assert ui.popups and ui.popups[0][0] == "config"
    assert "ui.toml" in ui.popups[0][1]


def test_config_command_unknown_section_reports_error(monkeypatch):
    ui = _UI()

    asyncio.run(cmd_config(ui, ["nope"]))

    assert any("Unknown section" in m for m in ui.chat_history_panel.messages)


def test_config_command_without_editor_reports_error(monkeypatch):
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr("pico_chat.ui.external_editor.shutil.which", lambda _name: None)
    ui = _UI()

    asyncio.run(cmd_config(ui, ["ui"]))

    assert any("No editor" in m for m in ui.chat_history_panel.messages)


def test_config_theme_materializes_section(monkeypatch, tmp_path):
    import pico_chat.pico_cfg as cfg_mod

    monkeypatch.setattr(cfg_mod, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg_mod, "get_state_path", lambda: tmp_path / "state.toml")
    monkeypatch.setenv("EDITOR", "my-editor")
    ui = _UI()
    opened = []

    monkeypatch.setattr(
        "pico_chat.ui.external_editor.open_editor",
        _recording_editor(opened),
    )

    asyncio.run(cmd_config(ui, ["theme", "nord"]))

    text = (tmp_path / "themes.toml").read_text(encoding="utf-8")
    assert "[themes.nord]" in text
    assert opened == [tmp_path / "themes.toml"]


def test_config_theme_unknown_reports_error(monkeypatch, tmp_path):
    import pico_chat.pico_cfg as cfg_mod

    monkeypatch.setattr(cfg_mod, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg_mod, "get_state_path", lambda: tmp_path / "state.toml")
    monkeypatch.setenv("EDITOR", "my-editor")
    ui = _UI()
    monkeypatch.setattr("pico_chat.ui.external_editor.open_editor", _recording_editor([]))

    asyncio.run(cmd_config(ui, ["theme", "nope"]))

    assert any("Unknown theme" in m for m in ui.chat_history_panel.messages)


def test_edit_command_opens_requested_file(monkeypatch, tmp_path):
    monkeypatch.setenv("EDITOR", "my-editor")
    ui = _UI()
    opened = []
    monkeypatch.setattr(
        "pico_chat.ui.external_editor.open_editor",
        _recording_editor(opened),
    )

    target = tmp_path / "notes.txt"
    asyncio.run(cmd_edit(ui, [str(target)]))

    assert opened == [target]


def test_reload_command_success(monkeypatch, tmp_path):
    import pico_chat.pico_cfg as cfg_mod
    import pico_chat.harness.roles as roles_mod

    monkeypatch.setattr(cfg_mod, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg_mod, "get_state_path", lambda: tmp_path / "state.toml")
    monkeypatch.setattr(roles_mod, "_ROLES_DIR", tmp_path / "roles")
    ui = _UI()

    asyncio.run(cmd_reload(ui, []))

    assert any("Config reloaded" in m for m in ui.chat_history_panel.messages)
    assert ui.refreshed == 1
