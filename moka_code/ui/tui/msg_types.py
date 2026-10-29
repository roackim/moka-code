"""Message type definitions for moka."""

from typing import Optional, List
from enum import Enum

class MsgAction(Enum):
    """Available actions for messages.

    Only non-destructive, non-editing actions are exposed on messages. Actions
    that change conversation state (retry/stop/steer/pause/resume) or remove
    content (delete/edit) are intentionally not message actions; when needed
    they belong to explicit commands. The exceptions are the user's own message
    (EDIT): a queued one is not in the conversation yet, and a sent one is cut
    back, with everything after it, to be edited and sent again; and a running
    bash call (KILL): it is killed, the model gets a cancelled result.
    """
    COPY = ("c", "copy")
    OUTPUT = ("o", "output")
    PROMPT = ("o", "prompt")    # ``!prompt`` message: fold/unfold the prompt
    KILL = ("k", "kill")        # running bash call: kill it, the turn goes on
    ALLOW = ("a", "allow")
    DENY = ("x", "deny")
    EDIT = ("e", "edit")        # user message: back to the input
    CANCEL = ("x", "cancel")    # queued message: dropped before it is sent
    
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

class AssistantMsg(MsgType):
    name = "assistant"
    title = "assistant"
    actions = [MsgAction.COPY]
    frame_color = "ASSISTANT"    # the bar, like USER for the user's turns
    gutter = "▌"

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

class ThinkingMsg(AssistantMsg):
    name = "thinking"
    title = "thinking"
    frame_color = "MUTED"
    content_color = "MUTED"
    actions = [MsgAction.COPY]
    # Same prefix bar as user/assistant so the thought line reads like any message.
    gutter = "▌"
    gutter_color = "MUTED"       # ui.thought_color / thought_expanded_color override it
    clamped = True

class CompactionMsg(MsgType):
    """The line where the history was compacted: folded to a rule with the
    counts; focused, it unfolds to the summary and the kept messages. Not a
    ``SysMsg``: it belongs in the transcript, not the activity overlay."""
    name = "compaction"
    title = "compacted"
    frame_color = "MUTED"
    content_color = "MUTED"
    actions = [MsgAction.COPY]
    gutter = "─"


class ToolCallMsg(MsgType):
    name = "tool"
    title = "tool"
    frame_color = "TOOL"
    content_color = None
    actions = [MsgAction.OUTPUT, MsgAction.COPY]
    # Normal message prefix, muted so tool activity reads apart from prose.
    gutter = "▌"
    gutter_color = "MUTED"
    clamped = True


class ToolOutputMsg(MsgType):
    name = "tool_output"
    title = "output"
    frame_color = "MUTED"
    content_color = "MUTED"
    actions = [MsgAction.OUTPUT, MsgAction.COPY]
    # Same prefix bar as the tool call it belongs to.
    gutter = "▌"
    gutter_color = "MUTED"
    clamped = True


class QuestionMsg(MsgType):
    """The ``ask`` form (``ui/question_form.py``), folded once answered."""
    name = "question"
    title = "question"
    frame_color = "PERMISSION"
    content_color = None
    actions = [MsgAction.COPY]
    gutter = "▌"


class AskPermissionMsg(MsgType):
    name = "permission"
    title = "permission"
    frame_color = "PERMISSION"
    content_color = None
    actions = [MsgAction.ALLOW, MsgAction.DENY, MsgAction.OUTPUT, MsgAction.COPY]
    # Normal prefix bar, colored PERMISSION to mark the ask.
    gutter = "▌"
    clamped = True
