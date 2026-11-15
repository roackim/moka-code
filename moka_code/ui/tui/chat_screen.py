"""Chat workspace screen composition."""

from typing import Callable, Optional

from moka_code.ui.tui.components.base import Component
from moka_code.ui.tui.components.bars import StatusBar
from moka_code.ui.tui.focus import FocusScope
from moka_code.ui.tui.scaffold import AppScaffold
from moka_code.ui.tui.screen import Screen


class Reserved(Component):
    """Empty rows under the history that an overlay drawn there (the open
    ``ask`` form) takes, so the history ends above it instead of under it."""

    def __init__(self, rows: Callable[[], int]):
        super().__init__()
        self.rows = rows

    def get_preferred_height(self, width: int) -> int:
        return max(0, self.rows())

    def render(self, buffer) -> None:
        pass


class ChatScreen(Screen):
    """Own the chat workspace layout while the application owns its state.

    Uses the shared ``AppScaffold``: an optional notice band on top, history +
    input as the body, status bar pinned to the bottom.
    """

    def __init__(self, history: Component, input_box: Component,
                 focus_scope: FocusScope, status_bar: StatusBar | None = None,
                 action_bar: Component | None = None,
                 notice_band: Component | None = None,
                 reserved_rows: Optional[Callable[[], int]] = None):
        self.status_bar = status_bar or StatusBar()
        self.status_bar.parent = self
        self.action_bar = action_bar
        from moka_code.ui.tui.container import Hsplit, Content, Fill
        # History fills the body; the reserved rows (when an overlay sits
        # there) and an optional action line come right above the input box;
        # the input box takes its preferred height.
        parts = [history]
        if reserved_rows is not None:
            parts.append(Reserved(reserved_rows))
        if action_bar is not None:
            parts.append(action_bar)
        parts.append(input_box)
        body = Hsplit(parts, [Fill()] + [Content()] * (len(parts) - 1))
        self.scaffold = AppScaffold(body, top=notice_band, bottom=self.status_bar)
        super().__init__(self.scaffold, focus_scope=focus_scope)

    @property
    def workspace(self):
        """Compatibility alias: previously the body container itself."""
        return self.scaffold.body
