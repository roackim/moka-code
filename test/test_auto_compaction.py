"""Compaction keeps the newest turns word for word, and runs on its own: after
a turn that ends over ``compact_at`` of the window, and inside a running turn
that goes over ``compact_stop_at`` (the turn then resumes)."""

import asyncio
import json
from unittest.mock import patch

from moka_code.harness import events
from moka_code.harness.compaction import COMPACTION_MARKER_PREFIX, cut_index
from moka_code.harness.endpoint import Chunk, ToolCallPiece
from moka_code.harness.harness import Harness
from moka_code.harness.providers import LlamaCpp
from moka_code.harness.roles import Role
from moka_code.harness.usage import TokenUsage

BIG = "x" * 2000          # ~500 tokens


def _turn(n, text=BIG):
    return [{"id": f"u{n}", "role": "user", "content": text},
            {"id": f"a{n}", "role": "assistant", "content": f"answer {n}"}]


# -- where the tail starts ----------------------------------------------------

def test_cut_keeps_the_newest_turns_that_fit():
    history = _turn(1) + _turn(2) + _turn(3)

    assert cut_index(history, keep_tokens=10_000) == 0      # everything fits: nothing to summarize
    assert cut_index(history, keep_tokens=600) == 4         # only turn 3 fits... plus none of 2
    assert cut_index(history, keep_tokens=1) == 4           # the last turn is always kept


