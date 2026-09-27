"""Interactive programs ($EDITOR, /terminal) run without freezing moka.

The Ctrl+C / Ctrl+Z and terminal-ownership behavior is verified end to end in
a pseudo-terminal (see the wiki); these tests pin the pieces that do not need
a real TTY.
"""

import asyncio
import os
import signal

import pytest

from moka_chat.sandbox import ContainerSpec, build_argv, shell_argv
from moka_chat.ui.external_editor import run_in_foreground


class _Recorder:
    def __init__(self, calls, name):
        self.calls, self.name = calls, name

    def __getattr__(self, attr):
        if attr not in ("pause", "resume", "suspend", "clear_screen"):
            raise AttributeError(attr)
        return lambda *a, **k: self.calls.append(f"{self.name}.{attr}")


def _ui(calls):
    compositor = _Recorder(calls, "compositor")
    terminal = _Recorder(calls, "terminal")
    # No ``fd``: no job control here, so no terminal handover (SIGINT backup only).
    compositor.__dict__["terminal"] = terminal
    return type("UI", (), {"compositor": compositor})()


def test_handoff_pauses_moka_returns_the_exit_code_and_restores_sigint():
    calls = []
    before = signal.getsignal(signal.SIGINT)
    code = asyncio.run(run_in_foreground(_ui(calls), ["sh", "-c", "exit 3"]))

    assert code == 3
    assert calls == ["compositor.pause", "terminal.suspend",
                     "terminal.resume", "compositor.resume"]
    assert signal.getsignal(signal.SIGINT) is before


def test_clear_screen_happens_after_suspend_and_only_on_request():
    calls = []
    asyncio.run(run_in_foreground(_ui(calls), ["true"], clear_screen=True))
    assert calls[:3] == ["compositor.pause", "terminal.suspend", "terminal.clear_screen"]
    calls = []
    asyncio.run(run_in_foreground(_ui(calls), ["true"]))
    assert "terminal.clear_screen" not in calls


def test_handoff_keeps_the_event_loop_running():
    ticks = []

    async def scenario():
        async def ticker():
            while True:
                ticks.append(1)
                await asyncio.sleep(0.02)

        task = asyncio.create_task(ticker())
        await run_in_foreground(_ui([]), ["sleep", "0.3"])
        task.cancel()

    asyncio.run(scenario())
    assert len(ticks) >= 5


def test_a_stopped_child_is_continued_instead_of_hanging():
    code = asyncio.run(asyncio.wait_for(
        run_in_foreground(_ui([]), ["sh", "-c", "kill -STOP $$; exit 4"]), timeout=5))
    assert code == 4


def test_compositor_pause_stays_off_the_terminal(monkeypatch):
    import moka_chat.ui.tui.compositor as compositor_mod

    class _Term:
        resized = False

        def get_size(self):
            return 80, 24

    monkeypatch.setattr(compositor_mod, "Terminal", _Term)

    class _Root:
        id = None
        children = []
        rendered = 0

        def render(self, buffer):
            _Root.rendered += 1

    compositor = compositor_mod.Compositor(_Root())
    compositor.width, compositor.height = 80, 24
    compositor.pause()
    compositor.render()
    assert _Root.rendered == 0
    compositor.resume()
    assert compositor._full_redraw and compositor._render_requested


def test_sandbox_shell_uses_the_tools_mounts_with_a_tty(tmp_path):
    podman = ContainerSpec(runtime="podman", image="python:3.12-slim")
    worker_argv = build_argv(podman, tmp_path)
    shell = shell_argv(podman, tmp_path)
    assert "-i" in worker_argv and "-it" not in worker_argv
    assert "-it" in shell
    assert f"{tmp_path.resolve()}:/workspace:Z" in shell
    assert shell[-3:-1] == ["/bin/sh", "-c"]

    bwrap = shell_argv(ContainerSpec(runtime="bubblewrap"), tmp_path)
    assert "--unshare-net" in bwrap
    assert bwrap[bwrap.index("--bind") + 1:bwrap.index("--bind") + 3] == [
        str(tmp_path.resolve()), "/workspace"]
    assert bwrap[-2] == "--" and bwrap[-1] in ("/bin/bash", "/bin/sh")


def _shell_ui(tmp_path, spec, monkeypatch):
    opened, messages = [], []

    async def _run(ui, argv, cwd=None, clear_screen=False, env=None):
        opened.append((argv, cwd))
        assert clear_screen  # a shell draws in place: start on a blank screen
        assert env["MOKA_TERMINAL"] == str(os.getpid())  # marks the shell: no nesting
        return 0

    monkeypatch.setattr("moka_chat.ui.external_editor.run_in_foreground", _run)
    monkeypatch.setenv("SHELL", "/bin/zsh")
    transport = type("T", (), {"spec": ContainerSpec(runtime=spec) if spec else None})()
    agent = type("A", (), {"workspace": str(tmp_path), "transport": transport})()
    panel = type("P", (), {"add_message": lambda self, text, **k: messages.append(text)})()
    ui = type("UI", (), {"agent": agent, "chat_history_panel": panel})()
    return ui, opened, messages


@pytest.mark.parametrize("spec", [None, "bubblewrap"])
def test_terminal_is_always_the_host(monkeypatch, tmp_path, spec):
    from moka_chat.ui.commands.core import cmd_terminal

    ui, opened, _ = _shell_ui(tmp_path, spec, monkeypatch)
    asyncio.run(cmd_terminal(ui, []))
    assert opened == [(["/bin/zsh"], str(tmp_path))]


def test_sandbox_terminal_opens_in_the_active_sandbox(monkeypatch, tmp_path):
    from moka_chat.ui.commands.sandbox import sandbox_terminal

    monkeypatch.chdir(tmp_path)
    ui, opened, _ = _shell_ui(tmp_path, "bubblewrap", monkeypatch)
    asyncio.run(sandbox_terminal(ui, []))
    argv, cwd = opened[0]
    assert argv[0] == "bwrap" and cwd == str(tmp_path)


def test_sandbox_terminal_without_a_sandbox_says_so(monkeypatch, tmp_path):
    from moka_chat.ui.commands.sandbox import sandbox_terminal

    ui, opened, messages = _shell_ui(tmp_path, None, monkeypatch)
    asyncio.run(sandbox_terminal(ui, []))
    assert opened == []
    assert "No active sandbox" in messages[0] and "/terminal" in messages[0]


def test_terminal_rejects_unknown_arguments(monkeypatch):
    from moka_chat.ui.commands.core import cmd_terminal

    messages = []
    panel = type("P", (), {"add_message": lambda self, text, **k: messages.append(text)})()
    ui = type("UI", (), {"agent": None, "chat_history_panel": panel})()
    asyncio.run(cmd_terminal(ui, ["host"]))
    assert messages and messages[0].startswith("Usage: /terminal")
    assert "/sandbox terminal" in messages[0]


def test_moka_refuses_to_start_inside_its_own_terminal(monkeypatch, capsys):
    import moka_chat.main as main_mod

    monkeypatch.setenv("MOKA_TERMINAL", "4242")
    monkeypatch.setattr(main_mod, "get_harness",
                        lambda: pytest.fail("must refuse before any setup"))

    assert main_mod.main() == 1
    err = capsys.readouterr().err
    assert "moka is already running (pid 4242)" in err
    assert "Type 'exit' to return to it." in err


def test_the_shell_marker_reaches_the_child():
    code = asyncio.run(run_in_foreground(
        _ui([]), ["sh", "-c", 'test "$MOKA_TERMINAL" = 7'],
        env={**os.environ, "MOKA_TERMINAL": "7"}))
    assert code == 0
