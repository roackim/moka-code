"""Tests for the sandbox registry (global + local, one file per sandbox)."""

import pytest
import toml

from moka_code import settings, projects


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "get_config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / "proj"
    path.mkdir()
    return path


def _write(scope, workspace, name, body):
    directory = projects.scope_dir(scope, workspace)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.toml"
    path.write_text(body, encoding="utf-8")
    return path


# --- locations -------------------------------------------------------------

def test_project_key_is_name_plus_path_hash(config_dir, tmp_path):
    a = tmp_path / "a" / "app"
    b = tmp_path / "b" / "app"
    a.mkdir(parents=True)
    b.mkdir(parents=True)

    key_a, key_b = projects.project_key(a), projects.project_key(b)

    assert key_a.startswith("app_") and key_b.startswith("app_")
    assert len(key_a) == len("app_") + 4
    assert key_a != key_b                                  # same name, different projects
    assert projects.project_key(a) == key_a                # stable
    assert projects.project_dir(a) == config_dir / "projects" / key_a


def test_project_key_uses_the_resolved_path(config_dir, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)

    assert projects.project_key(link) == projects.project_key(real)


def test_scope_dirs(config_dir, workspace):
    assert projects.scope_dir("global", workspace) == config_dir / "sandboxes"
    assert projects.scope_dir("local", workspace) == projects.project_dir(workspace) / "sandboxes"
    with pytest.raises(ValueError):
        projects.scope_dir("nowhere", workspace)


@pytest.mark.parametrize("name", ["dev", "my-box_2", "A1"])
def test_valid_names(name):
    assert projects.validate_name(name) == name


@pytest.mark.parametrize("name", ["", "has space", "a/b", ".hidden", "_meta", "-x", "a.b"])
def test_invalid_names(name):
    with pytest.raises(ValueError):
        projects.validate_name(name)


# --- loading ---------------------------------------------------------------

def test_load_missing_project_is_empty(config_dir, tmp_path):
    project = projects.load_project(tmp_path / "nope")

    assert project.name.startswith("nope_")
    assert project.active is None
    assert project.sandboxes == {}


def test_load_merges_global_and_local_with_scope_tags(config_dir, workspace):
    _write("global", workspace, "shared", 'type = "bubblewrap"\n')
    _write("local", workspace, "dev",
           'type = "podman"\ndescription = "toolchain"\nimage = "img"\n'
           "network = true\ntimeout = 30.0\n"
           'run_args = ["--userns=keep-id"]\ndockerfile = "Containerfile"\n')
    projects.set_active(workspace, "dev")

    project = projects.load_project(workspace)
    spec = projects.active_spec(project)

    assert set(project.sandboxes) == {"shared", "dev"}
    assert project.sandboxes["shared"].scope == "global"
    assert project.sandboxes["dev"].scope == "local"
    assert project.active == "dev"
    assert spec.runtime == "podman"
    assert spec.image == "img"
    assert spec.network is True
    assert spec.timeout == 30.0
    assert spec.run_args == ("--userns=keep-id",)


def test_dockerfile_resolves_next_to_the_sandbox_file(config_dir, workspace):
    _write("local", workspace, "dev",
           'type = "podman"\nimage = "img"\ndockerfile = "dev.Containerfile"\n')

    spec = projects.load_project(workspace).sandboxes["dev"].to_spec()

    assert spec.dockerfile == str(projects.scope_dir("local", workspace) / "dev.Containerfile")


def test_a_name_in_both_scopes_is_an_error_and_unusable(config_dir, workspace):
    _write("global", workspace, "dev", 'type = "bubblewrap"\n')
    _write("local", workspace, "dev", 'type = "bubblewrap"\n')
    _write("local", workspace, "other", 'type = "bubblewrap"\n')
    projects.set_active(workspace, "dev")

    errors: list[str] = []
    project = projects.load_project(workspace, errors)

    assert "dev" not in project.sandboxes
    assert "other" in project.sandboxes                    # unrelated entries unaffected
    assert project.active is None                          # nothing active
    assert any("'dev' exists in both global and local" in e for e in errors)
    assert not any("is not defined" in e for e in errors)  # one error, not two


def test_hidden_files_are_ignored(config_dir, workspace):
    _write("global", workspace, "_draft", 'type = "bubblewrap"\n')
    _write("global", workspace, ".old", 'type = "bubblewrap"\n')

    assert projects.load_project(workspace).sandboxes == {}


