"""
Clipboard support for headless Linux terminals.

Uses OSC 52 escape sequences to set the terminal clipboard.
Works over SSH and without X11/Wayland.
Requires the terminal emulator to support OSC 52
(e.g., iTerm2, Kitty, WezTerm, Alacritty, Foot, Terminal.app).
"""

import base64
import logging
import os

logger = logging.getLogger(__name__)

# Upper bound on the base64 payload. Terminals vary (often 100 KB or more);
# staying below this keeps the escape sequence manageable. The payload is
# truncated on a 4-byte base64 boundary so it remains decodable.
OSC52_MAX_BYTES = 100_000


def copy_to_clipboard(text: str) -> bool:
    """
    Copy *text* to the terminal clipboard via OSC 52.

    Returns True if the escape sequence was emitted, False if the
    terminal likely doesn't support it (e.g. dumb terminal).
    """
    term = os.environ.get("TERM", "")
    if term in ("dumb", "unknown", ""):
        return False

    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")

    if len(encoded) > OSC52_MAX_BYTES:
        # Cut on a base64 quantum boundary so the result still decodes.
        encoded = encoded[: OSC52_MAX_BYTES - (OSC52_MAX_BYTES % 4)]
        logger.warning("Clipboard payload truncated to %d bytes for OSC 52", OSC52_MAX_BYTES)

    # OSC 52 ; c ; <base64> ST
    # ST = String Terminator = ESC \  (or BEL / \x07)
    seq = f"\x1b]52;c;{encoded}\x07"
    stdout = os.fdopen(os.dup(1), "w")
    try:
        stdout.write(seq)
        stdout.flush()
    finally:
        stdout.close()

    return True
