"""
Sandbox registry, stored in the user config — never in the project.

One TOML file per sandbox, in one of two scopes:

- **global**: ``~/.config/moka/sandboxes/<name>.toml``, usable from any project;
- **local**: ``~/.config/moka/projects/<name>_<hash>/sandboxes/<name>.toml``,
  one project's own.

A project is identified by its workspace's directory name plus 4 hex chars of a
hash of its resolved path, so two workspaces named alike never share locals.
``projects/<name>_<hash>/project.toml`` holds the project's ``path`` and its
``active`` sandbox.  Names are unique across both scopes; a name that exists in
both is an error and neither entry is usable until one is renamed.

Locals live here, not in the workspace, on purpose: the agent can write the
workspace and must not be able to loosen its own sandbox.
"""
from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import toml

from moka_code import settings
from moka_code.sandbox import ContainerSpec


PROJECTS_DIRNAME = "projects"
SANDBOXES_DIRNAME = "sandboxes"
PROJECT_FILENAME = "project.toml"
SANDBOX_TYPES = ("podman", "docker", "bubblewrap")
SCOPES = ("global", "local")
#: The global sandbox seeded on first run (see ``seed_default_sandbox``).
DEFAULT_SANDBOX = "bubblewrap"

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
#: Files whose stem starts with one of these are ignored (hidden/meta).
_HIDDEN_PREFIXES = ("_", ".")
_SANDBOX_KEYS = frozenset(
    {"type", "description", "image", "dockerfile", "network", "timeout", "run_args"}
)

_COMMON_KEYS = """\
# description = ""            # shown in the sandbox picker
# network     = false         # allow sandbox networking (default: off)
# timeout     = 120.0         # per tool call, seconds
"""

_BUBBLEWRAP_TEMPLATE = """\
## Sandbox: host binaries under bubblewrap, no image, no build.
## Sees /usr /lib /lib64 /bin /etc read-only and the workspace (read-write, at
## /workspace); the host environment is cleared. Needs more tooling than that?
## Use a podman or docker sandbox instead.
type = "bubblewrap"
""" + _COMMON_KEYS + """\
# run_args    = [              # appended last, so they override moka's defaults
#   # bind extra host paths; the source MUST already exist, e.g.
#   "--ro-bind", "/opt/tools", "/opt/tools",
# ]
"""

_CONTAINER_TEMPLATE = """\
## Sandbox: {type} container. The image is built only by /sandbox build.
type = "{type}"
""" + _COMMON_KEYS + """\
# image       = "moka-{name}"  # image to run (and to build)
# dockerfile  = "{name}.{file}"  # build `image` from this file, kept next to this
#                              # one (/sandbox init {name} writes it)
# run_args    = [              # extra runtime flags, appended verbatim
{run_args}# ]
"""

_RUN_ARG_HINTS = {
    "podman": """\
#   "--userns=keep-id",         # container uid == host uid (rootless podman)
#   "--pids-limit", "4096",     # cap process count
#   "--memory", "4g",           # cap memory
#   "-v", "/data:/data:ro",     # extra read-only mount
#   "--env", "FOO=bar",         # extra environment variable
""",
    "docker": """\
#   "--user", "1000:1000",      # rootful docker: run as the host uid
#   "--pids-limit", "4096",     # cap process count
#   "--memory", "4g",           # cap memory
#   "-v", "/data:/data:ro",     # extra read-only mount
#   "-e", "FOO=bar",            # extra environment variable
""",
}


@dataclass
class SandboxEntry:
    """One sandbox definition (one file)."""

    name: str
    scope: str
    type: str
    description: Optional[str] = None
    image: Optional[str] = None
    dockerfile: Optional[str] = None
    network: bool = False
    timeout: float = 120.0
    run_args: list[str] = field(default_factory=list)
    file: Optional[Path] = None

    def to_spec(self) -> ContainerSpec:
        dockerfile = self.dockerfile
        if dockerfile and self.file is not None and not Path(dockerfile).is_absolute():
            # Relative to the sandbox's own file, never to the workspace.
            dockerfile = str(self.file.parent / dockerfile)
        return ContainerSpec(
            runtime=self.type,
            image=self.image,
            network=self.network,
            timeout=self.timeout,
            run_args=tuple(self.run_args),
            dockerfile=dockerfile,
        )


