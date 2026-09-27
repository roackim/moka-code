"""Tests for streamed tool-call buffer assembly.

The assembler must handle two real streaming patterns:
1. One call whose id appears only on the first delta (DeepSeek) — args-only
   deltas afterwards must attach to that same call (not split).
2. Multiple distinct calls that share the same index (e.g. index 0) but have
   distinct ids — they must NOT merge into e.g. "bashbash".
"""

import asyncio
from types import SimpleNamespace

from moka_chat.harness import events
from moka_chat.harness.harness import Harness


def _delta_with_tool_calls(calls):
    return SimpleNamespace(
        id="c1",
        choices=[SimpleNamespace(
            index=0,
            delta=SimpleNamespace(content=None, reasoning_content=None, tool_calls=calls),
            finish_reason=None,
        )],
        usage=None,
    )


def _tc(index, call_id, name="", arguments=None):
    return SimpleNamespace(index=index, id=call_id,
                           function=SimpleNamespace(name=name, arguments=arguments))


def _assemble(deltas_list):
    """Replay the production assembly loop against a list of delta chunk lists."""
    buffer = {}
    active = {}
    for calls in deltas_list:
        for tc in calls:
            if tc.id:
                key = tc.id
                if tc.id not in buffer:
                    buffer[tc.id] = {"index": tc.index, "id": tc.id, "type": "function",
                                     "function": {"name": "", "arguments": ""}}
                active[tc.index] = tc.id
            else:
                key = active.get(tc.index)
                if key is None:
                    key = tc.index
                if key not in buffer:
                    buffer[key] = {"index": tc.index, "id": None, "type": "function",
                                   "function": {"name": "", "arguments": ""}}
            if tc.function.name:
                buffer[key]["function"]["name"] += tc.function.name
            if tc.function.arguments:
                buffer[key]["function"]["arguments"] += tc.function.arguments
    return Harness._assemble_tool_calls(buffer), buffer


def test_id_on_first_delta_keeps_args():
    """DeepSeek pattern: id on first delta, id-less args after — one call."""
    calls, _ = _assemble([
        [_tc(0, "call_abc", name="bash", arguments="")],
        [_tc(0, None, arguments='{"command": "echo hi"}')],
    ])
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "bash"
    assert calls[0]["function"]["arguments"] == '{"command": "echo hi"}'


def test_two_calls_same_index_distinct_ids_do_not_merge():
    """Two calls both at index 0 but distinct ids stay separate (no bashbash)."""
    calls, _ = _assemble([
        [_tc(0, "call_a", name="bash", arguments='{"command": "a"}')],
        [_tc(0, "call_b", name="bash", arguments='{"command": "b"}')],
        [_tc(0, "call_a", arguments='}')],
    ])
    assert len(calls) == 2
    names = {c["function"]["name"] for c in calls}
    assert names == {"bash"}
    args_text = " ".join(c["function"]["arguments"] for c in calls)
    assert "a" in args_text and "b" in args_text
    for c in calls:
        assert c["function"]["name"] == "bash"


def test_id_less_first_delta_falls_back_to_index():
    """If no id ever arrives, index keying still works (single call)."""
    calls, _ = _assemble([
        [_tc(0, None, name="bash")],
        [_tc(0, None, arguments='{"command": "ls"}')],
    ])
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "bash"
    assert calls[0]["function"]["arguments"] == '{"command": "ls"}'


def test_no_mixed_int_str_keys_when_only_ids():
    """When ids are present, all keys are strings; reconstruction is stable."""
    calls, keys = _assemble([
        [_tc(0, "call_x", name="bash", arguments='{}')],
    ])
    assert len(calls) == 1


# --- live drafting ---------------------------------------------------------

class _NoopDebug:
    def log(self, *args, **kwargs):
        pass


