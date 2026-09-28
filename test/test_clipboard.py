"""Clipboard: native helpers, OSC 52 fallback, paste reading, copy actions."""

import base64

import pytest

from moka_code.ui import clipboard as ui_clipboard
from moka_code.ui.chat_action_handlers import ChatActionHandlers
from moka_code.ui.tui.msg_types import SysMsgError, UserMsg

OSC52_PREFIX = "\x1b]52;c;"
OSC52_SUFFIX = "\x07"


@pytest.fixture
def terminal_env(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")


@pytest.fixture
def no_native_tools(monkeypatch):
    def _raise(*args, **kwargs):
        raise FileNotFoundError("not installed")

    monkeypatch.setattr("moka_code.ui.clipboard.subprocess.run", _raise)


# ---------------------------------------------------------------------------
# ui.clipboard strategy
# ---------------------------------------------------------------------------

def test_osc52_fallback_emits_encoded_sequence(no_native_tools, terminal_env, capfd):
    assert ui_clipboard.copy_to_clipboard("hello") == "OSC 52"

    out, _ = capfd.readouterr()
    encoded = base64.b64encode(b"hello").decode("ascii")
    assert out == f"{OSC52_PREFIX}{encoded}{OSC52_SUFFIX}"


def test_native_tool_takes_priority(monkeypatch, terminal_env, capfd):
    calls = []

    def _run(command, **kwargs):
        calls.append((command, kwargs.get("input")))

    monkeypatch.setattr("moka_code.ui.clipboard.subprocess.run", _run)

    assert ui_clipboard.copy_to_clipboard("hello") == "xclip"
    assert calls == [(("xclip", "-selection", "clipboard"), b"hello")]
    assert capfd.readouterr().out == ""


def test_falls_through_to_wayland_when_x11_missing(monkeypatch, terminal_env):
    calls = []

    def _run(command, **kwargs):
        calls.append(command)
        if command[0] in ("xclip", "xsel"):
            raise FileNotFoundError
        return None

    monkeypatch.setattr("moka_code.ui.clipboard.subprocess.run", _run)

    assert ui_clipboard.copy_to_clipboard("hello") == "wl-copy"
    assert [c[0] for c in calls] == ["xclip", "xsel", "wl-copy"]


def test_empty_text_copies_nothing(monkeypatch, terminal_env, capfd):
    monkeypatch.setattr(
        "moka_code.ui.clipboard.subprocess.run",
        lambda *a, **k: pytest.fail("should not run a clipboard helper"),
    )

    assert ui_clipboard.copy_to_clipboard("") is None
    assert capfd.readouterr().out == ""


def test_no_method_returns_none(monkeypatch, capfd):
    monkeypatch.setenv("TERM", "dumb")

    def _raise(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr("moka_code.ui.clipboard.subprocess.run", _raise)

    assert ui_clipboard.copy_to_clipboard("hello") is None
    assert capfd.readouterr().out == ""


# ---------------------------------------------------------------------------
# OSC 52 primitive
# ---------------------------------------------------------------------------

def test_osc52_truncation_stays_valid_base64(monkeypatch, terminal_env, capfd):
    monkeypatch.setattr(ui_clipboard, "OSC52_MAX_BYTES", 8)

    assert ui_clipboard._osc52_copy("abcdefghijklmnop") is True

    out, _ = capfd.readouterr()
    payload = out[len(OSC52_PREFIX):-len(OSC52_SUFFIX)]
    assert len(payload) == 8
    assert base64.b64decode(payload) == b"abcdef"


def test_dumb_terminal_does_not_emit(monkeypatch, capfd):
    monkeypatch.setenv("TERM", "dumb")
    assert ui_clipboard._osc52_copy("hello") is False
    assert capfd.readouterr().out == ""


# ---------------------------------------------------------------------------
# read_clipboard (Ctrl+V)
# ---------------------------------------------------------------------------

def _fake_helpers(monkeypatch, outputs):
    """``outputs`` maps a command tuple to its stdout; others are missing."""
    calls = []

    class _Result:
        def __init__(self, stdout):
            self.stdout = stdout

    def _run(command, **kwargs):
        calls.append(tuple(command))
        if tuple(command) not in outputs:
            raise FileNotFoundError
        return _Result(outputs[tuple(command)])

    monkeypatch.setattr("moka_code.ui.clipboard.subprocess.run", _run)
    return calls


def test_read_prefers_image_on_wayland(monkeypatch):
    _fake_helpers(monkeypatch, {
        ("wl-paste", "--list-types"): b"text/plain\nimage/jpeg\nimage/png\n",
        ("wl-paste", "--no-newline", "--type", "image/png"): b"PNGDATA",
    })
    assert ui_clipboard.read_clipboard() == b"PNGDATA"


def test_read_text_on_x11(monkeypatch):
    calls = _fake_helpers(monkeypatch, {
        ("xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"): b"UTF8_STRING\nTEXT\n",
        ("xclip", "-selection", "clipboard", "-o"): "héllo\nworld".encode(),
    })
    assert ui_clipboard.read_clipboard() == "héllo\nworld"
    assert calls[0][0] == "wl-paste"


def test_read_empty_clipboard(monkeypatch):
    _fake_helpers(monkeypatch, {("wl-paste", "--list-types"): b"",
                                ("wl-paste", "--no-newline"): b""})
    assert ui_clipboard.read_clipboard() is None


def test_read_without_helpers(monkeypatch):
    _fake_helpers(monkeypatch, {})
    assert ui_clipboard.read_clipboard() is None


# ---------------------------------------------------------------------------
# handle_copy_action
# ---------------------------------------------------------------------------

class _Message:
    type = UserMsg()
    tool_name = None
    base_text = "hello"


class _Panel:
    def __init__(self):
        self.messages = []

    def add_message(self, text, msg_type=None):
        self.messages.append((text, msg_type))


class _UI(ChatActionHandlers):
    def __init__(self):
        self.chat_history_panel = _Panel()
        self.copy_feedback_count = 0
        self.last_method = None

    def _copy_feedback(self, method=None):
        self.copy_feedback_count += 1
        self.last_method = method


def test_copy_action_reports_success(monkeypatch):
    monkeypatch.setattr(
        "moka_code.ui.chat_action_handlers.copy_to_clipboard",
        lambda text: "OSC 52",
    )
    ui = _UI()

    ui.handle_copy_action(_Message())

    assert ui.copy_feedback_count == 1
    assert ui.last_method == "OSC 52"
    assert ui.chat_history_panel.messages == []


def test_copy_action_reports_failure(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "moka_code.ui.chat_action_handlers.copy_to_clipboard",
        lambda text: seen.append(text) or None,
    )
    ui = _UI()

    ui.handle_copy_action(_Message())

    assert ui.copy_feedback_count == 0
    assert seen and seen[0] == "hello\n"
    assert len(ui.chat_history_panel.messages) == 1
    text, msg_type = ui.chat_history_panel.messages[0]
    assert "no clipboard method found" in text
    assert isinstance(msg_type, SysMsgError)
