"""Accepting a completion adds a space, so the next word's menu opens.

A folder from the ``@`` menu is the exception: no space, the menu stays open.
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


def test_tab_on_a_command_adds_a_space():
    inp = _input()
    _type(inp, "/rel")
    assert inp.has_active_completion()
    inp.handle_input("\t")
    assert inp.buffer.text == "/reload "
    assert not inp.has_active_completion()   # /reload takes no argument


def test_tab_on_a_command_opens_its_subcommand_menu():
    inp = _input()
    _type(inp, "/conf")
    inp.handle_input("\t")
    assert inp.buffer.text == "/config "
    assert inp.has_active_completion()
    assert "servers" in inp.argument_completion.menu.items


def test_tab_on_an_argument_opens_the_next_arguments_menu():
    inp = _input()
    _type(inp, "/config ro")
    inp.handle_input("\t")
    assert inp.buffer.text == "/config role "
    assert inp.has_active_completion()        # role names


def test_tab_on_a_file_adds_a_space_but_a_folder_keeps_the_menu_open():
    inp = _input(["src/", "src/a.py", "README.md"])
    _type(inp, "see @READ")
    inp.handle_input("\t")
    assert inp.buffer.text == "see @README.md "
    assert not inp.has_active_completion()

    inp = _input(["src/", "src/a.py", "README.md"])
    _type(inp, "@sr")
    inp.handle_input("\t")
    assert inp.buffer.text == "@src/"
    assert inp.has_active_completion()


def test_tab_mid_text_reuses_the_existing_space():
    inp = _input(["README.md"])
    _type(inp, "see @READ now")
    inp.buffer.cursor_pos = len("see @READ")
    inp._on_text_changed()
    inp.handle_input("\t")
    assert inp.buffer.text == "see @README.md now"
    assert inp.buffer.cursor_pos == len("see @README.md ")


def test_model_space_keeps_the_text_and_lists_every_model(monkeypatch):
    """``/model `` stays in the input and shows every model inline."""
    from moka_code.harness.endpoint import ModelInfo
    from moka_code.ui.commands import models

    pairs = [("local", ModelInfo(id="Qwen:low")), ("local", ModelInfo(id="Qwen:high")),
             ("openrouter", ModelInfo(id="deepseek/x"))]
    monkeypatch.setattr(models, "_cached_pairs", lambda: pairs)
    inp = _input()
    submitted = []
    inp.on_submit = submitted.append
    _type(inp, "/model ")
    assert submitted == []
    assert inp.buffer.text == "/model "
    assert inp.has_active_completion()
    assert sorted(inp.argument_completion.menu.items) == ["Qwen:high", "Qwen:low", "deepseek/x"]
