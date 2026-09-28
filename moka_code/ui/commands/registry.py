"""Command registry: the single assembly point for all slash commands.

Handlers live in per-domain modules (``core``, ``models``, ``roles``,
``themes``, ``conversation``). Those modules depend only on
:mod:`moka_code.ui.commands.base`; this module is the only place that knows
about all of them, which keeps the import graph acyclic.

Most commands are plain handler functions with metadata; only the ``config``
command is a small ``Command`` subclass (for contextual role completions).
"""

from __future__ import annotations

from typing import Dict, List

from .base import (
    ChatUIProtocol,
    Command,
    Param,
    role_descriptions,
    role_name_completions,
)
from .conversation import (
    conversation_export, conversation_import, conversation_session, json_file_completions,
)
from .core import (
    ConfigCommand,
    cmd_activity,
    cmd_clear,
    cmd_compact,
    cmd_edit,
    cmd_exit,
    cmd_help,
    cmd_reload,
    cmd_stop,
    cmd_terminal,
)
from .models import effort_command, model_command
from .roles import cmd_role
from .sandbox import (
    sandbox_base_completions,
    sandbox_build,
    sandbox_config,
    sandbox_help,
    sandbox_id_completions,
    sandbox_id_descriptions,
    sandbox_init,
    sandbox_stop,
    sandbox_terminal,
    sandbox_runtime_completions,
    sandbox_start,
)
from .themes import theme_command

# Help needs the whole registry, so its handler is assembled here.
async def _help(ui: ChatUIProtocol, args: List[str]):
    await cmd_help(ui, args, COMMANDS)


# Command Registry
COMMANDS: Dict[str, Command] = {
    "help":         Command("help", "Show available commands", handler=_help),
    "clear":        Command("clear", "Clear chat history", handler=cmd_clear),
    "reload":       Command("reload", "Reload config files and validate role files from disk",
                            handler=cmd_reload),
    "config":       ConfigCommand(),
    "edit":         Command("edit", "Open a file in $EDITOR", handler=cmd_edit,
                            params=[Param("FILE", path=True)]),
    "export":       Command("export", "Export conversation history to a JSON file",
                            handler=conversation_export,
                            params=[Param("FILENAME", required=True)]),
    "import":       Command("import", "Import conversation history from a JSON file",
                            handler=conversation_import,
                            params=[Param("FILENAME", required=True,
                                          completions=json_file_completions)]),
    "session":      Command("session", "Resume a saved session of this project (opens a picker)",
                            handler=conversation_session),
    "compact":      Command("compact", "Compact context with an LLM summary marker",
                            handler=cmd_compact),
    "exit":         Command("exit", "Close the application", handler=cmd_exit),
    "stop":         Command("stop", "Stop current generation", handler=cmd_stop),
    "terminal":     Command("terminal", "Open a shell on the host; exit returns",
                            handler=cmd_terminal),
    "activity":     Command("activity", "Toggle the activity overlay (shell/status output)",
                            handler=cmd_activity),
    "model":        Command("model", "Change the active model (opens a picker)",
                            handler=model_command, picker=True),
    "effort":       Command("effort", "Set the model's reasoning effort (opens a picker)",
                            handler=effort_command, picker=True),
    "role":         Command("role", "List roles or switch the active one",
                            handler=cmd_role,
                            params=[Param("NAME", completions=role_name_completions,
                                          descriptions=role_descriptions)]),
    "sandbox":      Command(
                        "sandbox", "Project sandbox: config, build, start, stop, terminal, init",
                        handler=sandbox_help,
                        subcommands={
                            "config": Command(
                                "sandbox config", "Edit this project's sandbox file",
                                handler=sandbox_config),
                            "build": Command(
                                "sandbox build", "Build a sandbox image from its dockerfile",
                                handler=sandbox_build,
                                params=[Param("ID", required=True,
                                              completions=sandbox_id_completions,
                                              descriptions=sandbox_id_descriptions)]),
                            "start": Command(
                                "sandbox start", "Activate a sandbox (list when no id given)",
                                handler=sandbox_start,
                                params=[Param("ID", required=False,
                                              completions=sandbox_id_completions,
                                              descriptions=sandbox_id_descriptions)]),
                            "init": Command(
                                "sandbox init", "Write a starter Containerfile / Dockerfile",
                                handler=sandbox_init,
                                params=[Param("RUNTIME", required=True,
                                              completions=sandbox_runtime_completions),
                                        Param("BASE", required=False,
                                              completions=sandbox_base_completions)]),
                            "stop": Command(
                                "sandbox stop", "Deactivate and run tools in-process",
                                handler=sandbox_stop),
                            "terminal": Command(
                                "sandbox terminal", "Open a shell inside the active sandbox",
                                handler=sandbox_terminal),
                        }),
    "theme":        Command("theme", "Select the color theme (opens a picker)",
                            handler=theme_command, picker=True,
                            params=[Param("THEME", required=False)]),
}


async def handle_command(ui: ChatUIProtocol, text: str):
    parts = text.strip().split()
    if not parts:
        return

    cmd_name = parts[0][1:].lower()  # Remove '/'
    args = parts[1:]

    if cmd_name in COMMANDS:
        await COMMANDS[cmd_name].execute(ui, args)
    else:
        from moka_code.ui.tui.msg_types import SysMsgError
        ui.chat_history_panel.add_message(
            f"Unknown command: /{cmd_name}",
            msg_type=SysMsgError(),
        )


def get_command_list() -> List[str]:
    """Get list of top-level commands."""
    return list(COMMANDS.keys())


def get_command_descriptions() -> Dict[str, str]:
    """Map top-level command name -> one-line description."""
    return {name: cmd.description for name, cmd in COMMANDS.items()}


def get_subcommand_list(command: str) -> List[str]:
    """Get list of subcommands for a given command."""
    if command in COMMANDS:
        cmd = COMMANDS[command]
        if cmd.has_subcommands():
            return list(cmd.subcommands.keys())
    return []


def get_subcommand_descriptions(command: str) -> Dict[str, str]:
    """Map subcommand name -> one-line description for ``command``."""
    if command in COMMANDS:
        cmd = COMMANDS[command]
        if cmd.has_subcommands():
            return {name: sub.description for name, sub in cmd.subcommands.items()}
    return {}


__all__ = [
    "COMMANDS",
    "Command",
    "Param",
    "ChatUIProtocol",
    "handle_command",
    "get_command_list",
    "get_command_descriptions",
    "get_subcommand_list",
    "get_subcommand_descriptions",
]
