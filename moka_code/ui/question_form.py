"""The form the ``ask`` tool's questions are answered in.

A run of adjacent ``ask`` calls (``events.Questions``) is one form, drawn in a
selector above the input (like the ``@`` file menu): the tabs in its top
border, the question wrapped above the options, and a last row for the user's
own answer, which mirrors what they type in the input. The keys go to the
input line as usual and the app routes the form's keys here (``handle_key``).
One tab per question, plus a last ``Send`` tab when there are several; Enter on
it answers every call (``Harness.answer_questions``) and the transcript gets
one line per question. Esc first asks whether to dismiss them.
"""

from typing import Dict, List

from moka_code.harness import events
from moka_code.ui.tui.colors import theme
from moka_code.ui.tui.components.menu import SelectionMenu

_SEND = "Send"
#: The input's placeholder while a form is open.
PLACEHOLDER = "your own answer…"
_KEEP, _DISMISS = "Keep answering", "Dismiss"


class QuestionForm:
    """Tabs, highlights, picks and own text of one form (no drawing).

    On a question, rows ``0..n-1`` are its options and row ``n`` is the own
    answer (``own_row``)."""

    def __init__(self, items: List[events.Question]):
        self.items = list(items)
        self.tab = 0
        self.highlight = [0] * len(self.items)
        self.picked: List[List[int]] = [[] for _ in self.items]
        self.texts = [""] * len(self.items)
        self.typed = ""             # the input's text, mirrored on the own row
        self.confirming = False     # Esc was pressed: dismiss?
        self.confirm_index = 0      # 0 keep answering, 1 dismiss

    # -- state -------------------------------------------------------------

    @property
    def on_send(self) -> bool:
        return self.tab == len(self.items)

    def _tabs(self) -> int:
        """Tab count: a lone question has no Send tab (Enter sends it)."""
        return len(self.items) + (1 if len(self.items) > 1 else 0)

    def own_row(self, index: int) -> int:
        return len(self.items[index].options)

    def answered(self, index: int) -> bool:
        return bool(self.picked[index] or self.texts[index])

    def label(self, index: int) -> str:
        return self.items[index].label or f"Q{index + 1}"

    def summary(self, index: int) -> str:
        """The answer of question ``index`` in a few words."""
        options = self.items[index].options
        parts = [", ".join(options[i] for i in self.picked[index])] if self.picked[index] else []
        if self.texts[index]:
            parts.append(f"✎ {self.texts[index]}")
        return " · ".join(parts) or "— no answer"

    def answers(self) -> Dict[str, events.Answer]:
        return {
            question.id: events.Answer(
                choices=[question.options[i] for i in self.picked[index]],
                text=self.texts[index])
            for index, question in enumerate(self.items)
            if self.answered(index)
        }

    # -- moves -------------------------------------------------------------

    def move_tab(self, step: int) -> None:
        self.tab = (self.tab + step) % self._tabs()

    def move_option(self, step: int) -> None:
        if self.confirming:
            self.confirm_index = 1 - self.confirm_index
        elif not self.on_send:
            rows = self.own_row(self.tab) + 1
            self.highlight[self.tab] = (self.highlight[self.tab] + step) % rows

    def set_typed(self, text: str) -> None:
        """The input changed: typing goes to the own-answer row."""
        self.typed = text
        if text.strip() and not self.on_send:
            self.highlight[self.tab] = self.own_row(self.tab)

    def toggle(self) -> None:
        """Space on a ``multiple`` question: pick or unpick the highlight (on
        the own row: drop the own answer)."""
        if self.on_send:
            return
        option = self.highlight[self.tab]
        if option == self.own_row(self.tab):
            self.texts[self.tab] = ""
            return
        picked = self.picked[self.tab]
        if option in picked:
            picked.remove(option)
        else:
            picked.append(option)
            picked.sort()

    def enter(self, text: str) -> str:
        """Enter with the input's ``text``. Returns ``"send"`` (Enter on Send,
        or on a lone question), ``"write"`` (the own row with nothing to keep:
        the user must type first) or ``""`` (answered: on to the next tab)."""
        if self.on_send:
            return "send"
        question, index = self.items[self.tab], self.tab
        text = text.strip()
        on_own = self.highlight[index] == self.own_row(index)
        if text:
            self.texts[index] = text
            if not question.multiple:
                self.picked[index] = []         # their own answer instead of one offered
        elif on_own:
            if not self.texts[index]:
                return "write"
        elif not question.multiple:
            self.picked[index] = [self.highlight[index]]
            self.texts[index] = ""
        elif not self.picked[index]:
            self.picked[index] = [self.highlight[index]]
        if len(self.items) == 1:
            return "send"
        self.tab = self._next_tab(index)
        return ""

    def _next_tab(self, index: int) -> int:
        """The next unanswered question after ``index`` (wrapping), else Send."""
        count = len(self.items)
        for offset in range(1, count):
            other = (index + offset) % count
            if not self.answered(other):
                return other
        return count

    # -- drawing -----------------------------------------------------------

    def tab_strip(self) -> str:
        """The menu title: the tabs, the current one in brackets."""
        if self._tabs() == 1:
            return "question"
        names = [self.label(i) + (" ✓" if self.answered(i) else "")
                 for i in range(len(self.items))] + [_SEND]
        return " · ".join(f"[{name}]" if i == self.tab else name
                          for i, name in enumerate(names))

    def show(self, menu) -> None:
        """Draw the current tab (or the dismiss warning) into the selector."""
        menu.frame_color = theme.WARNING if self.confirming else theme.PERMISSION
        if self.confirming:
            menu.title = "dismiss?"
            count = "this question" if len(self.items) == 1 else "these questions"
            menu.header = f"Dismiss {count}? The model is told you declined to answer."
            menu.set_items([_KEEP, _DISMISS], descriptions={})
            menu.selected_index = self.confirm_index
            return
        menu.title = self.tab_strip()
        if self.on_send:
            menu.header = "Send these answers?"
            labels = [self.label(i) for i in range(len(self.items))]
            menu.set_items(labels, descriptions={label: self.summary(i)
                                                 for i, label in enumerate(labels)})
            menu.selected_index = -1
            return
        question = self.items[self.tab]
        menu.header = question.question + ("\n(several answers: [SPACE] picks)"
                                           if question.multiple else "")
        rows = [self.row(i) for i in range(self.own_row(self.tab) + 1)]
        menu.set_items(rows, descriptions={})
        menu.selected_index = self.highlight[self.tab]

    def row(self, option: int) -> str:
        """A row: an option (numbered, or a checkbox on a ``multiple``
        question), or the own answer, showing what is typed."""
        question, index = self.items[self.tab], self.tab
        if option == self.own_row(index):
            text = self.typed.strip() or self.texts[index]
            if not text:
                return "✎ your own answer (type it below)"
            done = "  ✓" if text == self.texts[index] and not self.typed.strip() else ""
            return f"✎ {text}{done}"
        if question.multiple:
            box = "[x]" if option in self.picked[index] else "[ ]"
            return f"{box} {question.options[option]}"
        check = "  ✓" if option in self.picked[index] else ""
        return f"{option + 1}. {question.options[option]}{check}"

    def folded(self, outcome: str) -> str:
        """One line per question once the form is closed; ``outcome`` is
        ``"sent"``, ``"dismissed"`` or ``"stopped"``."""
        return "\n".join(
            f"? {question.question} → "
            + (self.summary(i) if outcome == "sent" else outcome)
            for i, question in enumerate(self.items))

    def hint(self) -> str:
        """The action line while the form is open."""
        if self.confirming:
            return "↑↓ choose · [ENTER] confirm · [ESC] keep answering"
        tabs = "←→ question · " if self._tabs() > 1 else ""
        if self.on_send:
            return f"{tabs}[ENTER] send all · [ESC] dismiss"
        question = self.items[self.tab]
        if self.highlight[self.tab] == self.own_row(self.tab):
            return f"{tabs}type your answer · [ENTER] keep it · [ESC] dismiss"
        space = "[SPACE] toggle · " if question.multiple else ""
        return f"{tabs}↑↓ option · [ENTER] pick · {space}or type your own · [ESC] dismiss"


