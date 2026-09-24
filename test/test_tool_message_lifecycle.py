"""Tests for tool-message lifecycle status glyphs.

Verifies the "no ✓ until finished" rule and that a loading spinner is shown
while a tool command is running.
"""

from pico_chat.ui.chat_message import Message
from pico_chat.ui.tui.msg_types import ToolCallMsg
from pico_chat.ui.tui.colors import theme


class _StubBashTool:
    """Minimal bash-tool wrapper exposing cancel_active_run()."""
    def __init__(self, toolset):
        self.toolset = toolset

    def cancel_active_run(self) -> bool:
        return self.toolset.cancel_active_run()


def _tool(msg_type=None, status=None, finalized=False):
    msg = Message("", msg_type=msg_type or ToolCallMsg(), max_width=40)
    msg.tool_name = "bash"
    msg.tool_args = '{"command": "ls"}'
    if status is not None:
        msg.tool_status = status
    if finalized:
        msg.finalize()
    return msg


def test_spinner_while_not_finalized():
    msg = _tool(status="approved | executing", finalized=False)
    glyph, color = msg.status_glyph()
    assert glyph in ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
    assert color == theme.MUTED


def test_no_done_mark_while_running():
    """A running command never reports a ✓ glyph."""
    msg = _tool(status="approved | executing", finalized=False)
    glyph, _ = msg.status_glyph()
    assert glyph not in ("✓", "✗")


def test_check_mark_only_after_finalized_completed():
    msg = _tool(status="approved | completed", finalized=True)
    glyph, color = msg.status_glyph()
    assert glyph == "✓"
    assert color == theme.SUCCESS


def test_error_mark_after_finalized():
    msg = _tool(status="error", finalized=True)
    glyph, color = msg.status_glyph()
    assert glyph == "✗"
    assert color == theme.ERROR


def test_denied_mark_after_finalized():
    msg = _tool(status="denied", finalized=True)
    glyph, color = msg.status_glyph()
    assert glyph == "✗"
    assert color == theme.ERROR


def test_autoapproved_not_terminal():
    """auto-approved is a pending permission state, not a done state."""
    msg = _tool(status="auto-approved", finalized=False)
    glyph, _ = msg.status_glyph()
    assert glyph not in ("✓", "✗")


def test_advance_spinner_rebuilds_tool_display():
    """Spinner animates because advance_spinner rebuilds the tool display."""
    msg = _tool(status="approved | executing", finalized=False)
    before = msg.get_formatted()
    msg.advance_spinner()
    after = msg.get_formatted()
    assert before != after


def test_spinner_cadence_is_decoupled_from_render_fps(monkeypatch):
    """Tick events fire every frame; the glyph only advances at ui.spinner_fps."""
    from pico_chat import pico_cfg
    from pico_chat.ui.chat_history_panel import ChatHistoryPanel
    from pico_chat.ui.tui.events import TickEvent
    from pico_chat.ui.tui.msg_types import ThinkingMsg

    monkeypatch.setattr(pico_cfg.config, "ui_spinner_fps", 10)
    panel = ChatHistoryPanel()
    msg = panel.add_message("", msg_type=ThinkingMsg())

    panel.handle_input(TickEvent(0.0))
    first = msg.spinner_frame
    panel.handle_input(TickEvent(0.05))  # still inside the 100ms gate
    assert msg.spinner_frame == first
    panel.handle_input(TickEvent(0.11))  # gate elapsed: advances
    assert msg.spinner_frame != first


def test_tool_message_exposes_only_non_destructive_actions():
    """Tool messages expose output/copy; state-changing actions are commands."""
    from pico_chat.ui.tui.msg_types import MsgAction

    running = _tool(status="approved | executing", finalized=False)
    actions = running.get_active_actions()

    assert MsgAction.OUTPUT in actions
    assert MsgAction.COPY in actions
    assert all(a in (MsgAction.OUTPUT, MsgAction.COPY) for a in actions)


def test_harness_stop_tool_kills_bash(tmp_path):
    """Harness.stop_tool() terminates the active command."""
    import asyncio
    from pico_chat.harness.harness import Harness
    from pico_chat.harness.tools import MinimalToolset

    h = Harness.__new__(Harness)
    ts = MinimalToolset(tmp_path)
    bash_tool = _StubBashTool(ts)
    h.tools_map = {"bash": bash_tool}

    async def scenario():
        task = asyncio.create_task(ts.run_async("sleep 30"))
        await asyncio.sleep(0.2)
        assert h.stop_tool() is True
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.TimeoutError:
            raise AssertionError("stop_tool did not terminate the command")

    asyncio.run(scenario())


def test_bash_tool_schema_name_is_bash():
    """The LLM-facing tool name is 'bash'."""
    from pico_chat.harness.tools import create_toolset
    import tempfile

    tmp = tempfile.mkdtemp()
    tool = create_toolset(tmp)["bash"]
    assert tool.get_schema()["function"]["name"] == "bash"


def test_gutter_prefix_is_a_colored_bar_per_type():
    """Every message uses the ``▌`` bar; the color encodes the type."""
    from pico_chat.ui.tui.msg_types import AskPermissionMsg
    from pico_chat.ui.tui.colors import theme

    ask = Message("", msg_type=AskPermissionMsg(), max_width=40)
    ask.tool_name = "bash"
    assert ask.box.gutter == "▌"
    assert ask.box.gutter_color == theme.PERMISSION

    tool = _tool(status="approved | executing", finalized=False)
    assert tool.box.gutter == "▌"
    assert tool.box.gutter_color == theme.TOOL