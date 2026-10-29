"""The form the ``ask`` tool's questions are answered in.

A run of adjacent ``ask`` calls (``events.Questions``) is one form, drawn as a
transcript message; the keys go to the input line as usual and the app routes
the form's keys here (``handle_key``). One tab per question, plus a last
``Send`` tab when there are several; Enter on it answers every call
(``Harness.answer_questions``) and the message folds to one line per question.
"""

from typing import Dict, List, Optional

from moka_code.harness import events

_SEND = "Send"


class QuestionForm:
    """Tabs, highlights, picks and own text of one form (no drawing)."""

    def __init__(self, items: List[events.Question]):
        self.items = list(items)
        self.tab = 0
        self.highlight = [0] * len(self.items)
        self.picked: List[List[int]] = [[] for _ in self.items]
        self.texts = [""] * len(self.items)

    # -- state -------------------------------------------------------------

    @property
    def on_send(self) -> bool:
        return self.tab == len(self.items)

    def _tabs(self) -> int:
        """Tab count: a lone question has no Send tab (Enter sends it)."""
        return len(self.items) + (1 if len(self.items) > 1 else 0)

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
        if self.on_send:
            return
        count = len(self.items[self.tab].options)
        if count:
            self.highlight[self.tab] = (self.highlight[self.tab] + step) % count

    def toggle(self) -> None:
        """Space on a ``multiple`` question: pick or unpick the highlight."""
        if self.on_send or not self.items[self.tab].options:
            return
        picked, option = self.picked[self.tab], self.highlight[self.tab]
        if option in picked:
            picked.remove(option)
        else:
            picked.append(option)
            picked.sort()

    def enter(self, text: str) -> bool:
        """Enter with the input's ``text``: write it as the own answer, or
        pick the highlight; then go to the next question. True when the form
        is to be sent (Enter on Send, or on a lone question)."""
        if self.on_send:
            return True
        question, index = self.items[self.tab], self.tab
        text = text.strip()
        if text:
            self.texts[index] = text
            if not question.multiple:
                self.picked[index] = []         # their own answer instead of one offered
        elif question.options:
            if not question.multiple:
                self.picked[index] = [self.highlight[index]]
                self.texts[index] = ""
            elif not self.picked[index]:
                self.picked[index] = [self.highlight[index]]
        if len(self.items) == 1:
            return True
        self.tab = self._next_tab(index)
        return False

    def _next_tab(self, index: int) -> int:
        """The next unanswered question after ``index`` (wrapping), else Send."""
        count = len(self.items)
        for offset in range(1, count):
            other = (index + offset) % count
            if not self.answered(other):
                return other
        return count

    # -- drawing -----------------------------------------------------------

    def render(self) -> str:
        """The open form, as the transcript message's text."""
        lines = []
        if self._tabs() > 1:
            names = [self.label(i) + (" ✓" if self.answered(i) else "")
                     for i in range(len(self.items))] + [_SEND]
            lines.append(" · ".join(f"[{name}]" if i == self.tab else name
                                    for i, name in enumerate(names)))
        if self.on_send:
            lines.append("Send these answers?")
            width = max(len(self.label(i)) for i in range(len(self.items)))
            lines += [f"  {self.label(i).ljust(width)}  {self.summary(i)}"
                      for i in range(len(self.items))]
            return "\n".join(lines)
        question = self.items[self.tab]
        lines.append(f"? {question.question}"
                     + ("  (several: [SPACE] picks)" if question.multiple else ""))
        for i, option in enumerate(question.options):
            cursor = "▸" if i == self.highlight[self.tab] else " "
            check = "  ✓" if i in self.picked[self.tab] else ""
            lines.append(f"{cursor} {i + 1}  {option}{check}")
        if self.texts[self.tab]:
            lines.append(f"  ✎ {self.texts[self.tab]}")
        return "\n".join(lines)

    def folded(self, outcome: str) -> str:
        """One line per question once the form is closed; ``outcome`` is
        ``"sent"``, ``"dismissed"`` or ``"stopped"``."""
        return "\n".join(
            f"? {question.question} → "
            + (self.summary(i) if outcome == "sent" else outcome)
            for i, question in enumerate(self.items))

    def hint(self) -> str:
        """The action line while the form is open."""
        tabs = "←→ question · " if self._tabs() > 1 else ""
        if self.on_send:
            return f"{tabs}[ENTER] send all · [ESC] dismiss"
        question = self.items[self.tab]
        pick = "↑↓ option · [ENTER] pick or write · " if question.options else "[ENTER] write · "
        space = "[SPACE] toggle · " if question.multiple and question.options else ""
        return f"{tabs}{pick}{space}[ESC] dismiss"


# -- the app side -------------------------------------------------------------

def open_form(app, items: List[events.Question]) -> None:
    """Show the form for ``items`` and give the input the keys."""
    from moka_code.ui.tui.msg_types import QuestionMsg

    form = QuestionForm(items)
    app.question_form = form
    app.question_message = app.chat_history_panel.add_message(
        form.render(), msg_type=QuestionMsg())
    app.chat_history_panel.clear_focus()
    app._set_app_focus("input")
    _refresh(app)


def close_form(app, outcome: str) -> None:
    """Fold the form; ``"sent"`` answers the questions, ``"dismissed"``
    answers them as dismissed, ``"stopped"`` (the turn ended) answers nothing."""
    form, message = app.question_form, app.question_message
    if form is None:
        return
    app.question_form = None
    app.question_message = None
    if message is not None:
        message.replace_text(form.folded(outcome))
    if outcome != "stopped":
        app.agent.answer_questions(form.answers() if outcome == "sent" else None)
    app._update_action_strip()


def handle_key(app, key: str) -> bool:
    """A key while the form is open and the input focused; True when used.

    ←/→ change question only on an empty input (otherwise they move the text
    cursor); a ``/`` line still runs as a command."""
    form = app.question_form
    text = app.input_component.buffer.text
    if key == "\x1b":
        close_form(app, "dismissed")
        return True
    if key in ("\t", "\x1b[Z") or (key in ("\x1b[C", "\x1b[D") and not text):
        form.move_tab(-1 if key in ("\x1b[Z", "\x1b[D") else 1)
    elif key in ("\x1b[A", "\x1b[B"):
        form.move_option(-1 if key == "\x1b[A" else 1)
    elif key == " " and not text and not form.on_send and form.items[form.tab].multiple:
        form.toggle()
    elif key in ("\r", "\n") and not text.startswith("/"):
        send = form.enter(text)
        app.input_component.clear()
        if send:
            close_form(app, "sent")
            return True
    else:
        return False
    _refresh(app)
    return True


def _refresh(app) -> None:
    if app.question_message is not None and app.question_form is not None:
        app.question_message.replace_text(app.question_form.render())
    app._update_action_strip()
