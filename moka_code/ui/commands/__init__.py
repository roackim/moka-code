"""Command package public API.

Handler functions live in per-domain modules (``core``, ``models``, ``roles``,
``themes``, ``conversation``). The registry in
:mod:`moka_code.ui.commands.registry` assembles them; this module is the public
import path.
"""

from .base import ChatUIProtocol, Command, Param
from .registry import (
    COMMANDS,
    get_command_descriptions,
    get_command_list,
    get_subcommand_descriptions,
    get_subcommand_list,
    handle_command,
)

__all__ = [
    "COMMANDS",
    "ChatUIProtocol",
    "Command",
    "Param",
    "get_command_descriptions",
    "get_command_list",
    "get_subcommand_descriptions",
    "get_subcommand_list",
    "handle_command",
]
