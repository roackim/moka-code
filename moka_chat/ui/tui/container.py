from dataclasses import dataclass
from typing import List, Union, Optional
from moka_chat.ui.tui.components import Component
from moka_chat.ui.tui.buffer import Buffer
from moka_chat.ui.tui.events import KeyEvent, MouseEvent


@dataclass(frozen=True)
class Fixed:
    value: int


@dataclass(frozen=True)
class Percent:
    value: float


@dataclass(frozen=True)
class Content:
    pass


@dataclass(frozen=True)
class Fill:
    pass


SizeUnit = Union[int, float, str, Fixed, Percent, Content, Fill]

class Container(Component):
    def __init__(self, children: List[Component], id: Optional[str] = None):
        super().__init__(id)
        self.children = children
        for child in self.children:
            child.parent = self

    def render(self, buffer: Buffer):
        for child in self.children:
            child.render(buffer)

    def layout(self):
        for child in self.children:
            child.set_layout(self.x, self.y, self.width, self.height)
            child.layout()

    def handle_input(self, event) -> bool:
        for child in self.children:
            if child.handle_input(event):
                return True
        return False


class Padding(Component):
    """Allocate a child inside fixed top, right, bottom, and left insets."""

    def __init__(self, child: Component, padding=0, id: Optional[str] = None):
        super().__init__(id)
        self.child = child
        self.child.parent = self
        if isinstance(padding, int):
            self.padding = (padding, padding, padding, padding)
        elif len(padding) == 2:
            vertical, horizontal = padding
            self.padding = (vertical, horizontal, vertical, horizontal)
        elif len(padding) == 4:
            self.padding = tuple(padding)
        else:
            raise ValueError("padding must be an int or a 2/4-item tuple")

    @property
    def children(self):
        return [self.child]

    def set_layout(self, x: int, y: int, width: int, height: int):
        super().set_layout(x, y, width, height)
        top, right, bottom, left = self.padding
        self.child.set_layout(
            x + left,
            y + top,
            max(0, width - left - right),
            max(0, height - top - bottom),
        )

    def layout(self):
        self.child.layout()

    def render(self, buffer: Buffer):
        self.child.render(buffer)

    def handle_input(self, event) -> bool:
        return self.child.handle_input(event)


class Stack(Container):
    """Lay children over the same rectangle, painting them in list order."""

    def layout(self):
        for child in self.children:
            child.set_layout(self.x, self.y, self.width, self.height)
            child.layout()


class Overlay(Stack):
    """Named stack primitive for layered content and transient overlays."""


