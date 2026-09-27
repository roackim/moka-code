"""Tests for tool lines: format, lifecycle states, and live labels.

Tool lines carry no status glyph: the name color and a trailing word (only when
it is news) describe the state, and a sign in the metric means a file changed.
"""

import json

import pytest

import moka_chat.ui.chat_message as chat_message
from moka_chat.ui.chat_message import (
    Message,
    _edit_counts,
    _parse_tool_args,
    _tool_summary,
)
from moka_chat.ui.tui.msg_types import AskPermissionMsg, ToolCallMsg
from moka_chat.ui.tui.colors import theme
from moka_chat.ui.tui.layout_utils import strip_ansi

GLYPHS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏✓✗⏹?"


class _StubBashTool:
    """Minimal bash-tool wrapper exposing cancel_active_run()."""
    def __init__(self, toolset):
        self.toolset = toolset

    def cancel_active_run(self) -> bool:
        return self.toolset.cancel_active_run()


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(chat_message.time, "perf_counter", lambda: now[0])
    return now


def _tool(msg_type=None, status=None, finalized=False, name="bash",
          args=None, output=None, width=60, focused=False):
    msg = Message("", msg_type=msg_type or ToolCallMsg(), max_width=width)
    msg.tool_name = name
    msg.tool_args = json.dumps(args if args is not None else {"command": "ls"})
    msg.tool_output = output
    if status is not None:
        msg.set_tool_status(status)
    if focused:
        msg.set_focused(True)
    if finalized:
        msg.finalize()
    msg.rebuild_tool_display()
    return msg


def _lines(msg):
    return strip_ansi(msg.get_formatted()).splitlines()


def test_tool_line_has_no_status_glyph():
    for status, finalized in (("running", False), ("completed", True),
                              ("error", True), ("denied", True)):
        header = _lines(_tool(status=status, finalized=finalized, output="x"))[0]
        assert not any(g in header for g in GLYPHS), header


def test_running_tool_shows_elapsed_time_only_when_not_instant(clock):
    msg = _tool(status="running")
    assert _lines(msg)[0].rstrip() == "bash ls"
    clock[0] += 2.4
    msg.tick()
    assert _lines(msg)[0].endswith("running 2s")


def test_completed_line_has_no_trailing_word():
    msg = _tool(status="completed", finalized=True, output="a\n[exit:0]")
    assert _lines(msg)[0].rstrip() == "bash ls"


def test_bash_failure_shows_exit_code():
    msg = _tool(status="completed", finalized=True, output="[stderr]\nboom\n[exit:2]")
    assert _lines(msg)[0].endswith("exit 2")


def test_error_reason_is_on_the_collapsed_line_and_name_turns_red():
    msg = _tool(status="error", finalized=True, name="edit",
                args={"path": "a.py", "search": "x", "replace": "y"},
                output="Search block not found in a.py\ndetails")
    assert _lines(msg)[0].endswith("Search block not found in a.py")
    assert msg.get_formatted().startswith(f"{theme.ERROR}edit")


def test_denied_line():
    msg = _tool(status="denied", finalized=True, output="User denied")
    assert _lines(msg)[0].endswith("denied")


def test_permission_ask_names_its_keys():
    msg = _tool(msg_type=AskPermissionMsg())
    assert _lines(msg)[0].endswith("approve? a/x")


def test_panel_tick_refreshes_live_labels_at_a_steady_cadence(clock):
    from moka_chat.ui.chat_history_panel import ChatHistoryPanel
    from moka_chat.ui.tui.events import TickEvent

    panel = ChatHistoryPanel()
    msg = panel.add_message("", msg_type=ToolCallMsg())
    msg.tool_name, msg.tool_args = "bash", '{"command": "sleep 5"}'
    msg.set_tool_status("running")
    msg.rebuild_tool_display()

    clock[0] += 3.0
    panel.handle_input(TickEvent(10.0))
    assert _lines(msg)[0].endswith("running 3s")
    clock[0] += 1.0
    panel.handle_input(TickEvent(10.1))  # inside the refresh interval
    assert _lines(msg)[0].endswith("running 3s")
    panel.handle_input(TickEvent(10.3))
    assert _lines(msg)[0].endswith("running 4s")


def test_tool_message_exposes_only_non_destructive_actions():
    """Tool messages expose output/copy; state-changing actions are commands."""
    from moka_chat.ui.tui.msg_types import MsgAction

    running = _tool(status="approved | executing", finalized=False)
    actions = running.get_active_actions()

    assert MsgAction.OUTPUT in actions
    assert MsgAction.COPY in actions
    assert all(a in (MsgAction.OUTPUT, MsgAction.COPY) for a in actions)


