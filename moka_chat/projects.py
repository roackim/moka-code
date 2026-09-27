"""
Per-project settings, stored in the user config — never in the project.

A project is identified by its directory name (deliberately simple for now;
resolved-path / ancestor lookup can come later).  Its file lives at
``~/.config/moka/projects/<name>.toml`` and holds named sandbox
definitions plus the ``active`` selection.

This keeps per-project intent (which container/image a project runs in) out of
the repository, matching moka's "no project-local config" rule.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import toml

from moka_chat import settings
from moka_chat.sandbox import ContainerSpec


PROJECTS_DIRNAME = "projects"
SANDBOX_TYPES = ("podman", "docker", "bubblewrap")

DEFAULT_PROJECT_TOML = """\
# Project sandboxes for this directory. Sandboxes are per project and live in
# your user config, never in the repo. Start one with /sandbox start <id>,
# stop with /sandbox quit; edit this file with /sandbox config.
#
# One [sandboxes.<id>] table per sandbox. `type` is required.
#
# Common keys (all types)
#   description = ""            # shown by /sandbox start
#   network     = false         # allow sandbox networking (default: off)
#   timeout     = 120.0         # per tool call, seconds
#   run_args    = []            # extra runtime flags, appended verbatim
#
# Container types (podman, docker) add:
#   image       = ""            # image tag to run (and to build)
#   dockerfile  = ""            # optional: build `image` from this file
#                               #   (podman -> "Containerfile", docker -> "Dockerfile")
#
# --- podman (rootless) -------------------------------------------------
# [sandboxes.podman]
# type = "podman"
# description = "podman, project toolchain"
# image = "moka-myproject"
# dockerfile = "Containerfile"
# network = false
# timeout = 120.0
# run_args = [
#   "--userns=keep-id",             # container uid == host uid (rootless podman)
#   "--pids-limit", "4096",         # cap process count
#   "--memory", "4g",               # cap memory
#   "--cpus", "4",                  # cap cpus
#   "-v", "/data:/data:ro",         # extra read-only mount
#   "-v", "/cache:/cache",          # extra writable mount
#   "--env", "FOO=bar",             # extra environment variable
#   "--group-add", "keep-groups",   # keep host supplementary groups
# ]
#
# --- docker ------------------------------------------------------------
# [sandboxes.docker]
# type = "docker"
# description = "docker, project toolchain"
# image = "moka-myproject"
# dockerfile = "Dockerfile"
# network = false
# timeout = 120.0
# run_args = [
#   "--user", "1000:1000",          # rootful docker: run as the host uid
#   "--pids-limit", "4096",
#   "--memory", "4g",
#   "-v", "/data:/data:ro",
#   "-e", "FOO=bar",                # docker also accepts -e
# ]
#
# --- bubblewrap (host process, no image, no build) ---------------------
# [sandboxes.bubblewrap]
# type = "bubblewrap"
# description = "host binaries, network off"
# network = false
# timeout = 60.0
# run_args = [
#   "--proc", "/proc",            # fresh /proc (no host path needed)
#   "--dev", "/dev",              # minimal /dev (no host path needed)
#   # bind extra host paths here; the source MUST already exist, e.g.
#   # "--ro-bind", "/opt/tools", "/opt/tools",
# ]
"""


@dataclass
class SandboxEntry:
    """One named sandbox definition inside a project file."""

    id: str
    type: str
    description: Optional[str] = None
    image: Optional[str] = None
    dockerfile: Optional[str] = None
    network: bool = False
    timeout: float = 120.0
    run_args: list[str] = field(default_factory=list)

    def to_spec(self) -> ContainerSpec:
        return ContainerSpec(
            runtime=self.type,
            image=self.image,
            network=self.network,
            timeout=self.timeout,
            run_args=tuple(self.run_args),
            dockerfile=self.dockerfile,
        )


@dataclass
class ProjectConfig:
    """Parsed project file (empty when the project has no file yet)."""

    name: str
    path: Optional[str] = None
    active: Optional[str] = None
    sandboxes: dict[str, SandboxEntry] = field(default_factory=dict)


def get_projects_dir() -> Path:
    """Directory holding one file per project."""
    return settings.get_config_dir() / PROJECTS_DIRNAME


def project_name(workspace: str | Path) -> str:
    """Project key for a workspace: its directory name."""
    return Path(workspace).resolve().name


def project_path(name: str) -> Path:
    """Path to ``projects/<name>.toml``."""
    return get_projects_dir() / f"{name}.toml"


def ensure_project_file(workspace: str | Path) -> Path:
    """Create the project's file from the commented template if missing."""
    resolved = Path(workspace).resolve()
    path = project_path(project_name(workspace))
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'path = "{resolved}"\n\n' + DEFAULT_PROJECT_TOML, encoding="utf-8")
    return path


