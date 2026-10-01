import asyncio

from moka_code.harness.harness import Harness, COMPACTION_MARKER_PREFIX
from moka_code.harness.llm_status import AgentState


def test_effective_history_starts_from_last_compaction_marker(harness_stub_compaction):
    harness = harness_stub_compaction
    harness.history = [
        {"id": "u1", "role": "user", "content": "first"},
        {"id": "a1", "role": "assistant", "content": "first answer"},
        {"id": "c1", "role": "assistant", "content": f"{COMPACTION_MARKER_PREFIX}\ncompacted_messages=2\noriginal_tokens=100\n\nsummary"},
        {"id": "u2", "role": "user", "content": "second"},
    ]

    effective = harness._get_effective_history()

    assert [m["id"] for m in effective] == ["c1", "u2"]


def test_compact_history_inserts_new_marker_and_becomes_effective_start(harness_stub_compaction):
    harness = harness_stub_compaction
    harness.history = [
        {"id": "u1", "role": "user", "content": "first"},
        {"id": "a1", "role": "assistant", "content": "first answer"},
    ]

    result = asyncio.run(harness.compact_history())

    assert result["ok"] is True
    assert result["compacted_messages"] == 2
    assert harness.history[-1]["role"] == "assistant"
    assert harness.history[-1]["content"].startswith(COMPACTION_MARKER_PREFIX)

    effective = harness._get_effective_history()
    assert len(effective) == 1
    assert effective[0]["id"] == result["message_id"]


def test_deleting_compaction_marker_restores_full_history_behavior(harness_stub_compaction):
    harness = harness_stub_compaction
    harness.history = [
        {"id": "u1", "role": "user", "content": "first"},
        {"id": "c1", "role": "assistant", "content": f"{COMPACTION_MARKER_PREFIX}\ncompacted_messages=1\noriginal_tokens=50\n\nsummary"},
        {"id": "u2", "role": "user", "content": "second"},
    ]

    # Simulate user deleting the compaction message from history.
    harness.history = [msg for msg in harness.history if msg["id"] != "c1"]

    effective = harness._get_effective_history()

    assert [m["id"] for m in effective] == ["u1", "u2"]


def test_compact_history_summary_call_has_single_system_message_first(harness_stub_compaction):
    harness = harness_stub_compaction
    from moka_code.harness.roles import Role

    harness.role = Role("test", prompt="You are a test agent.")
    harness.history = [
        {"id": "u1", "role": "user", "content": "first"},
        {"id": "a1", "role": "assistant", "content": "first answer"},
    ]

    asyncio.run(harness.compact_history())

    roles = [m["role"] for m in harness.endpoint.last_messages]
    assert roles[0] == "system"
    assert roles.count("system") == 1


# -- the transcript the summarizer gets (PLAN.md step 3, 2026-10-01) ------------------

from moka_code.harness.compaction import transcript


def _call(call_id, name, args):
    import json
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def _conversation():
    return [
        {"id": "m", "role": "assistant",
         "content": f"{COMPACTION_MARKER_PREFIX}\ncompacted_messages=4\n\nearlier work"},
        {"id": "s", "role": "system", "content": "[Role changed from agent to chat]"},
        {"id": "u1", "role": "user", "content": "fix it [image #1]",
         "images": [{"n": 1, "hash": "abc"}]},
        {"id": "a1", "role": "assistant", "content": "looking", "reasoning": "secret thought",
         "reasoning_native": [{"type": "x"}], "origin": {"type": "llamacpp", "model": "m"},
         "tool_calls": [_call("c1", "read", {"path": "a.py", "offset": 39, "limit": 81}),
                        _call("c2", "bash", {"command": "pytest -q\nsecond line"}),
                        _call("c3", "read", {"path": "gone.py"}),
                        _call("c4", "write", {"path": "n.py", "content": "a\nb\nc"}),
                        _call("c5", "edit", {"path": "a.py", "search": "x", "replace": "y"})]},
        {"id": "t1", "role": "tool", "tool_call_id": "c1", "content": "LINE ONE\nLINE TWO"},
        {"id": "t2", "role": "tool", "tool_call_id": "c2", "content": "1 failed\n[exit:1]"},
        {"id": "t3", "role": "tool", "tool_call_id": "c3", "content": "Error: file not found\nmore"},
        {"id": "t4", "role": "tool", "tool_call_id": "c4", "content": "ok"},
        {"id": "t5", "role": "tool", "tool_call_id": "c5", "content": "ok"},
        {"id": "u2", "role": "user", "content": "[images returned by read: a.png]",
         "source": "tool", "images": [{"n": 1}]},
    ]


def test_transcript_filtered_is_readable_text_without_stored_fields():
    text = transcript(_conversation(), filter_thoughts=True, filter_tool_calls=True)

    assert text == "\n".join([
        "Earlier summary:\nearlier work",
        "Note: [Role changed from agent to chat]",
        "User: fix it [image #1]",
        "Assistant: looking",
        "  read a.py lines 40–120",
        "  bash: pytest -q → exit 1",
        "  read gone.py → Error: file not found",
        "  write n.py (3 lines)",
        "  edit a.py",
        "[images returned by read: a.png]",
    ])


def test_transcript_unfiltered_keeps_thoughts_and_tool_output():
    text = transcript(_conversation(), filter_thoughts=False, filter_tool_calls=False)

    assert "[thinking] secret thought" in text
    assert "[tool result] LINE ONE\nLINE TWO" in text
    assert '[tool call] bash {"command": "pytest -q\\nsecond line"}' in text
    for stored in ("reasoning_native", "origin", "hash", "'id'", "t1", "c1"):
        assert stored not in text


def test_compact_history_sends_the_transcript_not_stored_entries(harness_stub_compaction):
    harness = harness_stub_compaction
    harness.history = _conversation()[2:]

    asyncio.run(harness.compact_history())

    [request] = [m for m in harness.endpoint.last_messages if m["role"] == "user"]
    assert "Conversation:\nUser: fix it" in request["content"]
    assert "secret thought" not in request["content"]
    assert "LINE ONE" not in request["content"]
    assert '"id"' not in request["content"]
