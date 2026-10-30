"""The ``ask`` form: tabs, picks, own answers, send and dismiss, from the keys."""

from moka_code.harness import events
from moka_code.ui import question_form
from moka_code.ui.app import chatTUI
from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.chat_screen import ChatScreen
from moka_code.ui.tui.colors import theme

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
    ui.input_component.set_layout(0, 20, 50, 1)
    question_form.open_form(ui, list(items))

    def press(*keys):
        for key in keys:
            if not ui.handle_global_input(key):
                ui.input_component.handle_input(key)

    def shown():
        """The selector above the input, as drawn (borders stripped)."""
        if ui.question_form is None:
            return None
        buffer = Buffer(50, 21)
        ui.question_menu.render(buffer)
        menu = ui.question_menu
        rows = ["".join(c.char for c in buffer.cells[y][:50]).rstrip()
                for y in range(menu.y, menu.y + menu.height)]
        return "\n".join(rows)

    return ui, press, shown


def test_a_form_is_filled_question_by_question_then_sent_all_at_once():
    """2026-10-29: the form is a selector above the input: tabs in its top
    border, the question, the options. Enter picks the highlight (or keeps
    the typed text) and goes to the next unanswered question, then to Send;
    Enter there answers every call and the transcript gets one line each."""
    ui, press, shown = _ui(DB, TESTS, NAME)

    assert shown().splitlines() == [
        "┌─ [Database] · Tests · Name · Send ─────────────┐",
        "│ Which database?                                │",
        "│                                                │",
        "│ 1. Postgres                                    │",
        "│ 2. SQLite                                      │",
        "└────────────────────────────────────────────────┘",
    ]
    assert ui.question_menu.y + ui.question_menu.height == 20      # right above the input
    assert ui.action_bar.hint.startswith("←→ question · ↑↓ option")
    assert ui.input_component.placeholder == "your own answer…"
    assert ui._input_box_fg() is theme.PERMISSION              # the input answers the form
    line = Buffer(50, 21)
    ui.input_component.render(line)
    assert "your own answer…" in "".join(c.char for c in line.cells[20])

    press("\x1b[B", "\r")                            # SQLite
    assert shown().splitlines()[:5] == [
        "┌─ Database ✓ · [Tests] · Name · Send ───────────┐",
        "│ Which tests?                                   │",
        "│ (several answers: [SPACE] picks)               │",
        "│                                                │",
        "│ [ ] unit                                       │",
    ]
    assert "[SPACE] toggle" in ui.action_bar.hint

    press(" ", "\x1b[B", " ")                         # unit + slow
    assert ui.question_menu.selected_index == 1
    press(*"on CI")                                   # own words: typed in the input only
    assert "on CI" not in shown()
    assert ui.question_menu.selected_index == -1       # Enter keeps the text, not an option
    assert ui.action_bar.hint == "[ENTER] keep your answer · [ESC] dismiss"
    press("\r")
    assert ui.question_form.tab == 2
    press("\x1b[D")                                   # back to Tests to look
    assert "│ [x] unit" in shown() and "│ [x] slow" in shown()
    assert "└ ✎ on CI ─" in shown()                    # a kept answer: bottom border
    press("\x1b[C", "\x1b[C")                         # → on an empty input: Send
    assert "│ Name      — no answer" in shown()
    press("\x1b[D", *"moka", "\r")                    # back to Name; answered: to Send
    assert ui.question_form.on_send
    assert ui.action_bar.hint == "←→ question · [ENTER] send all · [ESC] dismiss"
    assert shown().splitlines()[1:6] == [
        "│ Send these answers?                            │",
        "│                                                │",
        "│ Database  SQLite                               │",
        "│ Tests     unit, slow · ✎ on CI                 │",
        "│ Name      ✎ moka                               │",
    ]

    press("\r")

    assert ui.agent.answered == [{
        "q1": events.Answer(["SQLite"], ""),
        "q2": events.Answer(["unit", "slow"], "on CI"),
        "q3": events.Answer([], "moka"),
    }]
    assert ui.question_form is None
    assert not ui.compositor or ui.question_menu not in ui.compositor.overlays
    assert ui.chat_history_panel.messages[-1].base_text.splitlines() == [
        "Which database? → SQLite",
        "Which tests? → unit, slow · ✎ on CI",
        "Name? → ✎ moka",
    ]
    assert ui.action_bar.hint.startswith("[/] command")


