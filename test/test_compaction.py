import asyncio

from moka_chat.harness.harness import Harness, COMPACTION_MARKER_PREFIX
from moka_chat.harness.llm_status import AgentState


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
    from moka_chat.harness.roles import Role

    harness.role = Role("test", prompt="You are a test agent.")
    harness.history = [
        {"id": "u1", "role": "user", "content": "first"},
        {"id": "a1", "role": "assistant", "content": "first answer"},
    ]

    asyncio.run(harness.compact_history())

    roles = [m["role"] for m in harness.endpoint.last_messages]
    assert roles[0] == "system"
    assert roles.count("system") == 1
