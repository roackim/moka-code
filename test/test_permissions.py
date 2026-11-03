"""Tests for the tool approval gate and the harness permission flow.

The engine is gone: a role maps each tool to ``no`` / ``ask`` / ``yes`` and the
gate turns that into ``deny`` / ``ask`` / ``allow``.  These tests cover the
gate, the prompt text, and the ask/deny/allow paths through the harness.
"""
import asyncio
import json

import pytest

from moka_code.harness import events as harness_events
from moka_code.harness.harness import Harness
from moka_code.harness.llm_status import AgentState
from moka_code.harness.permissions import PermissionGate
from moka_code.harness.roles import Role

from conftest import NoopDebugStream, StubReadTool, run_harness_tool_call


def test_run_permission_prompt_preserves_full_command():
    command = "printf " + "x" * 120

    prompt = PermissionGate.build_prompt("bash", {"command": command})

    assert command in prompt


def test_gate_denies_disabled_and_unknown_tools():
    gate = PermissionGate(role=Role(name="r", tools={"read": "yes"}))

    assert gate.check("read", {}) == "allow"
    assert gate.check("write", {}) == "deny"
    assert gate.check("bash", {"command": "ls"}) == "deny"


def test_gate_returns_ask_for_ask_tools():
    gate = PermissionGate(role=Role(name="r", tools={"edit": "ask", "bash": "ask"}))

    assert gate.check("edit", {}) == "ask"
    assert gate.check("bash", {}) == "ask"


def test_system_message_is_the_role_prompt():
    harness = Harness.__new__(Harness)

    harness.role = Role(name="r", prompt="You are a reviewer.")
    assert harness._system_messages() == [
        {"role": "system", "content": "You are a reviewer."}
    ]

    harness.role = Role(name="r", prompt="")
    assert harness._system_messages() == []


def _role_harness(name="agent", prompt="", tools=()):
    harness = Harness.__new__(Harness)
    harness.history = []
    harness.role = Role(name=name, prompt=prompt)
    harness.tools_map = {tool: object() for tool in tools}
    return harness


def test_role_notice_says_what_the_new_role_is_and_cancels_the_old_one():
    harness = _role_harness("agent", "You can use a computer.", ("read", "bash"))

    harness._record_role_change(Role(name="chat"))

    assert harness.history[0]["role"] == "system"
    assert harness.history[0]["content"] == "\n".join([
        "<system-reminder>",
        "Role changed: chat → agent. This notice is from moka, the harness, not from the user.",
        "Instructions and limits from earlier in this conversation no longer apply, "
        "and what was said earlier about your abilities may no longer hold.",
        "Your instructions now: You can use a computer.",
        "Tools available now: bash, read.",
        "</system-reminder>",
    ])


def test_role_notice_without_tools_forbids_tool_calls():
    harness = _role_harness("chat")

    harness._record_role_change(Role(name="agent"))

    content = harness.history[0]["content"]
    assert "There are no role instructions now." in content
    assert "No tools are available now: answer in text, do not attempt tool calls." in content


def _switch(harness, name):
    previous, harness.role = harness.role, Role(name=name)
    harness._record_role_change(previous)


def test_unsent_switches_collapse_into_one_notice_from_the_role_last_seen():
    harness = _role_harness("agent")
    _switch(harness, "chat")
    _switch(harness, "plan")

    assert len(harness.history) == 1
    assert "Role changed: agent → plan." in harness.history[0]["content"]


def test_unsent_switches_back_to_the_role_last_seen_leave_no_notice():
    harness = _role_harness("agent")
    _switch(harness, "chat")
    _switch(harness, "agent")

    assert harness.history == []


def test_a_sandbox_notice_in_between_still_collapses_role_notices():
    harness = _role_harness("agent")
    _switch(harness, "chat")
    harness._record_sandbox_change("bubblewrap")
    _switch(harness, "plan")

    notices = [e["content"] for e in harness.history]
    assert notices[0] == "[Sandbox: bubblewrap]"
    assert len(notices) == 2 and "Role changed: agent → plan." in notices[1]


def test_role_notice_replaces_a_legacy_notice():
    harness = _role_harness("agent")
    harness.history = [{"id": "s", "role": "system", "content": "[Role changed from agent to chat]"}]

    harness._record_role_change(Role(name="chat"))

    assert len(harness.history) == 1
    assert "Role changed: chat → agent." in harness.history[0]["content"]


