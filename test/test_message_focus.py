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
    assert "[@] file" in ui.action_bar.hint
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


