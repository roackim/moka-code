"""A collapsed paste (``[Pasted N lines]``) always expands on submit: edits
elsewhere in the input and drafts set aside keep it."""

from moka_code.ui.tui.components.input.input import InputComponent
from moka_code.ui.tui.components.input.text_buffer import TextBuffer
from moka_code.ui.tui.events import PasteEvent

PASTE = "line1\nline2\nline3\nline4"


def _input():
    component = InputComponent()
    component.focused = True
    component.setup_context(lambda: ["README.md", "src/"])
    component.width, component.height = 80, 5
    sent = []
    for handler in component.input_handlers:
        if hasattr(handler, "on_submit"):
            handler.on_submit = sent.append
    return component, sent


def test_accepting_a_completion_keeps_a_paste_before_it():
    component, sent = _input()
    component.handle_input(PasteEvent(PASTE))
    for key in " @READ":
        component.handle_input(key)
    component.handle_input("\r")        # accepts @README.md, then submits
    assert sent == [PASTE + " @README.md "]


def test_replace_text_shifts_a_paste_after_the_change():
    buffer = TextBuffer("see @RE ")
    buffer.cursor_pos = len(buffer.text)
    buffer.insert_token("[Pasted 4 lines]", PASTE, "paste")
    buffer.replace_text("see @README.md [Pasted 4 lines]")
    assert buffer.expanded_text() == "see @README.md " + PASTE


def test_a_draft_snapshot_brings_its_paste_back():
    buffer = TextBuffer()
    buffer.insert_token("[Pasted 4 lines]", PASTE, "paste")
    snapshot = buffer.snapshot()
    buffer.text = "something else"
    buffer.restore(snapshot)
    assert buffer.expanded_text() == PASTE
