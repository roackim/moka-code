"""The ``ask`` tool: adjacent asks are one form, answered in call order."""

import asyncio
import json
from unittest.mock import patch

from moka_code.harness import events
from moka_code.harness.endpoint import Chunk, ToolCallPiece
from moka_code.harness.harness import Harness
from moka_code.harness.providers import LlamaCpp
from moka_code.harness.roles import Role


def _calls(*calls):
    """One response holding several tool calls ``(id, name, args)``."""
    return Chunk(tool_calls=[
        ToolCallPiece(index=i, id=call_id, name=name,
                      arguments=args if isinstance(args, str) else json.dumps(args))
        for i, (call_id, name, args) in enumerate(calls)])


def _harness(tmp_path, tools, *calls):
    with patch("moka_code.harness.harness.get_active_endpoint",
               return_value=LlamaCpp(name="t")):
        harness = Harness(workspace_path=str(tmp_path))
    harness.set_role(Role(name="t", tools=tools))
    requests = []

    async def completion(messages, tools=None):
        requests.append(messages)
        if len(requests) == 1:
            yield _calls(*calls)
        else:
            yield Chunk(text="ok")

    harness.endpoint.stream = completion
    return harness, requests


def _run(harness, reply):
    """Run a turn; ``reply(questions)`` gives the answers for each form."""
    async def scenario():
        produced = []
        async for event in harness.chat("go"):
            produced.append(event)
            if isinstance(event, events.Questions):
                # The UI answers later, from its own input handling.
                asyncio.get_running_loop().call_soon(
                    harness.answer_questions, reply(event.items))
        return produced
    return asyncio.run(scenario())


def _tool_messages(harness):
    return [(m["tool_call_id"], m["content"]) for m in harness.history if m.get("role") == "tool"]


def test_adjacent_asks_are_one_form_and_results_keep_call_order(tmp_path):
    """2026-10-29: ``[ask, ask, bash, ask]`` is a form of two, the command, then
    a form of one; every result goes back in call order, under its own id."""
    harness, requests = _harness(
        tmp_path, {"ask": "yes", "bash": "yes"},
        ("q1", "ask", {"question": "Which database?", "options": ["Postgres", "SQLite"],
                       "label": "Database"}),
        ("q2", "ask", {"question": "Run the slow tests?", "multiple": True,
                       "options": ["unit", "slow"]}),
        ("b1", "bash", {"command": "echo hi"}),
        ("q3", "ask", {"question": "Name?"}),
    )
    forms = []

    def reply(items):
        forms.append([(q.id, q.question, q.options, q.multiple, q.label) for q in items])
        if len(forms) == 1:
            # Answered out of order: the results still follow the calls.
            return {"q2": events.Answer(choices=["unit", "slow"], text="only on CI"),
                    "q1": events.Answer(choices=["SQLite"])}
        return {}

    produced = _run(harness, reply)

    assert forms == [
        [("q1", "Which database?", ["Postgres", "SQLite"], False, "Database"),
         ("q2", "Run the slow tests?", ["unit", "slow"], True, "")],
        [("q3", "Name?", [], False, "")],
    ]
    assert _tool_messages(harness) == [
        ("q1", "The user picked: SQLite"),
        ("q2", "The user picked: unit, slow\nThe user wrote: only on CI"),
        ("b1", "[stdout]\nhi\n[exit:0]"),
        ("q3", "The user left this question unanswered."),
    ]
    results = [(e.id, e.outcome) for e in produced if isinstance(e, events.ToolResult)]
    assert results == [("q1", "completed"), ("q2", "completed"),
                       ("b1", "completed"), ("q3", "completed")]
    # ask needs no approval, and the turn goes on with the answers.
    assert not any(isinstance(e, events.PermissionRequest) and e.name == "ask" for e in produced)
    assert len(requests) == 2


