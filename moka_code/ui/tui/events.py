"""Events exchanged between the terminal adapter and TUI widgets."""

from dataclasses import dataclass
from typing import Any, Optional


class KeyEvent(str):
    """A normalized keyboard event with legacy string compatibility.

    It behaves like the raw key sequence for existing handlers, while exposing
    typed metadata for new routing and action code.
    """

    def __new__(cls, key: str, text: Optional[str] = None):
        event = str.__new__(cls, key)
        event.key = key
        event.text = text if text is not None else (key if len(key) == 1 and key.isprintable() else None)
        return event


def normalize_key(key: str) -> KeyEvent:
    """Convert a raw terminal key or escape sequence into a ``KeyEvent``."""
    if isinstance(key, KeyEvent):
        return key
    return KeyEvent(key)


@dataclass(frozen=True)
class MouseEvent:
    """A normalized mouse event using zero-based terminal coordinates."""

    x: int
    y: int
    button: int  # 0=left, 1=middle, 2=right, 64=scroll_up, 65=scroll_down
    pressed: bool
    drag: bool = False
    scroll_delta: int = 1
    alt: bool = False


@dataclass(frozen=True)
class PasteEvent:
    """Text delivered by bracketed paste."""

    text: str


@dataclass(frozen=True)
class ResizeEvent:
    """A terminal resize notification."""

    width: int
    height: int


@dataclass(frozen=True)
class TickEvent:
    """A scheduled application tick."""

    timestamp: float


@dataclass(frozen=True)
class CommandEvent:
    """An application-level command emitted by a widget or input binding."""

    name: str
    payload: Any = None