"""The conversation runs one generation at a time, in submission order.

A message sent while the model answers is queued: it waits for the running
generation, and it stays at the bottom of the transcript until picked up (the
running generation's lines go above it). ``/stop`` cancels only the running
generation; the worker keeps serving the queue.
"""

import asyncio

from moka_code import settings
from moka_code.harness import events
from moka_code.ui.app import chatTUI

from conftest import StubAgent


def _texts(ui):
    return [m.base_text for m in ui.chat_history_panel.messages if m.base_text]


def _ui(monkeypatch, chat):
    monkeypatch.setattr(settings.config, "ui_stream_smoothing", False)
    monkeypatch.setattr(settings.config, "ui_thought_min_tokens", 0)
    ui = chatTUI(StubAgent())
    ui.agent.chat = chat
    return ui


def test_one_generation_at_a_time_and_queued_message_stays_last(monkeypatch):
    running, overlap = [], []
    gate = {}

    async def chat(text, attached=None):
        running.append(text)
        if len(running) > 1:
            overlap.append(list(running))
        yield events.Start(message_id=text, role="assistant")
        yield events.Token(text=f"answer to {text}")
        if text == "first":
            gate["queued"] = asyncio.Event()
            await gate["queued"].wait()
            # Lines the running generation adds after a message was queued.
            yield events.ToolCall(id="t1", name="bash", args='{"command": "ls"}')
            yield events.PermissionRequest(id="t1", name="bash", args='{"command": "ls"}', prompt="?", auto=True)
            yield events.ToolResult(id="t1", name="bash", outcome="completed", output="[exit:0]")
            yield events.Start(message_id="first-2", role="assistant")
            yield events.Token(text="first, continued")
        running.remove(text)
        yield events.Done()

    async def scenario():
        ui = _ui(monkeypatch, chat)
        ui.worker_task = asyncio.create_task(ui.agent_worker())  # as run() does
        ui.on_user_submit("first")
        await asyncio.sleep(0.05)
        ui.on_user_submit("second")
        await asyncio.sleep(0.05)
        queued = ui.chat_history_panel.messages[-1]
        assert queued.is_queued and queued.base_text == "second"
        gate["queued"].set()
        await asyncio.sleep(0.2)
        ui.worker_task.cancel()
        return ui

    ui = asyncio.run(scenario())
    assert overlap == []
    texts = _texts(ui)
    assert texts.index("first, continued") < texts.index("second") < texts.index("answer to second")
    assert texts[-1] == "answer to second"


def test_stop_cancels_the_generation_but_not_the_worker(monkeypatch):
    started = []

    async def chat(text, attached=None):
        started.append(text)
        yield events.Start(message_id=text, role="assistant")
        yield events.Token(text=f"answer to {text}")
        if text == "slow":
            await asyncio.sleep(10)
        yield events.Done()

    async def scenario():
        ui = _ui(monkeypatch, chat)
        ui.worker_task = asyncio.create_task(ui.agent_worker())
        ui.on_user_submit("slow")
        await asyncio.sleep(0.05)
        assert ui.stop_generation() is True
        await asyncio.sleep(0.05)
        assert not ui.worker_task.done()
        ui.on_user_submit("next")
        await asyncio.sleep(0.1)
        ui.worker_task.cancel()
        return ui

    ui = asyncio.run(scenario())
    assert started == ["slow", "next"]
    assert any("Generation stopped" in line for line in ui.activity_panel.lines)
    assert _texts(ui)[-1] == "answer to next"


def test_submitting_never_starts_a_second_worker(monkeypatch):
    async def chat(text, attached=None):
        yield events.Done()

    async def scenario():
        ui = _ui(monkeypatch, chat)
        worker = asyncio.create_task(ui.agent_worker())
        ui.worker_task = worker
        ui.on_user_submit("hi")
        await asyncio.sleep(0.05)
        same = ui.worker_task is worker
        worker.cancel()
        return same

    assert asyncio.run(scenario())


