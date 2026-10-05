"""Tests for the /sandbox command tree (subcommands + positional completion)."""

import asyncio

import moka_code.settings as settings
import moka_code.sandbox as sandbox
from moka_code import projects
from moka_code.ui.commands.registry import COMMANDS


class _Panel:
    def __init__(self):
        self.messages = []

    def add_message(self, text, msg_type=None, title=None):
        self.messages.append(text)


class _Agent:
    def __init__(self, workspace):
        self.workspace = str(workspace)
        self.specs = []

    def set_sandbox(self, spec, name=None):
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
                           initial_index=0, ordered=False):
        self.modal = {"title": title, "items": items, "on_accept": on_accept,
                      "ordered": ordered}
        return None


def _sandbox_cmd():
    return COMMANDS["sandbox"]


def _project(config_dir, workspace, active=None):
    """Two local sandboxes: ``dev`` (podman) and ``tight`` (bubblewrap)."""
    directory = projects.scope_dir("local", workspace)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "dev.toml").write_text(
        'type = "podman"\n'
        'description = "project toolchain"\n'
        'image = "img"\n'
        'dockerfile = "Containerfile"\n', encoding="utf-8")
    (directory / "tight.toml").write_text('type = "bubblewrap"\n', encoding="utf-8")
    if active:
        projects.set_active(workspace, active)
    return directory


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
    assert set(cmd.get_completions(0)) == {
        "config", "new", "copy", "build", "start", "stop", "terminal", "init"}


def test_start_completes_sandbox_ids_and_descriptions(monkeypatch, tmp_path):
    _workdir(tmp_path, monkeypatch)
    cmd = _sandbox_cmd()

    start, offset = cmd.resolve_command(["start"])

    assert offset == 1
    assert start.get_completions(0) == ["dev", "tight"]
    # Scope first, then the description (or the type).
    assert start.get_descriptions(0)["dev"] == "local · project toolchain"
    assert start.get_descriptions(0)["tight"] == "local · bubblewrap"


def test_init_completes_names_then_bases(monkeypatch, tmp_path):
    _workdir(tmp_path, monkeypatch)
    cmd = _sandbox_cmd()

    init, offset = cmd.resolve_command(["init"])

    assert offset == 1
    assert init.get_completions(0) == ["dev", "tight"]
    assert init.get_completions(1) == ["python", "debian", "ubuntu"]


def test_new_and_copy_complete_scopes_and_types(monkeypatch, tmp_path):
    _workdir(tmp_path, monkeypatch)
    cmd = _sandbox_cmd()

    new, _ = cmd.resolve_command(["new"])
    copy, _ = cmd.resolve_command(["copy"])

    assert new.get_completions(0) == ["global", "local"]
    assert new.get_completions(1) == ["podman", "docker", "bubblewrap"]
    assert copy.get_completions(0) == ["dev", "tight"]
    assert copy.get_completions(1) == ["global", "local"]


def test_subcommand_descriptions_exposed():
    from moka_code.ui.commands.registry import get_subcommand_descriptions

    descriptions = get_subcommand_descriptions("sandbox")

    assert set(descriptions) == {
        "config", "new", "copy", "build", "start", "stop", "terminal", "init"}
    assert "sandbox" in descriptions["start"].lower() or descriptions["start"]


# --- dispatch --------------------------------------------------------------

def test_bare_sandbox_lists_subcommands(tmp_path):
    ui = _UI(tmp_path)
    _run(ui, [])
    assert "start" in ui.popups[-1]


def test_bare_sandbox_opens_the_picker_when_there_is_something_to_pick(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch, active="dev")
    _ready(monkeypatch)
    ui = _UI(workspace, with_modal=True)

    async def scenario():
        await _sandbox_cmd().execute(ui, [])
        assert ui.modal["title"] == "Sandboxes"
        assert ui.modal["items"] == ["dev", "tight"]
        ui.modal["on_accept"]("tight")
        await asyncio.gather(
            *[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])

    asyncio.run(scenario())

    assert projects.load_project(workspace).active == "tight"
    assert not any("Subcommands" in text for text in ui.popups)   # the picker, not the help


