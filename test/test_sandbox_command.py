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


def _run_background(ui, args):
    """Run a command and the background tasks it starts (a build) to the end."""
    async def scenario():
        await _sandbox_cmd().execute(ui, args)
        await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])

    asyncio.run(scenario())


# --- tree + completion -----------------------------------------------------

def test_sandbox_is_a_command_tree():
    cmd = _sandbox_cmd()

    assert cmd.has_subcommands()
    assert set(cmd.get_completions(0)) == {
        "edit", "path", "create", "remove", "copy", "build", "start", "restart",
        "stop", "terminal"}


def test_start_completes_sandbox_ids_and_descriptions(monkeypatch, tmp_path):
    _workdir(tmp_path, monkeypatch)
    cmd = _sandbox_cmd()

    start, offset = cmd.resolve_command(["start"])

    assert offset == 1
    assert start.get_completions(0) == ["dev", "tight"]
    # Scope first, then the description (or the type).
    assert start.get_descriptions(0)["dev"] == "local · project toolchain"
    assert start.get_descriptions(0)["tight"] == "local · bubblewrap"


def test_edit_and_path_complete_names_then_the_file(monkeypatch, tmp_path):
    _workdir(tmp_path, monkeypatch)
    cmd = _sandbox_cmd()

    for verb in ("edit", "path"):
        sub, _ = cmd.resolve_command([verb])
        assert sub.get_completions(0) == ["dev", "tight"]
        assert sub.get_completions(1) == ["runcfg", "image"]


def test_create_and_copy_complete_scopes_and_types(monkeypatch, tmp_path):
    _workdir(tmp_path, monkeypatch)
    cmd = _sandbox_cmd()

    create, _ = cmd.resolve_command(["create"])
    copy, _ = cmd.resolve_command(["copy"])

    assert create.get_completions(0) == []                       # a new name: free text
    assert create.get_completions(1) == ["podman", "docker", "bubblewrap"]
    assert create.get_completions(2) == ["global", "local"]
    assert copy.get_completions(0) == ["dev", "tight"]
    assert copy.get_completions(1) == ["global", "local"]


def test_subcommand_descriptions_exposed():
    from moka_code.ui.commands.registry import get_subcommand_descriptions

    descriptions = get_subcommand_descriptions("sandbox")

    assert set(descriptions) == {
        "edit", "path", "create", "remove", "copy", "build", "start", "restart",
        "stop", "terminal"}
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


def test_edit_opens_project_file(tmp_path, monkeypatch):
    called = []
    async def _open(ui, name=None, which="runcfg"):
        called.append((name, which))

    monkeypatch.setattr("moka_code.ui.commands.sandbox.open_project_sandbox", _open)
    ui = _UI(tmp_path)
    _run(ui, ["edit"])
    _run(ui, ["edit", "dev"])
    _run(ui, ["edit", "dev", "image"])
    _run(ui, ["edit", "dev", "runcfg"])
    _run(ui, ["edit", "dev", "nope"])
    assert called == [(None, "runcfg"), ("dev", "runcfg"), ("dev", "image"), ("dev", "runcfg")]
    assert "Usage: /sandbox edit [name] [runcfg|image]" in ui.chat_history_panel.messages[-1]


def _editor(monkeypatch):
    opened = []

    async def _open(ui, path):
        opened.append(path)

    monkeypatch.setattr("moka_code.ui.external_editor.open_editor", _open)
    monkeypatch.setattr("moka_code.ui.external_editor.resolve_editor", lambda: "vi")
    return opened


