"""One event protocol for harness-to-UI communication.

The harness yields a stream of these events; the UI renders them. There is a
single union — no parallel chunk/status types.
"""

from dataclasses import dataclass
from typing import Optional, Union


@dataclass
class Start:
    """A new message begins. ``role`` is ``"user"`` or ``"assistant"``."""
    message_id: str
    role: str


@dataclass
class Token:
    """Assistant response content."""
    text: str


@dataclass
class Reasoning:
    """Model reasoning content (e.g. DeepSeek R1 chain-of-thought)."""
    text: str


@dataclass
class ToolCall:
    """A tool call whose JSON arguments are still streaming.

    ``args`` is the cumulative JSON string so far.
    """
    id: str
    name: str
    args: str


@dataclass
class PermissionRequest:
    """A tool call awaiting a permission decision.

    ``auto`` is True when a configured policy made the decision without asking
    the user; False when the user must approve or deny.
    """
    id: str
    name: str
    args: str
    prompt: str
    auto: bool = False


@dataclass
class ToolResult:
    """Final outcome of a tool call.

    ``outcome`` is one of ``"completed"``, ``"denied"`` or ``"error"``.
    """
    id: str
    name: str
    outcome: str
    output: str = ""


@dataclass
class ToolOutput:
    """Interim output from a running tool (e.g. bash stdout/stderr).

    ``stream`` is ``"stdout"`` or ``"stderr"``; ``data`` is a chunk as it
    arrived.  The UI routes these to the activity surface so long commands are
    visible live.
    """
    id: str
    name: str
    stream: str
    data: str


@dataclass
class Usage:
    """Live generation usage metrics."""
    tokens: int
    tokens_per_second: float
    ttft_ms: Optional[float] = None
    duration_ms: Optional[float] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None
    estimated: bool = True


@dataclass
class Error:
    """A harness-level failure to report to the user."""
    message: str


@dataclass
class Done:
    """The generation stream has finished."""
    stop_reason: str = "end_turn"


Event = Union[
    Start, Token, Reasoning, ToolCall, PermissionRequest, ToolOutput,
    ToolResult, Usage, Error, Done
]
