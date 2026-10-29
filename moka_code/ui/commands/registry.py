"""Command registry: the single assembly point for all slash commands.

Handlers live in per-domain modules (``core``, ``models``, ``roles``,
``themes``, ``conversation``…). Those modules depend only on
:mod:`moka_code.ui.commands.base`; this module is the only place that knows
about all of them, which keeps the import graph acyclic.

Every command is a plain ``Command``: its ``params`` and ``subcommands`` are the
whole description of its arguments (see ``.wiki/notes/commands.md``).
"""

from __future__ import annotations

from typing import Dict, List

from .base import ChatUIProtocol, Command, Param
from .conversation import (
    conversation_export, conversation_import, conversation_session, json_file_completions,
)
from .core import (
    config_command,
    reload_and_apply,
    cmd_activity,
    cmd_clear,
    cmd_compact,
    cmd_debug,
    cmd_edit,
    cmd_exit,
    cmd_help,
    cmd_private,
    cmd_reload,
    cmd_running,
    cmd_stop,
    cmd_terminal,
)
from .models import (
    effort_command, effort_completions, effort_descriptions,
    model_command, model_completions, model_descriptions,
)
from .roles import role_command
from .sandbox import (
    sandbox_base_completions,
    sandbox_build,
    sandbox_create,
    sandbox_edit,
    sandbox_help,
    sandbox_id_completions,
    sandbox_id_descriptions,
    sandbox_init,
    sandbox_path,
    sandbox_remove,
    sandbox_restart,
    sandbox_stop,
    sandbox_terminal,
    sandbox_scope_completions,
    sandbox_type_completions,
    sandbox_copy,
    sandbox_start,
)
from .diff import changed_descriptions, changed_files, diff_command, live_description
from .prompts import PROMPT_SUBCOMMANDS, prompt_command
from .themes import theme_command

# Help needs the whole registry, so its handler is assembled here.
async def _help(ui: ChatUIProtocol, args: List[str]):
    await cmd_help(ui, args, COMMANDS)


async def _sandbox_help(ui: ChatUIProtocol, args: List[str]):
    await sandbox_help(ui, args, COMMANDS["sandbox"])


# Command Registry
COMMANDS: Dict[str, Command] = {
    "help":         Command("help", "Show available commands", handler=_help),
    "clear":        Command("clear", "Clear chat history (and leave private mode)", handler=cmd_clear),
    "private":      Command("private", "Stop saving this conversation until /clear",
                            handler=cmd_private),
    "reload":       Command("reload", "Reload config files and validate role files from disk",
                            handler=cmd_reload),
    "config":       config_command(),
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
    "running":      Command("running", "Running commands, with how long; pick one to kill it",
                            handler=cmd_running),
    "terminal":     Command("terminal", "Open a shell on the host; exit returns",
                            handler=cmd_terminal),
    "debug":        Command("debug", "View the debug log full screen (c copies, Esc closes)",
                            handler=cmd_debug),
    "activity":     Command("activity", "Toggle the activity overlay (shell/status output)",
                            handler=cmd_activity),
    "model":        Command("model", "Change the active model (opens a picker)",
                            handler=model_command,
                            params=[Param("MODEL", completions=model_completions,
                                          descriptions=model_descriptions, ordered=True)]),
    "effort":       Command("effort", "Set the model's reasoning effort (opens a picker)",
                            handler=effort_command,
                            params=[Param("LEVEL", completions=effort_completions,
                                          descriptions=effort_descriptions, ordered=True)]),
    "role":         role_command(reload_and_apply),
    "sandbox":      Command(
                        "sandbox", "Start a sandbox (opens a picker), or a subcommand",
                        handler=_sandbox_help,
                        subcommands={
                            "edit": Command(
                                "sandbox edit", "Edit a sandbox's file (picker when no name given)",
                                handler=sandbox_edit,
                                params=[Param("NAME", required=False,
                                              completions=sandbox_id_completions,
                                              descriptions=sandbox_id_descriptions)]),
                            "path": Command(
                                "sandbox path", "Print (and copy) a sandbox's file path",
                                handler=sandbox_path,
                                params=[Param("NAME", required=False,
                                              completions=sandbox_id_completions,
                                              descriptions=sandbox_id_descriptions)]),
                            "create": Command(
                                "sandbox create", "Create a sandbox file from a starter",
                                handler=sandbox_create,
                                params=[Param("NAME", required=True),
                                        Param("TYPE", required=True,
                                              completions=sandbox_type_completions, ordered=True),
                                        Param("SCOPE", required=False,
                                              completions=sandbox_scope_completions, ordered=True)]),
                            "remove": Command(
                                "sandbox remove", "Remove a sandbox's file (asks to confirm)",
                                handler=sandbox_remove,
                                params=[Param("NAME", required=True,
                                              completions=sandbox_id_completions,
                                              descriptions=sandbox_id_descriptions)]),
                            "copy": Command(
                                "sandbox copy", "Copy a sandbox into a global or local file",
                                handler=sandbox_copy,
                                params=[Param("NAME", required=True,
                                              completions=sandbox_id_completions,
                                              descriptions=sandbox_id_descriptions),
                                        Param("SCOPE", required=True,
                                              completions=sandbox_scope_completions, ordered=True),
                                        Param("NEW_NAME", required=True)]),
                            "build": Command(
                                "sandbox build", "Build a sandbox image from its dockerfile",
                                handler=sandbox_build,
                                params=[Param("NAME", required=True,
                                              completions=sandbox_id_completions,
                                              descriptions=sandbox_id_descriptions)]),
                            "start": Command(
                                "sandbox start", "Activate a sandbox (opens a picker when no name given)",
                                handler=sandbox_start,
                                params=[Param("NAME", required=False,
                                              completions=sandbox_id_completions,
                                              descriptions=sandbox_id_descriptions)]),
                            "restart": Command(
                                "sandbox restart", "Restart the active sandbox from its current image (no rebuild)",
                                handler=sandbox_restart),
                            "init": Command(
                                "sandbox init", "Write a starter Containerfile / Dockerfile next to a sandbox",
                                handler=sandbox_init,
                                params=[Param("NAME", required=True,
                                              completions=sandbox_id_completions,
                                              descriptions=sandbox_id_descriptions),
                                        Param("BASE", required=False,
                                              completions=sandbox_base_completions)]),
                            "stop": Command(
                                "sandbox stop", "Deactivate and run tools in-process",
                                handler=sandbox_stop),
                            "terminal": Command(
                                "sandbox terminal", "Open a shell inside the active sandbox",
                                handler=sandbox_terminal),
                        }),
    "diff":         Command("diff", "Review uncommitted changes (opens a picker)",
                            handler=diff_command, live_description=live_description,
                            params=[Param("FILE", completions=changed_files,
                                          descriptions=changed_descriptions, ordered=True)]),
    "prompt":       Command("prompt", "Edit a !prompt file (opens a picker), or create, path, remove",
                            handler=prompt_command, subcommands=PROMPT_SUBCOMMANDS),
    "theme":        theme_command(reload_and_apply),
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
    return {name: (cmd.live_description() if cmd.live_description else cmd.description)
            for name, cmd in COMMANDS.items()}


def get_subcommand_list(command: str) -> List[str]:
    """Get list of subcommands for a given command."""
    if command in COMMANDS:
        return list(COMMANDS[command].subcommands)
    return []


def get_subcommand_descriptions(command: str) -> Dict[str, str]:
    """Map subcommand name -> one-line description."""
    if command in COMMANDS:
        return {name: sub.description for name, sub in COMMANDS[command].subcommands.items()}
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