def test_cut_only_lands_on_user_messages_and_keeps_notices_with_them():
    history = [
        {"id": "u1", "role": "user", "content": BIG},
        {"id": "a1", "role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "function": {"name": "bash", "arguments": "{}"}}]},
        {"id": "t1", "role": "tool", "tool_call_id": "c1", "content": "out"},
        {"id": "n1", "role": "system", "content": "[Role changed from a to b]"},
        {"id": "u2", "role": "user", "content": "next"},
    ]

    assert cut_index(history, keep_tokens=1) == 3           # the notice goes with u2


# -- the summary keeps the tail ------------------------------------------------

def _compacting(harness_stub_compaction, turns, window=1000):
    harness = harness_stub_compaction
    harness.endpoint.context_window = lambda: window
    for n in range(1, turns + 1):
        harness.history += _turn(n)
    return harness


def test_compact_summarizes_the_older_turns_and_keeps_the_last(harness_stub_compaction):
    harness = _compacting(harness_stub_compaction, 3)

    result = asyncio.run(harness.compact_history())

    assert result["compacted_messages"] == 4 and result["kept_messages"] == 2
    assert [e["id"] for e in harness.history[:4]] == ["u1", "a1", "u2", "a2"]
    assert harness.history[4]["content"].startswith(COMPACTION_MARKER_PREFIX)
    assert [e["id"] for e in harness._get_effective_history()] == [result["message_id"], "u3", "a3"]
    prompt = harness.endpoint.last_messages[-1]["content"]
    assert "answer 2" in prompt and "answer 3" not in prompt     # only the head is summarized


def test_one_turn_is_summarized_whole_unless_a_turn_is_running(harness_stub_compaction):
    harness = _compacting(harness_stub_compaction, 1)

    refused = asyncio.run(harness.compact_history(whole_if_needed=False))
    assert refused["ok"] is False and len(harness.history) == 2

    result = asyncio.run(harness.compact_history())
    assert result["ok"] and result["kept_messages"] == 0
    assert [e["id"] for e in harness._get_effective_history()] == [result["message_id"]]


def test_a_second_compaction_folds_the_first_summary_in(harness_stub_compaction):
    harness = _compacting(harness_stub_compaction, 3)
    asyncio.run(harness.compact_history())
    harness.history += _turn(4)

    result = asyncio.run(harness.compact_history())

    assert result["compacted_messages"] == 3        # the first summary, u3, a3
    assert harness.endpoint.last_messages[-1]["content"].count("Earlier summary") == 1


# -- the triggers ------------------------------------------------------------------

def _harness(tmp_path, window, stream, tools=None):
    with patch("moka_code.harness.harness.get_active_endpoint", return_value=LlamaCpp(name="t")):
        harness = Harness(workspace_path=str(tmp_path))
    harness.set_role(Role(name="t", tools=tools or {}))
    harness.endpoint.context_window = lambda: window
    harness.endpoint.stream = stream
    harness.history += _turn(1)
    return harness


def _is_summary(messages):
    return "Summarize the following conversation" in str(messages[-1].get("content"))


def _chat(harness, text="go"):
    async def run():
        return [event async for event in harness.chat(text)]
    return asyncio.run(run())


def test_a_turn_that_ends_over_the_soft_limit_is_compacted_after_it(tmp_path):
    calls = []

    async def stream(messages, tools=None):
        calls.append("summary" if _is_summary(messages) else "turn")
        if _is_summary(messages):
            yield Chunk(text="the summary")
        else:
            yield Chunk(text="done", usage=TokenUsage(prompt_tokens=900, completion_tokens=5))

    harness = _harness(tmp_path, 1000, stream)

    seen = _chat(harness)

    assert calls == ["turn", "summary"]
    assert [type(e) for e in seen if isinstance(e, (events.Notice, events.Done))] == [
        events.Notice, events.Notice, events.Done]
    ids = [e["content"] for e in harness.history]
    marker = next(i for i, c in enumerate(ids) if str(c).startswith(COMPACTION_MARKER_PREFIX))
    assert ids[marker + 1] == "go"                      # the marker sits before the kept turn
    assert [e["role"] for e in harness._get_effective_history()] == ["assistant", "user", "assistant"]


def test_under_the_soft_limit_nothing_happens(tmp_path):
    async def stream(messages, tools=None):
        yield Chunk(text="done", usage=TokenUsage(prompt_tokens=100, completion_tokens=5))

    harness = _harness(tmp_path, 1000, stream)

    seen = _chat(harness)

    assert not [e for e in seen if isinstance(e, events.Notice)]
    assert not any(e["content"] and str(e["content"]).startswith(COMPACTION_MARKER_PREFIX)
                   for e in harness.history)


def test_an_unknown_window_never_auto_compacts(tmp_path):
    async def stream(messages, tools=None):
        yield Chunk(text="done", usage=TokenUsage(prompt_tokens=10 ** 6))

    harness = _harness(tmp_path, None, stream)

    assert not [e for e in _chat(harness) if isinstance(e, events.Notice)]


def test_a_running_turn_over_the_hard_limit_pauses_compacts_and_resumes(tmp_path):
    calls = []

    async def stream(messages, tools=None):
        if _is_summary(messages):
            calls.append("summary")
            yield Chunk(text="the summary")
        elif not any(m.get("role") == "tool" for m in messages):
            calls.append("tool call")
            yield Chunk(tool_calls=[ToolCallPiece(index=0, id="c1", name="bash",
                                                  arguments=json.dumps({"command": "echo hi"}))],
                        usage=TokenUsage(prompt_tokens=900, completion_tokens=5))
        else:
            calls.append("answer")
            yield Chunk(text="all done", usage=TokenUsage(prompt_tokens=300, completion_tokens=5))

    harness = _harness(tmp_path, 1000, stream, tools={"bash": "yes"})

    seen = _chat(harness)

    assert calls == ["tool call", "summary", "answer"]
    assert isinstance(seen[-1], events.Done)
    assert not [e for e in seen if isinstance(e, events.Error)]
    roles = [e["role"] for e in harness._get_effective_history()]
    assert roles == ["assistant", "user", "assistant", "tool", "assistant"]   # summary, then the whole turn


def test_a_running_turn_with_nothing_older_to_summarize_stops(tmp_path):
    async def stream(messages, tools=None):
        yield Chunk(text="never", usage=TokenUsage(prompt_tokens=10))

    harness = _harness(tmp_path, 1000, stream)
    harness.history.clear()
    big = "y" * 4000            # one turn of ~1000 tokens: over the hard limit by itself

    seen = _chat(harness, big)

    assert [type(e) for e in seen if isinstance(e, (events.Error, events.Done))] == [events.Error, events.Done]
    assert any(isinstance(e, events.Notice) and e.level == "error" for e in seen)
