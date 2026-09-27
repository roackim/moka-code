"""The system clipboard: copy (``c``) and paste (Ctrl+V).

Locally the clipboard is owned by ``xclip``/``xsel``/``wl-copy``. Over SSH
those helpers usually have no display, so copying falls back to OSC 52, which
asks the terminal emulator to own the clipboard. tmux needs
``set -g allow-passthrough on`` for the sequence to reach the outer terminal.
Pasting reads the clipboard of the machine moka runs on (over SSH: the remote
one); there is no OSC 52 fallback for reading.
"""

from __future__ import annotations

import base64
import logging
import os
import subprocess
from typing import Optional, Union

logger = logging.getLogger("tui")

# Native helpers first: they report success/failure reliably, whereas the
# terminal silently ignores an OSC 52 sequence it does not support.
_NATIVE_COMMANDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("xclip", "-selection", "clipboard"), "xclip"),
    (("xsel", "--clipboard", "--input"), "xsel"),
    (("wl-copy",), "wl-copy"),
)

# Upper bound on the OSC 52 base64 payload. Terminals vary (often 100 KB or
# more); the payload is truncated on a 4-byte base64 boundary so it remains
# decodable.
OSC52_MAX_BYTES = 100_000

# Image types a paste accepts, most preferred first.
_IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")

_READ_TIMEOUT = 2.0


def _osc52_copy(text: str) -> bool:
    """Emit an OSC 52 sequence; False on a terminal that cannot take it."""
    term = os.environ.get("TERM", "")
    if term in ("dumb", "unknown", ""):
        return False

    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    if len(encoded) > OSC52_MAX_BYTES:
        # Cut on a base64 quantum boundary so the result still decodes.
        encoded = encoded[: OSC52_MAX_BYTES - (OSC52_MAX_BYTES % 4)]
        logger.warning("Clipboard payload truncated to %d bytes for OSC 52", OSC52_MAX_BYTES)

    stdout = os.fdopen(os.dup(1), "w")
    try:
        stdout.write(f"\x1b]52;c;{encoded}\x07")
        stdout.flush()
    finally:
        stdout.close()
    return True


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


def _output(command: tuple[str, ...]) -> Optional[bytes]:
    try:
        result = subprocess.run(command, capture_output=True, check=True,
                                timeout=_READ_TIMEOUT)
    except (FileNotFoundError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired, OSError):
        return None
    return result.stdout


def _pick_image(types: bytes) -> Optional[str]:
    offered = set(types.decode("utf-8", "replace").split())
    return next((mime for mime in _IMAGE_TYPES if mime in offered), None)


def read_clipboard() -> Union[bytes, str, None]:
    """Read the clipboard: image bytes, text, or ``None`` when empty/unreadable."""
    readers = (
        (("wl-paste", "--list-types"),
         lambda mime: ("wl-paste", "--no-newline", "--type", mime),
         ("wl-paste", "--no-newline")),
        (("xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"),
         lambda mime: ("xclip", "-selection", "clipboard", "-t", mime, "-o"),
         ("xclip", "-selection", "clipboard", "-o")),
    )
    for list_types, read_image, read_text in readers:
        types = _output(list_types)
        if types is None:
            continue
        mime = _pick_image(types)
        if mime:
            data = _output(read_image(mime))
            if data:
                return data
        text = _output(read_text)
        return text.decode("utf-8", "replace") if text else None
    text = _output(("xsel", "--clipboard", "--output"))
    return text.decode("utf-8", "replace") if text else None
