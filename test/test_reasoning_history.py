"""Reasoning is persisted in history; the role's replay depth decides how much
of it goes back to the model, in the provider's own field (PLAN.md 2b)."""

import asyncio
from unittest.mock import patch

from moka_code import settings
from moka_code.harness.endpoint import Chunk, ToolCallPiece
from moka_code.harness.harness import Harness
from moka_code.harness.providers import LlamaCpp


def _chunk(content=None, reasoning=None, finish=None):
    return Chunk(text=content or "", reasoning=reasoning or "", finish=finish)


def _harness():
    # Only ``_to_api_message`` (and its static tag helper) are exercised.
    return Harness.__new__(Harness)


def test_chat_stores_reasoning_verbatim_in_history(tmp_path, monkeypatch):
    with patch(
        "moka_code.harness.harness.get_active_endpoint",
        return_value=LlamaCpp(name="test"),
    ):
        harness = Harness(workspace_path=str(tmp_path))

    async def fake_completion(messages, tools=None):
        yield _chunk(reasoning="let me think")
        yield _chunk(content="the answer")
        yield _chunk(finish="stop")

    harness.endpoint.stream = fake_completion

    async def drain():
        return [event async for event in harness.chat("hi")]

    asyncio.run(drain())

    assistant = [m for m in harness.history if m.get("role") == "assistant"]
    assert len(assistant) == 1
    assert assistant[0]["reasoning"] == "let me think"
    assert assistant[0]["content"] == "the answer"


def test_api_message_sends_only_api_fields():
    entry = {
        "id": "a1", "role": "assistant", "content": "answer", "source": "x",
        "reasoning": "a deep thought", "reasoning_tag": "<think>",
        "reasoning_native": [{"type": "reasoning.text", "text": "t"}],
        "origin": {"type": "llamacpp", "model": "m"},
    }

    assert _harness()._to_api_message(entry) == {"role": "assistant", "content": "answer"}


def _history_with_two_turns():
    harness = _harness()
    harness.endpoint = LlamaCpp(name="t")
    harness.history = [
        {"id": "u1", "role": "user", "content": "first"},
        {"id": "a1", "role": "assistant", "content": "old answer", "reasoning": "old thought"},
        {"id": "u2", "role": "user", "content": "second"},
        {"id": "a2", "role": "assistant", "content": None, "reasoning": "why I call",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "read", "arguments": "{}"}}]},
        {"id": "t1", "role": "tool", "content": "result", "tool_call_id": "c1"},
        {"id": "u3", "role": "user", "content": "[images returned by read: a.png]",
         "images": [], "source": "tool"},
    ]
    return harness


def _with_depth(harness, depth, endpoint=None):
    from moka_code.harness.roles import Role

    harness.role = Role(name="t", replay_reasoning_depth=depth)
    if endpoint is not None:
        harness.endpoint = endpoint
    return harness


def _sent_reasoning(harness):
    """``{entry id: reasoning_content}`` of the API messages that carry it."""
    return {entry["id"]: msg["reasoning_content"]
            for entry, msg in zip(harness._get_effective_history(), harness._api_history())
            if "reasoning_content" in msg}


def test_api_messages_never_carry_moka_fields():
    api = _with_depth(_history_with_two_turns(), 999)._api_history()

    assert all("reasoning" not in m and "reasoning_native" not in m and "origin" not in m for m in api)
    assert all("id" not in m and "source" not in m for m in api)


def test_replay_depth_counts_turns_not_tool_images():
    """2b (2026-10-01): 0 = none; 1 = the current turn (the user message in
    ``u2`` and what follows, though the tool's images are a user entry too);
    N = the last N turns; capped at what exists. Sent even when empty."""
    harness = _history_with_two_turns()
    harness.history[1].pop("reasoning")          # the old answer had none
    assert _sent_reasoning(_with_depth(harness, 0)) == {}
    assert _sent_reasoning(_with_depth(harness, 1)) == {"a2": "why I call"}
    assert _sent_reasoning(_with_depth(harness, 2)) == {"a1": "", "a2": "why I call"}
    assert _sent_reasoning(_with_depth(harness, 999)) == {"a1": "", "a2": "why I call"}


def test_replay_skips_the_compaction_marker_and_what_precedes_a_turn():
    from moka_code.harness.harness import COMPACTION_MARKER_PREFIX

    harness = _with_depth(_history_with_two_turns(), 999)
    harness.history.insert(0, {"id": "m", "role": "assistant",
                               "content": COMPACTION_MARKER_PREFIX + "\nsummary"})
    assert "m" not in _sent_reasoning(harness)
    assert set(_sent_reasoning(harness)) == {"a1", "a2"}


def test_no_endpoint_or_no_role_sends_nothing():
    harness = _history_with_two_turns()
    assert _sent_reasoning(harness) == {}                       # no role
    harness.endpoint = None
    assert _sent_reasoning(_with_depth(harness, 999)) == {}


