"""Stopping a turn mid-tool leaves nothing running and a valid history."""

import asyncio
import json
import os
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

from moka_code.harness import events
from moka_code.harness.endpoint import Endpoint
from moka_code.harness.harness import Harness
from moka_code.harness.roles import Role


def _tool_chunk(call_id, name, args):
    delta = NS(content=None, reasoning_content=None,
               tool_calls=[NS(index=0, id=call_id, function=NS(name=name, arguments=json.dumps(args)))])
    return NS(choices=[NS(delta=delta, finish_reason=None)], usage=None)


def _harness(tmp_path, tools, first_call):
    with patch("moka_code.harness.harness.get_active_endpoint",
               return_value=Endpoint(name="t", type="llamacpp")):
        harness = Harness(workspace_path=str(tmp_path))
    harness.set_role(Role(name="t", tools=tools))

    async def completion(messages, tools=None, stream=True):
        yield _tool_chunk("c1", *first_call)

    harness.endpoint.create_completion = completion
    return harness


async def _stop_after(harness, seconds):
    async def consume():
        return [event async for event in harness.chat("go")]

    task = asyncio.create_task(consume())
    await asyncio.sleep(seconds)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def _is_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_stop_kills_the_running_command_and_answers_the_call(tmp_path):
    harness = _harness(tmp_path, {"bash": "yes"},
                       ("bash", {"command": "echo $$ > pid; exec sleep 30"}))

    async def scenario():
        await _stop_after(harness, 1.0)
        await asyncio.sleep(0.3)

    asyncio.run(scenario())

    pid = int((tmp_path / "pid").read_text())
    assert not _is_alive(pid)
    tool_msgs = [m for m in harness.history if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["c1"]
    assert "CANCELLED" in tool_msgs[0]["content"]
    assert harness._running_tool is None


def test_stop_during_a_permission_prompt_drops_a_late_approval(tmp_path):
    harness = _harness(tmp_path, {"bash": "ask"}, ("bash", {"command": "echo hi"}))

    async def scenario():
        await _stop_after(harness, 0.2)
        # The user presses allow on the stale prompt after stopping...
        harness.set_user_response("approve")
        harness._abort_tool_calls([{"id": "c1"}])
        # ...which must not approve the next permission prompt.
        return harness._permission_gate._user_response_queue.empty()

    assert asyncio.run(scenario())
    assert [m["tool_call_id"] for m in harness.history if m.get("role") == "tool"] == ["c1"]


def test_presenter_unblocks_input_after_stop_during_permission(monkeypatch):
    from conftest import StubAgent
    from moka_code import settings
    from moka_code.ui.app import chatTUI
    import pytest

    monkeypatch.setattr(settings.config, "ui_stream_smoothing", False)
    ui = chatTUI(StubAgent())

    async def chat(_, attached=None):
        yield events.Start(message_id="a", role="assistant")
        yield events.ToolCall(id="t1", name="bash", args='{"command": "ls"}')
        yield events.PermissionRequest(id="t1", name="bash", args='{"command": "ls"}', prompt="?", auto=False)
        await asyncio.sleep(10)

    ui.agent.chat = chat

    async def scenario():
        task = asyncio.create_task(ui._process_generation("go", ui.chat_history_panel.add_message("go")))
        await asyncio.sleep(0.1)
        assert ui.pending_permission_prompt is not None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert ui.pending_permission_prompt is None
    assert all(not m.is_tool_message() or m.finalized for m in ui.chat_history_panel.messages)
