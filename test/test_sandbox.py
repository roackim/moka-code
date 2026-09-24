"""Tests for the host-side sandbox launcher and JSONL client.

No real container is used: the integration tests run the actual ``worker.py``
as a local subprocess (the "trivial runtime" that speaks the protocol).
"""

import asyncio
import sys

import pytest

from pico_chat.harness.tools import InProcessTransport, MinimalToolset
from pico_chat.sandbox import (
    ContainerSpec,
    SandboxError,
    SandboxProcess,
    SandboxTimeoutError,
    SandboxTransport,
    build_argv,
    worker_path,
)


def _run(coro):
    return asyncio.run(coro)


def _worker_command():
    return [sys.executable, str(worker_path())]


def test_spec_enabled_flag():
    assert ContainerSpec("none").enabled is False
    assert ContainerSpec("podman").enabled is True
    assert ContainerSpec(
        "podman", "img", network=True, timeout=30.0,
        run_args=("--userns=keep-id",), dockerfile="Containerfile",
    ).enabled is True


# --- build_argv ------------------------------------------------------------

def test_build_argv_none_runs_host_worker(tmp_path):
    argv = build_argv(ContainerSpec("none"), tmp_path)

    assert argv == [sys.executable, str(worker_path())]


def test_build_argv_podman_core_flags(tmp_path):
    argv = build_argv(ContainerSpec("podman", "img"), tmp_path, worker="/w/worker.py")

    assert argv[:3] == ["podman", "run", "-i"]
    assert "-t" not in argv
    assert "--rm" in argv
    assert "-w" in argv and "/workspace" in argv
    assert "--network=none" in argv
    assert "--read-only" in argv
    assert "--cap-drop=all" in argv
    assert "no-new-privileges" in argv
    assert f"{tmp_path}:/workspace:Z" in argv
    assert "/w/worker.py:/opt/worker.py:ro" in argv
    assert argv[-3:] == ["img", "python", "/opt/worker.py"]


def test_build_argv_docker_default_image(tmp_path):
    argv = build_argv(ContainerSpec("docker"), tmp_path)

    assert argv[0] == "docker"
    assert "python:3.12-slim" in argv


def test_build_argv_bubblewrap(tmp_path):
    argv = build_argv(ContainerSpec("bubblewrap"), tmp_path, worker="/w/worker.py")

    assert argv[0] == "bwrap"
    assert "--unshare-net" in argv
    assert "--die-with-parent" in argv
    assert "--chdir" in argv and "/workspace" in argv
    assert argv[-2:] == [sys.executable, "/opt/worker.py"]


def test_build_argv_honors_network_and_run_args(tmp_path):
    spec = ContainerSpec(
        "podman", "img", network=True,
        run_args=("--userns=keep-id", "--env", "FOO=bar"),
    )

    argv = build_argv(spec, tmp_path)

    assert "--network=none" not in argv
    assert "--userns=keep-id" in argv
    assert argv[argv.index("--userns=keep-id") + 1] == "--env"


def test_build_argv_bubblewrap_network_opt_in(tmp_path):
    argv = build_argv(ContainerSpec("bubblewrap", network=True), tmp_path)

    assert "--unshare-net" not in argv


# --- SandboxProcess (integration with the real worker) ---------------------

def test_process_starts_lazily_and_serves_requests(tmp_path):
    proc = SandboxProcess(_worker_command(), cwd=str(tmp_path))

    async def scenario():
        try:
            assert proc.running is False
            assert proc.start_count == 0

            out = await proc.request("bash", {"command": "echo hi"})
            assert "hi" in out
            assert proc.running is True
            assert proc.start_count == 1

            await proc.request("write", {"path": "f.txt", "content": "body"})
            read = await proc.request("read", {"path": "f.txt"})
            assert read == "body"
            assert proc.start_count == 1  # reused, not respawned
        finally:
            await proc.stop()

    _run(scenario())
    assert proc.running is False


def test_process_reports_tool_errors(tmp_path):
    proc = SandboxProcess(_worker_command(), cwd=str(tmp_path))

    async def scenario():
        try:
            with pytest.raises(SandboxError, match="Unknown tool"):
                await proc.request("nope", {})
            with pytest.raises(SandboxError, match="File not found"):
                await proc.request("read", {"path": "missing.txt"})
        finally:
            await proc.stop()

    _run(scenario())


