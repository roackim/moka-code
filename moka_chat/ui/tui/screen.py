"""Screen lifecycle and navigation primitives."""

from typing import Any, Optional

from moka_chat.ui.tui.actions import ActionMap
from moka_chat.ui.tui.components.base import Component
from moka_chat.ui.tui.focus import FocusScope


class Screen:
    """Own a root component and the screen-local routing state."""

    def __init__(self, root: Component, *, focus_scope: Optional[FocusScope] = None,
                 action_map: Optional[ActionMap] = None, model: Any = None):
        self.root = root
        self.focus_scope = focus_scope
        self.action_map = action_map
        self.model = model

    def on_enter(self) -> None:
        pass

    def on_leave(self) -> None:
        pass

    def on_suspend(self) -> None:
        pass

    def on_resume(self) -> None:
        pass