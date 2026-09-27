from typing import Optional

from moka_chat.ui.tui.buffer import Buffer
from moka_chat.ui.tui.colors import theme
from moka_chat.ui.tui.components.base import Component


class EmptyLine(Component):
    """A one-row component used to add vertical spacing to a layout."""

    min_height = 1

    def __init__(self, id: Optional[str] = None, bg=None):
        super().__init__(id)
        self.bg = theme.get_bg() if bg is None else bg

    def render(self, buffer: Buffer):
        buffer.fill(self.x, self.y, self.width, 1, " ", bg=self.bg)

    def get_preferred_height(self, width: int) -> int:
        return 1


class SeparatorLine(Component):
    """A one-row horizontal separator spanning the allocated width."""

    min_height = 1

    def __init__(self, character: str = "─", id: Optional[str] = None,
                 fg=None, bg=None):
        super().__init__(id)
        if not character:
            raise ValueError("character must not be empty")
        self.character = character[0]
        self.fg = theme.MUTED if fg is None else fg
        self.bg = theme.get_bg() if bg is None else bg

    def render(self, buffer: Buffer):
        buffer.fill(self.x, self.y, self.width, 1, self.character,
                    fg=self.fg, bg=self.bg)

    def get_preferred_height(self, width: int) -> int:
        return 1