def test_a_switch_after_a_message_adds_a_new_notice():
    harness = _role_harness("agent")
    _switch(harness, "chat")
    harness._add_message_to_history("user", "hello")
    _switch(harness, "agent")

    assert len(harness.history) == 3
    assert "Role changed: agent → chat." in harness.history[0]["content"]
    assert "Role changed: chat → agent." in harness.history[-1]["content"]


def _set_role_harness(role):
    harness = _role_harness(role.name, role.prompt)
    harness.debug_stream = NoopDebugStream()
    harness._permission_gate = PermissionGate(role=role)
    harness.role = role
    harness._rebuild_tools = lambda: None
    return harness


def test_reloading_a_changed_role_tells_the_model():
    harness = _set_role_harness(Role(name="agent", prompt="old"))

    harness.set_role(Role(name="agent", prompt="new"))

    assert "Role updated: agent." in harness.history[-1]["content"]
    assert "Your instructions now: new" in harness.history[-1]["content"]


def test_setting_the_same_role_adds_no_notice():
    harness = _set_role_harness(Role(name="agent", prompt="same"))

    harness.set_role(Role(name="agent", prompt="same"))

    assert harness.history == []


def _build_harness_stub(tmp_path, read_tool, role):
    harness = Harness.__new__(Harness)
    harness.debug_stream = NoopDebugStream()
    harness.state = AgentState.IDLE
    harness.history = []
    harness.workspace = str(tmp_path)
    harness.tools_map = {"read": read_tool}
    harness._permission_gate = PermissionGate(role=role)
    return harness


def _read_call(call_id="call_1"):
    return {
        "id": call_id,
        "function": {
            "name": "read",
            "arguments": json.dumps({"path": "test.txt"}),
        },
    }


