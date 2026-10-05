"""
Host-side sandbox launcher and JSONL client.

The container is the boundary: moka does no path confinement or command
parsing.  This module only

- parses a backend selection (``podman`` / ``docker`` / ``bubblewrap`` / ``none``),
- builds the runtime command line, and
- owns the worker process and its JSONL protocol (lazy start, timeout,
  restart, graceful stop).

The tool *transport* built on top of this lives in ``harness/tools.py``
(``SandboxTransport``), so this module imports no ``ui/`` and holds no policy.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Sequence


DEFAULT_IMAGE = "python:3.12-slim"

CONTAINER_RUNTIMES = ("podman", "docker")
HOST_RUNTIMES = ("bubblewrap",)
#: Friendly base names offered by ``/sandbox init``.
CONTAINERFILE_BASES = ("python", "debian", "ubuntu")

#: Friendly base name -> image tag. ``debian`` is the slim variant (bare
#: ``debian`` is the full image and has no ``debian:slim``); ``ubuntu`` has no
#: slim variant at all. Unknown names pass through as raw image tags.
_BASE_IMAGES = {
    "python": DEFAULT_IMAGE,
    "debian": "debian:stable-slim",
    "ubuntu": "ubuntu",
}

#: Conventional build-file name per container runtime.
CONTAINERFILE_NAMES = {"podman": "Containerfile", "docker": "Dockerfile"}


class SandboxError(Exception):
    """Base error for the sandbox launcher/client."""


class SandboxTimeoutError(SandboxError):
    """The worker did not answer within the wall-clock timeout."""


class SandboxProcessError(SandboxError):
    """The worker process could not be started, wrote bad output, or died."""


@dataclass(frozen=True)
class ContainerSpec:
    """Where (and how) tools run for a project's active sandbox.

    ``runtime="none"`` means in-process.  ``network``/``timeout``/``run_args``
    come from the project file's sandbox entry; ``dockerfile`` is remembered
    for the image-preflight step (moka never builds implicitly).
    """

    runtime: str
    image: Optional[str] = None
    network: bool = False
    timeout: float = 120.0
    run_args: tuple[str, ...] = ()
    dockerfile: Optional[str] = None

    @property
    def enabled(self) -> bool:
        """True when execution should leave the host (a real container/bwrap)."""
        return self.runtime != "none"


def worker_path() -> Path:
    """Absolute path to the installed ``worker.py`` (mounted read-only)."""
    return Path(__file__).with_name("worker.py")


def _container_argv(spec: ContainerSpec, workspace: str, worker: str,
                    interpreter: str, command: Optional[list[str]] = None) -> list[str]:
    argv = [
        spec.runtime, "run",
        # Pipe stdio; never -t for the worker (a TTY corrupts framing). An
        # interactive ``command`` (``/terminal``) does get a TTY.
        "-it" if command else "-i",
        "--rm",
        "-w", "/workspace",
        "--read-only",
        "--tmpfs", "/tmp",
        "--cap-drop=all",
        "--security-opt", "no-new-privileges",
    ]
    if not spec.network:
        argv.append("--network=none")
    argv += [
        *spec.run_args,
        "-v", f"{workspace}:/workspace:Z",
        "-v", f"{worker}:/opt/worker.py:ro",
        spec.image or DEFAULT_IMAGE,
        *(command or [interpreter, "/opt/worker.py"]),
    ]
    return argv


_STANDARD_PREFIXES = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc")

#: Host variables worth keeping inside bwrap; everything else (API keys,
#: HOME/PATH/VIRTUAL_ENV pointing at unbound paths) is dropped.
_BUBBLEWRAP_PASSTHROUGH_ENV = ("TERM", "LANG", "LC_ALL")
_BUBBLEWRAP_PATH = "/usr/local/bin:/usr/bin:/bin"


def _bubblewrap_interpreter() -> str:
    """A Python that exists inside the bwrap namespace.

    worker.py is stdlib-only, so the system ``python3`` (under the bound
    ``/usr``) is preferred over ``sys.executable``, which for a venv/pixi run
    lives outside the bound directories.
    """
    for candidate in ("/usr/bin/python3", "/usr/local/bin/python3", "/bin/python3"):
        if Path(candidate).exists():
            return candidate
    return sys.executable


def _bubblewrap_argv(spec: ContainerSpec, workspace: str, worker: str,
                     interpreter: str, command: Optional[list[str]] = None) -> list[str]:
    argv = [
        "bwrap",
        "--die-with-parent",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-cgroup-try",
        # Start from nothing: the host environment (API keys, venv paths) must
        # not leak in. /tmp is a tmpfs, so it doubles as the scratch HOME.
        "--clearenv",
        "--setenv", "HOME", "/tmp",
        "--setenv", "TMPDIR", "/tmp",
        "--setenv", "PATH", _BUBBLEWRAP_PATH,
    ]
    for name in _BUBBLEWRAP_PASSTHROUGH_ENV:
        if name in os.environ:
            argv += ["--setenv", name, os.environ[name]]
    if not spec.network:
        argv.append("--unshare-net")
    for path in ("/usr", "/lib", "/lib64", "/bin", "/etc"):
        if Path(path).exists():
            argv += ["--ro-bind", path, path]

    # If the interpreter lives outside the bound system dirs (a venv/pixi
    # python), bind its prefixes too so it can actually start.
    if not interpreter.startswith(_STANDARD_PREFIXES):
        for prefix in dict.fromkeys((sys.prefix, sys.base_prefix)):
            if prefix and not prefix.startswith(_STANDARD_PREFIXES) and Path(prefix).exists():
                argv += ["--ro-bind", prefix, prefix]
        if Path(interpreter).exists():
            argv += ["--ro-bind", interpreter, interpreter]

    argv += [
        # bwrap starts with an empty root; create the mountpoint for the worker.
        "--dir", "/opt",
        "--ro-bind", worker, "/opt/worker.py",
        "--bind", workspace, "/workspace",
        "--tmpfs", "/tmp",
        "--proc", "/proc",
        "--dev", "/dev",
        "--chdir", "/workspace",
        # Last, so the user can override anything above.
        *spec.run_args,
        "--", *(command or [interpreter, "/opt/worker.py"]),
    ]
    return argv


def build_argv(
    spec: ContainerSpec,
    workspace: str | Path,
    worker: str | Path | None = None,
    *,
    interpreter: str | None = None,
    command: Optional[list[str]] = None,
) -> list[str]:
    """Build the runtime command line for ``spec``.

    ``none`` runs the worker directly on the host (no isolation); container
    runtimes mirror the image-injection model (the host's ``worker.py`` is
    mounted read-only).  The launcher only has to produce a stdio pipe.
    ``command`` replaces the worker with an interactive program (same mounts,
    network and limits), for ``/terminal``.
    """
    workspace = str(Path(workspace).resolve())
    worker = str(Path(worker).resolve()) if worker is not None else str(worker_path())

    if spec.runtime == "none":
        return list(command) if command else [interpreter or sys.executable, worker]

    if spec.runtime in CONTAINER_RUNTIMES:
        # ``python3`` is present on python:* images and on any base with the
        # distro python3 package; ``python`` is not guaranteed outside python:*.
        return _container_argv(
            spec, workspace, worker, interpreter or "python3", command,
        )

    if spec.runtime == "bubblewrap":
        return _bubblewrap_argv(
            spec, workspace, worker, interpreter or _bubblewrap_interpreter(), command,
        )

    raise ValueError(f"Unknown container runtime: {spec.runtime!r}")


def shell_argv(spec: ContainerSpec, workspace: str | Path) -> list[str]:
    """An interactive shell inside ``spec``'s sandbox, where the tools run.

    bash when the sandbox has it, else ``sh`` (minimal images).
    """
    if spec.runtime == "bubblewrap":
        shell = ["/bin/bash"] if Path("/bin/bash").exists() else ["/bin/sh"]
    else:
        shell = ["/bin/sh", "-c", "command -v bash >/dev/null 2>&1 && exec bash || exec sh"]
    return build_argv(spec, workspace, command=shell)


def runtime_available(spec: ContainerSpec) -> bool:
    """True if the runtime binary needed by ``spec`` is on PATH."""
    if spec.runtime == "none":
        return True
    binary = "bwrap" if spec.runtime == "bubblewrap" else spec.runtime
    return shutil.which(binary) is not None


def image_present(spec: ContainerSpec) -> bool:
    """True if ``spec``'s image already exists locally (or none is needed)."""
    if spec.runtime not in CONTAINER_RUNTIMES or not spec.image:
        return True
    try:
        proc = subprocess.run(
            [spec.runtime, "image", "inspect", spec.image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def image_created(spec: ContainerSpec) -> Optional[float]:
    """When ``spec``'s image was built (epoch seconds); ``None`` when it has no
    image to build or the runtime cannot say (not built yet, runtime missing)."""
    if spec.runtime not in CONTAINER_RUNTIMES or not spec.image or not spec.dockerfile:
        return None
    try:
        proc = subprocess.run(
            [spec.runtime, "image", "inspect", spec.image],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode != 0:
            return None
        created = json.loads(proc.stdout)[0]["Created"]
        # RFC 3339 with nanoseconds; fromisoformat takes at most microseconds.
        created = re.sub(r"(\.\d{6})\d+", r"\1", created)
        return datetime.fromisoformat(created).timestamp()
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, TypeError):
        return None


def build_command(spec: ContainerSpec, workspace: str | Path) -> Optional[list[str]]:
    """The explicit image-build command, or None when there is nothing to build.

    moka never builds implicitly; this just puts the command in one place so it
    can be shown to the user and run by ``/sandbox build``.
    """
    if spec.runtime not in CONTAINER_RUNTIMES or not spec.image or not spec.dockerfile:
        return None
    dockerfile = Path(spec.dockerfile)
    if not dockerfile.is_absolute():
        dockerfile = Path(workspace).resolve() / dockerfile
    # The context is the Containerfile's folder, not the workspace: the workspace
    # is mounted at run time, and a shared image must not depend on the project.
    return [spec.runtime, "build", "-t", spec.image, "-f", str(dockerfile),
            str(dockerfile.parent)]


async def run_build(
    command: Sequence[str],
    *,
    cwd: str | Path | None = None,
    on_output: Optional[Callable[[str], None]] = None,
) -> int:
    """Run an image build, streaming merged output; return its exit code."""
    proc = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(cwd) if cwd is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        if on_output is not None:
            on_output(line.decode("utf-8", errors="replace").rstrip("\n"))
    return await proc.wait()


def containerfile_name(runtime: str) -> str:
    """Conventional build-file name for ``runtime`` (Containerfile/Dockerfile)."""
    return CONTAINERFILE_NAMES.get(runtime, "Containerfile")


def resolve_base(base: str = "python") -> str:
    """Map a friendly base name to its image tag (unknown names pass through)."""
    return _BASE_IMAGES.get(base, base)


def containerfile_starter(base: str = "python") -> str:
    """A ready-to-edit build file (real directives, deps left as hints).

    The image must provide ``python3`` (moka runs its stdlib-only ``worker.py``
    with it). ``python`` already does; for ``debian``/``ubuntu`` the starter
    installs it. Edit the file freely.
    """
    image = resolve_base(base)
    lines = ["# Built by moka (/sandbox init).", f"FROM {image}", ""]
    if image.lower().startswith("python"):
        lines += ["# This base already ships python3 (used to run moka's worker).", ""]
    else:
        lines += [
            "# moka runs its worker with `python3`, which this base lacks:",
            "RUN apt-get update && apt-get install -y --no-install-recommends python3 \\",
            "    && rm -rf /var/lib/apt/lists/*",
            "",
        ]
    lines += [
        "# Install the project's toolchain here, for example:",
        "# RUN apt-get update && apt-get install -y --no-install-recommends git make",
        "# RUN pip install --no-cache-dir -r requirements.txt",
        "",
        "WORKDIR /workspace",
    ]
    return "\n".join(lines) + "\n"


class SandboxProcess:
    """Owns one worker process and its JSONL request/response client.

    The process starts lazily on the first request and is reused for the
    session.  Because the worker is stateless, a dead process is simply
    respawned by the next request (no recovery protocol).
    """

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: str | None = None,
        timeout: float = 120.0,
        on_stderr: Optional[Callable[[str], None]] = None,
    ):
        self.command = list(command)
        self.cwd = cwd
        self.timeout = timeout
        self.on_stderr = on_stderr
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._next_id = 1
        self.start_count = 0
        self.last_stderr: list[str] = []

    @classmethod
    def from_spec(
        cls,
        spec: ContainerSpec,
        workspace: str | Path,
        *,
        worker: str | Path | None = None,
        interpreter: str | None = None,
        timeout: float | None = None,
        on_stderr: Optional[Callable[[str], None]] = None,
    ) -> "SandboxProcess":
        command = build_argv(spec, workspace, worker, interpreter=interpreter)
        # Containers set their own -w; a direct/bwrap run needs the cwd.
        cwd = None if spec.runtime in CONTAINER_RUNTIMES else str(Path(workspace))
        return cls(
            command,
            cwd=cwd,
            timeout=timeout if timeout is not None else spec.timeout,
            on_stderr=on_stderr,
        )

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        """Start the worker if it is not already running."""
        if self.running:
            return
        self._proc = await asyncio.create_subprocess_exec(
            *self.command,
            cwd=self.cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.start_count += 1
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            self.last_stderr.append(text)
            if self.on_stderr is not None:
                self.on_stderr(text)

    async def request(
        self,
        tool: str,
        args: dict,
        on_output: Optional[Callable[[str, str], None]] = None,
    ) -> str:
        """Send one request and return its result (or raise ``SandboxError``).

        ``on_output(stream, chunk)`` receives interim frames (bash streaming).
        """
        await self.start()
        proc = self._proc
        assert proc is not None and proc.stdin is not None and proc.stdout is not None

        request_id = self._next_id
        self._next_id += 1
        payload = json.dumps({"id": request_id, "tool": tool, "args": args}) + "\n"

        try:
            proc.stdin.write(payload.encode("utf-8"))
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as e:
            await self.stop()
            raise SandboxProcessError(f"worker is not accepting requests: {e}")

        try:
            return await asyncio.wait_for(
                self._read_response(request_id, on_output), timeout=self.timeout
            )
        except asyncio.TimeoutError:
            await self.stop()
            raise SandboxTimeoutError(f"worker timed out after {self.timeout:g}s")
        except SandboxError:
            raise
        except Exception as e:  # pragma: no cover - defensive
            await self.stop()
            raise SandboxProcessError(str(e))

    async def _drain_stderr_briefly(self, timeout: float = 0.3) -> None:
        """Give the stderr reader a moment to finish after the process exits."""
        task = self._stderr_task
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    async def _read_response(
        self,
        request_id: int,
        on_output: Optional[Callable[[str, str], None]] = None,
    ) -> str:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                # Surface the runtime's own error instead of a bare EOF.
                await self._drain_stderr_briefly()
                detail = " | ".join(self.last_stderr[-3:]) or "no stderr"
                code = proc.returncode
                raise SandboxProcessError(
                    f"worker exited before responding (exit={code}): {detail}"
                )
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not text:
                continue
            try:
                frame = json.loads(text)
            except json.JSONDecodeError:
                raise SandboxProcessError(f"unexpected output from worker: {text!r}")
            if frame.get("id") != request_id:
                continue  # stray line from a previous/unknown request
            if "stream" in frame:
                if on_output is not None:
                    on_output(frame.get("stream", ""), frame.get("data", ""))
                continue
            if frame.get("ok"):
                return frame.get("result", "")
            raise SandboxError(frame.get("error", "unknown worker error"))

    def kill(self) -> bool:
        """Synchronously kill the worker (used by the stop button).

        Because the worker is stateless, killing it is enough: the next
        request respawns a fresh one.  Returns True if a live process was
        killed.
        """
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return False
        try:
            proc.kill()
        except ProcessLookupError:
            return False
        self._proc = None
        try:
            asyncio.get_event_loop().create_task(proc.wait())
        except RuntimeError:  # pragma: no cover - no running loop
            pass
        return True

    async def stop(self) -> None:
        """Gracefully stop the worker (shutdown op, then kill on timeout)."""
        proc = self._proc
        self._proc = None
        if proc is None:
            return

        if proc.returncode is None:
            try:
                if proc.stdin is not None and not proc.stdin.is_closing():
                    proc.stdin.write(b'{"op": "shutdown"}\n')
                    await proc.stdin.drain()
                    proc.stdin.close()
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()

        if self._stderr_task is not None:
            self._stderr_task.cancel()
            self._stderr_task = None


class SandboxTransport:
    """A :class:`~moka_code.harness.tools.ToolTransport` over a worker process.

    Structural (duck-typed) rather than subclassed, so ``sandbox.py`` does not
    import the tool layer.  Tool-level failures are returned as result strings
    to match in-process behaviour; transport failures (timeout, dead worker)
    propagate so the harness reports an error.
    """

    is_sandbox = True

    def __init__(self, process: SandboxProcess):
        self.process = process
        # The spec this transport runs; ``/terminal`` opens a shell in it.
        self.spec: Optional[ContainerSpec] = None

    @classmethod
    def from_spec(
        cls,
        spec: ContainerSpec,
        workspace: str | Path,
        *,
        worker: str | Path | None = None,
        interpreter: str | None = None,
        timeout: float | None = None,
        on_stderr: Optional[Callable[[str], None]] = None,
    ) -> "SandboxTransport":
        process = SandboxProcess.from_spec(
            spec,
            workspace,
            worker=worker,
            interpreter=interpreter,
            timeout=timeout,
            on_stderr=on_stderr,
        )
        transport = cls(process)
        transport.spec = spec
        return transport

    async def execute(self, name: str, args: dict, on_output=None) -> str:
        try:
            return await self.process.request(name, args, on_output=on_output)
        except (SandboxTimeoutError, SandboxProcessError):
            raise
        except SandboxError as e:
            return str(e)

    def cancel_active(self, name: str) -> bool:
        if name != "bash":
            return False
        return self.process.kill()

    def close(self) -> None:
        """Synchronously tear down the worker (used when swapping sandboxes)."""
        self.process.kill()

    async def stop(self) -> None:
        await self.process.stop()


__all__ = [
    "DEFAULT_IMAGE",
    "CONTAINER_RUNTIMES",
    "HOST_RUNTIMES",
    "CONTAINERFILE_BASES",
    "CONTAINERFILE_NAMES",
    "SandboxError",
    "SandboxTimeoutError",
    "SandboxProcessError",
    "ContainerSpec",
    "worker_path",
    "build_argv",
    "runtime_available",
    "image_present",
    "build_command",
    "run_build",
    "containerfile_name",
    "containerfile_starter",
    "resolve_base",
    "SandboxProcess",
    "SandboxTransport",
]
