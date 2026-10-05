"""A running sandbox versus its file: the notice band says when they differ,
``/reload`` applies the edit (or the deletion), and registry errors are reported."""

import asyncio

import pytest

import moka_code.harness.roles as roles_module
from moka_code import projects, settings
from moka_code.harness.harness import Harness
from moka_code.ui.commands.core import cmd_reload
from moka_code.ui.status_presenter import notices


class _Panel:
    def __init__(self):
        self.messages = []

    def add_message(self, text, msg_type=None, title=None):
        self.messages.append(text)


class _UI:
    def __init__(self, agent, generating=False):
        self.agent = agent
        self.chat_history_panel = _Panel()
        self._generating = generating

    def is_generating(self):
        return self._generating


@pytest.fixture
def running(tmp_path, monkeypatch):
    """A harness whose active sandbox is the local ``box`` (bubblewrap)."""
    monkeypatch.setattr(settings, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(settings, "get_state_path", lambda: tmp_path / "state.toml")
    monkeypatch.setattr(roles_module, "_ROLES_DIR", tmp_path / "roles")
    workspace = tmp_path / "proj"
    workspace.mkdir()
    path = projects.create_sandbox(workspace, "local", "bubblewrap", "box")
    projects.set_active(workspace, "box")

    harness = Harness(workspace_path=str(workspace))
    project = projects.load_project(workspace)
    harness.set_sandbox(projects.active_spec(project), project.active)
    return harness, workspace, path


def _texts(harness):
    return [text for _, text in notices(harness)]


def test_drift_reports_how_the_running_sandbox_differs_from_its_file(running):
    harness, _workspace, path = running
    assert harness.sandbox_drift() is None

    path.write_text(path.read_text() + "\n# a comment\n", encoding="utf-8")
    assert harness.sandbox_drift() is None              # touched, same sandbox: no warning

    path.write_text(path.read_text() + "\nnetwork = true\n", encoding="utf-8")
    assert harness.sandbox_drift() == "changed"
    assert harness.transport.spec.network is False      # the running one is untouched

    path.unlink()
    assert harness.sandbox_drift() == "gone"


def test_no_active_sandbox_means_no_drift(running):
    harness, _workspace, path = running
    harness.set_sandbox(None)

    path.write_text('type = "bubblewrap"\nnetwork = true\n', encoding="utf-8")

    assert harness.sandbox_name is None and harness.sandbox_drift() is None


def test_the_band_names_a_gone_or_changed_sandbox(running):
    harness, _workspace, path = running
    assert not any("sandbox box" in t for t in _texts(harness))

    path.write_text(path.read_text() + "\nnetwork = true\n", encoding="utf-8")
    assert "sandbox box changed on disk → /reload" in _texts(harness)

    path.unlink()
    assert any("sandbox box: its file is gone, still running from memory" in t
               for t in _texts(harness))


def test_reload_applies_an_edited_sandbox(running):
    harness, _workspace, path = running
    path.write_text(path.read_text() + "\nnetwork = true\n", encoding="utf-8")

    asyncio.run(cmd_reload(_UI(harness), []))

    assert harness.transport.spec.network is True
    assert harness.sandbox_drift() is None
    assert not any("sandbox box" in t for t in _texts(harness))


def test_reload_of_a_touched_file_changes_nothing_but_clears_the_difference(running):
    harness, _workspace, path = running
    transport = harness.transport
    path.write_text(path.read_text() + "\n# a comment\n", encoding="utf-8")

    asyncio.run(cmd_reload(_UI(harness), []))

    assert harness.transport is transport               # not rebuilt


def test_reload_after_the_file_is_deleted_leaves_no_active_sandbox(running):
    harness, _workspace, path = running
    path.unlink()

    ui = _UI(harness)
    asyncio.run(cmd_reload(ui, []))

    assert harness.sandbox_name is None
    assert not harness.sandboxed()
    assert any("active sandbox 'box' is not defined" in m for m in ui.chat_history_panel.messages)


def test_a_sandbox_change_waits_for_the_running_response_and_says_so(running):
    harness, _workspace, path = running
    path.write_text(path.read_text() + "\nnetwork = true\n", encoding="utf-8")

    ui = _UI(harness, generating=True)
    asyncio.run(cmd_reload(ui, []))

    assert harness.transport.spec.network is False
    assert any("run /reload once the response is done" in m for m in ui.chat_history_panel.messages)


def test_reload_reports_a_name_in_both_scopes(running):
    harness, workspace, _path = running
    projects.create_sandbox(workspace, "global", "bubblewrap", "dup")
    (projects.scope_dir("local", workspace) / "dup.toml").write_text(
        'type = "bubblewrap"\n', encoding="utf-8")

    ui = _UI(harness)
    asyncio.run(cmd_reload(ui, []))

    assert any("'dup' exists in both global and local" in m for m in ui.chat_history_panel.messages)


# --- a stale image ---------------------------------------------------------

@pytest.fixture
def container(tmp_path, monkeypatch):
    """A harness running the local podman sandbox ``dev`` whose image was built
    at a time the test controls (``built["at"]``)."""
    from moka_code import sandbox

    monkeypatch.setattr(settings, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(settings, "get_state_path", lambda: tmp_path / "state.toml")
    monkeypatch.setattr(roles_module, "_ROLES_DIR", tmp_path / "roles")
    monkeypatch.setattr(sandbox, "runtime_available", lambda spec: True)
    monkeypatch.setattr(sandbox, "image_present", lambda spec: True)
    built = {"at": 1000.0}
    monkeypatch.setattr(sandbox, "image_created", lambda spec: built["at"])
    workspace = tmp_path / "proj"
    workspace.mkdir()
    sandbox_file = projects.scope_dir("local", workspace) / "dev.toml"
    sandbox_file.parent.mkdir(parents=True)
    sandbox_file.write_text(
        'type = "podman"\nimage = "img"\ndockerfile = "dev.Containerfile"\n', encoding="utf-8")
    containerfile = sandbox_file.parent / "dev.Containerfile"
    containerfile.write_text("FROM x\n", encoding="utf-8")
    projects.set_active(workspace, "dev")

    harness = Harness(workspace_path=str(workspace))
    project = projects.load_project(workspace)
    harness.set_sandbox(projects.active_spec(project), project.active)
    return harness, containerfile, built


def _age(path, seconds):
    import os

    os.utime(path, (seconds, seconds))


def test_a_dockerfile_edited_after_the_build_makes_the_image_stale(container):
    harness, containerfile, _built = container

    _age(containerfile, 500.0)                        # edited before the image was built
    assert harness.sandbox_stale() is False
    assert not any("older than its dockerfile" in t for t in _texts(harness))

    _age(containerfile, 2000.0)                       # edited after
    assert harness.sandbox_stale() is True
    assert "sandbox dev: its image is older than its dockerfile → /sandbox build dev" \
        in _texts(harness)


def test_a_rebuild_clears_the_stale_notice(container):
    harness, containerfile, built = container
    _age(containerfile, 2000.0)
    assert harness.sandbox_stale() is True

    built["at"] = 3000.0                              # /sandbox build finished
    harness.track_sandbox("dev")

    assert harness.sandbox_stale() is False


def test_reload_picks_up_an_image_rebuilt_outside_moka(container):
    harness, containerfile, built = container
    _age(containerfile, 2000.0)
    assert harness.sandbox_stale() is True

    built["at"] = 3000.0
    asyncio.run(cmd_reload(_UI(harness), []))

    assert harness.sandbox_stale() is False


def test_no_notice_without_a_known_build_time(container):
    harness, containerfile, built = container
    _age(containerfile, 2000.0)
    built["at"] = None                                # image absent or runtime silent
    harness.track_sandbox("dev")

    assert harness.sandbox_stale() is False


def test_build_command_success_retracks_the_active_sandbox(container, monkeypatch):
    from moka_code import sandbox
    from moka_code.ui.commands.sandbox import _do_build

    harness, containerfile, built = container
    _age(containerfile, 2000.0)
    assert harness.sandbox_stale() is True

    async def fake_build(command, cwd=None, on_output=None):
        built["at"] = 3000.0
        return 0

    monkeypatch.setattr(sandbox, "run_build", fake_build)
    ui = _UI(harness)
    ui.activity = lambda text, level="info": None
    project = projects.load_project(harness.workspace)

    assert asyncio.run(_do_build(ui, project, harness.workspace, "dev")) is True
    assert harness.sandbox_stale() is False