def test_unknown_subcommand_lists_options_even_with_sandboxes(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    ui = _UI(workspace, with_modal=True)

    _run(ui, ["nope"])

    assert ui.modal is None and "start" in ui.popups[-1]


def test_unknown_subcommand_lists_options(tmp_path):
    ui = _UI(tmp_path)
    _run(ui, ["nope"])
    assert "Subcommands" in ui.popups[-1] or "start" in ui.popups[-1]


def test_config_opens_project_file(tmp_path, monkeypatch):
    called = []
    async def _open(ui, name=None):
        called.append(name)

    monkeypatch.setattr("moka_code.ui.commands.sandbox.open_project_sandbox", _open)
    ui = _UI(tmp_path)
    _run(ui, ["config"])
    _run(ui, ["config", "dev"])
    assert called == [None, "dev"]


def test_start_without_id_lists_sandboxes(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch, active="dev")
    ui = _UI(workspace)
    _run(ui, ["start"])
    assert "active: dev" in ui.popups[-1]
    assert "local  · dev" in ui.popups[-1]
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
    directory = projects.scope_dir("local", workspace)
    # Containerfile and context are next to the sandbox file, not in the workspace.
    assert calls[0][-3:] == ["-f", str(directory / "Containerfile"), str(directory)]
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

def test_init_writes_a_containerfile_next_to_the_sandbox(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    ui = _UI(workspace)
    _run(ui, ["init", "dev", "debian"])
    path = projects.scope_dir("local", workspace) / "dev.Containerfile"
    text = path.read_text()
    assert "FROM debian:stable-slim" in text
    assert "install -y --no-install-recommends python3" in text
    assert "WORKDIR /workspace" in text
    assert "Wrote dev.Containerfile" in ui.chat_history_panel.messages[-1]
    assert not (workspace / "Containerfile").exists()


def test_init_docker_writes_dockerfile(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    projects.create_sandbox(workspace, "local", "docker", "dk")
    ui = _UI(workspace)
    _run(ui, ["init", "dk"])
    path = projects.scope_dir("local", workspace) / "dk.Dockerfile"
    assert path.read_text().startswith("# Built by moka")
    assert "FROM python:3.12-slim" in path.read_text()


def test_init_rejects_bubblewrap(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    ui = _UI(workspace)
    _run(ui, ["init", "tight"])
    assert "needs a podman or docker sandbox" in ui.chat_history_panel.messages[-1]


def test_init_unknown_sandbox(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    ui = _UI(workspace)
    _run(ui, ["init", "ghost"])
    assert "Unknown sandbox 'ghost'" in ui.chat_history_panel.messages[-1]


def test_init_refuses_overwrite(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    existing = projects.scope_dir("local", workspace) / "dev.Containerfile"
    existing.write_text("FROM x\n")
    ui = _UI(workspace)
    _run(ui, ["init", "dev"])
    assert "already exists" in ui.chat_history_panel.messages[-1]
    assert existing.read_text() == "FROM x\n"


# --- new / copy ------------------------------------------------------------

def test_new_creates_a_file_and_opens_it(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    opened = []

    async def _open(ui, name=None):
        opened.append(name)

    monkeypatch.setattr("moka_code.ui.commands.sandbox.open_project_sandbox", _open)
    ui = _UI(workspace)
    _run(ui, ["new", "global", "bubblewrap", "box"])

    assert (tmp_path / "sandboxes" / "box.toml").exists()
    assert opened == ["box"]
    assert "Created global sandbox 'box'" in ui.chat_history_panel.messages[-1]


def test_new_name_defaults_to_the_type(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)

    async def _open(ui, name=None):
        pass

    monkeypatch.setattr("moka_code.ui.commands.sandbox.open_project_sandbox", _open)
    _run(_UI(workspace), ["new", "local", "bubblewrap"])

    assert (projects.scope_dir("local", workspace) / "bubblewrap.toml").exists()


def test_new_errors(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    ui = _UI(workspace)

    _run(ui, ["new", "local"])
    assert "Usage: /sandbox new" in ui.chat_history_panel.messages[-1]
    _run(ui, ["new", "nowhere", "bubblewrap"])
    assert "Scope must be global or local" in ui.chat_history_panel.messages[-1]
    _run(ui, ["new", "global", "bubblewrap", "dev"])      # dev is a local: names are shared
    assert "already exists" in ui.chat_history_panel.messages[-1]


def test_copy_into_global(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    ui = _UI(workspace)
    _run(ui, ["copy", "tight", "global", "shared"])

    assert (tmp_path / "sandboxes" / "shared.toml").exists()
    assert "Copied 'tight' to global sandbox 'shared'" in ui.chat_history_panel.messages[-1]


def test_copy_errors(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    ui = _UI(workspace)

    _run(ui, ["copy", "tight"])
    assert "Usage: /sandbox copy" in ui.chat_history_panel.messages[-1]
    _run(ui, ["copy", "ghost", "local", "x"])
    assert "Sandbox not found: ghost" in ui.chat_history_panel.messages[-1]
    _run(ui, ["copy", "tight", "local", "dev"])
    assert "already exists" in ui.chat_history_panel.messages[-1]
    _run(ui, ["copy", "tight", "nowhere", "x"])
    assert "Scope must be global or local" in ui.chat_history_panel.messages[-1]


def test_stop_deactivates(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch, active="dev")
    ui = _UI(workspace)
    _run(ui, ["stop"])
    assert ui.agent.specs[-1] is None
    assert projects.load_project(workspace).active is None


def test_next_sandbox_command_cycles_through_none(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from moka_code.ui.commands import next_sandbox_command

    ui = _UI(tmp_path)
    project = SimpleNamespace(sandboxes={"b": object(), "a": object()}, active=None)
    monkeypatch.setattr(projects, "load_project", lambda *_a, **_k: project)

    assert next_sandbox_command(ui) == "/sandbox start a"
    project.active = "a"
    assert next_sandbox_command(ui) == "/sandbox start b"
    project.active = "b"
    assert next_sandbox_command(ui) == "/sandbox stop"

    project.sandboxes = {}
    project.active = None
    assert next_sandbox_command(ui) is None


def test_next_role_command_wraps(monkeypatch):
    from types import SimpleNamespace
    from moka_code.harness import roles
    from moka_code.ui.commands import next_role_command

    monkeypatch.setattr(roles, "list_roles", lambda: ["agent", "chat", "review"])
    ui = SimpleNamespace(agent=SimpleNamespace(role=SimpleNamespace(name="chat")))
    assert next_role_command(ui) == "/role review"
    ui.agent.role.name = "review"
    assert next_role_command(ui) == "/role agent"


def test_start_without_id_opens_a_picker_that_starts_the_choice(monkeypatch, tmp_path):
    """Like /model, /theme, /role: a bare command opens a picker over the same
    values the ``/sandbox start <id>`` suggestions use."""
    workspace = _workdir(tmp_path, monkeypatch, active="dev")
    _ready(monkeypatch)
    ui = _UI(workspace, with_modal=True)

    async def scenario():
        await _sandbox_cmd().execute(ui, ["start"])
        assert ui.modal["title"] == "Sandboxes"
        assert ui.modal["items"] == ["dev", "tight"]
        ui.modal["on_accept"]("tight")
        await asyncio.gather(
            *[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])

    asyncio.run(scenario())

    assert projects.load_project(workspace).active == "tight"