def test_load_reports_errors_and_skips_the_bad_file(config_dir, workspace):
    _write("local", workspace, "bad", 'type = "firecracker"\n')
    _write("local", workspace, "typo", 'type = "bubblewrap"\nnetwrok = true\n')
    _write("local", workspace, "args", 'type = "bubblewrap"\nrun_args = "x"\n')
    _write("local", workspace, "broken", "type = [")
    _write("local", workspace, "bad name", 'type = "bubblewrap"\n')
    projects.set_active(workspace, "ghost")

    errors: list[str] = []
    project = projects.load_project(workspace, errors)

    assert any("local sandbox 'bad': type must be one of" in e for e in errors)
    assert any("unknown key(s) netwrok" in e for e in errors)
    assert any("run_args must be a list of strings" in e for e in errors)
    assert any("local sandbox 'broken'" in e for e in errors)
    assert any("local sandbox 'bad name'" in e for e in errors)
    assert any("active sandbox 'ghost' is not defined" in e for e in errors)
    assert "bad" not in project.sandboxes and "broken" not in project.sandboxes
    assert project.active is None


# --- writing ---------------------------------------------------------------

def test_set_active_writes_project_toml(config_dir, workspace):
    projects.set_active(workspace, "dev")

    data = toml.load(projects.project_file(workspace))
    assert data == {"path": str(workspace.resolve()), "active": "dev"}

    projects.set_active(workspace, None)
    assert toml.load(projects.project_file(workspace)) == {"path": str(workspace.resolve())}


def test_ensure_project_is_idempotent(config_dir, workspace):
    path = projects.ensure_project(workspace)
    projects.set_active(workspace, "dev")

    assert projects.ensure_project(workspace) == path
    assert toml.load(path)["active"] == "dev"              # not reset


@pytest.mark.parametrize("stype", projects.SANDBOX_TYPES)
def test_every_starter_template_loads_cleanly(config_dir, workspace, stype):
    path = projects.create_sandbox(workspace, "local", stype, "starter")

    errors: list[str] = []
    project = projects.load_project(workspace, errors)

    assert path == projects.scope_dir("local", workspace) / "starter.toml"
    assert errors == []
    assert project.sandboxes["starter"].type == stype


def test_container_starter_names_its_containerfile(config_dir, workspace):
    text = projects.sandbox_template("podman", "dev")
    assert '# dockerfile  = "dev.Containerfile"' in text
    assert 'dev.Dockerfile' in projects.sandbox_template("docker", "dev")


def test_create_global_goes_to_the_global_dir(config_dir, workspace):
    path = projects.create_sandbox(workspace, "global", "bubblewrap", "box")

    assert path == config_dir / "sandboxes" / "box.toml"


def test_create_rejects_a_taken_name_in_either_scope(config_dir, workspace):
    projects.create_sandbox(workspace, "global", "bubblewrap", "box")

    with pytest.raises(ValueError, match="already exists"):
        projects.create_sandbox(workspace, "local", "bubblewrap", "box")
    with pytest.raises(ValueError, match="already exists"):
        projects.create_sandbox(workspace, "global", "bubblewrap", "box")


def test_create_rejects_bad_name_scope_and_type(config_dir, workspace):
    with pytest.raises(ValueError):
        projects.create_sandbox(workspace, "local", "bubblewrap", "has space")
    with pytest.raises(ValueError):
        projects.create_sandbox(workspace, "nowhere", "bubblewrap", "x")
    with pytest.raises(ValueError):
        projects.create_sandbox(workspace, "local", "firecracker", "x")


def test_copy_keeps_the_file_verbatim_into_the_other_scope(config_dir, workspace):
    src = _write("global", workspace, "base", '# keep me\ntype = "bubblewrap"\n')

    path = projects.copy_sandbox(workspace, "base", "local", "mine")

    assert path == projects.scope_dir("local", workspace) / "mine.toml"
    assert path.read_text() == src.read_text()             # comments survive
    assert set(projects.load_project(workspace).sandboxes) == {"base", "mine"}


def test_copy_brings_the_relative_containerfile_along(config_dir, workspace):
    _write("global", workspace, "base",
           'type = "podman"\nimage = "img"\ndockerfile = "base.Containerfile"\n')
    (projects.scope_dir("global", workspace) / "base.Containerfile").write_text("FROM x\n")

    projects.copy_sandbox(workspace, "base", "local", "mine")

    # The copy still names base.Containerfile, so it must exist beside the copy.
    assert (projects.scope_dir("local", workspace) / "base.Containerfile").read_text() == "FROM x\n"


