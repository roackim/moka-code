"""
Host-side sandbox launcher and JSONL client.

The container is the boundary: pico does no path confinement or command
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
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence


DEFAULT_IMAGE = "python:3.12-slim"

CONTAINER_RUNTIMES = ("podman", "docker")
HOST_RUNTIMES = ("bubblewrap",)


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
    for the image-preflight step (pico never builds implicitly).
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
                    interpreter: str) -> list[str]:
    argv = [
        spec.runtime, "run",
        "-i",                       # pipe stdio; never -t (a TTY corrupts framing)
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
        interpreter, "/opt/worker.py",
    ]
    return argv


def _bubblewrap_argv(spec: ContainerSpec, workspace: str, worker: str,
                     interpreter: str) -> list[str]:
    argv = [
        "bwrap",
        "--die-with-parent",
        "--unshare-pid",
    ]
    if not spec.network:
        argv.append("--unshare-net")
    argv += list(spec.run_args)
    for path in ("/usr", "/lib", "/lib64", "/bin", "/etc"):
        if Path(path).exists():
            argv += ["--ro-bind", path, path]
    argv += [
        "--ro-bind", worker, "/opt/worker.py",
        "--bind", workspace, "/workspace",
        "--tmpfs", "/tmp",
        "--chdir", "/workspace",
        "--", interpreter, "/opt/worker.py",
    ]
    return argv


def build_argv(
    spec: ContainerSpec,
    workspace: str | Path,
    worker: str | Path | None = None,
    *,
    interpreter: str | None = None,
) -> list[str]:
    """Build the runtime command line for ``spec``.

    ``none`` runs the worker directly on the host (no isolation); container
    runtimes mirror the image-injection model (the host's ``worker.py`` is
    mounted read-only).  The launcher only has to produce a stdio pipe.
    """
    workspace = str(Path(workspace).resolve())
    worker = str(Path(worker).resolve()) if worker is not None else str(worker_path())

    if spec.runtime == "none":
        return [interpreter or sys.executable, worker]

    if spec.runtime in CONTAINER_RUNTIMES:
        return _container_argv(
            spec, workspace, worker, interpreter or "python",
        )

    if spec.runtime == "bubblewrap":
        return _bubblewrap_argv(spec, workspace, worker, interpreter or sys.executable)

    raise ValueError(f"Unknown container runtime: {spec.runtime!r}")


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
                raise SandboxProcessError("worker exited before responding")
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
    """A :class:`~pico_chat.harness.tools.ToolTransport` over a worker process.

    Structural (duck-typed) rather than subclassed, so ``sandbox.py`` does not
    import the tool layer.  Tool-level failures are returned as result strings
    to match in-process behaviour; transport failures (timeout, dead worker)
    propagate so the harness reports an error.
    """

    def __init__(self, process: SandboxProcess):
        self.process = process

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
        return cls(process)

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
    "SandboxError",
    "SandboxTimeoutError",
    "SandboxProcessError",
    "ContainerSpec",
    "worker_path",
    "build_argv",
    "SandboxProcess",
    "SandboxTransport",
]
