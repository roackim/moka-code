"""Integration tests for stream smoothing wiring (S5).

A scripted agent yields harness events; a pump drives chatTUI._on_frame between
events so the revealer advances deterministically. No terminal or real clock.
"""

import asyncio

import pytest

from moka_chat import settings
from moka_chat.harness import events
from moka_chat.ui.app import chatTUI
from moka_chat.ui.tui.msg_types import AssistantMsg, ThinkingMsg, ToolCallMsg

from conftest import StubAgent

STEP = 1.0 / 60.0


def _answers(ui):
    """Assistant content messages only (ThinkingMsg subclasses AssistantMsg)."""
    return [m for m in ui.chat_history_panel.messages if type(m.type) is AssistantMsg]


def _run_script(ui, script, pumps=2):
    """Run a scripted generation, pumping frames after each yielded event."""
    clock = [0.0]
    ui._clock = lambda: clock[0]

    async def chat(_):
        for item in script:
            if callable(item):
                item()
                continue
            yield item
            if ui.stream_revealer is not None:
                for _ in range(pumps):
                    clock[0] += STEP
                    ui._on_frame(clock[0])
                    await asyncio.sleep(0)

    ui.agent.chat = chat
    try:
        asyncio.run(ui._process_generation("hello", ui.chat_history_panel.add_message("hello")))
    finally:
        # Force-drain anything still pending for deterministic assertions.
        if ui.stream_revealer is not None and ui.stream_revealer.active():
            ui._on_frame(clock[0] + 10.0)
    return clock


def _messages(ui):
    return ui.chat_history_panel.messages


def test_reveal_lags_then_converges():
    ui = chatTUI(StubAgent())
    observed = {}

    def capture_lag():
        msg = ui.stream_message
        assert msg is not None
        observed["reveal"] = msg._reveal_len
        observed["base"] = msg.base_text

    script = [
        events.Start(message_id="m1", role="assistant"),
        events.Token(text="hello "),
        capture_lag,
        events.Token(text="world"),
        events.Done(),
    ]
    _run_script(ui, script, pumps=1)

    # At the capture point the rendered prefix lagged the arrived text.
    assert observed["base"] == "hello "
    assert observed["reveal"] < len(observed["base"])

    moka = _answers(ui)
    assert len(moka) == 1
    assert moka[0].base_text == "hello world"
    assert moka[0]._reveal_len == len(moka[0].base_text)
    assert moka[0].finalized is True


def test_boundary_flush_orders_text_before_tool():
    ui = chatTUI(StubAgent())
    script = [
        events.Start(message_id="m1", role="assistant"),
        events.Token(text="checking"),
        events.ToolCall(id="t1", name="read", args='{"path":"a"}'),
        events.PermissionRequest(id="t1", name="read", args='{"path":"a"}', prompt="?", auto=True),
        events.ToolResult(id="t1", name="read", outcome="completed", output="ok"),
        events.Done(),
    ]
    _run_script(ui, script)

    msgs = _messages(ui)
    types = [type(m.type) for m in msgs]
    assert AssistantMsg in types
    assert ToolCallMsg in types
    answer_idx = types.index(AssistantMsg)
    tool_idx = types.index(ToolCallMsg)
    assert answer_idx < tool_idx

    moka = msgs[answer_idx]
    assert moka.base_text == "checking"
    assert moka._reveal_len == len(moka.base_text)
    assert moka.finalized is True
    # No text landed after the tool block.
    assert all("checking" not in (m.base_text or "") for m in msgs[tool_idx + 1:])


