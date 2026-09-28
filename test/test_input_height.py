"""Input box grows with content but is capped by ui_max_input_height."""

from types import SimpleNamespace

from moka_code.ui.tui.components.input.input import InputComponent


def _component(max_height):
    inp = InputComponent(" ")
    inp.config = SimpleNamespace(ui_max_input_height=max_height)
    return inp


def test_long_text_is_capped_by_max_input_height():
    inp = _component(8)
    inp.buffer.text = "\n".join(f"line {i}" for i in range(50))
    inp.buffer.cursor_pos = len(inp.buffer.text)

    assert inp.get_preferred_height(40) == 8


def test_short_text_uses_natural_height():
    inp = _component(8)
    inp.buffer.text = "one\ntwo"
    inp.buffer.cursor_pos = len(inp.buffer.text)

    assert inp.get_preferred_height(40) == 2


def test_missing_cap_leaves_height_unbounded():
    inp = InputComponent(" ")
    inp.config = SimpleNamespace()

    inp.buffer.text = "\n".join("x" for _ in range(30))
    assert inp.get_preferred_height(40) == 30
