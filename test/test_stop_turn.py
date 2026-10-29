"""Stopping a turn mid-tool leaves nothing running and a valid history."""

import asyncio
import json
import os
import time
from unittest.mock import patch

from moka_code.harness import events
from moka_code.harness.endpoint import Chunk, ToolCallPiece
from moka_code.harness.harness import Harness
from moka_code.harness.roles import Role
from moka_code.harness.providers import LlamaCpp


def _tool_chunk(call_id, name, args):
    return Chunk(tool_calls=[ToolCallPiece(index=0, id=call_id, name=name, arguments=json.dumps(args))])


def _harness(tmp_path, tools, first_call):
    with patch("moka_code.harness.harness.get_active_endpoint",
               return_value=LlamaCpp(name="t")):
        harness = Harness(workspace_path=str(tmp_path))
    harness.set_role(Role(name="t", tools=tools))

    async def completion(messages, tools=None):
        yield _tool_chunk("c1", *first_call)

    harness.endpoint.stream = completion
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

    async def chat(_, attached=None, prompt=None):
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


def test_killing_one_command_answers_it_and_the_turn_goes_on(tmp_path):
    """2026-10-29: ``cancel_tool`` kills just the running bash call; the model
    gets a cancelled result and is asked again (the turn is not stopped)."""
    with patch("moka_code.harness.harness.get_active_endpoint",
               return_value=LlamaCpp(name="t")):
        harness = Harness(workspace_path=str(tmp_path))
    harness.set_role(Role(name="t", tools={"bash": "yes"}))
    requests = []

    async def completion(messages, tools=None):
        requests.append(messages)
        if len(requests) == 1:
            yield _tool_chunk("c1", "bash", {"command": "echo $$ > pid; echo started; exec sleep 30"})
        else:
            yield Chunk(text="ok")

    harness.endpoint.stream = completion

    async def scenario():
        async def consume():
            produced = []
            async for event in harness.chat("go"):
                if isinstance(event, events.ToolStarted):
                    # The UI refreshes {running} here: the call must be listed.
                    assert [t.id for t in harness.running_tools()] == ["c1"]
                produced.append(event)
            return produced

        task = asyncio.create_task(consume())
        for _ in range(100):
            await asyncio.sleep(0.05)
            if harness.running_tools() and (tmp_path / "pid").exists():
                break
        [running] = harness.running_tools()
        assert (running.id, running.name) == ("c1", "bash")
        assert harness.cancel_tool("c1") is True
        assert harness.cancel_tool("c1") is False           # already cancelled
        return await asyncio.wait_for(task, 5)

    started = time.monotonic()
    produced = asyncio.run(scenario())

    assert time.monotonic() - started < 5
    assert not _is_alive(int((tmp_path / "pid").read_text()))
    assert [e.id for e in produced if isinstance(e, events.ToolStarted)] == ["c1"]
    [result] = [e for e in produced if isinstance(e, events.ToolResult)]
    assert result.outcome == "cancelled"
    assert result.output.startswith("cancelled by the user after")
    [tool_msg] = [m for m in harness.history if m.get("role") == "tool"]
    assert tool_msg["content"].startswith("[TOOL CANCELLED] The user stopped this command")
    assert "started" in tool_msg["content"]                  # the output so far
    assert len(requests) == 2                                # the turn went on
    assert harness.running_tools() == []