def test_content_resuming_after_tool_draft_stays_one_message():
    """Content after a tool-call delta must not be sliced below the tool line.

    Providers may interleave content and tool-call deltas within one response;
    all of the response's content belongs to one assistant message.
    """
    ui = chatTUI(StubAgent())
    args = '{"command": "ping -c 1 github.com"}'
    script = [
        events.Start(message_id="m1", role="assistant"),
        events.Token(text="I'll ping github.com onc"),
        events.ToolCallDraft(id="t1", name="bash", args=args),
        events.Token(text="e for you."),
        events.ToolCall(id="t1", name="bash", args=args),
        events.PermissionRequest(id="t1", name="bash", args=args, prompt="?", auto=True),
        events.ToolResult(id="t1", name="bash", outcome="completed", output="ok"),
        events.Done(),
    ]
    _run_script(ui, script)

    moka = _answers(ui)
    assert len(moka) == 1
    assert moka[0].base_text == "I'll ping github.com once for you."
    assert moka[0].finalized is True

    msgs = _messages(ui)
    tool = [m for m in msgs if isinstance(m.type, ToolCallMsg)]
    assert len(tool) == 1
    assert msgs.index(moka[0]) < msgs.index(tool[0])


def test_tool_draft_finalizes_text_above_it():
    """The text before a tool call is complete as soon as the draft opens.

    Its tail must not wait for the (possibly long) argument stream to finish.
    """
    ui = chatTUI(StubAgent())
    observed = {}

    def capture():
        moka = _answers(ui)[0]
        observed["text"] = moka.base_text[:moka._reveal_len]
        observed["finalized"] = moka.finalized

    args = '{"path": "a.py", "content": "x'
    script = [
        events.Start(message_id="m1", role="assistant"),
        events.Token(text="Writing the **file** now."),
        events.ToolCallDraft(id="t1", name="write", args=args),
        capture,
        events.Done(),
    ]
    _run_script(ui, script, pumps=0)

    assert observed == {"text": "Writing the **file** now.", "finalized": True}


def test_error_finalizes_in_flight_tool_draft():
    """A dropped stream must not leave a tool draft spinning forever."""
    ui = chatTUI(StubAgent())
    script = [
        events.Start(message_id="m1", role="assistant"),
        events.Token(text="writing"),
        events.ToolCallDraft(
            id="t1", name="write",
            args='{"path": "README.de.md", "content": "half',
        ),
        events.Error(message="connection lost"),
    ]
    _run_script(ui, script)

    tools = [m for m in _messages(ui) if m.is_tool_message()]
    assert tools
    assert all(m.finalized for m in tools)
    assert tools[0].tool_status in ("error", "cancelled")


def test_cancel_finalizes_in_flight_tool_draft():
    ui = chatTUI(StubAgent())
    clock = [0.0]
    ui._clock = lambda: clock[0]

    async def chat(_):
        yield events.Start(message_id="m1", role="assistant")
        yield events.ToolCallDraft(
            id="t1", name="write", args='{"path": "a", "content": "x',
        )
        for _ in range(2):
            clock[0] += STEP
            ui._on_frame(clock[0])
            await asyncio.sleep(0)
        raise asyncio.CancelledError()

    ui.agent.chat = chat
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ui._process_generation("hello", ui.chat_history_panel.add_message("hello")))

    tools = [m for m in _messages(ui) if m.is_tool_message()]
    assert tools
    assert all(m.finalized for m in tools)
    assert tools[0].tool_status == "cancelled"


def test_reasoning_complete_before_content_appears(monkeypatch):
    from moka_chat import settings
    monkeypatch.setattr(settings.config, "ui_thought_min_tokens", 0)
    ui = chatTUI(StubAgent())
    script = [
        events.Start(message_id="m1", role="assistant"),
        events.Reasoning(text="thinking hard"),
        events.Token(text="answer"),
        events.Done(),
    ]
    _run_script(ui, script)

    msgs = _messages(ui)
    think = [m for m in msgs if isinstance(m.type, ThinkingMsg)]
    moka = _answers(ui)
    assert len(think) == 1 and len(moka) == 1
    assert msgs.index(think[0]) < msgs.index(moka[0])
    assert think[0].base_text == "thinking hard"
    assert think[0]._reveal_len == len(think[0].base_text)
    assert think[0].finalized is True


