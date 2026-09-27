"""Tests for the per-project store and sandbox definitions."""

import pytest
import toml

from moka_chat import settings, projects


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "get_config_dir", lambda: tmp_path)
    return tmp_path


def test_ensure_project_file_seeds_template(config_dir, tmp_path):
    workspace = tmp_path / "myproj"
    workspace.mkdir()

    path = projects.ensure_project_file(workspace)

    assert path == config_dir / "projects" / "myproj.toml"
    text = path.read_text()
    assert f'path = "{workspace.resolve()}"' in text
    assert "# [sandboxes.podman]" in text     # commented possibilities kept
    assert "# [sandboxes.docker]" in text
    assert "# [sandboxes.bubblewrap]" in text
    assert '"--userns=keep-id"' in text       # useful run_args prewritten
    assert projects.ensure_project_file(workspace) == path  # idempotent


def test_load_missing_project_is_empty(config_dir, tmp_path):
    project = projects.load_project(tmp_path / "nope")

    assert project.name == "nope"
    assert project.active is None
    assert project.sandboxes == {}


def test_load_project_parses_sandboxes(config_dir, tmp_path):
    workspace = tmp_path / "proj"
    workspace.mkdir()
    p = config_dir / "projects" / "proj.toml"
    p.parent.mkdir(parents=True)
    p.write_text(
        'active = "dev"\n'
        "[sandboxes.dev]\n"
        'type = "podman"\n'
        'description = "toolchain"\n'
        'image = "img"\n'
        "network = true\n"
        "timeout = 30.0\n"
        'run_args = ["--userns=keep-id"]\n'
        'dockerfile = "Containerfile"\n'
        "[sandboxes.tight]\n"
        'type = "bubblewrap"\n',
        encoding="utf-8",
    )

    project = projects.load_project(workspace)
    spec = projects.active_spec(project)

    assert project.active == "dev"
    assert set(project.sandboxes) == {"dev", "tight"}
    assert project.sandboxes["dev"].description == "toolchain"
    assert spec.runtime == "podman"
    assert spec.image == "img"
    assert spec.network is True
    assert spec.timeout == 30.0
    assert spec.run_args == ("--userns=keep-id",)
    assert spec.dockerfile == "Containerfile"


def test_load_project_reports_errors(config_dir, tmp_path):
    workspace = tmp_path / "proj"
    workspace.mkdir()
    p = config_dir / "projects" / "proj.toml"
    p.parent.mkdir(parents=True)
    p.write_text(
        'active = "ghost"\n'
        "[sandboxes.bad]\n"
        'type = "firecracker"\n',
        encoding="utf-8",
    )

    errors: list[str] = []
    project = projects.load_project(workspace, errors)

    assert any("type must be one of" in e for e in errors)
    assert any("active sandbox 'ghost'" in e for e in errors)
    assert project.active is None


def test_set_active_preserves_comments(config_dir, tmp_path):
    workspace = tmp_path / "proj"
    workspace.mkdir()
    projects.ensure_project_file(workspace)

    projects.set_active(workspace, "dev")

    text = (config_dir / "projects" / "proj.toml").read_text()
    assert 'active = "dev"' in text
    assert "# [sandboxes.bubblewrap]" in text  # comments survive

    projects.set_active(workspace, None)
    text = (config_dir / "projects" / "proj.toml").read_text()
    assert "active =" not in text


def test_set_active_replaces_existing_line(config_dir, tmp_path):
    workspace = tmp_path / "proj"
    workspace.mkdir()
    p = config_dir / "projects" / "proj.toml"
    p.parent.mkdir(parents=True)
    p.write_text('path = "/x"\nactive = "old"\n# keep me\n', encoding="utf-8")

    projects.set_active(workspace, "new")

    data = toml.load(p)
    assert data["active"] == "new"
    assert "# keep me" in p.read_text()