# -- the app side -------------------------------------------------------------

class FormMenu(SelectionMenu):
    """The selector above the input (where ``@`` files show); placed again on
    every draw so it follows the input."""

    def __init__(self, app):
        super().__init__(compositor=app.compositor, frame_color=theme.PERMISSION,
                         content_color=theme.DEFAULT)
        self.fill_width = True
        self.max_height = 16
        self._input = app.input_component

    def render(self, buffer) -> None:
        if self.is_visible:
            self._input.place_menu_above_input(self)
        super().render(buffer)


def open_form(app, items: List[events.Question]) -> None:
    """Show the form for ``items``; the input keeps the keys (and its cursor:
    the menu is one of its own, typing still reaches it) and shows the
    placeholder."""
    form = QuestionForm(items)
    menu = FormMenu(app)
    menu.on_click = lambda: _click(app)
    app.input_component._own_menus.append(menu)
    app.input_component.placeholder = PLACEHOLDER
    app.question_form, app.question_menu = form, menu
    app.chat_history_panel.clear_focus()
    app._set_app_focus("input")
    _refresh(app)


def close_form(app, outcome: str) -> None:
    """Close the form and add its folded record to the transcript; ``"sent"``
    answers the questions, ``"dismissed"`` answers them as dismissed,
    ``"stopped"`` (the turn ended) answers nothing."""
    from moka_code.ui.tui.msg_types import QuestionMsg

    form, menu = app.question_form, app.question_menu
    if form is None:
        return
    app.question_form = app.question_menu = None
    menu.hide()
    if menu in app.input_component._own_menus:
        app.input_component._own_menus.remove(menu)
    app.input_component.placeholder = ""
    app.input_box.mark_changed()
    app.chat_history_panel.add_message(form.folded(outcome), msg_type=QuestionMsg())
    if outcome != "stopped":
        app.agent.answer_questions(form.answers() if outcome == "sent" else None)
    app._update_action_strip()