def test_processing_phase_shown_while_context_is_ingested():
    """The wait line is labelled "processing" until the request is in flight."""
    ui = chatTUI(StubAgent())
    observed = {}
    clock = [0.0]
    ui._clock = lambda: clock[0]

    async def chat(_):
        # Runs during context ingestion, before the first harness event.
        observed["phase"] = _messages(ui)[-1].status_phase
        yield events.Start(message_id="m1", role="assistant")
        observed["after_start"] = _messages(ui)[-1].status_phase
        yield events.Token(text="hi")
        yield events.Done()

    ui.agent.chat = chat
    asyncio.run(ui._process_generation("hello", ui.chat_history_panel.add_message("hello")))

    assert observed["phase"] == "processing"
    assert observed["after_start"] == "thinking"


def test_waiting_line_removed_when_model_exposes_no_reasoning():
    """With no Reasoning events the wait line was only an indicator: removed."""
    ui = chatTUI(StubAgent())
    script = [
        events.Start(message_id="m1", role="assistant"),
        events.Token(text="hi"),
        events.Done(),
    ]
    _run_script(ui, script)

    assert not [m for m in _messages(ui) if isinstance(m.type, ThinkingMsg)]
    assert len(_answers(ui)) == 1


def test_waiting_line_removed_before_a_tool_call():
    """A tool loop without reasoning shows only the tool lines."""
    ui = chatTUI(StubAgent())
    args = '{"path": "a.py"}'
    script = [
        events.Start(message_id="m1", role="assistant"),
        events.ToolCallDraft(id="t1", name="read", args=args),
        events.ToolCall(id="t1", name="read", args=args),
        events.PermissionRequest(id="t1", name="read", args=args, prompt="?", auto=True),
        events.ToolResult(id="t1", name="read", outcome="completed", output="x\ny"),
        events.Start(message_id="m2", role="assistant"),
        events.Token(text="done"),
        events.Done(),
    ]
    _run_script(ui, script)

    kinds = [type(m.type).__name__ for m in _messages(ui)]
    assert "ThinkingMsg" not in kinds
    assert kinds.count("ToolCallMsg") == 1

def test_thinking_message_finalizes_to_duration_summary(monkeypatch):
    from moka_chat import settings
    monkeypatch.setattr(settings.config, "ui_thought_min_tokens", 0)
    ui = chatTUI(StubAgent())
    script = [
        events.Start(message_id="m1", role="assistant"),
        events.Reasoning(text="thinking hard"),
        events.Token(text="answer"),
        events.Done(),
    ]
    _run_script(ui, script)

    think = [m for m in _messages(ui) if isinstance(m.type, ThinkingMsg)]
    assert len(think) == 1
    assert think[0].status_phase == "thinking"
    assert think[0].phase_seconds is not None
    assert think[0]._thinking_label().startswith("thought for ")

def test_error_retains_arrived_text_and_drains():
    ui = chatTUI(StubAgent())
    script = [
        events.Start(message_id="m1", role="assistant"),
        events.Token(text="partial"),
        events.Error(message="boom"),
    ]
    _run_script(ui, script)

    moka = _answers(ui)
    assert len(moka) == 1
    assert moka[0].base_text == "partial"
    assert moka[0]._reveal_len == len(moka[0].base_text)
    assert moka[0].finalized is True
    assert ui.stream_message is None
    assert any("boom" in line for line in ui.activity_panel.lines)


def test_cancel_retains_text_and_finalizes():
    ui = chatTUI(StubAgent())
    clock = [0.0]
    ui._clock = lambda: clock[0]

    async def chat(_):
        yield events.Start(message_id="m1", role="assistant")
        yield events.Token(text="partial")
        for _ in range(2):
            clock[0] += STEP
            ui._on_frame(clock[0])
            await asyncio.sleep(0)
        raise asyncio.CancelledError()

    ui.agent.chat = chat
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ui._process_generation("hello", ui.chat_history_panel.add_message("hello")))

    moka = _answers(ui)
    assert len(moka) == 1
    assert moka[0].base_text == "partial"
    assert moka[0]._reveal_len == len(moka[0].base_text)
    assert moka[0].finalized is True