def _parse_entry(sandbox_id: str, data: object, errors: list[str], source: str) -> Optional[SandboxEntry]:
    if not isinstance(data, dict):
        errors.append(f"{source}: [sandboxes.{sandbox_id}] must be a table")
        return None

    stype = data.get("type")
    if stype not in SANDBOX_TYPES:
        errors.append(
            f"{source}: [sandboxes.{sandbox_id}] type must be one of "
            f"{', '.join(SANDBOX_TYPES)}"
        )
        return None

    run_args = data.get("run_args", [])
    if not isinstance(run_args, list) or not all(isinstance(a, str) for a in run_args):
        errors.append(f"{source}: [sandboxes.{sandbox_id}] run_args must be a list of strings")
        run_args = []

    timeout = data.get("timeout", 120.0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        errors.append(f"{source}: [sandboxes.{sandbox_id}] timeout must be a positive number")
        timeout = 120.0

    image = data.get("image")
    dockerfile = data.get("dockerfile")
    description = data.get("description")
    return SandboxEntry(
        id=sandbox_id,
        type=stype,
        description=description if isinstance(description, str) else None,
        image=image if isinstance(image, str) else None,
        dockerfile=dockerfile if isinstance(dockerfile, str) else None,
        network=bool(data.get("network", False)),
        timeout=float(timeout),
        run_args=[str(a) for a in run_args],
    )


def load_project(workspace: str | Path, errors: Optional[list[str]] = None) -> ProjectConfig:
    """Load the project file for ``workspace`` (tolerant of errors)."""
    errors = errors if errors is not None else []
    name = project_name(workspace)
    path = project_path(name)
    project = ProjectConfig(name=name)
    if not path.exists():
        return project

    source = path.name
    try:
        data = toml.load(path)
    except (toml.TomlDecodeError, OSError) as exc:
        errors.append(f"{source}: {exc}")
        return project
    if not isinstance(data, dict):
        errors.append(f"{source}: top level must be a table")
        return project

    project.path = data.get("path") if isinstance(data.get("path"), str) else None
    project.active = data.get("active") if isinstance(data.get("active"), str) else None

    sandboxes = data.get("sandboxes", {})
    if not isinstance(sandboxes, dict):
        errors.append(f"{source}: 'sandboxes' must be a table")
        sandboxes = {}
    for sandbox_id, entry in sandboxes.items():
        parsed = _parse_entry(sandbox_id, entry, errors, source)
        if parsed is not None:
            project.sandboxes[sandbox_id] = parsed

    if project.active is not None and project.active not in project.sandboxes:
        errors.append(f"{source}: active sandbox '{project.active}' is not defined")
        project.active = None

    return project


def active_spec(project: ProjectConfig) -> Optional[ContainerSpec]:
    """The ``ContainerSpec`` for the project's active sandbox, if any."""
    if not project.active:
        return None
    entry = project.sandboxes.get(project.active)
    return entry.to_spec() if entry else None


_ACTIVE_RE = re.compile(r'^active\s*=')


def set_active(workspace: str | Path, sandbox_id: Optional[str]) -> None:
    """Persist the active sandbox id, preserving comments in the file."""
    path = ensure_project_file(workspace)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    out: list[str] = []
    replaced = False
    for line in lines:
        if _ACTIVE_RE.match(line.lstrip()):
            if sandbox_id is not None:
                out.append(f'active = "{sandbox_id}"\n')
                replaced = True
            # None → drop the line
        else:
            out.append(line)

    if sandbox_id is not None and not replaced:
        # Insert right after the "path" line (or at the top).
        insert_at = 0
        for i, line in enumerate(out):
            if line.lstrip().startswith("path"):
                insert_at = i + 1
                break
        out.insert(insert_at, f'active = "{sandbox_id}"\n')

    path.write_text("".join(out), encoding="utf-8")


__all__ = [
    "PROJECTS_DIRNAME",
    "SANDBOX_TYPES",
    "DEFAULT_PROJECT_TOML",
    "SandboxEntry",
    "ProjectConfig",
    "get_projects_dir",
    "project_name",
    "project_path",
    "ensure_project_file",
    "load_project",
    "active_spec",
    "set_active",
]