class TestHarnessReadPermissionFlow:
    """The ask/allow/deny paths through ``_execute_tool_calls``."""

    def test_auto_deny_enforced_before_execution(self, tmp_path):
        role = Role(name="r", tools={"read": "no"})
        read_tool = StubReadTool("content")
        harness = _build_harness_stub(tmp_path, read_tool, role)

        events, messages = run_harness_tool_call(harness, _read_call())

        assert isinstance(events[0], harness_events.ToolCall)
        assert isinstance(events[1], harness_events.PermissionRequest)
        assert isinstance(events[2], harness_events.ToolResult)
        assert events[1].auto is True
        assert events[2].outcome == "denied"
        assert events[2].output == "Auto-denied by security policy"
        assert read_tool.called is False
        denial_content = messages[-1]["content"]
        assert "[TOOL DENIED]" in denial_content

    def test_user_deny_enforced_for_ask(self, tmp_path):
        role = Role(name="r", tools={"read": "ask"})
        read_tool = StubReadTool("content")
        harness = _build_harness_stub(tmp_path, read_tool, role)
        harness.set_user_response("no")

        events, messages = run_harness_tool_call(harness, _read_call("call_2"))

        assert isinstance(events[0], harness_events.ToolCall)
        assert events[1].auto is False
        assert events[2].outcome == "denied"
        assert events[2].output == "User denied"
        assert read_tool.called is False

    @staticmethod
    def _quit_while_asking(harness, quitting):
        """Start a call that asks, stop the stream at the prompt."""
        call = _read_call("call_q")
        harness.history.append({"id": "a1", "role": "assistant", "content": None,
                                "tool_calls": [call]})
        harness.keep_pending = quitting

        async def run():
            stream = harness._execute_tool_calls([call])
            async for event in stream:
                if isinstance(event, harness_events.PermissionRequest):
                    break
            try:
                raise GeneratorExit
            except GeneratorExit:
                harness._abort_tool_calls([call])

        asyncio.run(run())

    def test_quitting_at_a_prompt_leaves_the_call_for_resume(self, tmp_path):
        """2026-11-03: a session saved while a call waited for approval asks
        again when resumed; a /stop still cancels it."""
        role = Role(name="r", tools={"read": "ask"})
        harness = _build_harness_stub(tmp_path, StubReadTool("content"), role)
        self._quit_while_asking(harness, quitting=True)
        assert [c["id"] for c in harness.pending_tool_calls()] == ["call_q"]

        stopped = _build_harness_stub(tmp_path, StubReadTool("content"), role)
        self._quit_while_asking(stopped, quitting=False)
        assert stopped.pending_tool_calls() == []

    def test_resume_asks_again_then_carries_on(self, tmp_path):
        role = Role(name="r", tools={"read": "ask"})
        read_tool = StubReadTool("content")
        harness = _build_harness_stub(tmp_path, read_tool, role)
        self._quit_while_asking(harness, quitting=True)
        harness.keep_pending = False
        harness.set_user_response("approve")
        looped = []

        async def loop():
            looped.append(True)
            yield harness_events.Done()

        harness._agent_loop = loop

        async def collect():
            return [event async for event in harness.resume()]

        seen = asyncio.run(collect())
        asking = [e for e in seen if isinstance(e, harness_events.PermissionRequest)]
        assert asking and asking[0].auto is False
        assert read_tool.called and looped and harness.pending_tool_calls() == []

    def test_a_typed_reason_reaches_the_model(self, tmp_path):
        """2026-11-03: text typed in the permission form denies with it."""
        role = Role(name="r", tools={"read": "ask"})
        read_tool = StubReadTool("content")
        harness = _build_harness_stub(tmp_path, read_tool, role)
        harness.set_user_response("read b.txt instead")

        events, messages = run_harness_tool_call(harness, _read_call("call_3"))

        assert events[2].outcome == "denied"
        assert events[2].output == "User denied: read b.txt instead"
        assert "read b.txt instead" in messages[-1]["content"]
        assert read_tool.called is False

    def test_allow_executes_tool(self, tmp_path):
        role = Role(name="r", tools={"read": "yes"})
        read_tool = StubReadTool("stubbed content")
        harness = _build_harness_stub(tmp_path, read_tool, role)

        events, messages = run_harness_tool_call(harness, _read_call("call_3"))

        assert isinstance(events[0], harness_events.ToolCall)
        assert events[1].auto is True
        assert isinstance(events[-1], harness_events.ToolResult)
        assert events[-1].outcome == "completed"
        assert read_tool.called is True
        assert events[-1].output == "stubbed content"
        assert messages[-1]["content"] == "stubbed content"

    def test_tool_calls_are_emitted_in_order_with_ask_blocking_the_next(self, tmp_path):
        """A pending ``ask`` must be shown before any later tool call appears."""
        role = Role(name="r", tools={"read": "ask"})
        read_tool = StubReadTool("content")
        harness = _build_harness_stub(tmp_path, read_tool, role)
        # Pre-answer both prompts as denials so the run is deterministic.
        harness.set_user_response("no")
        harness.set_user_response("no")

        calls = [_read_call("call_1"), _read_call("call_2")]

        async def _collect():
            collected = []
            async for event in harness._execute_tool_calls(calls):
                collected.append(event)
            return collected

        events = asyncio.run(_collect())

        assert [type(e).__name__ for e in events] == [
            "ToolCall", "PermissionRequest", "ToolResult",
            "ToolCall", "PermissionRequest", "ToolResult",
        ]
        assert [e.id for e in events if isinstance(e, harness_events.ToolCall)] == [
            "call_1", "call_2",
        ]


class TestFileToolLayer:
    """The tool layer no longer enforces permissions; the gate does."""

    def test_read_supports_line_ranges_and_line_numbers(self, tmp_path):
        (tmp_path / "test.txt").write_text("one\ntwo\nthree\nfour\n")
        from moka_code.harness.tools import MinimalToolset

        tools = MinimalToolset(tmp_path)

        assert tools.read("test.txt", offset=1, limit=2) == "two\nthree\n"
        assert tools.read("test.txt", offset=1, limit=2, include_line_numbers=True) == (
            "     2\ttwo\n     3\tthree\n"
        )

    def test_read_rejects_invalid_offset(self, tmp_path):
        from moka_code.harness.tools import MinimalToolset, ToolError

        (tmp_path / "test.txt").write_text("content")
        tools = MinimalToolset(tmp_path)

        with pytest.raises(ToolError, match="Invalid offset"):
            tools.read("test.txt", offset=-1)

    def test_read_marks_character_truncation(self, tmp_path):
        from moka_code.harness.tools import MinimalToolset

        (tmp_path / "test.txt").write_text("abcdefgh")
        tools = MinimalToolset(tmp_path)

        result = tools.read("test.txt", max_chars=3)
        assert result.startswith("abc\n[truncated:")

    def test_write_and_run_work_without_a_profile(self, tmp_path):
        from moka_code.harness.tools import MinimalToolset

        tools = MinimalToolset(tmp_path)

        assert "[OK]" in tools.write("output.txt", "data")
        assert (tmp_path / "output.txt").read_text() == "data"
        assert "hello" in tools.run("echo hello")
