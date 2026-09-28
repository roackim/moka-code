"""Tests for the tool-transport seam (bare/in-process mode)."""

import asyncio
import json

from moka_code.harness import events as events_module
from moka_code.harness.harness import Harness
from moka_code.harness.llm_status import AgentState
from moka_code.harness.permissions import PermissionGate
from moka_code.harness.roles import Role
from moka_code.harness.tools import (
    InProcessTransport,
    MinimalToolset,
    create_toolset,
)

from conftest import NoopDebugStream, run_harness_tool_call


def _run(coro):
    return asyncio.run(coro)


def test_default_toolset_uses_in_process_transport(tmp_path):
    tools = create_toolset(tmp_path)

    assert isinstance(tools["read"].transport, InProcessTransport)


def test_registered_tool_executes_via_transport(tmp_path):
    (tmp_path / "f.txt").write_text("content")

    tool = create_toolset(tmp_path)["read"]

    assert _run(tool.execute(path="f.txt")) == "content"


def test_explicit_transport_is_shared_across_tools(tmp_path):
    transport = InProcessTransport(MinimalToolset(tmp_path))

    tools = create_toolset(tmp_path, transport=transport)

    assert tools["read"].transport is transport
    assert tools["bash"].transport is transport


def test_cancel_active_only_applies_to_bash(tmp_path):
    tools = create_toolset(tmp_path)

    assert tools["read"].cancel_active_run() is False
    assert tools["bash"].cancel_active_run() is False


def test_transport_dispatches_async_handler(tmp_path):
    transport = InProcessTransport(MinimalToolset(tmp_path))

    out = _run(transport.execute("bash", {"command": "echo seam"}))

    assert "seam" in out


def test_transport_forwards_interim_output(tmp_path):
    chunks = []
    transport = InProcessTransport(MinimalToolset(tmp_path))

    out = _run(transport.execute(
        "bash",
        {"command": "echo streamed"},
        on_output=lambda stream, data: chunks.append((stream, data)),
    ))

    assert any("streamed" in data for _, data in chunks)
    assert "streamed" in out


class _HugeReadTool:
    def __init__(self):
        self.result = "z" * 50_000

    def execute(self, on_output=None, **kwargs):
        return self.result


def test_harness_elides_history_but_not_the_ui_event(tmp_path):
    harness = Harness.__new__(Harness)
    harness.debug_stream = NoopDebugStream()
    harness.state = AgentState.IDLE
    harness.history = []
    harness.workspace = str(tmp_path)
    tool = _HugeReadTool()
    harness.tools_map = {"read": tool}
    harness._permission_gate = PermissionGate(role=Role(name="t", tools={"read": "yes"}))

    tool_call = {
        "id": "call_1",
        "function": {"name": "read", "arguments": json.dumps({"path": "x"})},
    }
    events, _messages = run_harness_tool_call(harness, tool_call)

    result_event = events[-1]
    assert result_event.outcome == "completed"
    assert result_event.output == tool.result  # UI keeps the full output

    assert harness.history[-1]["content"] != tool.result
    assert "chars elided" in harness.history[-1]["content"]


def test_harness_emits_tool_output_events(tmp_path):
    harness = Harness.__new__(Harness)
    harness.debug_stream = NoopDebugStream()
    harness.state = AgentState.IDLE
    harness.history = []
    harness.workspace = str(tmp_path)
    transport = InProcessTransport(MinimalToolset(tmp_path))
    harness.tools_map = {"bash": create_toolset(tmp_path, transport=transport)["bash"]}
    harness._permission_gate = PermissionGate(role=Role(name="t", tools={"bash": "yes"}))

    tool_call = {
        "id": "call_1",
        "function": {
            "name": "bash",
            "arguments": json.dumps({"command": "echo streamed"}),
        },
    }
    events, _messages = run_harness_tool_call(harness, tool_call)

    outputs = [e for e in events if isinstance(e, events_module.ToolOutput)]
    assert outputs, "expected at least one ToolOutput event"
    assert outputs[0].id == "call_1"
    assert outputs[0].stream == "stdout"
    assert any("streamed" in e.data for e in outputs)


def test_sandbox_required_locks_chat(tmp_path):
    harness = Harness.__new__(Harness)
    harness.debug_stream = NoopDebugStream()
    harness.state = AgentState.IDLE
    harness.history = []
    harness.workspace = str(tmp_path)
    harness.transport = InProcessTransport(MinimalToolset(tmp_path))
    harness.role = Role(name="locked", tools={"read": "yes"}, require_sandbox=True)

    assert harness.sandboxed() is False
    assert harness.sandbox_required() is True

    async def _collect():
        return [event async for event in harness.chat("hi")]

    out = _run(_collect())
    assert isinstance(out[0], events_module.Error)
    assert "requires an active sandbox" in out[0].message
    assert isinstance(out[-1], events_module.Done)


def test_sandbox_required_false_when_sandboxed():
    harness = Harness.__new__(Harness)
    harness.role = Role(name="locked", require_sandbox=True)
    harness.transport = type("_FakeSandbox", (), {"is_sandbox": True})()

    assert harness.sandbox_required() is False
