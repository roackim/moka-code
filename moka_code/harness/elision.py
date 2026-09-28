"""
Head/tail elision for oversized tool output.

The model's context is bounded but a ``find``/``cat``/test run can emit
megabytes.  Host-side, before a tool result enters history, keep the head and
tail and replace the middle with a marker telling the model how to narrow the
command.  The worker stays dumb; this is only about what the model sees.
"""

DEFAULT_LIMIT = 10_000

_MARKER = ("\n… {n} chars elided; narrow the command (head/tail/grep/sed), "
           "or if it is slow to rerun, redirect it to a file and grep that\n")


def elide(text: str, *, limit: int = DEFAULT_LIMIT) -> str:
    """Bound ``text`` to roughly ``limit`` characters (head + tail + marker).

    Returns ``text`` unchanged when it already fits (or ``limit <= 0``).
    """
    if not isinstance(text, str) or limit <= 0 or len(text) <= limit:
        return text

    half = limit // 2
    head = text[:half]
    tail = text[-half:] if half else ""
    elided = len(text) - len(head) - len(tail)
    return f"{head}{_MARKER.format(n=elided)}{tail}"


__all__ = ["DEFAULT_LIMIT", "elide"]
