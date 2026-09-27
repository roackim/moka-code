"""Clipboard writing for the TUI.

Locally the clipboard is owned by ``xclip``/``xsel``/``wl-copy``. Over SSH
those helpers usually have no display, so we fall back to OSC 52, which asks
the terminal emulator to own the clipboard. tmux needs
``set -g allow-passthrough on`` for the sequence to reach the outer terminal.
"""

from __future__ import annotations

import logging
import subprocess

from moka_chat.harness.clipboard import copy_to_clipboard as _osc52_copy

logger = logging.getLogger("tui")

# Native helpers first: they report success/failure reliably, whereas the
# terminal silently ignores an OSC 52 sequence it does not support.
_NATIVE_COMMANDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("xclip", "-selection", "clipboard"), "xclip"),
    (("xsel", "--clipboard", "--input"), "xsel"),
    (("wl-copy",), "wl-copy"),
)


def copy_to_clipboard(text: str) -> str | None:
    """Copy *text* to the system clipboard.

    Returns the method that succeeded (``"xclip"``, ``"xsel"``, ``"wl-copy"``
    or ``"OSC 52"``), or ``None`` when every method failed.
    """
    if not text:
        return None

    data = text.encode("utf-8")
    for command, name in _NATIVE_COMMANDS:
        try:
            subprocess.run(command, input=data, check=True, stderr=subprocess.DEVNULL)
        except (FileNotFoundError, subprocess.CalledProcessError, OSError):
            continue
        logger.info("Copied to clipboard (%s)", name)
        return name

    if _osc52_copy(text):
        logger.info("Copied to clipboard (OSC 52)")
        return "OSC 52"

    logger.warning("No clipboard method succeeded (xclip, xsel, wl-copy, OSC 52)")
    return None
