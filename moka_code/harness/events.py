"""One event protocol for harness-to-UI communication.

The harness yields a stream of these events; the UI renders them. There is a
single union — no parallel chunk/status types.
"""

from dataclasses import dataclass, field
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
class ToolCallDraft:
    """A tool call whose JSON arguments are still streaming.

    Emitted live as argument deltas arrive so the UI can show progress before
    the call is complete. ``id``/``name``/``args`` may be partial; ``args`` is
    the cumulative JSON string so far. The complete call is announced later by
    :class:`ToolCall` at execution time.
    """
    id: str
    name: str
    args: str


@dataclass
class ToolCall:
    """A completed tool call, announced as it is executed.

    ``args`` is the full JSON argument string.
    """
    id: str
    name: str
    args: str


@dataclass
class PermissionRequest:
    """A tool call awaiting a permission decision.

    ``auto`` is True when a configured policy made the decision without asking
    the user; False when the user must approve or deny. ``diff`` is the unified
    diff an ``edit`` would make, read before the decision ("" when unknown);
    it is for the UI only, never sent to the model.
    """
    id: str
    name: str
    args: str
    prompt: str
    auto: bool = False
    diff: str = ""


@dataclass
class ToolResult:
    """Final outcome of a tool call.

    ``outcome`` is one of ``"completed"``, ``"denied"``, ``"cancelled"`` (the
    user killed it, ``Harness.cancel_tool``, or dismissed an ``ask``) or ``"error"``.
    """
    id: str
    name: str
    outcome: str
    output: str = ""


@dataclass
class Question:
    """One ``ask`` call: a question, the answers offered (the user may also
    type their own) and a short tab ``label``."""
    id: str
    question: str
    options: list = field(default_factory=list)
    multiple: bool = False
    label: str = ""


@dataclass
class Questions:
    """Adjacent ``ask`` calls, shown together; the harness waits for
    ``Harness.answer_questions``."""
    items: list


@dataclass
class Answer:
    """The user's answer to one question: the options picked and/or own text."""
    choices: list = field(default_factory=list)
    text: str = ""


@dataclass
class ToolStarted:
    """A tool call began executing (``Harness.running_tools`` lists it now)."""
    id: str
    name: str


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


@dataclass
class Error:
    """A harness-level failure to report to the user."""
    message: str


@dataclass
class Notice:
    """Something the harness did on its own (auto-compaction). ``level`` is
    ``"info"`` or ``"error"``."""
    message: str
    level: str = "info"


@dataclass
class CompactionStarted:
    """The summary request for a compaction is sent."""


@dataclass
class CompactionText:
    """A piece of the compaction summary, as it streams."""
    text: str


@dataclass
class Compacted:
    """The history was compacted; ``marker_id`` is the summary entry's id
    (``compaction.compaction_view`` describes it)."""
    marker_id: str


@dataclass
class CompactionFailed:
    """An auto-compaction did not happen; nothing in the history changed."""
    message: str


@dataclass
class Done:
    """The generation stream has finished."""


Event = Union[
    Start, Token, Reasoning, ToolCallDraft, ToolCall, PermissionRequest,
    Questions, ToolStarted, ToolOutput, ToolResult, Usage, Error, Notice,
    CompactionStarted, CompactionText, Compacted, CompactionFailed, Done
]
