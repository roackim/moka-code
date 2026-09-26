"""Message type definitions for Pico-Chat."""

from typing import Optional, List
from enum import Enum

class MsgAction(Enum):
    """Available actions for messages.

    Only non-destructive, non-editing actions are exposed on messages. Actions
    that change conversation state (retry/stop/steer/pause/resume) or remove
    content (delete/edit) are intentionally not message actions; when needed
    they belong to explicit commands.
    """
    COPY = ("c", "copy")
    OUTPUT = ("o", "output")
    ALLOW = ("a", "allow")
    DENY = ("x", "deny")
    
    def __init__(self, key: str, label: str):
        self.key = key
        self.label = label
    
    def format(self) -> str:
        """Format action as [key] label."""
        return f"[{self.key}] {self.label}"

class MsgType:
    """Base class for message types."""
    name: str = "default"
    title: str = ""
    frame_color: str = "DEFAULT"
    content_color: Optional[str] = None
    actions: List[MsgAction] = []
    # Thread-mode gutter symbol and color (None = use frame_color).
    gutter: str = "▸"
    gutter_color: Optional[str] = None
    # Clamped messages render with no inter-message gap when adjacent to
    # another clamped message, so a turn's thought + tool calls read as one
    # block. The final answer is not clamped: gaps still separate it (and
    # turns) from everything else.
    clamped: bool = False

class UserMsg(MsgType):
    name = "user"
    title = "user"
    actions = [MsgAction.COPY]
    frame_color = "USER"
    # Content renders in the normal text color (like the input field); the
    # user color is kept for the gutter/prefix bar.
    content_color = None
    gutter = "▌"

class PicoMsg(MsgType):
    name = "pico"
    title = "pico"
    actions = [MsgAction.COPY]
    frame_color = "PICO"
    gutter = "▌"
    gutter_color = "MUTED"

class SysMsg(MsgType):
    name = "system"
    title = "system"
    frame_color = "MUTED"
    content_color = "MUTED"
    actions = [MsgAction.COPY]
    gutter = "·"

class SysMsgError(SysMsg):
    name = "error"
    title = "error"
    frame_color = "ERROR"
    content_color = "ERROR"
    actions = [MsgAction.COPY]
    gutter = "✗"

class SysMsgWarning(SysMsg):
    name = "warning"
    title = "warning"
    frame_color = "WARNING"
    content_color = "WARNING"
    gutter = "!"

class ThinkingMsg(PicoMsg):
    name = "thinking"
    title = "thinking"
    frame_color = "MUTED"
    content_color = "MUTED"
    actions = [MsgAction.COPY]
    # Same prefix bar as user/pico so the thought line reads like any message.
    gutter = "▌"
    clamped = True

class ToolCallMsg(MsgType):
    name = "tool"
    title = "tool"
    frame_color = "TOOL"
    content_color = None
    actions = [MsgAction.OUTPUT, MsgAction.COPY]
    # Normal message prefix, like any other transcript entry.
    gutter = "▌"
    clamped = True


class AskPermissionMsg(MsgType):
    name = "permission"
    title = "permission"
    frame_color = "PERMISSION"
    content_color = None
    actions = [MsgAction.ALLOW, MsgAction.DENY, MsgAction.OUTPUT, MsgAction.COPY]
    # Normal prefix bar, colored PERMISSION to mark the ask.
    gutter = "▌"
    clamped = True
