"""Tests for the /sandbox command (per-project selection)."""

import asyncio

import pico_chat.pico_cfg as pico_cfg
from pico_chat import projects
from pico_chat.ui.commands.registry import COMMANDS
from pico_chat.ui.commands.sandbox import cmd_sandbox, sandbox_name_completions


class _Panel:
    def __init__(self):
        self.messages = []

    def add_message(self, text, msg_type=None, title=None):
        self.messages.append(text)


class _Agent:
    def __init__(self, workspace):
        self.workspace = str(workspace)
        self.specs = []

    def set_sandbox(self, spec):
        self.specs.append(spec)


class _UI:
    def __init__(self, workspace):
        self.agent = _Agent(workspace)
        self.chat_history_panel = _Panel()
        self.popups = []
        self.refreshed = 0

    def show_popup(self, name, text, **kwargs):
        self.popups.append(text)

    def refresh_status_bar(self):
        self.refreshed += 1


def _project_with_sandboxes(config_dir, workspace, active=None):
    p = config_dir / "projects" / f"{workspace.name}.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'path = "{workspace}"']
    if active:
        lines.append(f'active = "{active}"')
    lines += [
        "[sandboxes.dev]",
        'type = "podman"',
        'image = "img"',
        "[sandboxes.tight]",
        'type = "bubblewrap"',
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_sandbox_command_registered():
    assert "sandbox" in COMMANDS
    assert COMMANDS["sandbox"].params[0].name == "ID"


def test_cmd_sandbox_lists(monkeypatch, tmp_path):
    workspace = tmp_path / "proj"
    workspace.mkdir()
    monkeypatch.setattr(pico_cfg, "get_config_dir", lambda: tmp_path)
    _project_with_sandboxes(tmp_path, workspace, active="dev")
    ui = _UI(workspace)

    asyncio.run(cmd_sandbox(ui, []))

    assert "active: dev" in ui.popups[-1]
    assert "dev" in ui.popups[-1]


def test_cmd_sandbox_selects_and_persists(monkeypatch, tmp_path):
    workspace = tmp_path / "proj"
    workspace.mkdir()
    monkeypatch.setattr(pico_cfg, "get_config_dir", lambda: tmp_path)
    _project_with_sandboxes(tmp_path, workspace)
    ui = _UI(workspace)

    asyncio.run(cmd_sandbox(ui, ["tight"]))

    assert ui.agent.specs[-1].runtime == "bubblewrap"
    assert projects.load_project(workspace).active == "tight"


def test_cmd_sandbox_none_deactivates(monkeypatch, tmp_path):
    workspace = tmp_path / "proj"
    workspace.mkdir()
    monkeypatch.setattr(pico_cfg, "get_config_dir", lambda: tmp_path)
    _project_with_sandboxes(tmp_path, workspace, active="dev")
    ui = _UI(workspace)

    asyncio.run(cmd_sandbox(ui, ["none"]))

    assert ui.agent.specs[-1] is None
    assert projects.load_project(workspace).active is None


def test_cmd_sandbox_unknown_id(monkeypatch, tmp_path):
    workspace = tmp_path / "proj"
    workspace.mkdir()
    monkeypatch.setattr(pico_cfg, "get_config_dir", lambda: tmp_path)
    _project_with_sandboxes(tmp_path, workspace)
    ui = _UI(workspace)

    asyncio.run(cmd_sandbox(ui, ["ghost"]))

    assert "Unknown sandbox 'ghost'" in ui.chat_history_panel.messages[-1]
    assert ui.agent.specs == []


def test_sandbox_name_completions(monkeypatch, tmp_path):
    workspace = tmp_path / "proj"
    workspace.mkdir()
    monkeypatch.setattr(pico_cfg, "get_config_dir", lambda: tmp_path)
    monkeypatch.chdir(workspace)
    _project_with_sandboxes(tmp_path, workspace)

    assert sandbox_name_completions() == ["none", "dev", "tight"]
