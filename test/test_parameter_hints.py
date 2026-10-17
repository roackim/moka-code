"""The grey hint after the typed text names the slots still to type (the
completion menu lists the options), and shows while a menu is open."""

import pytest

from moka_code.ui.commands.registry import COMMANDS
from moka_code.ui.tui.components.input.input import InputComponent


@pytest.fixture
def entry():
    component = InputComponent("", id="entry")
    component.setup_command_registry(COMMANDS)
    return component


def _hint(entry, text):
    entry.buffer.text = text
    return entry._get_parameter_hint()


@pytest.mark.parametrize("text,hint", [
    ("/sandbox ", " <subcommand>"),                     # a branch: just the generic slot
    ("/sandbox copy ", " <NAME> <SCOPE> <NEW_NAME>"),
    ("/sandbox copy dev ", " <SCOPE> <NEW_NAME>"),      # the typed slot is not hinted
    ("/sandbox copy dev", " <SCOPE> <NEW_NAME>"),       # nor the one being typed
    ("/sandbox copy dev global new", ""),
    ("/model ", " [MODEL]"),                            # optional params in brackets
    ("/clear ", ""),                                    # no params
])
def test_the_hint_names_the_slots_still_to_type(entry, text, hint):
    assert _hint(entry, text) == hint


@pytest.mark.parametrize("text", ["/", "/sa", "/sandbox", "/sandbox co", "/model", "hello", "/zzz "])
def test_nothing_is_hinted_while_a_name_is_still_being_typed(entry, text):
    assert _hint(entry, text) == ""


def test_the_hint_is_drawn_while_a_completion_menu_is_open(entry):
    from moka_code.ui.commands.registry import (
        get_command_descriptions, get_command_list, get_subcommand_descriptions, get_subcommand_list)
    from moka_code.ui.tui.buffer import Buffer

    entry.setup_commands(get_command_list(), get_command_descriptions)
    entry.setup_subcommands(get_subcommand_list, get_subcommand_descriptions)
    entry.focused = True
    entry.x, entry.y, entry.width, entry.height = 0, 0, 60, 1
    entry.buffer.text = "/sandbox "
    entry.buffer.cursor_pos = len("/sandbox ")
    entry._on_text_changed()
    assert entry.has_active_completion()                # the subcommand menu is open

    screen = Buffer(60, 3)
    entry.render(screen)

    assert "<subcommand>" in screen.render()
