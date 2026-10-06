"""Tests for the host-side sandbox launcher and JSONL client.

No real container is used: the integration tests run the actual ``worker.py``
as a local subprocess (the "trivial runtime" that speaks the protocol).
"""

import asyncio
import sys

import pytest

from conftest import wait_until
from moka_code.harness.tools import InProcessTransport, MinimalToolset
from moka_code.sandbox import (
    ContainerSpec,
    SandboxError,
    SandboxProcess,
    SandboxProcessError,
    SandboxTimeoutError,
    SandboxTransport,
    build_argv,
    build_command,
    containerfile_name,
    containerfile_starter,
    image_present,
    run_build,
    runtime_available,
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
        "podman", "img", network=True,
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
    assert argv[-3:] == ["img", "python3", "/opt/worker.py"]


def test_build_argv_docker_default_image(tmp_path):
    argv = build_argv(ContainerSpec("docker"), tmp_path)

    assert argv[0] == "docker"
    assert "python:3.12-slim" in argv


def test_build_argv_bubblewrap(tmp_path):
    argv = build_argv(
        ContainerSpec("bubblewrap"), tmp_path, worker="/w/worker.py",
        interpreter="/usr/bin/python3",
    )

    assert argv[0] == "bwrap"
    assert "--unshare-net" in argv
    assert "--die-with-parent" in argv
    assert "--chdir" in argv and "/workspace" in argv
    # bwrap has an empty root, so /opt must be created before the worker bind.
    assert argv[argv.index("--dir") + 1] == "/opt"
    assert argv[-2:] == ["/usr/bin/python3", "/opt/worker.py"]


def test_build_argv_bubblewrap_defaults_to_a_bound_interpreter(tmp_path):
    from pathlib import Path

    argv = build_argv(ContainerSpec("bubblewrap"), tmp_path, worker="/w/worker.py")

    expected = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable
    # The interpreter must be inside the bound system dirs (or itself bound);
    # a venv/pixi sys.executable would not be, hence the preference.
    assert argv[-2] == expected


def test_build_argv_bubblewrap_binds_venv_interpreter(tmp_path):
    argv = build_argv(
        ContainerSpec("bubblewrap"), tmp_path, worker="/w/worker.py",
        interpreter="/home/u/.venv/bin/python",
    )

    assert argv.count("--ro-bind") >= 1
    assert "/home/u/.venv/bin/python" in argv
    assert argv[-2] == "/home/u/.venv/bin/python"


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


def test_build_argv_bubblewrap_clears_host_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    monkeypatch.setenv("LANG", "C.UTF-8")

    argv = build_argv(ContainerSpec("bubblewrap"), tmp_path, worker="/w/worker.py")

    assert "--clearenv" in argv
    assert "secret" not in argv and "DEEPSEEK_API_KEY" not in argv
    assert argv[argv.index("HOME") + 1] == "/tmp"
    assert argv[argv.index("LANG") + 1] == "C.UTF-8"


def test_build_argv_bubblewrap_mounts_proc_and_dev_and_unshares(tmp_path):
    argv = build_argv(ContainerSpec("bubblewrap"), tmp_path, worker="/w/worker.py")

    assert argv[argv.index("--proc") + 1] == "/proc"
    assert argv[argv.index("--dev") + 1] == "/dev"
    for flag in ("--unshare-ipc", "--unshare-uts", "--unshare-cgroup-try"):
        assert flag in argv


def test_build_argv_bubblewrap_run_args_apply_last(tmp_path):
    spec = ContainerSpec("bubblewrap", run_args=("--setenv", "HOME", "/mine"))

    argv = build_argv(spec, tmp_path, worker="/w/worker.py")

    # After every moka-set flag (so it wins) and before the command.
    assert argv.index("--chdir") < argv.index("/mine") < argv.index("--")


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


def test_process_error_surfaces_stderr(tmp_path):
    proc = SandboxProcess(
        [sys.executable, "-c", "import sys; sys.stderr.write('boom\\n'); sys.exit(3)"],
        cwd=str(tmp_path),
    )

    async def scenario():
        try:
            with pytest.raises(SandboxProcessError, match="boom"):
                await proc.request("read", {"path": "x"})
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


def test_a_bash_commands_own_timeout_is_enforced_by_the_worker(tmp_path):
    """The command's limit (the role's ``tool_timeout``) ends it with a normal
    tool result; the worker is not restarted. The request limit only catches a
    worker that hangs, so it is the command's plus slack, not the 1 s default."""
    proc = SandboxProcess(_worker_command(), cwd=str(tmp_path), timeout=1.0)

    async def scenario():
        try:
            with pytest.raises(SandboxError, match="timed out after 1s") as raised:
                await proc.request("bash", {"command": "sleep 30", "timeout": 1})
            # The worker's own tool error, not the request limit: nothing restarted.
            assert not isinstance(raised.value, SandboxTimeoutError)
            assert proc.running and proc.start_count == 1
        finally:
            await proc.stop()

    _run(scenario())


def test_the_request_limit_follows_the_commands_timeout_plus_slack(tmp_path):
    from moka_code import sandbox as sandbox_module

    proc = SandboxProcess(_worker_command(), cwd=str(tmp_path), timeout=0.2)

    async def scenario():
        try:
            # A 0.2 s request limit would kill this 0.5 s command; the command's
            # own 5 s timeout (+ slack) lets it finish.
            result = await proc.request("bash", {"command": "sleep 0.5; echo ok", "timeout": 5})
            assert "ok" in result and proc.start_count == 1
        finally:
            await proc.stop()

    assert sandbox_module._BASH_SLACK > 0
    _run(scenario())


def test_no_command_limit_means_no_request_limit_either(tmp_path):
    proc = SandboxProcess(_worker_command(), cwd=str(tmp_path), timeout=0.2)

    async def scenario():
        try:
            for no_limit in (0, -1):
                result = await proc.request(
                    "bash", {"command": "sleep 0.5; echo ok", "timeout": no_limit})
                assert "ok" in result
            assert proc.start_count == 1
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
            await wait_until(lambda: transport.process.running)
            assert transport.cancel_active("bash") is True
            with pytest.raises(SandboxError):
                await asyncio.wait_for(task, timeout=5)
            assert transport.cancel_active("read") is False
        finally:
            await transport.stop()

    _run(scenario())


# --- preflight / build ------------------------------------------------------

def test_build_command_podman(tmp_path):
    (tmp_path / "Containerfile.moka").write_text("FROM x\n")
    spec = ContainerSpec("podman", "img", dockerfile="Containerfile.moka")

    command = build_command(spec, tmp_path)

    assert command == [
        "podman", "build", "-t", "img",
        "-f", str(tmp_path / "Containerfile.moka"), str(tmp_path),
    ]


def test_build_command_none_without_dockerfile_or_image(tmp_path):
    assert build_command(ContainerSpec("podman", "img"), tmp_path) is None
    assert build_command(ContainerSpec("podman", dockerfile="Containerfile"), tmp_path) is None
    assert build_command(ContainerSpec("none"), tmp_path) is None


def test_containerfile_starter_has_base_and_workdir():
    text = containerfile_starter("debian")

    assert "FROM debian" in text
    assert "WORKDIR /workspace" in text


def test_starter_bases_are_friendly_names():
    from moka_code.sandbox import CONTAINERFILE_BASES

    assert CONTAINERFILE_BASES == ("python", "debian", "ubuntu")


def test_resolve_base_maps_friendly_names_to_slim_images():
    from moka_code.sandbox import resolve_base

    assert resolve_base("python") == "python:3.12-slim"
    assert resolve_base("debian") == "debian:stable-slim"
    assert resolve_base("ubuntu") == "ubuntu"
    assert resolve_base("custom:tag") == "custom:tag"  # raw passthrough


def test_starter_installs_python3_for_debian_base():
    text = containerfile_starter("debian")

    assert "FROM debian:stable-slim" in text
    assert "install -y --no-install-recommends python3" in text


def test_starter_does_not_install_python3_for_python_base():
    text = containerfile_starter("python")

    assert "FROM python:3.12-slim" in text
    assert "install -y --no-install-recommends python3" not in text


def test_containerfile_name_is_conventional():
    assert containerfile_name("podman") == "Containerfile"
    assert containerfile_name("docker") == "Dockerfile"


def test_runtime_available_none_true():
    assert runtime_available(ContainerSpec("none")) is True


def test_runtime_available_uses_which(monkeypatch):
    import moka_code.sandbox as sandbox

    monkeypatch.setattr(
        sandbox.shutil, "which", lambda b: "/usr/bin/podman" if b == "podman" else None
    )

    assert runtime_available(ContainerSpec("podman")) is True
    assert runtime_available(ContainerSpec("docker")) is False
    assert runtime_available(ContainerSpec("bubblewrap")) is False


def test_image_present(monkeypatch):
    import moka_code.sandbox as sandbox

    class _Result:
        returncode = 0

    monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **k: _Result())

    assert image_present(ContainerSpec("podman", "img")) is True
    assert image_present(ContainerSpec("none")) is True
    assert image_present(ContainerSpec("podman")) is True


