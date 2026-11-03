"""Tests for input submission behavior during tool permission prompts."""

import asyncio
import pytest
import moka_code.ui.app as app_module
from moka_code.ui.app import chatTUI
from moka_code.ui.tui.events import normalize_key
from moka_code.ui.tui.msg_types import SysMsg
from moka_code.harness import events

from conftest import StubAgent, make_chunk_stream


class TestCommandMenuNavigation:
    def test_up_arrow_stays_in_command_menu(self):
        ui = chatTUI(StubAgent())
        ui.root = type("_Root", (), {"handle_input": lambda self, event: ui.input_component.handle_input(event)})()
        ui._original_handle_input = ui.root.handle_input

        ui.chat_history_panel.add_message("first")
        ui.chat_history_panel.add_message("second")
        ui.input_component.set_focused(True)
        ui.input_component.buffer.text = "/c"
        ui.input_component.buffer.cursor_pos = len("/c")
        ui.input_component._on_text_changed()
        ui._last_focus_id = "input"

        assert ui.input_component.command_completion is not None
        assert ui.input_component.command_completion.is_active is True

        selected_before = ui.input_component.command_completion.menu.selected_index

        handled = ui.handle_global_input(normalize_key('\x1b[A'))

        assert handled is True
        assert ui._last_focus_id == "input"
        assert ui.chat_history_panel.focused_message_index is None
        assert ui.input_component.command_completion.menu.selected_index != selected_before


class TestCommandWorker:
    def test_command_worker_dispatches_queued_commands(self, monkeypatch):
        ui = chatTUI(StubAgent())
        dispatched = []

        async def fake_handle_command(_ui, command):
            dispatched.append(command)
            ui.shutdown_event.set()

        monkeypatch.setattr(app_module, "handle_command", fake_handle_command)
        ui.on_command_submit("/help")
        asyncio.run(ui.command_worker())

        assert dispatched == ["/help"]


class TestPermissionForm:
    """2026-11-03: a call waiting for approval is asked in the ``ask`` form's
    selector above the input (Allow / Deny); its tool line waits unfolded,
    prefix in PERMISSION. The permission message type is gone."""

    @pytest.fixture(autouse=True)
    def _no_smoothing(self, monkeypatch):
        from moka_code import settings

        monkeypatch.setattr(settings.config, "ui_stream_smoothing", False)

    @staticmethod
    def _asking(responses):
        """An app inside a generation stopped at a permission request."""
        agent = StubAgent()
        agent.set_user_response = responses.append
        ui = chatTUI(agent)

        async def chat(_, attached=None, prompt=None):
            yield events.Start(message_id="a", role="assistant")
            yield events.ToolCall(id="t1", name="bash", args='{"command": "make"}')
            yield events.PermissionRequest(id="t1", name="bash", args='{"command": "make"}',
                                           prompt="?", auto=False)
            await asyncio.sleep(10)

        ui.agent.chat = chat
        return ui

    def _run(self, ui, answer):
        async def scenario():
            task = asyncio.create_task(
                ui._process_generation("go", ui.chat_history_panel.add_message("go")))
            await asyncio.sleep(0.05)
            line = ui.active_tool_messages["t1"]
            seen = (ui.question_form.title, ui.question_menu.items, line.tool_state())
            answer(ui)
            state = line.tool_state()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return seen, state

        return asyncio.run(scenario())

    def test_allow_runs_the_call(self):
        responses = []
        ui = self._asking(responses)
        seen, state = self._run(ui, lambda ui: ui.handle_global_input("\r"))
        assert seen == ("permission", ["1. Allow", "2. Deny"], "asking")
        assert responses == ["approve"] and state == "running"
        assert not any(m.type.name == "question" for m in ui.chat_history_panel.messages)

    def test_deny_and_a_typed_reason(self):
        responses = []

        def deny(ui):
            ui.handle_global_input("\x1b[B")
            ui.handle_global_input("\r")

        self._run(self._asking(responses), deny)

        def reason(ui):
            ui.input_component.update("use make -j4")
            ui.handle_global_input("\r")

        self._run(self._asking(responses), reason)
        assert responses == ["deny", "use make -j4"]

    def test_the_waiting_line_is_unfolded_in_the_permission_color(self):
        from moka_code.ui.chat_message import Message
        from moka_code.ui.tui.colors import theme
        from moka_code.ui.tui.msg_types import ToolCallMsg

        msg = Message("", msg_type=ToolCallMsg(), max_width=60)
        msg.tool_name, msg.tool_args = "edit", '{"path": "a.py", "search": "a", "replace": "b"}'
        msg.set_tool_status("asking")
        msg.rebuild_tool_display()
        assert msg.box.gutter_color == theme.PERMISSION
        assert msg.base_text.startswith(f"{theme.PERMISSION}edit")   # the name too
        assert len(msg.base_text.split("\n")) > 1          # the diff shows
        msg.set_tool_status("running")
        msg.rebuild_tool_display()
        assert msg.box.gutter_color == theme.MUTED
        assert len(msg.base_text.split("\n")) == 1
