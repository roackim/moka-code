from abc import ABC, abstractmethod
from typing import Optional, Any
from moka_chat.ui.tui.buffer import Buffer

class Component(ABC):
    def __init__(self, id: Optional[str] = None):
        self.id = id
        self.x = 0
        self.y = 0
        self.width = 0
        self.height = 0
        self.parent: Optional['Component'] = None
        self._dirty = True
        self._layout_dirty = True
        self._dirty_rect: Optional[tuple[int, int, int, int]] = None
        self.min_width: Optional[int] = None
        self.max_width: Optional[int] = None
        self.min_height: Optional[int] = None
        self.max_height: Optional[int] = None

    def _constrain_dimension(self, value: int, minimum: Optional[int], maximum: Optional[int]) -> int:
        if minimum is not None:
            value = max(value, minimum)
        if maximum is not None:
            value = min(value, maximum)
        return max(0, value)

    def set_layout(self, x: int, y: int, width: int, height: int):
        old_layout = (self.x, self.y, self.width, self.height)
        self.x = x
        self.y = y
        self.width = self._constrain_dimension(width, self.min_width, self.max_width)
        self.height = self._constrain_dimension(height, self.min_height, self.max_height)
        new_layout = (x, y, self.width, self.height)
        if old_layout != new_layout:
            self.mark_layout_changed()
            self.mark_changed(new_layout)

    def layout(self):
        """Calculate child geometry after this component has been allocated."""
        return None

    def mark_layout_changed(self):
        self._layout_dirty = True
        if self.parent and hasattr(self.parent, "mark_layout_changed"):
            self.parent.mark_layout_changed()

    def is_layout_dirty(self) -> bool:
        return self._layout_dirty

    def mark_changed(self, rect: Optional[tuple[int, int, int, int]] = None):
        self._dirty = True
        if rect is None:
            rect = (self.x, self.y, self.width, self.height)
        self._dirty_rect = rect
        if self.parent and hasattr(self.parent, 'mark_changed'):
            self.parent.mark_changed(rect)

    def is_dirty(self) -> bool:
        return self._dirty

    def collect_dirty_rects(self, rects: list[tuple[int, int, int, int]]):
        if self._dirty_rect is not None:
            rects.append(self._dirty_rect)
        if hasattr(self, 'children'):
            for child in self.children:
                if hasattr(child, 'collect_dirty_rects'):
                    child.collect_dirty_rects(rects)

    def clear_dirty(self):
        self._dirty = False
        self._layout_dirty = False
        self._dirty_rect = None
        if hasattr(self, 'children'):
            for child in self.children:
                if hasattr(child, 'clear_dirty'):
                    child.clear_dirty()

    @abstractmethod
    def render(self, buffer: Buffer):
        pass

    def handle_input(self, event: Any) -> bool:
        """Return True if event was handled."""
        return False

    def update(self, data: Any):
        """Update component state with new data."""
        self.mark_changed()
