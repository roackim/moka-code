"""Tests for the redesigned message selection and action mode line."""

from moka_code.ui.app import chatTUI
from moka_code.ui.tui.chat_screen import ChatScreen
from moka_code.ui.tui.msg_types import SysMsg, ToolCallMsg, UserMsg


class Stub:
    endpoint = None
    workspace = "."
    role = None

    def list_files_and_folders(self):
        return []


def _ui_with_screen():
    ui = chatTUI(Stub())
    screen = ChatScreen(ui.chat_history_panel, ui.input_box, ui._focus_scope,
                        ui.status_bar, ui.action_bar)
    ui.chat_screen = screen
    return ui, screen


def test_sysmsg_is_routed_to_activity_not_transcript():
    ui, _ = _ui_with_screen()
    ui.chat_history_panel.add_message("Config reloaded.", msg_type=SysMsg())

    assert ui.chat_history_panel.messages == []
    assert any("Config reloaded." in line for line in ui.activity_panel.lines)
    assert ui.action_bar.hint == "Config reloaded."


def test_action_line_expands_only_when_selected():
    ui, _ = _ui_with_screen()
    ui.chat_history_panel.add_message("hello", msg_type=UserMsg()).finalize()

    assert ui.action_bar.expanded is False

    ui.chat_history_panel.set_focused_message(0)
    assert ui.action_bar.expanded is True
    assert [item.key for item in ui.action_bar.actions] == ["c"]
    assert ui.action_bar.prefix == ""
    # The input component is never swapped out.
    assert ui.input_box.child is ui.input_component

    ui._last_focus_id = "history"  # Esc leaves focus in history
    ui.chat_history_panel.clear_focus()
    assert ui.action_bar.expanded is False


def test_input_hints_while_input_focused():
    ui, _ = _ui_with_screen()
    ui._set_app_focus("input")

    assert ui.action_bar.expanded is True
    assert ui.action_bar.actions == []
    assert "[/] command" in ui.action_bar.hint
    assert "[@] file  [!] prompt  [↑↓] move" in ui.action_bar.hint
    assert "move" in ui.action_bar.hint

    # @ works mid-text, so the hint stays visible while typing.
    ui.input_component.update("hello @no")
    assert ui.action_bar.expanded is True
    assert "[/] command" in ui.action_bar.hint


def test_action_strip_actions_track_message_type():
    ui, _ = _ui_with_screen()
    tool = ui.chat_history_panel.add_message("read(file)", msg_type=ToolCallMsg())
    tool.finalize()

    ui.chat_history_panel.set_focused_message(0)
    assert [item.key for item in ui.action_bar.actions] == ["o", "c"]


def test_copy_feedback_flashes_hint_without_clearing_selection():
    ui, _ = _ui_with_screen()
    ui.chat_history_panel.add_message("hello", msg_type=UserMsg()).finalize()
    ui.chat_history_panel.set_focused_message(0)

    ui._copy_feedback()

    assert ui.action_bar.hint == "copied ✓"
    assert ui.chat_history_panel.focused_message_index == 0
    assert ui.action_bar.expanded is True


def test_copy_feedback_distinguishes_osc52_from_native():
    ui, _ = _ui_with_screen()
    ui.chat_history_panel.add_message("hello", msg_type=UserMsg()).finalize()
    ui.chat_history_panel.set_focused_message(0)

    ui._copy_feedback("OSC 52")
    assert ui.action_bar.hint == "sent via OSC 52"

    ui._copy_feedback("xclip")
    assert ui.action_bar.hint == "copied ✓"


def test_escape_clears_selection_and_hides_action_line():
    ui, _ = _ui_with_screen()
    ui.chat_history_panel.add_message("hello", msg_type=UserMsg()).finalize()
    ui._set_app_focus("history")
    ui.chat_history_panel.set_focused_message(0)

    assert ui.chat_history_panel.handle_input("\x1b") is True
    assert ui.chat_history_panel.focused_message_index is None
    assert ui.action_bar.expanded is False


def test_action_line_has_hint():
    ui, _ = _ui_with_screen()
    assert "esc back" in ui.action_bar.hint




