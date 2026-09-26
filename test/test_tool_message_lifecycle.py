"""Tests for tool-message lifecycle status glyphs.

Verifies the "no ✓ until finished" rule and that a loading spinner is shown
while a tool command is running.
"""

from pico_chat.ui.chat_message import (
    Message,
    _edit_counts,
    _parse_tool_args,
    _tool_summary,
)
from pico_chat.ui.tui.msg_types import ToolCallMsg
from pico_chat.ui.tui.colors import theme
from pico_chat.ui.tui.layout_utils import strip_ansi


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


# --- per-tool summary ------------------------------------------------------

def test_read_summary_uses_actual_line_count_once_done():
    assert _tool_summary("read", {"path": "src/main.py"}, None) == "src/main.py"
    assert _tool_summary(
        "read", {"path": "src/main.py"}, "one\ntwo\nthree\n"
    ) == "src/main.py 3 lines"


def test_write_summary_counts_content_lines():
    assert _tool_summary(
        "write", {"path": "a.py", "content": "x\ny\n"}, None
    ) == "a.py +2"


def test_edit_summary_reports_added_and_removed():
    args = {"path": "a.py", "search": "a\nb", "replace": "a\nc\nd"}
    assert _tool_summary("edit", args, None) == "a.py +2 -1"
    assert _edit_counts("a\nb", "a\nc\nd") == (2, 1)


def test_bash_summary_uses_first_command_line():
    assert _tool_summary("bash", {"command": "ls -la\necho hi"}, None) == "ls -la"


def test_parse_tool_args_salvages_partial_json():
    parsed = _parse_tool_args('{"path": "src/main.py", "content": "half')
    assert parsed["path"] == "src/main.py"


def test_parse_tool_args_bounds_huge_partial_content():
    """A large, unterminated content value must not be scanned into the summary.

    The path (early) is salvaged; the still-streaming content is skipped so the
    per-draft cost stays constant no matter how large the streamed body grows.
    """
    raw = '{"path": "README.de.md", "content": "' + ("x" * 200_000)
    parsed = _parse_tool_args(raw)
    assert parsed == {"path": "README.de.md"}


def test_parse_tool_args_full_parse_when_closed():
    raw = '{"path": "a.py", "content": "x\\ny"}'
    assert _parse_tool_args(raw) == {"path": "a.py", "content": "x\ny"}


def test_draft_never_full_parses_even_when_args_look_closed():
    """A draft must not scan the whole body even if the buffer ends like JSON.

    Streaming a `write` whose body contains ``"}"`` can make the partial args
    look closed at a chunk boundary; a full parse there would be O(n) per delta.
    """
    raw = '{"path": "README.de.md", "content": "' + ("x" * 100_000) + '"}'
    assert _parse_tool_args(raw, full=False) == {"path": "README.de.md"}
    # The completed call still parses fully.
    assert _parse_tool_args(raw)["content"] == "x" * 100_000


def test_collapsed_tool_line_leads_with_glyph_and_has_no_dot():
    from pico_chat.ui.tui.components.box import SPINNER_FRAMES

    msg = Message("", msg_type=ToolCallMsg(), max_width=60)
    msg.tool_name = "read"
    msg.tool_args = '{"path": "src/main.py"}'
    msg.rebuild_tool_display()

    plain = strip_ansi(msg.get_formatted())
    assert plain[0] in SPINNER_FRAMES
    assert "read" in plain and "src/main.py" in plain
    assert "·" not in plain


def test_focused_draft_shows_raw_stream():
    from pico_chat.ui.tui.msg_types import ToolDraftMsg

    msg = Message("", msg_type=ToolDraftMsg(), max_width=80)
    msg.tool_name = "write"
    msg.tool_args = '{"path": "README.de.md", "content": "hello world'
    msg.tool_status = "drafting"
    msg.set_focused(True)
    msg.rebuild_tool_display()

    plain = strip_ansi(msg.get_formatted())
    assert "draft:" in plain
    assert "hello world" in plain


def test_write_draft_counts_streamed_lines():
    """A streaming write shows its line count so far, not just the path."""
    from pico_chat.ui.tui.msg_types import ToolDraftMsg

    msg = Message("", msg_type=ToolDraftMsg(), max_width=80)
    msg.tool_name = "write"
    msg.tool_args = '{"path": "a.py", "content": "one\\ntwo\\nthr'
    msg.tool_status = "drafting"
    msg.rebuild_tool_display()

    assert "a.py +2" in strip_ansi(msg.get_formatted())
