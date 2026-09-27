"""Tests for the /sandbox command tree (subcommands + positional completion)."""

import asyncio

import moka_chat.settings as settings
import moka_chat.sandbox as sandbox
from moka_chat import projects
from moka_chat.ui.commands.registry import COMMANDS


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
    def __init__(self, workspace, with_modal=False):
        self.agent = _Agent(workspace)
        self.chat_history_panel = _Panel()
        self.popups = []
        self.activity_lines = []
        self.refreshed = 0
        self.modal = None
        if with_modal:
            self.show_search_modal = self._show_search_modal

    def show_popup(self, name, text, **kwargs):
        self.popups.append(text)

    def activity(self, text, level="info"):
        self.activity_lines.append(text)

    def refresh_status_bar(self):
        self.refreshed += 1

    def is_generating(self):
        return False

    def _show_search_modal(self, title, items, descriptions=None, footers=None,
                           on_accept=None, on_cancel=None, on_highlight=None,
                           initial_index=0):
        self.modal = {"title": title, "items": items, "on_accept": on_accept}
        return None


def _sandbox_cmd():
    return COMMANDS["sandbox"]


def _project(config_dir, workspace, active=None):
    p = config_dir / "projects" / f"{workspace.name}.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'path = "{workspace}"']
    if active:
        lines.append(f'active = "{active}"')
    lines += [
        "[sandboxes.dev]",
        'type = "podman"',
        'description = "project toolchain"',
        'image = "img"',
        'dockerfile = "Containerfile"',
        "[sandboxes.tight]",
        'type = "bubblewrap"',
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _ready(monkeypatch):
    monkeypatch.setattr(sandbox, "runtime_available", lambda spec: True)
    monkeypatch.setattr(sandbox, "image_present", lambda spec: True)


def _workdir(tmp_path, monkeypatch, active=None):
    workspace = tmp_path / "proj"
    workspace.mkdir()
    monkeypatch.setattr(settings, "get_config_dir", lambda: tmp_path)
    monkeypatch.chdir(workspace)
    _project(tmp_path, workspace, active=active)
    return workspace


def _run(ui, args):
    asyncio.run(_sandbox_cmd().execute(ui, args))


# --- tree + completion -----------------------------------------------------

def test_sandbox_is_a_command_tree():
    cmd = _sandbox_cmd()

    assert cmd.has_subcommands()
    assert set(cmd.get_completions(0)) == {"config", "build", "start", "stop", "terminal", "init"}


def test_start_completes_sandbox_ids_and_descriptions(monkeypatch, tmp_path):
    _workdir(tmp_path, monkeypatch)
    cmd = _sandbox_cmd()

    start, offset = cmd.resolve_command(["start"])

    assert offset == 1
    assert start.get_completions(0) == ["dev", "tight"]
    assert start.get_descriptions(0)["dev"] == "project toolchain"
    assert start.get_descriptions(0)["tight"] == "bubblewrap"


def test_init_completes_runtimes_then_bases(monkeypatch, tmp_path):
    _workdir(tmp_path, monkeypatch)
    cmd = _sandbox_cmd()

    init, offset = cmd.resolve_command(["init"])

    assert offset == 1
    assert init.get_completions(0) == ["podman", "docker"]
    assert init.get_completions(1) == ["python", "debian", "ubuntu"]


def test_subcommand_descriptions_exposed():
    from moka_chat.ui.commands.registry import get_subcommand_descriptions

    descriptions = get_subcommand_descriptions("sandbox")

    assert set(descriptions) == {"config", "build", "start", "stop", "terminal", "init"}
    assert "sandbox" in descriptions["start"].lower() or descriptions["start"]


# --- dispatch --------------------------------------------------------------

def test_bare_sandbox_lists_subcommands(tmp_path):
    ui = _UI(tmp_path)
    _run(ui, [])
    assert "start" in ui.popups[-1]


def test_unknown_subcommand_lists_options(tmp_path):
    ui = _UI(tmp_path)
    _run(ui, ["nope"])
    assert "Subcommands" in ui.popups[-1] or "start" in ui.popups[-1]


def test_config_opens_project_file(tmp_path, monkeypatch):
    called = []
    async def _open(ui):
        called.append(True)

    monkeypatch.setattr("moka_chat.ui.commands.sandbox.open_project_sandbox", _open)
    ui = _UI(tmp_path)
    _run(ui, ["config"])
    assert called == [True]


def test_start_without_id_lists_sandboxes(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch, active="dev")
    ui = _UI(workspace)
    _run(ui, ["start"])
    assert "active: dev" in ui.popups[-1]
    assert "project toolchain" in ui.popups[-1]


def test_start_activates(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    _ready(monkeypatch)
    ui = _UI(workspace)
    _run(ui, ["start", "dev"])
    assert ui.agent.specs[-1].runtime == "podman"
    assert projects.load_project(workspace).active == "dev"


def test_start_refuses_without_runtime(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    monkeypatch.setattr(sandbox, "runtime_available", lambda spec: False)
    ui = _UI(workspace)
    _run(ui, ["start", "dev"])
    assert "not found on PATH" in ui.chat_history_panel.messages[-1]
    assert ui.agent.specs == []


def test_start_warns_when_image_missing_without_modal(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    monkeypatch.setattr(sandbox, "runtime_available", lambda spec: True)
    monkeypatch.setattr(sandbox, "image_present", lambda spec: False)
    ui = _UI(workspace)
    _run(ui, ["start", "dev"])
    assert "not built" in ui.chat_history_panel.messages[-1]
    assert ui.agent.specs == []


def test_start_offers_build_then_start(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    monkeypatch.setattr(sandbox, "runtime_available", lambda spec: True)
    monkeypatch.setattr(sandbox, "image_present", lambda spec: False)
    calls = []

    async def fake_build(command, cwd=None, on_output=None):
        calls.append(command)
        return 0

    monkeypatch.setattr(sandbox, "run_build", fake_build)
    ui = _UI(workspace, with_modal=True)

    async def scenario():
        await _sandbox_cmd().execute(ui, ["start", "dev"])
        assert ui.modal["items"] == ["Build now", "Cancel"]
        ui.modal["on_accept"]("Build now")
        await asyncio.gather(
            *[t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        )

    asyncio.run(scenario())

    assert calls
    assert ui.agent.specs[-1].runtime == "podman"
    assert projects.load_project(workspace).active == "dev"


def test_start_offer_cancel_does_nothing(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    monkeypatch.setattr(sandbox, "runtime_available", lambda spec: True)
    monkeypatch.setattr(sandbox, "image_present", lambda spec: False)
    ui = _UI(workspace, with_modal=True)

    async def scenario():
        await _sandbox_cmd().execute(ui, ["start", "dev"])
        ui.modal["on_accept"]("Cancel")
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert ui.agent.specs == []
    assert projects.load_project(workspace).active is None


def test_start_unknown_id(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    ui = _UI(workspace)
    _run(ui, ["start", "ghost"])
    assert "Unknown sandbox 'ghost'" in ui.chat_history_panel.messages[-1]


# --- build -----------------------------------------------------------------

def test_build_runs_without_activating(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    monkeypatch.setattr(sandbox, "runtime_available", lambda spec: True)
    calls = []

    async def fake_build(command, cwd=None, on_output=None):
        calls.append(command)
        if on_output:
            on_output("step 1")
        return 0

    monkeypatch.setattr(sandbox, "run_build", fake_build)
    ui = _UI(workspace)
    _run(ui, ["build", "dev"])

    assert calls and calls[0][0] == "podman"
    assert any("step 1" in line for line in ui.activity_lines)
    assert ui.agent.specs == []
    assert projects.load_project(workspace).active is None


def test_build_failure_reports(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    monkeypatch.setattr(sandbox, "runtime_available", lambda spec: True)

    async def failing(command, cwd=None, on_output=None):
        return 2

    monkeypatch.setattr(sandbox, "run_build", failing)
    ui = _UI(workspace)
    _run(ui, ["build", "dev"])
    assert "build failed" in ui.chat_history_panel.messages[-1].lower()


def test_build_requires_id(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    ui = _UI(workspace)
    _run(ui, ["build"])
    assert "Usage: /sandbox build" in ui.chat_history_panel.messages[-1]


# --- init / quit -----------------------------------------------------------

def test_init_podman_writes_containerfile(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    ui = _UI(workspace)
    _run(ui, ["init", "podman", "debian"])
    text = (workspace / "Containerfile").read_text()
    assert "FROM debian:stable-slim" in text
    assert "install -y --no-install-recommends python3" in text
    assert "WORKDIR /workspace" in text
    assert "Wrote Containerfile" in ui.chat_history_panel.messages[-1]


def test_init_docker_writes_dockerfile(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    ui = _UI(workspace)
    _run(ui, ["init", "docker"])
    assert (workspace / "Dockerfile").exists()
    assert (workspace / "Dockerfile").read_text().startswith("# Built by moka")
    assert "FROM python:3.12-slim" in (workspace / "Dockerfile").read_text()
    assert not (workspace / "Containerfile").exists()


def test_init_rejects_bubblewrap(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    ui = _UI(workspace)
    _run(ui, ["init", "bubblewrap"])
    assert "supports podman, docker" in ui.chat_history_panel.messages[-1]


def test_init_refuses_overwrite(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    (workspace / "Containerfile").write_text("FROM x\n")
    ui = _UI(workspace)
    _run(ui, ["init", "podman"])
    assert "already exists" in ui.chat_history_panel.messages[-1]


def test_stop_deactivates(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch, active="dev")
    ui = _UI(workspace)
    _run(ui, ["stop"])
    assert ui.agent.specs[-1] is None
    assert projects.load_project(workspace).active is None