def test_pgup_pgdown_move_between_user_messages():
    """PgUp/PgDn jump to the previous/next user message; PgUp from the input
    starts at the last one, PgDn past the last one returns to the input."""
    from moka_code.ui.tui.msg_types import AssistantMsg

    ui, _ = _ui_with_screen()
    panel = ui.chat_history_panel
    for text, msg_type in [("q1", UserMsg), ("a1", AssistantMsg), ("t", ToolCallMsg),
                           ("q2", UserMsg), ("a2", AssistantMsg)]:
        panel.add_message(text, msg_type=msg_type()).finalize()
    ui._set_app_focus("input")

    def press(key):
        if not ui.handle_global_input(key):
            panel.handle_input(key)

    press("\x1b[5~")
    assert (ui._last_focus_id, panel.focused_message_index) == ("history", 3)
    press("\x1b[5~")
    assert panel.focused_message_index == 0
    press("\x1b[5~")                                 # no earlier user message
    assert panel.focused_message_index == 0
    press("\x1b[6~")
    assert panel.focused_message_index == 3
    press("\x1b[6~")                                 # past the last: the input
    assert ui._last_focus_id == "input"
    assert panel.focused_message_index is None


def test_status_bar_values_open_their_picker_from_the_keyboard():
    """↓ from the input's last line focuses the status bar (one picker value
    inverted); ←/→ move between model, role and sandbox, Enter opens that
    picker, Esc returns to the input, other keys type into it."""
    ui, _ = _ui_with_screen()
    bar = ui.status_bar
    bar.set_template("{model}[:{effort}][  role {role}][  ctx {context}][  {sandbox}]")
    bar.set_values({"model": "m", "effort": "high", "role": "agent", "context": "1k",
                    "sandbox": "none"})
    ui._set_app_focus("input")

    assert ui.handle_global_input("\x1b[B") is True
    assert (ui._last_focus_id, bar.focused_value) == ("status", "model")
    assert ui._focus_scope.focused.handle_input("\x1b[C") is True
    assert bar.focused_value == "effort"
    ui._focus_scope.focused.handle_input("\x1b[C")
    assert bar.focused_value == "role"
    ui._focus_scope.focused.handle_input("\r")
    assert ui.command_queue.get_nowait() == "/role select"       # the picker, not the subcommands
    ui.handle_global_input("\x1b[B")
    bar.set_focused_value("role")
    ui._focus_scope.focused.handle_input("\x1b[C")
    ui._focus_scope.focused.handle_input("\x1b[C")          # context is skipped; stops at the end
    assert bar.focused_value == "sandbox"
    assert ui.action_bar.hint == "←→ field · [ENTER] open · [ESC] back"

    ui._focus_scope.focused.handle_input("\r")
    assert ui.command_queue.get_nowait() == "/sandbox"
    assert (ui._last_focus_id, bar.focused_value) == ("input", None)

    ui.handle_global_input("\x1b[B")
    ui._focus_scope.focused.handle_input("\x1b")
    assert ui._last_focus_id == "input"
    ui.handle_global_input("\x1b[B")
    ui._focus_scope.focused.handle_input("x")
    assert (ui._last_focus_id, ui.input_component.buffer.text) == ("input", "x")


def test_clicking_the_focused_status_value_opens_it():
    from moka_code.ui.tui.buffer import Buffer
    from moka_code.ui.tui.terminal import MouseEvent

    ui, _ = _ui_with_screen()
    bar = ui.status_bar
    bar.set_template("{model}  {role}")
    bar.set_values({"model": "mm", "role": "rr"})
    bar.set_layout(0, 10, 20, 1)
    bar.render(Buffer(20, 11))                                   # records where values are
    ui._set_app_focus("input")

    ui.handle_global_input(MouseEvent(5, 10, 0, True))           # first click: focus role
    assert (ui._last_focus_id, bar.focused_value) == ("status", "role")
    assert ui.command_queue.empty()
    ui.handle_global_input(MouseEvent(5, 10, 0, False))
    ui.handle_global_input(MouseEvent(5, 10, 0, True))           # again: like Enter
    assert ui.command_queue.get_nowait() == "/role select"
    assert ui._last_focus_id == "input"


def test_the_focused_status_value_is_drawn_inverted():
    from moka_code.ui.tui.buffer import Buffer
    from moka_code.ui.tui.components.bars import StatusBar

    bar = StatusBar(template="{model}  {role}")
    bar.set_values({"model": "mm", "role": "rr"})
    bar.set_layout(0, 0, 20, 1)
    bar.set_focused_value("role")
    buffer = Buffer(20, 1)
    bar.render(buffer)

    assert [cell.reverse for cell in buffer.cells[0][1:7]] == [
        False, False, False, False, True, True]
    assert bar.value_at(5) == "role" and bar.value_at(1) == "model"