def test_run_build_streams_and_returns_code(tmp_path):
    lines = []

    code = _run(run_build(
        [sys.executable, "-c", "print('building')"],
        cwd=tmp_path,
        on_output=lines.append,
    ))

    assert code == 0
    assert any("building" in line for line in lines)


# --- selection / wiring -----------------------------------------------------

def test_build_transport_spec_none_is_none():
    from moka_code.harness.harness import _build_transport

    assert _build_transport(None, ".") is None
    assert _build_transport(ContainerSpec("none"), ".") is None


def test_build_transport_spec_container(tmp_path):
    from moka_code.harness.harness import _build_transport

    transport = _build_transport(ContainerSpec("bubblewrap"), str(tmp_path))

    assert isinstance(transport, SandboxTransport)


# --- image build time --------------------------------------------------------

def _inspect(monkeypatch, stdout, returncode=0):
    import subprocess

    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, returncode, stdout, ""))


def test_image_created_reads_an_rfc3339_time_with_nanoseconds(monkeypatch):
    from moka_code.sandbox import image_created

    _inspect(monkeypatch, '[{"Created": "2026-10-05T12:00:00.123456789Z"}]')

    spec = ContainerSpec("podman", "img", dockerfile="/x/Containerfile")
    assert image_created(spec) == 1791201600.123456


def test_image_created_is_none_when_unknown(monkeypatch):
    from moka_code.sandbox import image_created

    spec = ContainerSpec("podman", "img", dockerfile="/x/Containerfile")
    _inspect(monkeypatch, "", returncode=1)                       # not built
    assert image_created(spec) is None
    _inspect(monkeypatch, "not json")
    assert image_created(spec) is None
    _inspect(monkeypatch, '[{"Created": "2026-10-05T12:00:00Z"}]')
    assert image_created(ContainerSpec("podman", "img")) is None  # nothing to build
    assert image_created(ContainerSpec("bubblewrap")) is None
