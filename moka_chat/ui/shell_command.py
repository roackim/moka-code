"""The ``$`` shell-command escape for the chat input.

Runs a command in the agent workspace and renders its output as transcript
messages. This is deliberately separate from the tool-call path: shell output
is not visible to the LLM.
"""
from __future__ import annotations

import os
import subprocess
import time

from moka_chat.ui.tui.colors import theme
from moka_chat.ui.tui.msg_types import SysMsg, SysMsgError


def handle_shell_command(app, command: str) -> None:
    """Execute a shell command and display output (not visible to LLM).

    Args:
        command: The shell command to execute (without the ``$`` prefix)
    """
    if not command:
        app.chat_history_panel.add_message(
            "Usage: $ <command>\nExample: $ ls -la",
            msg_type=SysMsgError(),
        )
        return

    # Get workspace directory
    workspace = app.agent.workspace if hasattr(app.agent, "workspace") else os.getcwd()

    # Show command being executed
    app.chat_history_panel.add_message(
        f"{theme.MUTED}$ {command}{theme.reset()}",
        msg_type=SysMsg(),
        title="shell",
    )

    # Execute command
    start_time = time.time()
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=30,  # 30 second timeout
        )

        elapsed = time.time() - start_time

        # Build output
        output_parts = []

        if result.stdout:
            output_parts.append(result.stdout.rstrip())

        if result.stderr:
            if output_parts:
                output_parts.append("")
            output_parts.append(f"{theme.ERROR}[stderr]{theme.reset()}")
            output_parts.append(result.stderr.rstrip())

        # Add exit code and timing
        exit_color = theme.SUCCESS if result.returncode == 0 else theme.ERROR
        output_parts.append(
            f"\n{exit_color}[exit:{result.returncode}]{theme.reset()} "
            f"{theme.MUTED}{elapsed:.1f}ms{theme.reset()}"
        )

        # Display output
        output_text = "\n".join(output_parts)
        if output_text.strip():
            app.chat_history_panel.add_message(
                output_text,
                msg_type=SysMsg(),
                title="output",
            )
        else:
            app.chat_history_panel.add_message(
                f"{theme.MUTED}(no output){theme.reset()}",
                msg_type=SysMsg(),
                title="output",
            )

    except subprocess.TimeoutExpired:
        app.chat_history_panel.add_message(
            f"{theme.ERROR}Command timed out after 30 seconds{theme.reset()}",
            msg_type=SysMsgError(),
            title="shell",
        )
    except Exception as e:
        app.chat_history_panel.add_message(
            f"{theme.ERROR}Command failed: {e}{theme.reset()}",
            msg_type=SysMsgError(),
            title="shell",
        )

    # Enable auto-scroll to show the output
    app.chat_history_panel.auto_scroll = True
