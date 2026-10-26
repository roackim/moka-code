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
          "compaction": {"before": 85_000, "after": 12_300, "until": "a2"}}


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


def test_view_has_the_counts_the_summary_and_the_kept_turns():
    """Counts are turns, not entries (an example had "242 messages" kept, every
    tool call and result counted), and the kept turns are one line each: their
    messages are in the transcript above (2026-10-26)."""
    label, text = compaction_view(_history(), "m1")

    assert label == "compacted · 1 turn summarized, 1 kept · ~85k → ~12k tokens"
    assert compaction_view([dict(MARKER, compaction={**MARKER["compaction"], "after": 9_400})],
                           "m1")[0].endswith("~85k → ~9.4k tokens")
    assert text.startswith("## Goal\nship it")
    assert text.endswith("**Kept word for word** (1 turn, shown above):\n\n- two")
    assert "answer two" not in text and "three" not in text   # one line; after the compaction
    assert compaction_view(_history(), "u1") is None


def test_kept_turns_show_their_first_line_and_tool_results_are_not_turns():
    long = "x" * 300
    history = [dict(MARKER, compaction={"until": "t1"}),
               {"id": "u2", "role": "user", "content": "\n  fix the parser\nplease"},
               {"id": "a2", "role": "assistant", "content": None, "tool_calls": []},
               {"id": "t1", "role": "user", "source": "tool", "content": "image"},
               {"id": "u3", "role": "user", "content": long}]
    history[0]["compaction"]["until"] = "u3"

    label, text = compaction_view(history, "m1")

    assert label == "compacted · 0 turns summarized, 2 kept"
    assert "- fix the parser\n" in text and "please" not in text
    assert f"- {'x' * 99}…" in text


def test_a_marker_from_before_the_metadata_still_shows():
    history = [{"id": "u1", "role": "user", "content": "one"},
               {"id": "m0", "role": "assistant",
                "content": f"{COMPACTION_MARKER_PREFIX}\ncompacted_messages=7\n\nolder"}]

    label, text = compaction_view(history, "m0")

    assert label == "compacted · 1 turn summarized" and text == "older"


def test_resume_shows_the_line_where_it_happened_never_the_raw_marker(monkeypatch):
    ui = _ui(monkeypatch)

    assert _shown(ui) == ["one", "answer one", "two", "answer two", "LINE", "three"]
    line = ui.chat_history_panel.messages[4]
    assert line.collapsed and line.collapsible
    assert line.fold_label.startswith("compacted · 1 turn summarized")
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


def _row(msg, width=60):
    from moka_code.ui.tui.buffer import Buffer

    buffer = Buffer(width, 1)
    msg.render_collapsed_line(buffer, max_width=width - 2, fg=None, bg=None)
    return "".join(cell.char for cell in buffer.cells[0][1:width])


def test_the_live_line_ticks_streams_then_becomes_the_folded_line(monkeypatch):
    """2026-10-26: compacting showed nothing until done, which was unclear; it
    is a live transcript line now, like a running tool."""
    from moka_code.harness import events

    ui = _ui(monkeypatch)
    panel = ui.chat_history_panel
    history = ui.agent.history

    panel.on_compaction(events.CompactionStarted(), history)
    live = panel.messages[-1]
    assert isinstance(live.type, CompactionMsg) and live.collapsed and not live.finalized
    assert _row(live).startswith("─ compacting conversation · 0s ──")

    panel.on_compaction(events.CompactionText("## Goal\nship "), history)
    panel.on_compaction(events.CompactionText("it now"), history)
    assert _row(live).startswith("─ compacting conversation · 0s  ship it now")

    at = panel.messages.index(live)
    panel.on_compaction(events.Compacted("m1"), history)
    folded = panel.messages[at]
    assert live not in panel.messages and folded.finalized
    assert folded.fold_label.startswith("compacted · 1 turn summarized")


def test_a_failed_compaction_leaves_no_line(monkeypatch):
    from moka_code.harness import events

    ui = _ui(monkeypatch)
    panel = ui.chat_history_panel
    before = _shown(ui)

    panel.on_compaction(events.CompactionStarted(), ui.agent.history)
    panel.on_compaction(events.CompactionFailed("Auto-compact failed: boom"), ui.agent.history)

    assert _shown(ui)[:len(before)] == before and _shown(ui).count("LINE") == 1
    assert panel._live_compaction is None


def test_compact_command_shows_the_live_line_and_drops_it_on_failure(monkeypatch):
    from moka_code.harness import events
    from moka_code.ui.commands.core import cmd_compact

    ui = _ui(monkeypatch)
    lines = []

    async def compact(result=None):
        yield events.CompactionStarted()
        lines.append(_row(ui.chat_history_panel.messages[-1]))
        raise RuntimeError("server down")
    ui.agent.compact = compact

    asyncio.run(cmd_compact(ui, []))

    assert lines[0].startswith("─ compacting conversation")
    assert _shown(ui).count("LINE") == 1                 # only the resumed one
    assert ui.chat_history_panel._live_compaction is None


def _compacting_chat(ui, gate=None):
    from moka_code.harness import events

    async def chat(text, attached=None):
        ui.agent.history.append({"id": "u9", "role": "user", "content": text})
        yield events.Start(message_id="u9", role="user")
        yield events.CompactionStarted()
        yield events.CompactionText("the summary")
        if gate is not None:
            await gate.wait()
        ui.agent.history.append(dict(MARKER, id="m9", compaction=dict(MARKER["compaction"], until="u9")))
        yield events.Compacted("m9")
        yield events.Start(message_id="a9", role="assistant")
        yield events.Token(text="the answer")
        yield events.Done()
    return chat


def test_a_turn_that_compacts_shows_the_line_before_its_answer(monkeypatch):
    async def scenario():
        ui = _ui(monkeypatch)
        ui.chat_history_panel.clear()
        ui.agent.history = []
        ui.is_generating = lambda: False
        ui.agent.chat = _compacting_chat(ui)
        ui.worker_task = asyncio.create_task(ui.agent_worker())
        ui.on_user_submit("go")
        await asyncio.sleep(0.1)
        ui.worker_task.cancel()
        return ui

    ui = asyncio.run(scenario())
    kinds = [type(m.type).__name__ for m in ui.chat_history_panel.messages]
    # The request's empty "waiting" line went with the compaction; the
    # request opened a fresh one after it.
    assert kinds[:2] == ["UserMsg", "CompactionMsg"]
    assert [s for s in _shown(ui) if s] == ["go", "LINE", "the answer"]


def test_stopping_while_compacting_removes_the_live_line(monkeypatch):
    async def scenario():
        ui = _ui(monkeypatch)
        ui.chat_history_panel.clear()
        ui.agent.history = []
        gate = asyncio.Event()
        ui.agent.chat = _compacting_chat(ui, gate)
        ui.worker_task = asyncio.create_task(ui.agent_worker())
        ui.on_user_submit("go")
        await asyncio.sleep(0.05)
        assert "LINE" in _shown(ui)
        ui.stop_generation()
        await asyncio.sleep(0.05)
        ui.worker_task.cancel()
        return ui

    ui = asyncio.run(scenario())
    assert "LINE" not in _shown(ui)
    assert ui.chat_history_panel._live_compaction is None


def test_the_folded_line_is_a_rule_across_the_width(monkeypatch):
    ui = _ui(monkeypatch)
    line = ui.chat_history_panel.messages[4]

    row = _row(line, width=90)

    assert row.startswith("─ compacted · 1 turn summarized, 1 kept · ~85k → ~12k tokens ─")
    assert row.endswith("──")
