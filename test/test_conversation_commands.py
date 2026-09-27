import asyncio
import json
from types import SimpleNamespace

from pico_chat.harness.roles import Role
from pico_chat.ui.commands.conversation import conversation_export, conversation_import


class FakeAgent:
    def __init__(self):
        self.role = Role("default")
        self.history = [{"role": "user", "content": "hello"}]

    def set_role(self, role):
        self.role = role


class FakePanel:
    def __init__(self):
        self.messages = []

    def add_message(self, *args, **kwargs):
        msg = SimpleNamespace(
            text=args[0] if args else "",
            harness_message_ids=kwargs.get("harness_message_ids"),
            type=kwargs.get("msg_type"),
            tool_name=None, tool_args=None, tool_output=None,
            tool_status=None, show_output=True,
            rebuild_tool_display=lambda: None, finalize=lambda: None,
            set_collapsed=lambda *a, **k: None,
        )
        self.messages.append(msg)
        return msg

    def clear(self):
        self.messages.clear()


class FakeUI:
    def __init__(self):
        self.agent = FakeAgent()
        self.chat_history_panel = FakePanel()

    def switch_role(self, role):
        self.agent.set_role(role)
        return role


def test_conversation_export_includes_active_role(tmp_path):
    ui = FakeUI()
    filename = tmp_path / "conversation.json"

    asyncio.run(conversation_export(ui, [str(filename)]))

    exported = json.loads(filename.read_text())
    assert exported["role"] == "default"
    assert exported["history"] == ui.agent.history


def test_conversation_import_restores_role_before_history_replay(tmp_path, monkeypatch):
    ui = FakeUI()
    filename = tmp_path / "conversation.json"
    history = [{"role": "user", "content": "imported"}]
    filename.write_text(json.dumps({"role": "reviewer", "history": history}))
    monkeypatch.setattr(
        "pico_chat.harness.roles.load_role",
        lambda name: Role(name),
    )

    monkeypatch.setattr(
        "pico_chat.ui.commands.conversation._rebuild_ui_from_history",
        lambda ui, history: setattr(ui, "replayed_role", ui.agent.role.name),
    )
    asyncio.run(conversation_import(ui, [str(filename)]))

    assert ui.agent.role.name == "reviewer"
    assert ui.agent.history == history
    assert ui.replayed_role == "reviewer"


def test_conversation_import_accepts_legacy_history_array(tmp_path):
    ui = FakeUI()
    filename = tmp_path / "legacy.json"
    history = [{"role": "user", "content": "legacy"}]
    filename.write_text(json.dumps(history))

    asyncio.run(conversation_import(ui, [str(filename)]))

    assert ui.agent.history == history
    assert ui.agent.role.name == "default"


def test_conversation_import_defaults_role_when_missing(tmp_path, monkeypatch):
    ui = FakeUI()
    filename = tmp_path / "missing-role.json"
    history = [{"role": "user", "content": "imported"}]
    filename.write_text(json.dumps({"role": "ghost-role", "history": history}))

    def fake_load(name):
        if name == "ghost-role":
            raise KeyError(f"Role not found: {name}")
        return Role(name)

    monkeypatch.setattr("pico_chat.harness.roles.load_role", fake_load)

    asyncio.run(conversation_import(ui, [str(filename)]))

    # Defaulted to 'agent' and warned in the chat.
    assert ui.agent.role.name == "agent"
    assert ui.agent.history == history
    all_text = "\n".join(m.text for m in ui.chat_history_panel.messages)
    assert "ghost-role" in all_text
    assert "agent" in all_text