def test_edit_image_writes_the_starter_once_and_opens_it(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    opened = _editor(monkeypatch)
    ui = _UI(workspace)
    build = projects.scope_dir("local", workspace) / "Containerfile"   # its dockerfile key

    _run(ui, ["edit", "dev", "image"])
    assert opened == [build]
    assert build.read_text().startswith("# Builds the image of sandbox dev")
    assert "Wrote a starter Containerfile" in ui.chat_history_panel.messages[-1]

    build.write_text("FROM mine\n")
    _run(ui, ["edit", "dev", "image"])
    assert build.read_text() == "FROM mine\n"          # never overwritten
    assert ui.agent.specs == []                         # nothing re-applied or built


def test_edit_image_without_a_dockerfile_key_says_to_add_it(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    directory = projects.scope_dir("local", workspace)
    (directory / "bare.toml").write_text('type = "docker"\nimage = "img"\n', encoding="utf-8")
    opened = _editor(monkeypatch)
    ui = _UI(workspace)

    _run(ui, ["edit", "bare", "image"])

    assert opened == [directory / "bare.Dockerfile"]
    assert 'add dockerfile = "bare.Dockerfile"' in ui.chat_history_panel.messages[-1]


def test_edit_image_of_bubblewrap_is_refused(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    opened = _editor(monkeypatch)
    ui = _UI(workspace)

    _run(ui, ["edit", "tight", "image"])

    assert opened == []
    assert "no image to edit" in ui.chat_history_panel.messages[-1]


def test_path_image_prints_the_build_file(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    ui = _UI(workspace)

    _run(ui, ["path", "dev", "image"])
    assert str(projects.scope_dir("local", workspace) / "Containerfile") in ui.chat_history_panel.messages[-1]
    _run(ui, ["path", "tight", "image"])
    assert "has no image" in ui.chat_history_panel.messages[-1]


def test_path_prints_the_active_sandbox_file(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch, active="dev")
    ui = _UI(workspace)

    _run(ui, ["path"])

    expected = projects.scope_dir("local", workspace) / "dev.toml"
    assert str(expected) in ui.chat_history_panel.messages[-1]


def test_path_takes_a_name_and_reports_unknown_and_none(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    ui = _UI(workspace)

    _run(ui, ["path", "tight"])
    assert "tight.toml" in ui.chat_history_panel.messages[-1]

    _run(ui, ["path", "ghost"])
    assert "Unknown sandbox 'ghost'" in ui.chat_history_panel.messages[-1]

    _run(ui, ["path"])              # no active sandbox, no name
    assert "No active sandbox" in ui.chat_history_panel.messages[-1]


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
    _run_background(ui, ["build", "dev"])

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
        on_output("STEP 3/5: RUN dotnet restore")
        on_output("error NU1301: Unable to load the service index")
        on_output("Error: building at STEP 3/5: exit status 1")
        on_output("")
        return 2

    monkeypatch.setattr(sandbox, "run_build", failing)
    ui = _UI(workspace)
    _run_background(ui, ["build", "dev"])
    text = ui.chat_history_panel.messages[-1]
    head = text.splitlines()[0]
    # The band line names the sandbox, the exit, the likely reason and the fix;
    # the end of the log is behind the click.
    assert head == ("build dev failed (exit 2): error NU1301: Unable to load the service index "
                    "→ /sandbox edit dev image")
    assert "Error: building at STEP 3/5" in text        # the full tail, behind the click


def test_build_runs_as_a_job_and_says_when_it_is_done(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    monkeypatch.setattr(sandbox, "runtime_available", lambda spec: True)
    from moka_code.ui.notice_feed import NoticeFeed

    seen = []

    async def fake_build(command, cwd=None, on_output=None):
        on_output("STEP 1/2: FROM python")
        seen.append([job.line(0) for job in ui.notice_feed.jobs])
        return 0

    monkeypatch.setattr(sandbox, "run_build", fake_build)
    ui = _UI(workspace)
    ui.notice_feed = NoticeFeed()
    ui.start_job = ui.notice_feed.start
    ui.end_job = ui.notice_feed.finish
    _run_background(ui, ["build", "dev"])

    assert len(seen[0]) == 1 and "building dev" in seen[0][0]
    assert seen[0][0].endswith("STEP 1/2: FROM python")
    assert ui.notice_feed.jobs == []
    assert ui.chat_history_panel.messages[-1].startswith("built dev in 0s (image img)")


def test_build_requires_id(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    ui = _UI(workspace)
    _run(ui, ["build"])
    assert "Usage: /sandbox build" in ui.chat_history_panel.messages[-1]


# --- create / remove / copy ------------------------------------------------

def test_create_writes_a_file_and_opens_it(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    opened = []

    async def _open(ui, name=None):
        opened.append(name)

    monkeypatch.setattr("moka_code.ui.commands.sandbox.open_project_sandbox", _open)
    ui = _UI(workspace)
    _run(ui, ["create", "box", "bubblewrap"])                 # global by default

    assert (tmp_path / "sandboxes" / "box.toml").exists()
    assert opened == ["box"]
    assert "Created global sandbox 'box'" in ui.chat_history_panel.messages[-1]


def test_create_local_on_request(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)

    async def _open(ui, name=None):
        pass

    monkeypatch.setattr("moka_code.ui.commands.sandbox.open_project_sandbox", _open)
    _run(_UI(workspace), ["create", "box", "bubblewrap", "local"])

    assert (projects.scope_dir("local", workspace) / "box.toml").exists()


def test_create_errors(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    ui = _UI(workspace)

    _run(ui, ["create", "box"])
    assert "Usage: /sandbox create" in ui.chat_history_panel.messages[-1]
    _run(ui, ["create", "box", "bubblewrap", "nowhere"])
    assert "Scope must be global or local" in ui.chat_history_panel.messages[-1]
    _run(ui, ["create", "dev", "bubblewrap"])             # dev is a local: names are shared
    assert "already exists" in ui.chat_history_panel.messages[-1]


def test_remove_asks_then_deletes_and_stops_the_active_one(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch, active="dev")
    ui = _UI(workspace)
    ui.agent.set_sandbox = lambda spec, name: None
    asked = {}
    ui.show_search_modal = lambda title, items, **k: asked.update(title=title, items=items, **k)
    path = projects.scope_dir("local", workspace) / "dev.toml"

    _run(ui, ["remove", "dev"])
    assert asked["items"] == ["Remove", "Cancel"]
    asked["on_accept"]("Cancel")
    assert path.exists()

    asked["on_accept"]("Remove")
    assert not path.exists()
    assert projects.load_project(workspace).active is None
    assert "Removed local sandbox: dev" in ui.chat_history_panel.messages[-1]


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


class _Transport:
    """The running worker: records a graceful stop (and that it was awaited)."""

    def __init__(self, events):
        self.events = events

    async def stop(self):
        await asyncio.sleep(0)
        self.events.append("stopped")


def test_restart_stops_the_running_worker_then_applies_the_file(monkeypatch, tmp_path):
    """2026-10-22: restart shuts the old worker down and waits for it (its --rm
    container exits) before the fresh spec runs; it never rebuilds the image."""
    workspace = _workdir(tmp_path, monkeypatch, active="dev")
    _ready(monkeypatch)
    ui = _UI(workspace)
    events = []
    ui.agent.transport = _Transport(events)
    ui.agent.set_sandbox = lambda spec, name=None: events.append(("set", spec.runtime, name))
    monkeypatch.setattr(sandbox, "run_build", lambda *a, **k: events.append("built"))

    _run(ui, ["restart"])

    assert events == ["stopped", ("set", "podman", "dev")]
    assert projects.load_project(workspace).active == "dev"     # unchanged
    assert "restarted" in ui.chat_history_panel.messages[-1]


def test_restart_with_a_missing_image_keeps_the_running_worker(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch, active="dev")
    ui = _UI(workspace)
    events = []
    ui.agent.transport = _Transport(events)
    monkeypatch.setattr(sandbox, "runtime_available", lambda spec: True)
    monkeypatch.setattr(sandbox, "image_present", lambda spec: False)

    _run(ui, ["restart"])

    assert events == [] and ui.agent.specs == []
    assert "not built" in ui.chat_history_panel.messages[-1]


def test_restart_without_an_active_sandbox_does_nothing(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch)
    ui = _UI(workspace)

    _run(ui, ["restart"])

    assert ui.agent.specs == []
    assert "No active sandbox" in ui.chat_history_panel.messages[-1]


def test_restart_rejects_arguments(monkeypatch, tmp_path):
    workspace = _workdir(tmp_path, monkeypatch, active="dev")
    ui = _UI(workspace)

    _run(ui, ["restart", "dev"])

    assert "Usage: /sandbox restart" in ui.chat_history_panel.messages[-1]


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
    assert next_role_command(ui) == "/role select review"
    ui.agent.role.name = "review"
    assert next_role_command(ui) == "/role select agent"


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