def test_copy_errors(config_dir, workspace):
    _write("global", workspace, "base", 'type = "bubblewrap"\n')

    with pytest.raises(KeyError):
        projects.copy_sandbox(workspace, "ghost", "local", "x")
    with pytest.raises(ValueError, match="already exists"):
        projects.copy_sandbox(workspace, "base", "local", "base")


def test_sandbox_file_stat_tracks_the_file(config_dir, workspace):
    path = _write("local", workspace, "dev", 'type = "bubblewrap"\n')

    before = projects.sandbox_file_stat(workspace, "dev")
    path.write_text('type = "bubblewrap"\n# edited\n', encoding="utf-8")

    assert before is not None
    assert projects.sandbox_file_stat(workspace, "dev") != before
    assert projects.sandbox_file_stat(workspace, "ghost") is None


# --- seeded default --------------------------------------------------------

def test_first_run_seeds_the_default_bubblewrap_globally(config_dir, workspace):
    path = projects.seed_default_sandbox(workspace)

    assert path == config_dir / "sandboxes" / "bubblewrap.toml"
    errors: list[str] = []
    project = projects.load_project(workspace, errors)
    assert errors == []
    assert project.sandboxes["bubblewrap"].scope == "global"
    assert project.sandboxes["bubblewrap"].type == "bubblewrap"


def test_the_seed_is_not_rewritten_once_the_folder_exists(config_dir, workspace):
    path = projects.seed_default_sandbox(workspace)
    path.write_text('type = "bubblewrap"\nnetwork = true\n', encoding="utf-8")
    assert projects.seed_default_sandbox(workspace) is None        # edits are kept
    assert path.read_text().endswith("network = true\n")

    path.unlink()                                                  # deleted stays deleted
    assert projects.seed_default_sandbox(workspace) is None
    assert projects.load_project(workspace).sandboxes == {}


def test_the_seed_never_collides_with_an_existing_local(config_dir, workspace):
    _write("local", workspace, "bubblewrap", 'type = "bubblewrap"\n')

    assert projects.seed_default_sandbox(workspace) is None
    errors: list[str] = []
    assert "bubblewrap" in projects.load_project(workspace, errors).sandboxes
    assert errors == []


# --- a moved repo ----------------------------------------------------------

def _orphan(config_dir, name, recorded, with_sandbox=True):
    folder = config_dir / "projects" / f"{name}_1a2b"
    (folder / "sandboxes").mkdir(parents=True)
    (folder / "project.toml").write_text(f'path = "{recorded}"\n', encoding="utf-8")
    if with_sandbox:
        (folder / "sandboxes" / "dev.toml").write_text('type = "bubblewrap"\n', encoding="utf-8")
    return folder


def test_a_moved_repo_is_told_where_its_sandboxes_stayed(config_dir, workspace):
    old = _orphan(config_dir, "proj", "/gone/proj")

    (note,) = projects.moved_project_notes(workspace)

    assert "proj_1a2b was at /gone/proj, which is gone" in note
    # The command is copy-pasteable and lands the folder under this project's key.
    target = projects.project_dir(workspace)
    assert f"mkdir -p {target} && mv {old / 'sandboxes'} {target}/" in note


def test_no_moved_note_when_the_old_path_still_exists_or_is_another_project(config_dir, workspace, tmp_path):
    _orphan(config_dir, "proj", str(tmp_path))                     # path exists: not moved
    _orphan(config_dir, "other", "/gone/other")                    # a different project
    _orphan(config_dir, "proj2", "/gone/proj2")                    # name only starts alike

    assert projects.moved_project_notes(workspace) == []


def test_no_moved_note_without_sandboxes_to_lose(config_dir, workspace):
    _orphan(config_dir, "proj", "/gone/proj", with_sandbox=False)

    assert projects.moved_project_notes(workspace) == []


def test_no_moved_note_once_this_project_has_its_own_locals(config_dir, workspace):
    _orphan(config_dir, "proj", "/gone/proj")
    _write("local", workspace, "mine", 'type = "bubblewrap"\n')

    assert projects.moved_project_notes(workspace) == []