def input_changed(app) -> None:
    """The input's text changed (``InputComponent.on_change``)."""
    if app.question_form is not None:
        app.question_form.set_typed(app.input_component.buffer.text)
        _refresh(app)


def _click(app) -> None:
    """A click on a row: like Enter on it (Space on a ``multiple`` option)."""
    form, menu = app.question_form, app.question_menu
    if form is None or form.on_send:
        return
    if form.confirming:
        form.confirm_index = menu.selected_index
        _confirm(app)
        return
    form.highlight[form.tab] = menu.selected_index
    if form.items[form.tab].multiple and menu.selected_index != form.own_row(form.tab):
        form.toggle()
        _refresh(app)
    else:
        _enter(app)


def _confirm(app) -> None:
    form = app.question_form
    if form.confirm_index == 1:
        close_form(app, "dismissed")
        return
    form.confirming = False
    _refresh(app)


def _enter(app) -> None:
    form = app.question_form
    result = form.enter(app.input_component.buffer.text)
    if result == "write":
        app.flash_hint("type your answer in the input first")
        return
    app.input_component.clear()
    if result == "send":
        close_form(app, "sent")
    else:
        _refresh(app)


def handle_key(app, key: str) -> bool:
    """A key while the form is open and the input focused; True when used.

    ←/→ change question only on an empty input (otherwise they move the text
    cursor); a ``/`` line still runs as a command. Esc asks before
    dismissing; while it asks, only ↑↓, Enter and Esc do anything."""
    form = app.question_form
    text = app.input_component.buffer.text
    if form.confirming:
        if key == "\x1b":
            form.confirming = False
        elif key in ("\x1b[A", "\x1b[B"):
            form.move_option(1)
        elif key in ("\r", "\n"):
            _confirm(app)
            return True
        _refresh(app)
        return True
    if key == "\x1b":
        form.confirming, form.confirm_index = True, 0
    elif key in ("\t", "\x1b[Z") or (key in ("\x1b[C", "\x1b[D") and not text):
        form.move_tab(-1 if key in ("\x1b[Z", "\x1b[D") else 1)
    elif key in ("\x1b[A", "\x1b[B"):
        form.move_option(-1 if key == "\x1b[A" else 1)
    elif key == " " and not text and not form.on_send and form.items[form.tab].multiple:
        form.toggle()
    elif key in ("\r", "\n") and not text.startswith("/"):
        _enter(app)
        return True
    else:
        return False
    _refresh(app)
    return True


def _refresh(app) -> None:
    if app.question_form is not None:
        app.question_form.show(app.question_menu)
        app.question_menu.mark_changed()
        if app.compositor is not None:
            app.compositor.request_render()
    app._update_action_strip()