def test_harness_stop_tool_kills_bash(tmp_path):
    """Harness.stop_tool() terminates the active command."""
    import asyncio
    from moka_chat.harness.harness import Harness
    from moka_chat.harness.tools import MinimalToolset

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
    from moka_chat.harness.tools import create_toolset
    import tempfile

    tmp = tempfile.mkdtemp()
    tool = create_toolset(tmp)["bash"]
    assert tool.get_schema()["function"]["name"] == "bash"


def test_tool_lines_use_a_muted_bar():
    """Tool lines get a muted ``▌``; an ask keeps its permission color."""
    from moka_chat.ui.tui.msg_types import AskPermissionMsg, AssistantMsg
    from moka_chat.ui.tui.colors import theme

    ask = Message("", msg_type=AskPermissionMsg(), max_width=40)
    ask.tool_name = "bash"
    assert ask.box.gutter == "▌"
    assert ask.box.gutter_color == theme.PERMISSION

    tool = _tool(status="running", finalized=False)
    assert tool.box.gutter == "▌"
    assert tool.box.gutter_color == theme.MUTED

    assert Message("hi", msg_type=AssistantMsg()).box.gutter == "▌"


# --- per-tool summary ------------------------------------------------------

def test_read_summary_uses_actual_line_count_once_done():
    assert _tool_summary("read", {"path": "src/main.py"}, None) == "src/main.py"
    assert _tool_summary(
        "read", {"path": "src/main.py"}, "one\ntwo\nthree\n"
    ) == "src/main.py 3 lines"
    assert _tool_summary("read", {"path": "a.py"}, "one") == "a.py 1 line"


def test_read_summary_shows_the_range():
    args = {"path": "a.py", "offset": 9, "limit": 50}
    assert _tool_summary("read", args, None) == "a.py:10-59"


def test_write_summary_counts_content_lines():
    assert _tool_summary(
        "write", {"path": "a.py", "content": "x\ny\n"}, None
    ) == "a.py +2 lines"


def test_edit_summary_reports_added_and_removed():
    args = {"path": "a.py", "search": "a\nb", "replace": "a\nc\nd"}
    assert _tool_summary("edit", args, None) == "a.py +2 lines −1 line"
    assert _edit_counts("a\nb", "a\nc\nd") == (2, 1)


def test_each_side_is_colored_with_its_own_unit():
    from moka_chat.ui.chat_message import _line_metric

    metric = _line_metric(added=2, removed=2)
    assert metric == (f"{theme.SUCCESS}+2 lines{theme.reset()} "
                      f"{theme.ERROR}−2 lines{theme.reset()}")


def test_edit_summary_shows_only_nonzero_sides_and_singular():
    add_one = {"path": "a.py", "search": "a", "replace": "a\nb"}
    assert _tool_summary("edit", add_one, None) == "a.py +1 line"
    remove_two = {"path": "a.py", "search": "a\nb\nc", "replace": "a"}
    assert _tool_summary("edit", remove_two, None) == "a.py −2 lines"


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


def test_tool_line_uses_single_spaces_and_mutes_the_target():
    read = _tool(name="read", args={"path": "src/main.py"}, status="completed",
                 finalized=True, output="a\nb")
    assert _lines(read)[0] == "read src/main.py 2 lines"
    assert f"{theme.MUTED}src/main.py" in read.get_formatted()


def test_long_path_is_shortened_from_the_left_keeping_the_metric():
    path = "moka_chat/ui/commands/" + "deep/" * 10 + "models.py"
    msg = _tool(name="write", args={"path": path, "content": "a\nb\n"},
                status="completed", finalized=True, output="ok", width=50)
    header = _lines(msg)[0]
    assert "…" in header and header.endswith("models.py +2 lines")
    assert len(header) <= 50


def test_focused_edit_shows_a_diff_keeping_indentation():
    args = {"path": "a.py", "search": "def a():\n    return 1\n",
            "replace": "def a():\n    x = 2\n    return x\n"}
    msg = _tool(name="edit", args=args, status="completed", finalized=True,
                output="ok", focused=True)
    body = _lines(msg)[1:]
    assert body == ["  def a():", "-     return 1", "+     x = 2", "+     return x"]


