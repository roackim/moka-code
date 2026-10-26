"""The transcript's compaction line (2026-10-26): the conversation stays as it
was; where it was compacted, one folded rule with the counts, which unfolds to
what the model now sees (the summary, then the messages kept word for word).
Editing a message the summary covers drops the compaction, so it asks first."""

import asyncio
from unittest.mock import patch

from moka_code import settings
from moka_code.harness.compaction import COMPACTION_MARKER_PREFIX, compaction_view
from moka_code.harness.harness import Harness
from moka_code.harness.providers import LlamaCpp
from moka_code.ui.app import chatTUI
from moka_code.ui.commands.conversation import _rebuild_ui_from_history
from moka_code.ui.tui.msg_types import CompactionMsg

from conftest import StubAgent

MARKER = {"id": "m1", "role": "assistant",
          "content": f"{COMPACTION_MARKER_PREFIX}\ncompacted_messages=2\n\n## Goal\nship it",
          "compaction": {"messages": 2, "before": 85_000, "after": 12_300, "until": "a2"}}


def _history():
    return [{"id": "u1", "role": "user", "content": "one"},
            {"id": "a1", "role": "assistant", "content": "answer one"},
            dict(MARKER, compaction=dict(MARKER["compaction"])),
            {"id": "u2", "role": "user", "content": "two"},
            {"id": "a2", "role": "assistant", "content": "answer two"},
            {"id": "u3", "role": "user", "content": "three"}]


def _ui(monkeypatch):
    monkeypatch.setattr(settings.config, "ui_stream_smoothing", False)
    ui = chatTUI(StubAgent())
    agent = ui.agent
    agent.history = _history()
    agent._last_usage = None
    agent.truncate_history = lambda i: Harness.truncate_history(agent, i)
    agent._is_compaction_message = lambda e: Harness._is_compaction_message(agent, e)

    def uncompacted_estimate(message_id):       # the cost itself is tested on a Harness below
        index = next(i for i, e in enumerate(agent.history) if e["id"] == message_id)
        return 85_000 if any(agent._is_compaction_message(e) for e in agent.history[index:]) else None
    agent.uncompacted_estimate = uncompacted_estimate
    ui.is_generating = lambda: False
    _rebuild_ui_from_history(ui, agent.history)
    return ui


def _shown(ui):
    return [("LINE" if isinstance(m.type, CompactionMsg) else m.base_text)
            for m in ui.chat_history_panel.messages]


def test_view_has_the_counts_the_summary_and_the_kept_messages():
    label, text = compaction_view(_history(), "m1")

    assert label == "compacted · 2 messages · 2 kept · ~85k → ~12k tokens"
    assert compaction_view([dict(MARKER, compaction={**MARKER["compaction"], "after": 9_400})],
                           "m1")[0].endswith("~85k → ~9.4k tokens")
    assert text.startswith("## Goal\nship it")
    assert "User: two" in text and "Assistant: answer two" in text
    assert "three" not in text                      # came after the compaction
    assert compaction_view(_history(), "u1") is None


def test_a_marker_from_before_the_metadata_still_shows():
    history = [{"id": "m0", "role": "assistant",
                "content": f"{COMPACTION_MARKER_PREFIX}\ncompacted_messages=7\n\nolder"}]

    label, text = compaction_view(history, "m0")

    assert label == "compacted · 7 messages · 0 kept" and text == "older"


def test_resume_shows_the_line_where_it_happened_never_the_raw_marker(monkeypatch):
    ui = _ui(monkeypatch)

    assert _shown(ui) == ["one", "answer one", "two", "answer two", "LINE", "three"]
    line = ui.chat_history_panel.messages[4]
    assert line.collapsed and line.collapsible
    assert line.fold_label.startswith("compacted · 2 messages")
    assert not any(COMPACTION_MARKER_PREFIX in m.base_text for m in ui.chat_history_panel.messages)


def test_editing_a_kept_message_keeps_the_compaction_and_its_line(monkeypatch):
    ui = _ui(monkeypatch)
    two = ui.chat_history_panel.messages[2]
    asked = []
    ui.show_search_modal = lambda *a, **k: asked.append(a)

    ui.handle_edit_action(two)

    assert not asked                                # nothing summarized is lost
    assert [e["id"] for e in ui.agent.history] == ["u1", "a1", "m1"]
    assert ui.agent.history[2]["compaction"]["until"] == "m1"
    assert _shown(ui) == ["one", "answer one", "LINE"]
    assert ui.input_component.buffer.text == "two"


def test_editing_a_summarized_message_asks_then_drops_the_compaction(monkeypatch):
    ui = _ui(monkeypatch)
    one = ui.chat_history_panel.messages[0]
    asked = {}
    ui.show_search_modal = lambda title, items, **k: asked.update(title=title, items=items, **k)

    ui.handle_edit_action(one)

    assert "~85k tokens sent again" in asked["title"]
    asked["on_accept"]("Cancel")
    assert len(ui.agent.history) == 6 and "LINE" in _shown(ui)

    asked["on_accept"](asked["items"][0])
    assert ui.agent.history == [] and _shown(ui) == []
    assert ui.input_component.buffer.text == "one"


def test_the_edit_cost_is_the_history_sent_again(tmp_path):
    with patch("moka_code.harness.harness.get_active_endpoint", return_value=LlamaCpp(name="t")):
        harness = Harness(workspace_path=str(tmp_path))
    harness.history = _history()

    assert harness.uncompacted_estimate("u2") is None      # the compaction stays
    cost = harness.uncompacted_estimate("u1")
    assert cost is not None and cost < harness.context_estimate()
    assert len(harness.history) == 6                       # only estimated


def test_the_folded_line_is_a_rule_across_the_width(monkeypatch):
    from moka_code.ui.tui.buffer import Buffer

    ui = _ui(monkeypatch)
    line = ui.chat_history_panel.messages[4]
    buffer = Buffer(60, 1)

    line.render_collapsed_line(buffer, max_width=58, fg=None, bg=None)

    row = "".join(cell.char for cell in buffer.cells[0][1:60])
    assert row.startswith("─ compacted · 2 messages") and row.endswith("──")
