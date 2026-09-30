"""Chat workspace screen composition."""

from moka_code.ui.tui.components.base import Component
from moka_code.ui.tui.components.bars import StatusBar
from moka_code.ui.tui.focus import FocusScope
from moka_code.ui.tui.scaffold import AppScaffold
from moka_code.ui.tui.screen import Screen


class ChatScreen(Screen):
    """Own the chat workspace layout while the application owns its state.

    Uses the shared ``AppScaffold``: an optional notice band on top, history +
    input as the body, status bar pinned to the bottom.
    """

    def __init__(self, history: Component, input_box: Component,
                 focus_scope: FocusScope, status_bar: StatusBar | None = None,
                 action_bar: Component | None = None,
                 notice_band: Component | None = None):
        self.status_bar = status_bar or StatusBar()
        self.status_bar.parent = self
        self.action_bar = action_bar
        from moka_code.ui.tui.container import Hsplit, Content, Fill
        # History fills the body; an optional action line sits right above the
        # input box; the input box takes its preferred height.
        if action_bar is not None:
            body = Hsplit([history, action_bar, input_box], [Fill(), Content(), Content()])
        else:
            body = Hsplit([history, input_box], [Fill(), Content()])
        self.scaffold = AppScaffold(body, top=notice_band, bottom=self.status_bar)
        super().__init__(self.scaffold, focus_scope=focus_scope)

    @property
    def workspace(self):
        """Compatibility alias: previously the body container itself."""
        return self.scaffold.body