def test_process_respawns_after_crash(tmp_path):
    proc = SandboxProcess(_worker_command(), cwd=str(tmp_path))

    async def scenario():
        try:
            await proc.request("bash", {"command": "echo one"})
            assert proc.start_count == 1

            proc._proc.kill()
            await proc._proc.wait()
            assert proc.running is False

            out = await proc.request("bash", {"command": "echo two"})
            assert "two" in out
            assert proc.start_count == 2
        finally:
            await proc.stop()

    _run(scenario())


def test_process_timeout_stops_the_worker(tmp_path):
    proc = SandboxProcess(_worker_command(), cwd=str(tmp_path), timeout=1.0)

    async def scenario():
        try:
            with pytest.raises(SandboxTimeoutError, match="timed out"):
                await proc.request("bash", {"command": "sleep 30"})
            assert proc.running is False
        finally:
            await proc.stop()

    _run(scenario())


def test_from_spec_none_builds_host_command(tmp_path):
    proc = SandboxProcess.from_spec(ContainerSpec("none"), tmp_path)

    assert proc.command == _worker_command()
    assert proc.cwd == str(tmp_path)


def test_from_spec_container_sets_no_host_cwd(tmp_path):
    proc = SandboxProcess.from_spec(ContainerSpec("podman"), tmp_path)

    assert proc.cwd is None
    assert proc.command[0] == "podman"


# --- SandboxTransport -------------------------------------------------------

def test_transport_executes_over_the_process(tmp_path):
    (tmp_path / "f.txt").write_text("body")
    transport = SandboxTransport.from_spec(ContainerSpec("none"), tmp_path)

    async def scenario():
        try:
            return await transport.execute("read", {"path": "f.txt"})
        finally:
            await transport.stop()

    assert _run(scenario()) == "body"


def test_transport_matches_in_process_on_tool_errors(tmp_path):
    inproc = InProcessTransport(MinimalToolset(tmp_path))
    sandbox = SandboxTransport.from_spec(ContainerSpec("none"), tmp_path)

    async def scenario():
        try:
            return (
                await inproc.execute("read", {"path": "missing.txt"}),
                await sandbox.execute("read", {"path": "missing.txt"}),
            )
        finally:
            await sandbox.stop()

    inproc_result, sandbox_result = _run(scenario())
    assert inproc_result == sandbox_result


def test_transport_streams_bash_output(tmp_path):
    transport = SandboxTransport.from_spec(ContainerSpec("none"), tmp_path)
    chunks = []

    async def scenario():
        try:
            return await transport.execute(
                "bash",
                {"command": "echo streamed"},
                on_output=lambda stream, data: chunks.append((stream, data)),
            )
        finally:
            await transport.stop()

    result = _run(scenario())

    assert any("streamed" in data for _, data in chunks)
    assert chunks[0][0] == "stdout"
    assert "streamed" in result


def test_transport_cancel_kills_the_worker(tmp_path):
    transport = SandboxTransport.from_spec(ContainerSpec("none"), tmp_path, timeout=30.0)

    async def scenario():
        try:
            task = asyncio.create_task(
                transport.execute("bash", {"command": "sleep 30"})
            )
            await asyncio.sleep(0.5)
            assert transport.cancel_active("bash") is True
            with pytest.raises(SandboxError):
                await asyncio.wait_for(task, timeout=5)
            assert transport.cancel_active("read") is False
        finally:
            await transport.stop()

    _run(scenario())


# --- selection / wiring -----------------------------------------------------

def test_build_transport_spec_none_is_none():
    from pico_chat.harness.harness import _build_transport

    assert _build_transport(None, ".") is None
    assert _build_transport(ContainerSpec("none"), ".") is None


def test_build_transport_spec_container(tmp_path):
    from pico_chat.harness.harness import _build_transport

    transport = _build_transport(ContainerSpec("bubblewrap"), str(tmp_path))

    assert isinstance(transport, SandboxTransport)
