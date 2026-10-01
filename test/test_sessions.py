"""Saved sessions: the conversation file, autosave, /session and --resume."""

import asyncio
import os
from types import SimpleNamespace

import pytest

from moka_code import settings
from moka_code.harness import sessions
from moka_code.ui.app import chatTUI

from conftest import StubAgent

from moka_code.harness.providers import OpenAICompatible


@pytest.fixture(autouse=True)
def state_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return tmp_path / "state"


def _history(question="how do I test?"):
    return [{"id": "1", "role": "user", "content": question},
            {"id": "2", "role": "assistant", "content": "like this"}]


# ---------------------------------------------------------------------------
# the file and the store
# ---------------------------------------------------------------------------

def test_write_read_round_trip_is_atomic(tmp_path):
    path = tmp_path / "a" / "conv.json"
    sessions.write(path, "agent", _history(), "m1")

    assert sessions.read(path) == ("agent", _history())
    assert not list(path.parent.glob("*.tmp"))


def test_read_rejects_what_is_not_a_conversation(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"history": [42]}')
    with pytest.raises(ValueError, match="message 0"):
        sessions.read(path)


def test_each_project_has_its_own_folder(tmp_path, state_home):
    one, two = sessions.sessions_dir(str(tmp_path / "app")), sessions.sessions_dir(str(tmp_path / "x" / "app"))
    assert one != two and one.parent == two.parent == state_home / "moka" / "sessions"
    assert one.name.startswith("app-")


def test_list_is_newest_first_and_titled_by_the_first_question(tmp_path):
    old, new = sessions.new_path(str(tmp_path)), sessions.new_path(str(tmp_path))
    new = new.with_name("z" + new.name)
    tool_images = [{"role": "user", "content": "[images returned by read: a.png]", "source": "tool"}]
    sessions.write(old, "agent", tool_images + _history("first question"))
    sessions.write(new, "agent", _history("second question"), "m2")
    os.utime(old, (1, 1))

    found = sessions.list_sessions(str(tmp_path))

    assert [s.title for s in found] == ["second question", "first question"]
    assert (found[0].messages, found[0].model) == (2, "m2")


def test_prune_keeps_the_most_recent(tmp_path):
    for i in range(4):
        path = sessions.sessions_dir(str(tmp_path)) / f"{i}.json"
        sessions.write(path, "agent", _history(f"q{i}"))
        os.utime(path, (i + 1, i + 1))

    sessions.prune(str(tmp_path), keep=2)

    assert [s.title for s in sessions.list_sessions(str(tmp_path))] == ["q3", "q2"]


# ---------------------------------------------------------------------------
# autosave in the app
# ---------------------------------------------------------------------------

def _ui(tmp_path, history=None, resume=False):
    agent = StubAgent()
    agent.workspace = str(tmp_path)
    agent.history = list(history or [])
    agent.role = SimpleNamespace(name="agent")
    agent.endpoint = OpenAICompatible(name="t", base_url="http://t/v1", model="m1")
    return chatTUI(agent, resume=resume)


def test_save_session_writes_the_conversation(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.config, "context_sessions", 10)
    ui = _ui(tmp_path, _history())

    ui.save_session()

    assert sessions.read(ui.session_path) == ("agent", _history())


def test_nothing_is_saved_when_off_or_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.config, "context_sessions", 0)
    ui = _ui(tmp_path, _history())
    ui.save_session()
    monkeypatch.setattr(settings.config, "context_sessions", 10)
    _ui(tmp_path).save_session()

    assert sessions.list_sessions(str(tmp_path)) == []


def test_clear_starts_a_new_session_and_keeps_the_old_one(tmp_path, monkeypatch):
    from moka_code.ui.commands.core import cmd_clear

    monkeypatch.setattr(settings.config, "context_sessions", 10)
    ui = _ui(tmp_path, _history())
    ui.save_session()
    first = ui.session_path

    asyncio.run(cmd_clear(ui, []))

    assert ui.session_path != first and first.exists()


# ---------------------------------------------------------------------------
# /session and --resume
# ---------------------------------------------------------------------------

def test_session_picker_resumes_into_the_same_file(tmp_path, monkeypatch):
    from moka_code.ui.commands.conversation import conversation_session

    saved = sessions.sessions_dir(str(tmp_path)) / "old.json"
    sessions.write(saved, "agent", _history("an older question"))
    ui = _ui(tmp_path)
    ui.switch_role = lambda role: role
    shown = {}
    ui.show_search_modal = lambda title, items, descriptions=None, on_accept=None, **k: \
        shown.update(items=items, descriptions=descriptions, accept=on_accept)

    asyncio.run(conversation_session(ui, []))
    assert shown["items"] == ["an older question"]
    assert "2 messages" in shown["descriptions"]["an older question"]

    shown["accept"]("an older question")

    assert ui.agent.history == _history("an older question")
    assert ui.session_path == saved          # continues that session's file


def test_session_picker_waits_for_the_generation(tmp_path):
    from moka_code.ui.commands.conversation import conversation_session

    ui = _ui(tmp_path)
    ui.is_generating = lambda: True
    ui.show_search_modal = lambda *a, **k: pytest.fail("no picker while generating")

    asyncio.run(conversation_session(ui, []))


def test_resume_flag_opens_the_picker(tmp_path, monkeypatch):
    import moka_code.ui.app as app_module
    from test_app_exit import _SlowServerAgent

    monkeypatch.setattr(app_module, "ModalHost", lambda compositor: None)

    class StoppingCompositor:
        def __init__(self, root, fps=30, shutdown_event=None):
            self.terminal = None
            self.padding = 0
            self.event_router = SimpleNamespace(set_interceptor=lambda *a: None,
                                                set_focus_scope=lambda *a: None)

        def add_frame_callback(self, callback):
            pass

        def request_render(self):
            pass

        async def run(self):
            await asyncio.sleep(0.05)

    monkeypatch.setattr(app_module, "Compositor", StoppingCompositor)
    agent = _SlowServerAgent()
    agent.workspace = str(tmp_path)
    ui = chatTUI(agent, resume=True)
    submitted = []
    ui.on_command_submit = submitted.append

    asyncio.run(asyncio.wait_for(ui.run(), timeout=5))

    assert submitted == ["/session"]