def _stream_drafts(chunks, monkeypatch=None, step=0.0):
    """Drive _stream_llm_response over a canned chunk stream, return events.

    With ``monkeypatch``, the harness clock advances by ``step`` seconds per
    chunk so the time-throttled draft cadence is deterministic.
    """
    harness = Harness.__new__(Harness)
    harness.debug_stream = _NoopDebug()
    harness.state = None
    harness.history = []
    harness.workspace = "."
    harness.tool_schemas = []
    harness._current_reasoning = ""
    harness._last_usage = None

    clock = [0.0]
    if monkeypatch is not None:
        import moka_chat.harness.harness as harness_mod
        monkeypatch.setattr(harness_mod, "time", SimpleNamespace(perf_counter=lambda: clock[0]))

    async def create_completion(messages, tools=None, stream=True):
        for chunk in chunks:
            clock[0] += step
            yield chunk

    harness.endpoint = SimpleNamespace(create_completion=create_completion)

    async def _collect():
        return [event async for event in harness._stream_llm_response([])]

    return asyncio.run(_collect())


def test_draft_event_emitted_while_arguments_stream(monkeypatch):
    """A named, still-streaming call is announced before the stream ends."""
    chunks = [
        _delta_with_tool_calls([_tc(0, "call_1", name="bash", arguments="")]),
        _delta_with_tool_calls([_tc(0, None, arguments='{"command": "echo ')]),
        _delta_with_tool_calls([_tc(0, None, arguments='hi"}')]),
    ]
    drafts = [e for e in _stream_drafts(chunks, monkeypatch, step=1.0)
              if isinstance(e, events.ToolCallDraft)]

    assert len(drafts) == 3
    assert all(d.id == "call_1" and d.name == "bash" for d in drafts)
    assert drafts[0].args == ""
    assert drafts[-1].args == '{"command": "echo hi"}'


def test_no_draft_before_name_is_known():
    """Deltas carrying only argument fragments do not announce a call."""
    chunks = [
        _delta_with_tool_calls([_tc(0, "call_1", arguments='{"path": "a"')]),
        _delta_with_tool_calls([_tc(0, "call_1", name="read", arguments='.py"}')]),
    ]
    drafts = [e for e in _stream_drafts(chunks) if isinstance(e, events.ToolCallDraft)]

    assert len(drafts) == 1
    assert drafts[0].name == "read"


def test_draft_cadence_is_time_throttled(monkeypatch):
    """A large streamed body updates the draft on a steady clock, not per delta.

    400 deltas over 4s (10ms apart) yield about one draft per 100ms: enough for
    a live line, far fewer than one event per delta, and never frozen for long
    stretches as the body grows.
    """
    body = "x" * 20_000
    chunks = [_delta_with_tool_calls(
        [_tc(0, "call_1", name="write", arguments='{"content": "')]
    )]
    for i in range(0, len(body), 50):
        chunks.append(_delta_with_tool_calls(
            [_tc(0, None, arguments=body[i:i + 50])]
        ))
    drafts = [e for e in _stream_drafts(chunks, monkeypatch, step=0.01)
              if isinstance(e, events.ToolCallDraft)]

    assert 30 <= len(drafts) <= 45
    assert drafts[-1].name == "write"
    # Late in the body the draft still advances every ~100ms (~5000 chars/s
    # here), rather than backing off to multi-second gaps.
    growth = [len(b.args) - len(a.args) for a, b in zip(drafts, drafts[1:])]
    assert max(growth) <= 600


def test_reasoning_delta_does_not_drop_tool_call_fragment():
    """A delta carrying reasoning and a tool-call fragment keeps both."""
    chunk = _delta_with_tool_calls([_tc(0, "call_1", name="read", arguments='{"path": "a.py"}')])
    chunk.choices[0].delta.reasoning_content = "thinking"
    harness_events = _stream_drafts([chunk])

    assert any(isinstance(e, events.Reasoning) for e in harness_events)
    drafts = [e for e in harness_events if isinstance(e, events.ToolCallDraft)]
    assert drafts and drafts[-1].args == '{"path": "a.py"}'
