"""Semantic actions independent of physical keyboard or mouse input."""

from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class Action:
    """A named UI operation with an optional payload."""

    name: str
    payload: Any = None


class Actions:
    """Canonical names for common widget and screen operations."""

    SUBMIT = "submit"
    CANCEL = "cancel"
    CLOSE = "close"
    ACTIVATE = "activate"
    NEXT = "next"
    PREVIOUS = "previous"
    SCROLL = "scroll"


ActionHandler = Callable[[Action], bool]


class ActionMap:
    """Map semantic action names to handlers."""

    def __init__(self, parent: Optional["ActionMap"] = None):
        self._handlers: dict[str, ActionHandler] = {}
        self.parent = parent

    def bind(self, name: str, handler: ActionHandler) -> None:
        self._handlers[name] = handler

    def unbind(self, name: str) -> None:
        self._handlers.pop(name, None)

    def dispatch(self, action: Action) -> bool:
        handler = self._handlers.get(action.name)
        if handler and handler(action):
            return True
        return self.parent.dispatch(action) if self.parent else False



def action(name: str, payload: Any = None) -> Action:
    """Construct an action for concise widget and binding code."""
    return Action(name, payload)