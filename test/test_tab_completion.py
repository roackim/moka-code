"""Accepting a completion inserts no space and closes the menu (like ESC).

The next menu opens only once the user types the space themselves.
"""

from moka_code.ui.commands.registry import COMMANDS
from moka_code.ui.tui.components.input.input import InputComponent


def _input(context_items=()):
    inp = InputComponent("> ")
    inp.set_layout(0, 0, 80, 3)
    inp.setup_commands(sorted(COMMANDS), {})
    inp.setup_command_registry(COMMANDS)
    inp.setup_context(lambda: list(context_items))
    return inp


def _type(inp, text):
    for char in text:
        inp.handle_input(char)


def test_tab_on_a_command_completes_without_space_and_closes():
    inp = _input()
    _type(inp, "/rel")
    assert inp.has_active_completion()
    inp.handle_input("\t")
    assert inp.buffer.text == "/reload"
    assert not inp.has_active_completion()


def test_tab_on_an_argument_adds_no_space():
    inp = _input()
    _type(inp, "/config serv")
    assert inp.has_active_completion()
    inp.handle_input("\t")
    assert inp.buffer.text == "/config servers"
    assert not inp.has_active_completion()


def test_typing_a_space_after_completion_opens_the_next_menu():
    inp = _input()
    _type(inp, "/conf")
    inp.handle_input("\t")
    assert inp.buffer.text == "/config"
    _type(inp, " ")
    assert inp.buffer.text == "/config "
    assert inp.has_active_completion()


def test_tab_on_a_file_adds_no_space_but_a_folder_keeps_the_menu_open():
    inp = _input(["src/", "src/a.py", "README.md"])
    _type(inp, "see @READ")
    inp.handle_input("\t")
    assert inp.buffer.text == "see @README.md"
    assert not inp.has_active_completion()

    inp = _input(["src/", "src/a.py", "README.md"])
    _type(inp, "@sr")
    inp.handle_input("\t")
    assert inp.buffer.text == "@src/"
    assert inp.has_active_completion()


def test_space_after_a_picker_command_submits_it():
    """``/model `` opens the picker (submits ``/model``) instead of an inline menu."""
    inp = _input()
    submitted = []
    inp.on_submit = submitted.append
    _type(inp, "/model ")
    assert submitted == ["/model"]
    assert inp.buffer.text == ""
    assert not inp.has_active_completion()


def test_space_after_other_commands_does_not_submit():
    inp = _input()
    submitted = []
    inp.on_submit = submitted.append
    _type(inp, "/config ")
    assert submitted == []
    assert inp.buffer.text == "/config "