class Align(Padding):
    """Place a child at an alignment point within the allocated rectangle."""

    def __init__(self, child: Component, horizontal: str = "left", vertical: str = "top",
                 width: Optional[int] = None, height: Optional[int] = None,
                 id: Optional[str] = None):
        super().__init__(child, 0, id)
        if horizontal not in ("left", "center", "right"):
            raise ValueError("horizontal must be left, center, or right")
        if vertical not in ("top", "center", "bottom"):
            raise ValueError("vertical must be top, center, or bottom")
        self.horizontal = horizontal
        self.vertical = vertical
        self.content_width = width
        self.content_height = height

    def set_layout(self, x: int, y: int, width: int, height: int):
        Component.set_layout(self, x, y, width, height)
        if self.content_width is not None:
            child_width = min(width, self.content_width)
        elif hasattr(self.child, "get_preferred_width"):
            child_width = min(width, self.child.get_preferred_width())
        else:
            child_width = width
        if self.content_height is not None:
            child_height = min(height, self.content_height)
        elif hasattr(self.child, "get_preferred_height"):
            child_height = min(height, self.child.get_preferred_height(child_width))
        else:
            child_height = height
        child_x = x if self.horizontal == "left" else x + (width - child_width if self.horizontal == "right" else (width - child_width) // 2)
        child_y = y if self.vertical == "top" else y + (height - child_height if self.vertical == "bottom" else (height - child_height) // 2)
        self.child.set_layout(child_x, child_y, child_width, child_height)


class ScrollView(Component):
    """Clip and scroll one child inside the allocated viewport."""

    def __init__(self, child: Component, id: Optional[str] = None):
        super().__init__(id)
        self.child = child
        self.child.parent = self
        self.scroll_offset = 0
        self.content_height = 0

    @property
    def children(self):
        return [self.child]

    @property
    def max_scroll(self) -> int:
        return max(0, self.content_height - self.height)

    def set_scroll_offset(self, offset: int):
        clamped = min(max(0, offset), self.max_scroll)
        if clamped != self.scroll_offset:
            self.scroll_offset = clamped
            self.mark_changed()
            self.layout()

    def set_layout(self, x: int, y: int, width: int, height: int):
        super().set_layout(x, y, width, height)
        self.layout()

    def layout(self):
        preferred_height = self.child.get_preferred_height(self.width) if hasattr(self.child, "get_preferred_height") else self.height
        self.content_height = max(self.height, preferred_height)
        self.scroll_offset = min(self.scroll_offset, self.max_scroll)
        self.child.set_layout(self.x, self.y - self.scroll_offset, self.width, self.content_height)
        self.child.layout()

    def render(self, buffer: Buffer):
        previous_clip = buffer.clip_rect
        viewport = (self.x, self.y, self.width, self.height)
        if previous_clip is None:
            buffer.set_clip(*viewport)
        else:
            px, py, pw, ph = previous_clip
            left = max(self.x, px)
            top = max(self.y, py)
            right = min(self.x + self.width, px + pw)
            bottom = min(self.y + self.height, py + ph)
            buffer.set_clip(left, top, max(0, right - left), max(0, bottom - top))
        self.child.render(buffer)
        buffer.clip_rect = previous_clip

    def handle_input(self, event) -> bool:
        if isinstance(event, MouseEvent) and event.x >= self.x and event.x < self.x + self.width and event.y >= self.y and event.y < self.y + self.height:
            if event.button == 64:
                self.set_scroll_offset(self.scroll_offset - max(1, event.scroll_delta))
                return True
            if event.button == 65:
                self.set_scroll_offset(self.scroll_offset + max(1, event.scroll_delta))
                return True
        if isinstance(event, (str, KeyEvent)):
            key = event.key if isinstance(event, KeyEvent) else event
            if key == "\x1b[A":
                self.set_scroll_offset(self.scroll_offset - 1)
                return True
            if key == "\x1b[B":
                self.set_scroll_offset(self.scroll_offset + 1)
                return True
            if key == "\x1b[5~":
                self.set_scroll_offset(self.scroll_offset - self.height)
                return True
            if key == "\x1b[6~":
                self.set_scroll_offset(self.scroll_offset + self.height)
                return True
        return self.child.handle_input(event)

class Split(Container):
    def __init__(self, children: List[Component], sizes: List[SizeUnit], id: Optional[str] = None):
        """
        sizes: list of int (chars), float (0.0-1.0 for percentage), or str ("10c", "60%")
        """
        super().__init__(children, id)
        self.sizes = sizes
        if len(self.sizes) != len(self.children):
            raise ValueError("Number of sizes must match number of children")

    def _calculate_actual_sizes(self, total: int, preferred: Optional[List[int]] = None) -> List[int]:
        actual_sizes = [0] * len(self.sizes)
        remaining = total
        fill_indices = []

        def policy(size):
            if isinstance(size, Fixed):
                return "fixed", size.value
            if isinstance(size, Percent):
                return "percent", size.value
            if isinstance(size, Content):
                return "content", 0
            if isinstance(size, Fill):
                return "fill", 0
            if size == "auto":
                return "content", 0
            if isinstance(size, int):
                return "fixed", size
            if isinstance(size, float):
                return "percent", size
            if isinstance(size, str) and size.endswith("c"):
                return "fixed", int(size[:-1])
            if isinstance(size, str) and size.endswith("%"):
                return "percent", float(size[:-1]) / 100.0
            return "fill", 0
        
        for i, size in enumerate(self.sizes):
            kind, value = policy(size)
            if kind == "fixed":
                actual_sizes[i] = value
                remaining -= value
            elif kind == "content":
                actual_sizes[i] = preferred[i] if preferred else 0
                remaining -= actual_sizes[i]
            elif kind == "fill":
                fill_indices.append(i)
        
        percent_indices = []
        total_percent = 0.0
        for i, size in enumerate(self.sizes):
            kind, value = policy(size)
            if kind == "percent":
                percent_indices.append(i)
                total_percent += value
        
        if percent_indices:
            scale = 1.0 if total_percent <= 1.0 else 1.0 / total_percent
            available_for_percents = max(0, remaining)
            for i in percent_indices:
                s = int(available_for_percents * policy(self.sizes[i])[1] * scale)
                actual_sizes[i] = s
                remaining -= s

        if fill_indices and remaining > 0:
            auto_size = remaining // len(fill_indices)
            for i in fill_indices:
                actual_sizes[i] = auto_size
                remaining -= auto_size
            if remaining > 0:
                actual_sizes[fill_indices[-1]] += remaining
        
        return actual_sizes

class Vsplit(Split):
    def layout(self):
        preferred = [child.get_preferred_width() if hasattr(child, "get_preferred_width") else 0 for child in self.children]
        actual_sizes = self._calculate_actual_sizes(self.width, preferred)
        curr_x = self.x
        for i, child in enumerate(self.children):
            child.set_layout(curr_x, self.y, actual_sizes[i], self.height)
            child.layout()
            curr_x += actual_sizes[i]

    def render(self, buffer: Buffer):
        for child in self.children:
            child.render(buffer)

class Hsplit(Split):
    def layout(self):
        preferred = [child.get_preferred_height(self.width) if hasattr(child, "get_preferred_height") else 0 for child in self.children]
        actual_sizes = self._calculate_actual_sizes(self.height, preferred)

        curr_y = self.y
        for i, child in enumerate(self.children):
            final_h = actual_sizes[i]
            
            child.set_layout(self.x, curr_y, self.width, final_h)
            child.layout()
            curr_y += final_h

    def render(self, buffer: Buffer):
        for child in self.children:
            child.render(buffer)
