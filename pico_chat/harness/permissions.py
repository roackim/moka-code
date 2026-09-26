"""Tool approval gate.

A :class:`~pico_chat.harness.roles.Role` maps each tool name to ``"no"``,
``"ask"`` or ``"yes"``.  This module is the single place that turns that into
the harness decision (``"allow"`` / ``"ask"`` / ``"deny"``) and owns the queue
the UI uses to deliver the user's answer on the ``ask`` path.

There is no permission engine: no profiles, no command allowlists, no path
confinement.  pico implements no sandbox — the user runs pico inside their own
container or accepts the ``ask`` prompts (see ``plans/containerization.md``).
"""

from __future__ import annotations

import asyncio
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pico_chat.harness.roles import Role


def build_prompt(tool_name: str, args: dict) -> str:
    """Build a human-readable permission prompt for a tool call."""
    if tool_name == "read":
        return f"Allow reading file: {args.get('path', 'unknown')}?"
    elif tool_name == "write":
        return f"Allow writing to file: {args.get('path', 'unknown')}?"
    elif tool_name == "edit":
        return f"Allow editing file: {args.get('path', 'unknown')}?"
    elif tool_name == "bash":
        command = args.get("command", "unknown")
        return f"Allow running command: {command}?"
    return f"Allow {tool_name}?"


class PermissionGate:
    """Maps a role's per-tool setting to a decision and handles ``ask``.

    It owns the user-response queue, so the Harness doesn't have to.
    """

    def __init__(self, role: Optional["Role"] = None):
        self._role = role
        self._user_response_queue: asyncio.Queue[str] = asyncio.Queue()

    @property
    def role(self) -> Optional["Role"]:
        return self._role

    def set_role(self, role: Optional["Role"]) -> None:
        """Replace the active role policy for a conversation."""
        self._role = role

    def set_user_response(self, text: str) -> None:
        """Called by the UI when a response to a tool's prompt is ready."""
        self._user_response_queue.put_nowait(text)

    async def wait_for_user_input(self, prompt: str) -> str:
        """Wait for the user to provide text via the UI."""
        return await self._user_response_queue.get()

    def clear_pending(self) -> None:
        """Drop answers nobody is waiting for (e.g. after a stopped turn).

        Otherwise an "approve" given to a prompt that was stopped would be
        consumed by the *next* permission prompt.
        """
        while not self._user_response_queue.empty():
            self._user_response_queue.get_nowait()

    @staticmethod
    def build_prompt(tool_name: str, args: dict) -> str:
        """Build a human-readable permission prompt for a tool call."""
        return build_prompt(tool_name, args)

    def check(self, tool_name: str, args: dict) -> str:
        """Return ``"allow"``, ``"ask"`` or ``"deny"`` for a tool call."""
        value = self._role.permission_for(tool_name) if self._role else "no"
        if value == "yes":
            return "allow"
        if value == "ask":
            return "ask"
        return "deny"


__all__ = [
    "build_prompt",
    "PermissionGate",
]