def test_dollar_prefix_is_an_ordinary_message(monkeypatch):
    """Decided 2026-09-30 (PLAN.md step 0.4): no ``$`` shell escape; a
    message starting with ``$`` goes to the model like any other."""
    sent = []

    async def chat(text, attached=None):
        sent.append(text)
        yield events.Done()

    async def scenario():
        ui = _ui(monkeypatch, chat)
        ui.worker_task = asyncio.create_task(ui.agent_worker())
        ui.on_user_submit("$ ls")
        await asyncio.sleep(0.05)
        ui.worker_task.cancel()

    asyncio.run(scenario())
    assert sent == ["$ ls"]


def test_escape_while_generating_stops_it(monkeypatch):
    async def chat(text, attached=None):
        yield events.Start(message_id=text, role="assistant")
        yield events.Token(text="partial")
        await asyncio.sleep(10)
        yield events.Done()

    async def scenario():
        ui = _ui(monkeypatch, chat)
        ui.worker_task = asyncio.create_task(ui.agent_worker())
        ui.on_user_submit("slow")
        await asyncio.sleep(0.05)
        assert ui.is_generating()
        assert ui.handle_global_input("\x1b") is True
        await asyncio.sleep(0.05)
        assert not ui.is_generating()
        assert not ui.worker_task.done()
        ui.worker_task.cancel()
        return ui

    ui = asyncio.run(scenario())
    assert any("Generation stopped" in line for line in ui.activity_panel.lines)


def _queue_two(monkeypatch, scenario_body):
    started = []
    gate = asyncio.Event

    async def chat(text, attached=None):
        started.append(text)
        yield events.Start(message_id=text, role="assistant")
        yield events.Token(text=f"answer to {text}")
        if text == "first":
            await asyncio.sleep(0.3)
        yield events.Done()

    async def scenario():
        ui = _ui(monkeypatch, chat)
        ui.worker_task = asyncio.create_task(ui.agent_worker())
        ui.on_user_submit("first")
        await asyncio.sleep(0.05)
        ui.on_user_submit("second")
        ui.on_user_submit("third")
        queued = [m for m in ui.chat_history_panel.messages if m.is_queued]
        await scenario_body(ui, queued)
        await asyncio.sleep(0.6)
        ui.worker_task.cancel()
        return ui

    return asyncio.run(scenario()), started


def test_queued_messages_are_numbered_and_dashed(monkeypatch):
    async def body(ui, queued):
        assert [m.title for m in queued] == ["queued #1", "queued #2"]
        assert all(m.box.gutter == "╎" for m in queued)

    ui, started = _queue_two(monkeypatch, body)
    assert started == ["first", "second", "third"]
    sent = [m for m in ui.chat_history_panel.messages if m.base_text in ("second", "third")]
    assert all(m.title == "user" and m.box.gutter == "▌" for m in sent)


def test_cancel_drops_a_queued_message_and_renumbers(monkeypatch):
    async def body(ui, queued):
        ui.handle_cancel_queued_action(queued[0])
        assert queued[1].title == "queued #1"

    ui, started = _queue_two(monkeypatch, body)
    assert started == ["first", "third"]
    assert "second" not in _texts(ui)


def test_edit_unqueues_and_fills_the_input_replacing_the_draft(monkeypatch):
    async def body(ui, queued):
        ui.input_component.buffer.text = "a draft"
        ui.handle_edit_queued_action(queued[0])
        assert ui.input_component.buffer.text == "second"
        assert ui._last_focus_id == "input"

    ui, started = _queue_two(monkeypatch, body)
    assert started == ["first", "third"]
    assert "second" not in _texts(ui)


def test_a_queued_message_offers_edit_and_cancel_only_while_waiting(monkeypatch):
    from moka_code.ui.tui.msg_types import MsgAction

    async def body(ui, queued):
        assert [a.label for a in queued[0].get_active_actions()] == ["edit", "cancel", "copy"]

    ui, _ = _queue_two(monkeypatch, body)
    sent = next(m for m in ui.chat_history_panel.messages if m.base_text == "second")
    assert MsgAction.EDIT not in sent.get_active_actions()
