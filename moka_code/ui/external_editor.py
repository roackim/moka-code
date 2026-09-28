"""Run interactive programs ($EDITOR, a shell) in the foreground of the TUI.

Configuration is edited as files: ``/config``, ``/edit`` and ``/config role``
shell out to ``$VISUAL``/``$EDITOR`` (falling back to nano/vim/vi) rather than
building in-TUI forms; ``/terminal`` opens a shell the same way.

The conversation keeps running meanwhile. :func:`run_in_foreground` hands the
terminal to the child with standard job control, so Ctrl+C / Ctrl+Z reach only
the child, and moka stays off the terminal (compositor paused) until the child
exits:

- the child gets its own process group and makes it the terminal's foreground
  group before exec (no race with moka), with default signal handling;
- moka ignores SIGINT meanwhile as a backup, never reads or draws, waits
  without blocking the event loop (streams and tools keep going), and
  continues a child that was stopped (Ctrl+Z) instead of hanging;
- afterwards moka takes the terminal back and redraws.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Any, Callable, List, Optional

# Signals the child must see with their default behavior (moka may ignore or
# handle them itself; ignored dispositions would survive exec).
_CHILD_DEFAULT_SIGNALS = (
    signal.SIGINT, signal.SIGQUIT, signal.SIGTSTP, signal.SIGTTIN, signal.SIGTTOU,
)
# Poll interval while waiting for the child (the event loop stays free).
_WAIT_POLL = 0.05


def resolve_editor() -> List[str]:
    """Command used to edit a file: ``$VISUAL``, ``$EDITOR``, then a fallback."""
    for variable in ("VISUAL", "EDITOR"):
        value = os.environ.get(variable)
        if value:
            return shlex.split(value)
    for candidate in ("nano", "vim", "vi"):
        if shutil.which(candidate):
            return [candidate]
    return []


def edit_file(path: Path) -> bool:
    """Open ``path`` in the resolved editor. Returns False if none was found."""
    command = resolve_editor()
    if not command:
        return False
    return subprocess.call(command + [str(path)]) == 0


def _owned_tty(terminal: Any) -> Optional[int]:
    """The terminal fd when moka is its foreground process group, else None.

    Without job control (moka not in the foreground group) the handover is
    skipped and only the SIGINT backup protects moka.
    """
    fd = getattr(terminal, "fd", None)
    if fd is None:
        return None
    try:
        if os.isatty(fd) and os.tcgetpgrp(fd) == os.getpgrp():
            return fd
    except OSError:
        pass
    return None


def _child_setup(tty_fd: Optional[int]) -> Callable[[], None]:
    """``preexec_fn``: default signals; own foreground process group."""
    def setup() -> None:
        for sig in _CHILD_DEFAULT_SIGNALS:
            signal.signal(sig, signal.SIG_DFL)
        if tty_fd is None:
            return
        os.setpgid(0, 0)
        # Taking the terminal from a background group raises SIGTTOU.
        signal.signal(signal.SIGTTOU, signal.SIG_IGN)
        os.tcsetpgrp(tty_fd, os.getpgrp())
        signal.signal(signal.SIGTTOU, signal.SIG_DFL)
    return setup


def _reclaim_terminal(tty_fd: int) -> None:
    previous = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    try:
        os.tcsetpgrp(tty_fd, os.getpgrp())
    finally:
        signal.signal(signal.SIGTTOU, previous)


async def _wait(proc: subprocess.Popen, own_group: bool) -> int:
    """Wait for ``proc`` without blocking the loop; continue it if stopped.

    Ctrl+Z on a program that does not handle it stops the child's whole
    process group (e.g. ``sh`` and the ``sleep`` it runs). moka cannot take
    it to the background, so the whole group is continued in the
    foreground; continuing only the direct child would leave it waiting on a
    stopped grandchild forever.
    """
    while True:
        pid, status = os.waitpid(proc.pid, os.WNOHANG | os.WUNTRACED)
        if pid == 0:
            await asyncio.sleep(_WAIT_POLL)
            continue
        if os.WIFSTOPPED(status):
            if own_group:
                os.killpg(proc.pid, signal.SIGCONT)
            else:
                os.kill(proc.pid, signal.SIGCONT)
            continue
        proc.returncode = os.waitstatus_to_exitcode(status)
        return proc.returncode


async def run_in_foreground(ui: Any, argv: List[str], cwd: Optional[str] = None,
                            *, env: Optional[dict] = None) -> int:
    """Run ``argv`` interactively with the terminal; return its exit code.

    moka leaves its alternate screen meanwhile, so the program draws on the
    normal screen (or its own alternate screen) and moka repaints on return.
    """
    compositor = getattr(ui, "compositor", None)
    terminal = getattr(compositor, "terminal", None)
    tty_fd = _owned_tty(terminal)
    if compositor is not None:
        compositor.pause()
    if terminal is not None:
        terminal.suspend()
    previous_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        proc = subprocess.Popen(argv, cwd=cwd, env=env, preexec_fn=_child_setup(tty_fd))
        return await _wait(proc, own_group=tty_fd is not None)
    finally:
        if tty_fd is not None:
            _reclaim_terminal(tty_fd)
        signal.signal(signal.SIGINT, previous_sigint)
        if terminal is not None:
            terminal.resume()
        if compositor is not None:
            compositor.resume()


async def open_editor(ui: Any, path: Path) -> bool:
    """Edit ``path`` in the foreground; the conversation keeps running."""
    command = resolve_editor()
    if not command:
        return False
    return await run_in_foreground(ui, command + [str(path)]) == 0


__all__ = ["resolve_editor", "edit_file", "open_editor", "run_in_foreground"]
