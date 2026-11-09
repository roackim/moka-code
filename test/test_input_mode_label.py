"""Alternative input modes label themselves on the action line.

One input component serves the composer, an edited sent message, and a
one-line prompt from a command (renaming a session). The mode name is drawn
italic at the left of the action line above the input, in the input's own
color, opposite the right-aligned key hints; the input's border stays a clean
rule.
"""

from moka_code import settings
from moka_code.ui.app import chatTUI
from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.colors import theme
from moka_code.ui.tui.msg_types import UserMsg

from conftest import StubAgent


def _ui(monkeypatch):
    monkeypatch.setattr(settings.config, "ui_stream_smoothing", False)
    ui = chatTUI(StubAgent())
    ui._set_app_focus("input")      # expands the action line, as at startup
    return ui


def _action_cells(ui, width=70):
    """The action bar's content-row cells (mode label + key hints)."""
    height = ui.action_bar.get_preferred_height(width)
    ui.action_bar.set_layout(0, 0, width, height)
    buffer = Buffer(width, max(1, height))
    ui.action_bar.render(buffer)
    return buffer.cells[height - 1]


def _action_line(ui, width=70):
    return "".join(cell.char for cell in _action_cells(ui, width))


def _top_rule(ui, width=60):
    """The input box's top border: it must not carry the mode label."""
    ui.input_box.set_layout(0, 0, width, 3)
    buffer = Buffer(width, 3)
    ui.input_box.render(buffer)
    return "".join(cell.char for cell in buffer.cells[0])


def test_editing_a_sent_message_labels_the_action_line(monkeypatch):
    ui = _ui(monkeypatch)
    ui.agent.history = [{"id": "u0", "role": "user", "content": "one"}]
    message = ui.chat_history_panel.add_message(
        "one", msg_type=UserMsg(), harness_message_ids=["u0"])
    assert "Editing message" not in _action_line(ui)

    ui.handle_edit_action(message)

    line = _action_line(ui)
    assert ui._input_mode_label() == "Editing message"
    assert line.startswith(" Editing message")
    assert "[ENTER] send" in line and line.rstrip().endswith("[ESC] cancel")
    assert "Editing message" not in _top_rule(ui)

    ui.cancel_edit()

    assert ui._input_mode_label() == ""
    assert "Editing message" not in _action_line(ui)


def test_a_command_prompt_labels_the_action_line(monkeypatch):
    ui = _ui(monkeypatch)
    assert "Renaming session" not in _action_line(ui)

    ui.prompt_text("session name…", "old name", lambda text: None,
                   label="Renaming session")

    line = _action_line(ui)
    assert ui._input_mode_label() == "Renaming session"
    assert line.startswith(" Renaming session")
    assert "[ENTER] save" in line and line.rstrip().endswith("[ESC] cancel")
    assert "Renaming session" not in _top_rule(ui)

    ui._end_text_prompt(submit=False)

    assert ui._input_mode_label() == ""
    assert "Renaming session" not in _action_line(ui)


def test_the_mode_label_uses_the_input_fields_color(monkeypatch):
    """The label is the same color as the input's bars/prefix, so it reads as
    a mode of that field; losing the input focus mutes both together."""
    ui = _ui(monkeypatch)
    ui.agent.history = [{"id": "u0", "role": "user", "content": "one"}]
    message = ui.chat_history_panel.add_message(
        "one", msg_type=UserMsg(), harness_message_ids=["u0"])
    ui.handle_edit_action(message)

    cells = _action_cells(ui)
    line = "".join(cell.char for cell in cells)
    start = line.index("Editing message")
    assert cells[start].italic
    assert cells[start].fg == ui._input_box_fg() == theme.USER

    ui._set_input_focus(False)          # terminal lost focus
    cells = _action_cells(ui)
    line = "".join(cell.char for cell in cells)
    start = line.index("Editing message")
    assert cells[start].fg == ui._input_box_fg() == theme.MUTED