@dataclass
class ProjectConfig:
    """The sandboxes visible to a project and its active selection."""

    name: str
    path: Optional[str] = None
    active: Optional[str] = None
    sandboxes: dict[str, SandboxEntry] = field(default_factory=dict)


# --- locations -------------------------------------------------------------

def get_projects_dir() -> Path:
    """Directory holding one folder per project."""
    return settings.get_config_dir() / PROJECTS_DIRNAME


def get_global_dir() -> Path:
    """Directory holding the global sandbox files."""
    return settings.get_config_dir() / SANDBOXES_DIRNAME


def project_name(workspace: str | Path) -> str:
    """Readable part of a project's key: the workspace directory name."""
    return Path(workspace).resolve().name


def project_key(workspace: str | Path) -> str:
    """``<name>_<4 hex of a hash of the resolved path>``."""
    resolved = Path(workspace).resolve()
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:4]
    return f"{resolved.name}_{digest}"


def project_dir(workspace: str | Path) -> Path:
    return get_projects_dir() / project_key(workspace)


def project_file(workspace: str | Path) -> Path:
    return project_dir(workspace) / PROJECT_FILENAME


def scope_dir(scope: str, workspace: str | Path) -> Path:
    """Folder holding the sandbox files of ``scope`` (for this project if local)."""
    if scope == "global":
        return get_global_dir()
    if scope == "local":
        return project_dir(workspace) / SANDBOXES_DIRNAME
    raise ValueError(f"Scope must be one of {', '.join(SCOPES)}")


def validate_name(name: str) -> str:
    """A sandbox name: letters, digits, ``_`` and ``-``; no spaces; not hidden."""
    name = name.strip()
    if not _NAME_RE.match(name):
        raise ValueError(
            "Sandbox name may only contain letters, digits, '_' and '-' "
            "and must start with a letter or digit")
    return name


# --- reading ---------------------------------------------------------------

def _iter_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        path for path in directory.glob("*.toml")
        if not path.stem.startswith(_HIDDEN_PREFIXES)
    )