def test_expanded_diff_lines_are_colored_whole():
    args = {"path": "a.py", "search": "keep\nold", "replace": "keep\nnew"}
    msg = _tool(name="edit", args=args, status="completed", finalized=True,
                output="ok", focused=True)
    body = msg.get_formatted().splitlines()[1:]
    assert body[1] == f"{theme.ERROR}- old{theme.reset()}"
    assert body[2] == f"{theme.SUCCESS}+ new{theme.reset()}"
    # Context lines keep only a muted marker.
    assert body[0] == f"{theme.MUTED}  {theme.reset()}keep"


def test_focused_write_shows_a_capped_head():
    content = "".join(f"line {i}\n" for i in range(30))
    msg = _tool(name="write", args={"path": "a.py", "content": content},
                status="completed", finalized=True, output="ok", focused=True)
    body = _lines(msg)[1:]
    assert body[0] == "+ line 0"
    assert body[-1] == "… +10 more lines"
    assert len(body) == 21


def test_focused_bash_shows_the_full_command_wrapped_not_repeated():
    msg = _tool(args={"command": "cd x\nmake test"}, status="completed",
                finalized=True, output="[exit:0]", focused=True)
    assert _lines(msg) == ["bash", "$ cd x", "  make test"]
    long = _tool(args={"command": "echo " + "a" * 30}, status="completed",
                 finalized=True, output="[exit:0]", focused=True, width=20)
    assert _lines(long) == ["bash", "$ echo aaaaaaaaaaaaa", "  aaaaaaaaaaaaaaaaa"]


def test_compact_line_has_no_body():
    msg = _tool(args={"command": "cd x\nmake"}, status="completed",
                finalized=True, output="[exit:0]")
    assert len(_lines(msg)) == 1


def test_toggled_output_is_capped_head_and_tail():
    output = "[stdout]\n" + "".join(f"out {i}\n" for i in range(100)) + "[exit:0]"
    msg = _tool(status="completed", finalized=True, output=output, focused=True)
    msg.show_output = True
    msg.rebuild_tool_display()
    out = _lines(msg)[2:]
    assert out[0] == "out 0"
    assert "… 70 more lines …" in out
    assert out[-1] == "out 99"
    assert "[exit:0]" not in out


def test_running_command_shows_its_last_output_lines():
    msg = _tool(status="running")
    msg.append_live_output("one\ntwo\nthr")
    msg.append_live_output("ee\nfour\nfive\nsix\n")
    assert _lines(msg)[1:] == ["two", "three", "four", "five", "six"]
    msg.set_tool_status("completed")
    msg.tool_output = "[exit:0]"
    msg.finalize()
    assert len(_lines(msg)) == 1


def test_write_draft_grows_live_in_the_final_format():
    """A streaming write is the tool line itself, counting lines so far."""
    msg = Message("", msg_type=ToolCallMsg(), max_width=80)
    msg.tool_name = "write"
    msg.tool_args = '{"path": "a.py", "content": "one\\ntwo\\nthr'
    msg.set_tool_status("drafting")
    msg.rebuild_tool_display()

    assert _lines(msg) == ["write a.py +2 lines"]


def test_approving_turns_the_ask_into_a_running_tool_line():
    from conftest import StubAgent
    from moka_chat.ui.app import chatTUI

    agent = StubAgent()
    responses = []
    agent.set_user_response = responses.append
    ui = chatTUI(agent)
    ask = ui.chat_history_panel.add_message("", msg_type=AskPermissionMsg())
    ask.tool_name, ask.tool_args = "bash", '{"command": "make"}'
    ask.rebuild_tool_display()
    ui.active_tool_messages["t1"] = ask

    ui.handle_allow_action(ask)

    running = ui.active_tool_messages["t1"]
    assert responses == ["approve"]
    assert isinstance(running.type, ToolCallMsg)
    assert running in ui.chat_history_panel.messages and ask not in ui.chat_history_panel.messages
    assert running.tool_state() == "running"
    assert "approve?" not in strip_ansi(running.get_formatted())


def test_focusing_a_permission_prompt_reveals_what_it_changes():
    args = {"path": "a.py", "search": "a", "replace": "b"}
    ask = _tool(msg_type=AskPermissionMsg(), name="edit", args=args)
    assert len(_lines(ask)) == 1
    ask.set_focused(True)
    assert _lines(ask)[1:] == ["- a", "+ b"]
    ask.set_focused(False)
    assert len(_lines(ask)) == 1
