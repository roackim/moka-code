"""The startup banner: the moka letters and cup, shown in an empty transcript.

It is drawn by the history panel (not a message), so it never enters history
or exports, disappears with the first message and returns after ``/clear``.
It degrades with the width: letters + cup, then letters only, then the plain
name. Disable it with ``show_banner = false`` in ``ui.toml``. Setup notes
(config errors, ...) are drawn with it: under the art, or next to the cup.
"""

from __future__ import annotations

from moka_code.ui.tui.layout_utils import display_width

# Letters occupy columns 0-49, the cup the rest.
_ART = """\
                                                         ▒    ▒
                                                          ▒    ▒
                           █████                         ▒    ▒
                          ▒▒███                         ▄▄▄▄▄▄▄▄
  █████████████    ██████  ▒███ █████  ██████        ▄▀▀        ▀▀█▄▄▄
 ▒▒███▒▒███▒▒███  ███▒▒███ ▒███▒▒███  ▒▒▒▒▒███       ██▄▄▄▄▄▄▄▄▄▄█▒▀ ▀█
  ▒███ ▒███ ▒███ ▒███ ▒███ ▒██████▒    ███████       ████████████▒▒▄ ▄█
  ▒███ ▒███ ▒███ ▒███ ▒███ ▒███▒▒███  ███▒▒███        █████████▒▒▒▒█▀▀
  █████▒███ █████▒▒██████  ████ █████▒▒████████      ▄▄▀███▒▒▒▒▒▒▀▄▄
 ▒▒▒▒▒ ▒▒▒ ▒▒▒▒▒  ▒▒▒▒▒▒  ▒▒▒▒ ▒▒▒▒▒  ▒▒▒▒▒▒▒▒      ▀▀▄▄▄▄▄▄▄▄▄▄▄▄▄▀▀
"""
_LOGO_COLUMN = 50


def _block(lines: list[str]) -> list[str]:
    """Trim trailing spaces and the blank margin shared by every line."""
    lines = [line.rstrip() for line in lines]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    indent = min((len(l) - len(l.lstrip(" ")) for l in lines if l), default=0)
    return [line[indent:] for line in lines]


_FULL = _block(_ART.split("\n"))
_LETTERS = _block([line[:_LOGO_COLUMN] for line in _ART.split("\n")])
_CUP = _block([line[_LOGO_COLUMN:] for line in _ART.split("\n")])


def banner_lines(width: int) -> list[str]:
    """The largest banner variant that fits ``width`` columns."""
    for variant in (_FULL, _LETTERS):
        if max(display_width(line) for line in variant) <= width:
            return variant
    return ["moka"] if width >= 4 else []


def cup_lines() -> list[str]:
    """The cup alone: the setup notes take the letters' place."""
    return _CUP


__all__ = ["banner_lines", "cup_lines"]