def test_a_click_on_an_option_picks_or_toggles_it():
    from moka_code.ui.tui.events import MouseEvent

    ui, press, shown = _ui(TESTS, DB)
    shown()
    menu = ui.question_menu
    first_option = menu.y + 1 + menu._header_rows
    menu.handle_input(MouseEvent(2, first_option + 1, 0, True))     # slow
    assert ui.question_form.picked[0] == [1]
    press("\t")
    shown()
    menu.handle_input(MouseEvent(2, menu.y + 1 + menu._header_rows, 0, True))   # Postgres
    assert ui.question_form.picked[1] == [0] and ui.question_form.on_send


def test_arrows_move_the_text_cursor_once_something_is_typed():
    ui, press, shown = _ui(DB, NAME)
    press(*"ab", "\x1b[D", "X")
    assert ui.input_component.buffer.text == "aXb"
    assert ui.question_form.tab == 0
    press("\t")                                       # Tab still changes question
    assert ui.question_form.tab == 1


def test_a_lone_question_is_sent_by_enter_and_own_words_replace_the_pick():
    ui, press, shown = _ui(DB)
    assert "Send" not in shown()
    press(*"MySQL", "\r")
    assert ui.agent.answered == [{"q1": events.Answer([], "MySQL")}]


def test_a_question_without_options_needs_typed_text():
    ui, press, shown = _ui(NAME, DB)
    assert "│ (type your answer below)" in shown()
    press("\r")
    assert ui.question_form.tab == 0                   # nothing to keep: stays
    assert ui.action_bar.hint == "type your answer first"


def test_escape_asks_before_dismissing_and_a_command_line_still_runs():
    """2026-10-29: Esc shows a warning in the selector; Enter on its default
    (keep answering) or Esc again goes back; ↓ Enter dismisses."""
    ui, press, shown = _ui(DB, NAME)
    ui.input_component.update("/x")
    assert question_form.handle_key(ui, "\r") is False    # left to the command path
    ui.input_component.clear()

    press("\x1b")
    assert shown().splitlines()[:2] == [
        "┌─ dismiss? ─────────────────────────────────────┐",
        "│ Dismiss these questions? The model is told you │",
    ]
    press("\r")                                         # keep answering
    assert ui.agent.answered == [] and not ui.question_form.confirming
    press("\x1b", "\x1b")                               # Esc again: back too
    assert ui.agent.answered == [] and not ui.question_form.confirming

    press("\x1b", "\x1b[B", "\r")

    assert ui.agent.answered == [None]
    assert ui.input_component.placeholder == ""
    assert ui.chat_history_panel.messages[-1].base_text.splitlines() == [
        "Which database? → dismissed", "Name? → dismissed"]


def test_a_stopped_turn_folds_the_form_without_answering():
    ui, _, _ = _ui(DB)
    question_form.close_form(ui, "stopped")
    assert ui.agent.answered == []
    assert ui.chat_history_panel.messages[-1].base_text == "Which database? → stopped"


def test_the_folded_record_colors_the_question_and_the_answer():
    """2026-10-30: no ``?`` prefix; the question in the form's color
    (PERMISSION), the answer in the user's, also on a wrapped row."""
    from moka_code.ui.chat_message import Message, wrapped_spans
    from moka_code.ui.tui.colors import theme
    from moka_code.ui.tui.msg_types import QuestionMsg

    ui, _, _ = _ui(DB)
    lines = ui.question_form.folded_lines("dismissed")
    line, spans = lines[0]
    assert line == "Which database? → dismissed"
    assert spans[0] == (0, len("Which database?"), "PERMISSION")
    assert spans[-1][2] == "MUTED"                    # no answer: muted

    logical = [("What is the name? → moka chat", [(0, 17, "ASSISTANT_TEXT"), (17, 20, "MUTED"),
                                                  (20, 29, "USER_TEXT")])]
    rows = wrapped_spans(logical, "What is the name?\n→ moka chat")
    assert rows["What is the name?"] == [(0, 17, "ASSISTANT_TEXT")]
    assert rows["→ moka chat"] == [(0, 2, "MUTED"), (2, 11, "USER_TEXT")]

    msg = Message(logical[0][0], msg_type=QuestionMsg(), max_width=40)
    msg.set_line_spans(logical)
    assert msg.component.highlighter(logical[0][0])[-1] == (20, 29, theme.USER_TEXT)