def test_dismissing_the_form_answers_each_ask_as_dismissed(tmp_path):
    harness, _ = _harness(tmp_path, {"ask": "yes"},
                          ("q1", "ask", {"question": "A?"}), ("q2", "ask", {"question": "B?"}))

    produced = _run(harness, lambda items: None)

    assert _tool_messages(harness) == [
        ("q1", "The user dismissed the question without answering."),
        ("q2", "The user dismissed the question without answering."),
    ]
    assert [e.outcome for e in produced if isinstance(e, events.ToolResult)] == ["cancelled"] * 2


def test_an_invalid_ask_gets_its_error_and_stays_out_of_the_form(tmp_path):
    harness, _ = _harness(tmp_path, {"ask": "yes"},
                          ("q1", "ask", {"question": ""}),
                          ("q2", "ask", {"question": "B?", "options": ["x"] * 7}),
                          ("q3", "ask", {"question": "C?"}))
    forms = []

    _run(harness, lambda items: forms.append([q.id for q in items]) or {})

    assert forms == [["q3"]]
    messages = dict(_tool_messages(harness))
    assert messages["q1"] == "Error: Invalid ask: 'question' must be a non-empty string"
    assert messages["q2"] == "Error: Invalid ask: at most 6 options"


def test_a_disabled_ask_is_hidden_and_refused(tmp_path):
    harness, _ = _harness(tmp_path, {"ask": "no"}, ("q1", "ask", {"question": "A?"}))

    assert "ask" not in [s["function"]["name"] for s in harness.tool_schemas or []]
    produced = _run(harness, lambda items: {})

    assert not any(isinstance(e, events.Questions) for e in produced)
    [result] = [e for e in produced if isinstance(e, events.ToolResult)]
    assert result.outcome == "denied"


def test_stopping_the_turn_while_asked_leaves_a_valid_history(tmp_path):
    harness, _ = _harness(tmp_path, {"ask": "yes"}, ("q1", "ask", {"question": "A?"}))

    async def scenario():
        async def consume():
            return [event async for event in harness.chat("go")]
        task = asyncio.create_task(consume())
        await asyncio.sleep(0.2)
        assert harness._answers is not None                 # waiting for the user
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())

    assert harness._answers is None
    assert harness.answer_questions({}) is False            # nothing waits any more
    assert [m.get("tool_call_id") for m in harness.history if m.get("role") == "tool"] == ["q1"]


def test_the_form_answers_the_harness_end_to_end(tmp_path, monkeypatch):
    """2026-10-29: through the real presenter: the asks show as one form (no
    ask tool lines), the keys answer it, and the turn goes on to the answer."""
    from moka_code import settings
    from moka_code.ui.app import chatTUI
    from moka_code.ui.tui.msg_types import QuestionMsg

    monkeypatch.setattr(settings.config, "ui_stream_smoothing", False)
    harness, requests = _harness(
        tmp_path, {"ask": "yes"},
        ("q1", "ask", {"question": "Which database?", "options": ["Postgres", "SQLite"]}),
        ("q2", "ask", {"question": "Name?"}))
    ui = chatTUI(harness)

    async def scenario():
        task = asyncio.create_task(
            ui._process_generation("go", ui.chat_history_panel.add_message("go")))
        for _ in range(50):
            await asyncio.sleep(0.02)
            if ui.question_form is not None:
                break
        for key in ("\x1b[B", "\r", *"moka", "\r", "\r"):   # SQLite, "moka", Send
            if not ui.handle_global_input(key):
                ui.input_component.handle_input(key)
        await asyncio.wait_for(task, 5)

    asyncio.run(scenario())

    assert _tool_messages(harness) == [("q1", "The user picked: SQLite"),
                                       ("q2", "The user wrote: moka")]
    assert len(requests) == 2
    messages = ui.chat_history_panel.messages
    assert not any(m.is_tool_message() for m in messages)
    [form] = [m for m in messages if isinstance(m.type, QuestionMsg)]
    assert form.base_text == "? Which database? → SQLite\n? Name? → ✎ moka"
