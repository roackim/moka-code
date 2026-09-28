"""Bracketed paste keeps multi-byte UTF-8 characters whole."""

import os

from moka_code.ui.tui.terminal import Terminal


def _paste(raw: bytes):
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, raw)
        terminal = Terminal.__new__(Terminal)
        terminal.fd = read_fd
        return terminal._read_bracketed_paste()
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_paste_decodes_multibyte_characters_whole():
    text = "`CLIMB_PROB` → ≈ — climb, é 🙂"
    event = _paste(text.encode() + b"\x1b[201~trailing")
    assert event.text == text


def test_paste_without_end_marker_keeps_what_arrived():
    read_fd, write_fd = os.pipe()
    os.write(write_fd, "partial →".encode())
    os.close(write_fd)
    terminal = Terminal.__new__(Terminal)
    terminal.fd = read_fd
    try:
        assert terminal._read_bracketed_paste().text == "partial →"
    finally:
        os.close(read_fd)
