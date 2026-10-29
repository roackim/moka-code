"""The ``ask`` form: tabs, picks, own answers, send and dismiss, from the keys."""

from moka_code.harness import events
from moka_code.ui import question_form
from moka_code.ui.app import chatTUI
from moka_code.ui.tui.chat_screen import ChatScreen

DB = events.Question("q1", "Which database?", ["Postgres", "SQLite"], label="Database")
TESTS = events.Question("q2", "Which tests?", ["unit", "slow"], multiple=True, label="Tests")
NAME = events.Question("q3", "Name?", label="Name")


class Stub:
    endpoint = None
    workspace = "."
    role = None

    def __init__(self):
        self.answered = []

    def list_files_and_folders(self):
        return []

    def answer_questions(self, answers):
        self.answered.append(answers)


def _ui(*items):
    ui = chatTUI(Stub())
    ui.chat_screen = ChatScreen(ui.chat_history_panel, ui.input_box, ui._focus_scope,
                                ui.status_bar, ui.action_bar)
    question_form.open_form(ui, list(items))

    def press(*keys):
        for key in keys:
            if not ui.handle_global_input(key):
                ui.input_component.handle_input(key)

    def shown():
        return ui.question_message.base_text if ui.question_form else None

    return ui, press, shown


def test_a_form_is_filled_question_by_question_then_sent_all_at_once():
    """2026-10-29: Enter picks the highlight (or keeps the typed text) and goes
    to the next unanswered question, then to Send; Enter there answers every
    call and the message folds to one line per question."""
    ui, press, shown = _ui(DB, TESTS, NAME)

    assert shown().splitlines() == [
        "[Database] · Tests · Name · Send",
        "? Which database?",
        "▸ 1  Postgres",
        "  2  SQLite",
    ]
    assert ui.action_bar.hint.startswith("←→ question · ↑↓ option")

    press("\x1b[B", "\r")                            # SQLite
    assert shown().splitlines()[:2] == ["Database ✓ · [Tests] · Name · Send",
                                        "? Which tests?  (several: [SPACE] picks)"]
    assert "[SPACE] toggle" in ui.action_bar.hint

    press(" ", "\x1b[B", " ", *"on CI", "\r")         # unit + slow + own words
    assert ui.question_form.tab == 2
    press("\x1b[C")                                   # → on an empty input: Send
    assert shown().startswith("Database ✓ · Tests ✓ · Name · [Send]")
    assert "  Name      — no answer" in shown()
    press("\x1b[D", *"moka", "\r")                    # back to Name; answered: to Send
    assert ui.question_form.on_send
    assert ui.action_bar.hint == "←→ question · [ENTER] send all · [ESC] dismiss"

    press("\r")

    assert ui.agent.answered == [{
        "q1": events.Answer(["SQLite"], ""),
        "q2": events.Answer(["unit", "slow"], "on CI"),
        "q3": events.Answer([], "moka"),
    }]
    assert ui.question_form is None
    assert ui.chat_history_panel.messages[-1].base_text.splitlines() == [
        "? Which database? → SQLite",
        "? Which tests? → unit, slow · ✎ on CI",
        "? Name? → ✎ moka",
    ]
    assert ui.action_bar.hint.startswith("[/] command")


def test_arrows_move_the_text_cursor_once_something_is_typed():
    ui, press, shown = _ui(DB, NAME)
    press(*"ab", "\x1b[D", "X")
    assert ui.input_component.buffer.text == "aXb"
    assert ui.question_form.tab == 0
    press("\t")                                       # Tab still changes question
    assert ui.question_form.tab == 1


def test_a_lone_question_is_sent_by_enter_and_own_words_replace_the_pick():
    ui, press, _ = _ui(DB)
    assert "Send" not in ui.question_message.base_text
    press(*"MySQL", "\r")
    assert ui.agent.answered == [{"q1": events.Answer([], "MySQL")}]


def test_escape_dismisses_and_a_command_line_still_runs():
    ui, press, _ = _ui(DB, NAME)
    ui.input_component.update("/x")
    assert question_form.handle_key(ui, "\r") is False    # left to the command path
    ui.input_component.clear()

    press("\x1b")

    assert ui.agent.answered == [None]
    assert ui.chat_history_panel.messages[-1].base_text.splitlines() == [
        "? Which database? → dismissed", "? Name? → dismissed"]


def test_a_stopped_turn_folds_the_form_without_answering():
    ui, _, _ = _ui(DB)
    question_form.close_form(ui, "stopped")
    assert ui.agent.answered == []
    assert ui.chat_history_panel.messages[-1].base_text == "? Which database? → stopped"
