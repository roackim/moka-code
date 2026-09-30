"""Reasoning is persisted in history and never sent back to the model.

Decided 2026-09-30: the replay implementation is removed; replay is rebuilt
per provider in PLAN.md step 2."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from moka_code import settings
from moka_code.harness.endpoint import Endpoint
from moka_code.harness.harness import Harness


def _chunk(content=None, reasoning=None, finish=None):
    delta = SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish)],
        usage=None,
    )


def _harness():
    # Only ``_to_api_message`` (and its static tag helper) are exercised.
    return Harness.__new__(Harness)


def test_chat_stores_reasoning_verbatim_in_history(tmp_path, monkeypatch):
    with patch(
        "moka_code.harness.harness.get_active_endpoint",
        return_value=Endpoint(name="test", type="llamacpp"),
    ):
        harness = Harness(workspace_path=str(tmp_path))

    async def fake_completion(messages, tools=None, stream=True):
        yield _chunk(reasoning="let me think")
        yield _chunk(content="the answer")
        yield _chunk(finish="stop")

    harness.endpoint.create_completion = fake_completion

    async def drain():
        return [event async for event in harness.chat("hi")]

    asyncio.run(drain())

    assistant = [m for m in harness.history if m.get("role") == "assistant"]
    assert len(assistant) == 1
    assert assistant[0]["reasoning"] == "let me think"
    assert assistant[0]["content"] == "the answer"


def test_openai_adapter_reads_openrouter_reasoning_field():
    """OpenRouter streams ``reasoning``; DeepSeek streams ``reasoning_content``."""
    from moka_code.harness.endpoint_openai import _adapt_stream_chunk

    def reason(payload):
        chunk = _adapt_stream_chunk(
            {"choices": [{"index": 0, "delta": payload, "finish_reason": None}]}
        )
        return chunk.choices[0].delta.reasoning_content

    assert reason({"reasoning": "pondering"}) == "pondering"
    assert reason({"reasoning_content": "pondering"}) == "pondering"
    assert reason({"reasoning_details": [{"text": "a"}, {"text": "b"}]}) == "ab"


def test_openrouter_streamed_reasoning_reaches_history(tmp_path, monkeypatch):
    """The whole pipe: adapter → harness → history entry."""
    from moka_code.harness.endpoint_openai import _adapt_stream_chunk

    with patch(
        "moka_code.harness.harness.get_active_endpoint",
        return_value=Endpoint(name="test", type="openrouter"),
    ):
        harness = Harness(workspace_path=str(tmp_path))

    raw = [
        {"choices": [{"index": 0, "delta": {"reasoning": "OR THOUGHT"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"content": "answer"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]

    async def fake_completion(messages, tools=None, stream=True):
        for payload in raw:
            yield _adapt_stream_chunk(payload)

    harness.endpoint.create_completion = fake_completion

    async def drain():
        return [event async for event in harness.chat("hi")]

    asyncio.run(drain())

    assistant = [m for m in harness.history if m.get("role") == "assistant"]
    assert assistant[0]["reasoning"] == "OR THOUGHT"
    assert assistant[0]["content"] == "answer"


def test_api_message_sends_only_api_fields():
    entry = {
        "id": "a1", "role": "assistant", "content": "answer", "source": "x",
        "reasoning": "a deep thought", "reasoning_tag": "<think>",
        "reasoning_details": [{"type": "reasoning.text", "text": "t"}],
    }

    assert _harness()._to_api_message(entry) == {"role": "assistant", "content": "answer"}


def _history_with_two_turns():
    harness = _harness()
    harness.endpoint = Endpoint(name="t", type="llamacpp")
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


def test_no_reasoning_is_sent_back():
    api = _history_with_two_turns()._api_history()

    assert all("reasoning" not in m and "reasoning_details" not in m for m in api)
    assert all("id" not in m and "source" not in m for m in api)


def test_streamed_reasoning_details_are_rebuilt_per_block():
    from moka_code.harness.endpoint_openai import merge_reasoning_details

    blocks = []
    merge_reasoning_details(blocks, [{"type": "reasoning.text", "text": "Let ", "index": 0,
                                      "format": "anthropic-claude-v1", "signature": None}])
    merge_reasoning_details(blocks, [{"type": "reasoning.text", "text": "me see", "index": 0}])
    merge_reasoning_details(blocks, [{"type": "reasoning.text", "text": "", "index": 0,
                                      "signature": "sig"}])
    merge_reasoning_details(blocks, [{"type": "reasoning.encrypted", "data": "xyz", "index": 1}])

    assert blocks == [
        {"type": "reasoning.text", "text": "Let me see", "index": 0,
         "format": "anthropic-claude-v1", "signature": "sig"},
        {"type": "reasoning.encrypted", "data": "xyz", "index": 1},
    ]


def test_tool_calls_are_sent_back_with_their_results(tmp_path, monkeypatch):
    """The follow-up request must pair each tool result with the call it answers.

    A regression stored the assistant turn without ``tool_calls``, so the model
    received orphaned ``tool`` results and never saw its own call.
    """
    from moka_code.harness.roles import Role

    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    with patch(
        "moka_code.harness.harness.get_active_endpoint",
        return_value=Endpoint(name="test", type="llamacpp"),
    ):
        harness = Harness(workspace_path=str(tmp_path))
    harness.set_role(Role(name="t", tools={"read": "yes"}))

    call = SimpleNamespace(
        index=0, id="call_1",
        function=SimpleNamespace(name="read", arguments='{"path": "a.txt"}'),
    )
    requests = []

    async def fake_completion(messages, tools=None, stream=True):
        requests.append([dict(m) for m in messages])
        if len(requests) == 1:
            delta = SimpleNamespace(content=None, reasoning_content=None, tool_calls=[call])
            yield SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=None)], usage=None)
        else:
            yield _chunk(content="done")
        yield _chunk(finish="stop")

    harness.endpoint.create_completion = fake_completion

    async def drain():
        return [event async for event in harness.chat("read it")]

    asyncio.run(drain())

    follow_up = requests[1]
    tool_msg = next(m for m in follow_up if m.get("role") == "tool")
    caller = follow_up[follow_up.index(tool_msg) - 1]
    assert caller["role"] == "assistant"
    assert [tc["id"] for tc in caller["tool_calls"]] == [tool_msg["tool_call_id"]]
    assert caller["tool_calls"][0]["function"]["arguments"] == '{"path": "a.txt"}'


def test_tool_loop_request_carries_no_reasoning(tmp_path, monkeypatch):
    from moka_code.harness.roles import Role

    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    with patch("moka_code.harness.harness.get_active_endpoint",
               return_value=Endpoint(name="test", type="llamacpp")):
        harness = Harness(workspace_path=str(tmp_path))
    harness.set_role(Role(name="t", tools={"read": "yes"}))
    call = SimpleNamespace(index=0, id="call_1",
                           function=SimpleNamespace(name="read", arguments='{"path": "a.txt"}'))
    details = [{"type": "reasoning.text", "text": "need the file", "index": 0}]
    requests = []

    async def fake_completion(messages, tools=None, stream=True):
        requests.append([dict(m) for m in messages])
        if len(requests) == 1:
            delta = SimpleNamespace(content=None, reasoning_content="need the file",
                                    reasoning_details=details, tool_calls=[call])
            yield SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=None)], usage=None)
        else:
            yield _chunk(content="done")
        yield _chunk(finish="stop")

    harness.endpoint.create_completion = fake_completion

    async def drain():
        return [event async for event in harness.chat("read it")]

    asyncio.run(drain())

    caller = next(m for m in requests[1] if m.get("tool_calls"))
    assert "reasoning" not in caller and "reasoning_details" not in caller
    stored = next(m for m in harness.history if m.get("tool_calls"))
    assert stored["reasoning_details"] == details


def test_ollama_native_response_reads_thinking_or_reasoning():
    """Ollama streams ``thinking``; llama.cpp-backed proxies stream ``reasoning``."""
    from moka_code.harness.endpoint_ollama import native_response

    for field in ("thinking", "reasoning"):
        chunk = native_response({"message": {"content": "", field: "hmm"}, "done": False})
        assert chunk.choices[0].delta.reasoning_content == "hmm"


def _stream(tmp_path, chunks):
    from moka_code.harness import events

    with patch("moka_code.harness.harness.get_active_endpoint",
               return_value=Endpoint(name="test", type="llamacpp")):
        harness = Harness(workspace_path=str(tmp_path))

    async def fake_completion(messages, tools=None, stream=True):
        for chunk in chunks:
            yield chunk
        yield _chunk(finish="stop")

    harness.endpoint.create_completion = fake_completion

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