def _parse_entry(path: Path, scope: str, errors: list[str]) -> Optional[SandboxEntry]:
    name = path.stem
    source = f"{scope} sandbox '{name}'"
    try:
        validate_name(name)
    except ValueError as exc:
        errors.append(f"{source}: {exc}")
        return None
    try:
        data = toml.load(path)
    except (toml.TomlDecodeError, OSError) as exc:
        errors.append(f"{source}: {exc}")
        return None

    stype = data.get("type")
    if stype not in SANDBOX_TYPES:
        errors.append(f"{source}: type must be one of {', '.join(SANDBOX_TYPES)}")
        return None
    unknown = sorted(set(data) - _SANDBOX_KEYS)
    if unknown:
        errors.append(f"{source}: unknown key(s) {', '.join(unknown)}")

    run_args = data.get("run_args", [])
    if not isinstance(run_args, list) or not all(isinstance(a, str) for a in run_args):
        errors.append(f"{source}: run_args must be a list of strings")
        run_args = []

    timeout = data.get("timeout", 120.0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        errors.append(f"{source}: timeout must be a positive number")
        timeout = 120.0

    image = data.get("image")
    dockerfile = data.get("dockerfile")
    description = data.get("description")
    return SandboxEntry(
        name=name,
        scope=scope,
        type=stype,
        description=description if isinstance(description, str) else None,
        image=image if isinstance(image, str) else None,
        dockerfile=dockerfile if isinstance(dockerfile, str) else None,
        network=bool(data.get("network", False)),
        timeout=float(timeout),
        run_args=[str(a) for a in run_args],
        file=path,
    )


def _read_meta(workspace: str | Path, errors: list[str]) -> dict:
    path = project_file(workspace)
    if not path.exists():
        return {}
    try:
        data = toml.load(path)
    except (toml.TomlDecodeError, OSError) as exc:
        errors.append(f"{path.parent.name}/{PROJECT_FILENAME}: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def load_project(workspace: str | Path, errors: Optional[list[str]] = None) -> ProjectConfig:
    """The sandboxes visible from ``workspace`` (tolerant of errors).

    A name present in both scopes is reported and excluded; so is a broken file.
    """
    errors = errors if errors is not None else []
    meta = _read_meta(workspace, errors)
    project = ProjectConfig(
        name=project_key(workspace),
        path=meta.get("path") if isinstance(meta.get("path"), str) else None,
        active=meta.get("active") if isinstance(meta.get("active"), str) else None,
    )

    found: dict[str, list[SandboxEntry]] = {}
    for scope in SCOPES:
        for path in _iter_files(scope_dir(scope, workspace)):
            entry = _parse_entry(path, scope, errors)
            if entry is not None:
                found.setdefault(entry.name, []).append(entry)

    conflicts: set[str] = set()
    for name, entries in found.items():
        if len(entries) > 1:
            conflicts.add(name)
            errors.append(
                f"sandbox '{name}' exists in both global and local — rename one")
        else:
            project.sandboxes[name] = entries[0]

    if project.active is not None and project.active not in project.sandboxes:
        if project.active not in conflicts:
            errors.append(f"active sandbox '{project.active}' is not defined")
        project.active = None

    return project


def active_spec(project: ProjectConfig) -> Optional[ContainerSpec]:
    """The ``ContainerSpec`` for the project's active sandbox, if any."""
    if not project.active:
        return None
    entry = project.sandboxes.get(project.active)
    return entry.to_spec() if entry else None


def sandbox_file_stat(workspace: str | Path, name: str) -> Optional[tuple[int, int]]:
    """``(mtime_ns, size)`` of the sandbox's file, ``None`` when it is gone: the
    cheap check that tells whether a running sandbox may differ from its file."""
    for scope in SCOPES:
        try:
            stat = (scope_dir(scope, workspace) / f"{name}.toml").stat()
        except OSError:
            continue
        return stat.st_mtime_ns, stat.st_size
    return None


def validate_sandboxes(workspace: str | Path) -> list[str]:
    """Validation errors for every sandbox file visible from ``workspace`` and the
    project's ``active`` entry (surfaced by ``/reload`` and the banner)."""
    errors: list[str] = []
    load_project(workspace, errors)
    return errors


# --- writing ---------------------------------------------------------------

def _existing_names(workspace: str | Path) -> set[str]:
    return {
        path.stem
        for scope in SCOPES
        for path in _iter_files(scope_dir(scope, workspace))
    }


def _check_new_name(workspace: str | Path, name: str) -> str:
    name = validate_name(name)
    if name in _existing_names(workspace):
        raise ValueError(f"A sandbox named '{name}' already exists (names are unique "
                         "across global and local)")
    return name


def sandbox_template(stype: str, name: str) -> str:
    """Starter content for a new sandbox of ``stype``."""
    if stype == "bubblewrap":
        return _BUBBLEWRAP_TEMPLATE
    if stype in _RUN_ARG_HINTS:
        from moka_code.sandbox import containerfile_name

        return _CONTAINER_TEMPLATE.format(
            type=stype, name=name, file=containerfile_name(stype),
            run_args=_RUN_ARG_HINTS[stype])
    raise ValueError(f"Type must be one of {', '.join(SANDBOX_TYPES)}")


def create_sandbox(workspace: str | Path, scope: str, stype: str, name: str) -> Path:
    """Write a new sandbox file of ``stype`` in ``scope``; return its path."""
    directory = scope_dir(scope, workspace)
    name = _check_new_name(workspace, name)
    text = sandbox_template(stype, name)
    path = directory / f"{name}.toml"
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def copy_sandbox(workspace: str | Path, source: str, scope: str, name: str) -> Path:
    """Copy sandbox ``source`` to ``name`` in ``scope`` (a plain file copy, so
    comments survive); return the new path.

    A relative ``dockerfile`` sitting next to the source is copied along when the
    target folder differs, so the copy still builds.
    """
    entry = load_project(workspace).sandboxes.get(source)
    if entry is None or entry.file is None:
        raise KeyError(f"Sandbox not found: {source}")
    directory = scope_dir(scope, workspace)
    name = _check_new_name(workspace, name)
    path = directory / f"{name}.toml"
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(entry.file, path)
    if entry.dockerfile and not Path(entry.dockerfile).is_absolute():
        origin = entry.file.parent / entry.dockerfile
        target = directory / entry.dockerfile
        if origin.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, target)
    return path


def seed_default_sandbox(workspace: str | Path) -> Optional[Path]:
    """First run: write the default ``bubblewrap`` as a global sandbox, so a fresh
    install has one to pick with no config. Only when the global folder does not
    exist yet (deleting the file later stays deleted, like a role), and not when
    the name is already taken from ``workspace``. Returns the new path, if any."""
    if get_global_dir().exists() or DEFAULT_SANDBOX in _existing_names(workspace):
        return None
    return create_sandbox(workspace, "global", "bubblewrap", DEFAULT_SANDBOX)


_KEY_RE = re.compile(r"^(?P<name>.*)_[0-9a-f]{4}$")


def moved_project_notes(workspace: str | Path) -> list[str]:
    """Why this project's locals may be missing: a folder of a same-named project
    whose recorded path no longer exists (the repo moved or was renamed, so its
    hash changed) and that holds sandboxes. Said only while this project has no
    local sandbox of its own; each note carries the command that moves them."""
    base = get_projects_dir()
    if not base.is_dir() or _iter_files(scope_dir("local", workspace)):
        return []
    key, name = project_key(workspace), project_name(workspace)
    target = project_dir(workspace)
    notes: list[str] = []
    for folder in sorted(base.iterdir()):
        match = _KEY_RE.match(folder.name)
        if not folder.is_dir() or folder.name == key or not match or match["name"] != name:
            continue
        if not _iter_files(folder / SANDBOXES_DIRNAME):
            continue
        try:
            recorded = toml.load(folder / PROJECT_FILENAME).get("path")
        except (toml.TomlDecodeError, OSError):
            continue
        if isinstance(recorded, str) and not Path(recorded).exists():
            notes.append(
                f"project {folder.name} was at {recorded}, which is gone; its sandboxes "
                f"are not visible here → mkdir -p {target} && "
                f"mv {folder / SANDBOXES_DIRNAME} {target}/")
    return notes


def ensure_project(workspace: str | Path) -> Path:
    """Create the project's folder and ``project.toml`` if missing."""
    path = project_file(workspace)
    if not path.exists():
        _write_meta(workspace, None)
    return path


def _write_meta(workspace: str | Path, active: Optional[str]) -> None:
    path = project_file(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, str] = {"path": str(Path(workspace).resolve())}
    if active is not None:
        data["active"] = active
    path.write_text(
        "# Managed by moka: this project's path and active sandbox.\n" + toml.dumps(data),
        encoding="utf-8")


def set_active(workspace: str | Path, name: Optional[str]) -> None:
    """Persist the project's active sandbox (``None`` clears it)."""
    _write_meta(workspace, name)


__all__ = [
    "PROJECTS_DIRNAME",
    "SANDBOXES_DIRNAME",
    "PROJECT_FILENAME",
    "SANDBOX_TYPES",
    "SCOPES",
    "SandboxEntry",
    "ProjectConfig",
    "get_projects_dir",
    "get_global_dir",
    "project_name",
    "project_key",
    "project_dir",
    "project_file",
    "scope_dir",
    "validate_name",
    "load_project",
    "active_spec",
    "sandbox_file_stat",
    "validate_sandboxes",
    "sandbox_template",
    "create_sandbox",
    "copy_sandbox",
    "seed_default_sandbox",
    "moved_project_notes",
    "DEFAULT_SANDBOX",
    "ensure_project",
    "set_active",
]