def test_conversation_import_handles_tool_call_only_assistant(tmp_path):
    """Assistant messages with tool_calls and no content must not crash."""
    ui = FakeUI()
    filename = tmp_path / "toolcall.json"
    history = [
        {"role": "user", "content": "list files"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "function": {"name": "read", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
    ]
    filename.write_text(json.dumps({"role": "default", "history": history}))

    asyncio.run(conversation_import(ui, [str(filename)]))

    assert ui.agent.history == history
    assert "Import failed" not in "\n".join(m.text for m in ui.chat_history_panel.messages)


def test_conversation_import_keeps_assistant_reply_in_one_message(tmp_path):
    """A plain assistant reply must be one PicoMsg, not split by the tag parser.

    ``ThinkingTagParser.feed`` may hold back a partial thinking tag and ``flush``
    then emits it as a separate segment. Import used to turn each segment into
    its own message, splitting the reply into a second PicoMsg (rendered as a
    mid-word break with the inter-message gap in between).
    """
    from pico_chat.ui.tui.msg_types import PicoMsg

    ui = FakeUI()
    content = "If you tell me what you're looking for, I can narrow it down."
    history = [
        {"role": "user", "content": "3 death metal albums"},
        {"role": "assistant", "content": content},
    ]
    filename = tmp_path / "music.json"
    filename.write_text(json.dumps({"role": "default", "history": history}))

    asyncio.run(conversation_import(ui, [str(filename)]))

    assistant = [m for m in ui.chat_history_panel.messages
                 if isinstance(m.type, PicoMsg)]
    assert len(assistant) == 1
    assert assistant[0].text == content


def test_conversation_import_restores_stored_reasoning(tmp_path, monkeypatch):
    """The explicit ``reasoning`` field survives export/import as a ThinkingMsg."""
    from pico_chat import pico_cfg
    monkeypatch.setattr(pico_cfg.config, "ui_thought_min_tokens", 0)
    from pico_chat.ui.tui.msg_types import PicoMsg, ThinkingMsg

    ui = FakeUI()
    history = [
        {"role": "user", "content": "hi"},
        {"id": "a1", "role": "assistant", "content": "answer",
         "reasoning": "deep thought", "reasoning_tag": "<think>"},
    ]
    filename = tmp_path / "thoughts.json"
    filename.write_text(json.dumps({"role": "default", "history": history}))

    asyncio.run(conversation_import(ui, [str(filename)]))

    # History keeps the reasoning field verbatim (nothing lost).
    assert ui.agent.history == history
    thinking = [m for m in ui.chat_history_panel.messages
                if isinstance(m.type, ThinkingMsg)]
    answers = [m for m in ui.chat_history_panel.messages
               if type(m.type) is PicoMsg]
    assert len(thinking) == 1 and thinking[0].text == "deep thought"
    assert len(answers) == 1 and answers[0].text == "answer"


def test_conversation_import_rejects_malformed_envelope(tmp_path):
    ui = FakeUI()
    filename = tmp_path / "malformed.json"
    filename.write_text(json.dumps({"role": "reviewer", "history": [{"content": "missing role"}]}))

    asyncio.run(conversation_import(ui, [str(filename)]))

    assert "missing 'role'" in ui.chat_history_panel.messages[-1].text


def test_conversation_import_rejects_non_string_role(tmp_path):
    ui = FakeUI()
    filename = tmp_path / "bad-role.json"
    filename.write_text(json.dumps({"role": 42, "history": []}))

    asyncio.run(conversation_import(ui, [str(filename)]))

    assert "role must be a string" in ui.chat_history_panel.messages[-1].text


def test_conversation_import_rejects_invalid_json(tmp_path):
    ui = FakeUI()
    filename = tmp_path / "invalid.json"
    filename.write_text("not json")

    asyncio.run(conversation_import(ui, [str(filename)]))

    assert "Invalid JSON file" in ui.chat_history_panel.messages[-1].text


def test_import_splits_answers_like_live_ones(tmp_path):
    from pico_chat.ui.chat_history_panel import ChatHistoryPanel
    from pico_chat.ui.commands.conversation import _rebuild_ui_from_history

    ui = FakeUI()
    ui.chat_history_panel = ChatHistoryPanel()
    _rebuild_ui_from_history(ui, [
        {"id": "u", "role": "user", "content": "q"},
        {"id": "a", "role": "assistant", "content": "Text\n\n```\ncode\n```"},
    ])
    kinds = [m.segment_kind for m in ui.chat_history_panel.messages if m.group is not None]
    assert kinds == ["text", "code"]