def test_openrouter_replays_native_blocks_only_to_their_model():
    from moka_code.harness.providers import OpenRouter

    native = [{"type": "reasoning.text", "text": "t", "index": 0}]
    endpoint = OpenRouter(name="o", model="vendor/m")
    mine = {"reasoning": "t", "reasoning_native": native,
            "origin": {"type": "openrouter", "model": "vendor/m"}}
    other = {**mine, "origin": {"type": "openrouter", "model": "vendor/other"}}
    foreign = {**mine, "origin": {"type": "llamacpp", "model": "vendor/m"}}
    assert endpoint.replay(mine) == {"reasoning_details": native}
    assert endpoint.replay(other) == {"reasoning": "t"}
    assert endpoint.replay(foreign) == {"reasoning": "t"}
    assert endpoint.replay({"role": "assistant"}) == {}         # nothing produced
    assert LlamaCpp(name="l").replay(mine) == {"reasoning_content": "t"}
    assert LlamaCpp(name="l").replay({"role": "assistant"}) == {"reasoning_content": ""}


def test_tool_calls_are_sent_back_with_their_results(tmp_path, monkeypatch):
    """The follow-up request must pair each tool result with the call it answers.

    A regression stored the assistant turn without ``tool_calls``, so the model
    received orphaned ``tool`` results and never saw its own call.
    """
    from moka_code.harness.roles import Role

    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    with patch(
        "moka_code.harness.harness.get_active_endpoint",
        return_value=LlamaCpp(name="test"),
    ):
        harness = Harness(workspace_path=str(tmp_path))
    harness.set_role(Role(name="t", tools={"read": "yes"}))

    call = ToolCallPiece(index=0, id="call_1", name="read", arguments='{"path": "a.txt"}')
    requests = []

    async def fake_completion(messages, tools=None):
        requests.append([dict(m) for m in messages])
        if len(requests) == 1:
            yield Chunk(tool_calls=[call])
        else:
            yield _chunk(content="done")
        yield _chunk(finish="stop")

    harness.endpoint.stream = fake_completion

    async def drain():
        return [event async for event in harness.chat("read it")]

    asyncio.run(drain())

    follow_up = requests[1]
    tool_msg = next(m for m in follow_up if m.get("role") == "tool")
    caller = follow_up[follow_up.index(tool_msg) - 1]
    assert caller["role"] == "assistant"
    assert [tc["id"] for tc in caller["tool_calls"]] == [tool_msg["tool_call_id"]]
    assert caller["tool_calls"][0]["function"]["arguments"] == '{"path": "a.txt"}'


def test_tool_loop_request_carries_the_current_turns_reasoning(tmp_path, monkeypatch):
    from moka_code.harness.roles import Role

    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    with patch("moka_code.harness.harness.get_active_endpoint",
               return_value=LlamaCpp(name="test")):
        harness = Harness(workspace_path=str(tmp_path))
    harness.set_role(Role(name="t", tools={"read": "yes"}))
    call = ToolCallPiece(index=0, id="call_1", name="read", arguments='{"path": "a.txt"}')
    details = [{"type": "reasoning.text", "text": "need the file", "index": 0}]
    requests = []

    async def fake_completion(messages, tools=None):
        requests.append([dict(m) for m in messages])
        if len(requests) == 1:
            yield Chunk(reasoning="need the file", tool_calls=[call])
            yield Chunk(reasoning_native=details)
        else:
            yield _chunk(content="done")
        yield _chunk(finish="stop")

    harness.endpoint.stream = fake_completion

    async def drain():
        return [event async for event in harness.chat("read it")]

    asyncio.run(drain())

    caller = next(m for m in requests[1] if m.get("tool_calls"))
    assert caller["reasoning_content"] == "need the file"          # depth 1 (default)
    assert "reasoning_native" not in caller and "origin" not in caller
    stored = next(m for m in harness.history if m.get("tool_calls"))
    assert stored["reasoning"] == "need the file"
    assert stored["reasoning_native"] == details
    # 2b (2026-10-01): where it came from, for the replay rules.
    assert stored["origin"] == {"type": "llamacpp", "model": None}
    # One builder: the second request is exactly what history builds.
    assert requests[1] == harness._request_messages()[:len(requests[1])]


def _stream(tmp_path, chunks):
    from moka_code.harness import events

    with patch("moka_code.harness.harness.get_active_endpoint",
               return_value=LlamaCpp(name="test")):
        harness = Harness(workspace_path=str(tmp_path))

    async def fake_completion(messages, tools=None):
        for chunk in chunks:
            yield chunk
        yield _chunk(finish="stop")

    harness.endpoint.stream = fake_completion

    async def drain():
        return [event async for event in harness.chat("hi")]

    got = asyncio.run(drain())
    tokens = "".join(e.text for e in got if isinstance(e, events.Token))
    reasoning = "".join(e.text for e in got if isinstance(e, events.Reasoning))
    assistant = [m for m in harness.history if m.get("role") == "assistant"][0]
    return tokens, reasoning, assistant


def test_inline_think_tags_stay_in_the_answer_verbatim(tmp_path):
    """Decided 2026-09-30 (PLAN.md mode A off): content is never parsed."""
    for pieces in (["a <thi", "nk>b</think> c"], ["<think>unclosed ", "tail"]):
        tokens, reasoning, stored = _stream(tmp_path, [_chunk(content=p) for p in pieces])
        assert tokens == stored["content"] == "".join(pieces)
        assert reasoning == "" and "reasoning" not in stored


def test_reasoning_field_still_shows_as_reasoning(tmp_path):
    tokens, reasoning, stored = _stream(
        tmp_path, [_chunk(reasoning="why"), _chunk(content="answer")])
    assert (reasoning, tokens) == ("why", "answer")
    assert (stored["reasoning"], stored["content"]) == ("why", "answer")
