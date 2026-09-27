"""The conversation runs one generation at a time, in submission order.

A message sent while the model answers is queued: it waits for the running
generation, and it stays at the bottom of the transcript until picked up (the
running generation's lines go above it). ``/stop`` cancels only the running
generation; the worker keeps serving the queue.
"""

import asyncio

from moka_chat import settings
from moka_chat.harness import events
from moka_chat.ui.app import chatTUI

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