def test_finalize_deferred_until_revealer_drains():
    ui = chatTUI(StubAgent())
    script = [
        events.Start(message_id="m1", role="assistant"),
        events.Token(text="a fairly long sentence that will not drain in one frame"),
        events.Done(),
    ]
    # Pump only once after Done; the revealer cannot have fully drained.
    clock = [0.0]
    ui._clock = lambda: clock[0]

    async def chat(_):
        for item in script:
            yield item
            if ui.stream_revealer is not None:
                clock[0] += STEP
                ui._on_frame(clock[0])
                await asyncio.sleep(0)

    ui.agent.chat = chat
    asyncio.run(ui._process_generation("hello", ui.chat_history_panel.add_message("hello")))

    msg = ui.stream_message
    assert msg is not None
    assert msg.finalized is False
    assert ui._stream_finalize_pending is True

    # Frame callback finishes the job once the revealer is empty.
    ui._on_frame(clock[0] + 10.0)
    assert msg.finalized is True
    assert ui.stream_message is None
    assert ui._stream_finalize_pending is False


def test_frame_callback_idle_when_gated():
    """The callback must not signal work while only waiting for the next step.

    Returning True with no dirty rects makes the compositor full-redraw, so a
    stream must not request a repaint on every frame.
    """
    ui = chatTUI(StubAgent())
    ui.reset_stream_revealer()
    assert ui.stream_revealer is not None
    ui._clock = lambda: 0.0

    msg = ui.chat_history_panel.add_message("", msg_type=AssistantMsg())
    ui.start_stream_message(msg)
    msg.ingest("hello")
    ui.stream_ingest("hello")

    assert ui._on_frame(0.0) is True        # released a grain
    assert ui._on_frame(0.001) is False     # gated: nothing changed


def test_feature_off_uses_direct_append(monkeypatch):
    monkeypatch.setattr(settings.config, "ui_stream_smoothing", False)
    ui = chatTUI(StubAgent())
    script = [
        events.Start(message_id="m1", role="assistant"),
        events.Token(text="direct"),
        events.Done(),
    ]
    _run_script(ui, script)

    assert ui.stream_revealer is None
    assert ui.stream_message is None
    moka = _answers(ui)
    assert len(moka) == 1
    assert moka[0].base_text == "direct"
    assert moka[0]._reveal_len == len(moka[0].base_text)
    assert moka[0].finalized is True


def test_tool_call_flushes_pending_text_without_frames():
    """A tool boundary must drain the revealer even if no frame ticked first."""
    ui = chatTUI(StubAgent())
    clock = [0.0]
    ui._clock = lambda: clock[0]
    seen = {}

    async def chat(_):
        yield events.Start(message_id="m1", role="assistant")
        yield events.Token(text="hello world")
        # No frame pump: the revealer still holds every cluster.
        yield events.ToolCall(id="t1", name="read", args='{"path":"a"}')
        moka = [m for m in ui.chat_history_panel.messages if type(m.type) is AssistantMsg]
        seen["base"] = moka[0].base_text
        seen["reveal"] = moka[0]._reveal_len
        seen["finalized"] = moka[0].finalized
        yield events.Done()

    ui.agent.chat = chat
    asyncio.run(ui._process_generation("hello", ui.chat_history_panel.add_message("hello")))

    assert seen["reveal"] == len(seen["base"])
    assert seen["finalized"] is True


def test_short_reasoning_gets_no_line_but_long_reasoning_does(monkeypatch):
    monkeypatch.setattr(settings.config, "ui_thought_min_tokens", 10)
    ui = chatTUI(StubAgent())
    script = [
        events.Start(message_id="m1", role="assistant"),
        events.Reasoning(text="ok, ls."),                      # ~2 tokens: hidden
        events.ToolCall(id="t1", name="bash", args='{"command": "ls"}'),
        events.PermissionRequest(id="t1", name="bash", args='{"command": "ls"}', prompt="?", auto=True),
        events.ToolResult(id="t1", name="bash", outcome="completed", output="[exit:0]"),
        events.Start(message_id="m2", role="assistant"),
        events.Reasoning(text="The listing shows two files; compare them. " * 2),  # ~21 tokens
        events.Token(text="done"),
        events.Done(),
    ]
    _run_script(ui, script)

    think = [m for m in _messages(ui) if isinstance(m.type, ThinkingMsg)]
    assert len(think) == 1
    assert think[0].base_text.startswith("The listing")
