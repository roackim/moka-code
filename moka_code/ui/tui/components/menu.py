import time
from typing import Any, Callable, List, Optional, Tuple, Union
from enum import Enum
from moka_code.ui.tui.components.base import Component
from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.fuzzy import fuzzy_search
from moka_code.ui.tui.colors import RGB, theme
from moka_code.ui.tui.events import MouseEvent


# A description or footer: plain text, or ``[(text, color)]`` segments (color
# ``None`` keeps the default), e.g. a status word and its numbers coloured apart.
Cell = Union[str, List[Tuple[str, Optional[RGB]]]]


def cell_text(cell: Cell) -> str:
    return cell if isinstance(cell, str) else "".join(text for text, _color in cell)


def _write_cell(buffer: Buffer, x: int, y: int, cell: Cell, fg, bg, max_width: int) -> None:
    if isinstance(cell, str):
        buffer.write_str(x, y, cell, fg=fg, bg=bg, max_width=max_width)
        return
    used = 0
    for text, color in cell:
        if used >= max_width:
            break
        buffer.write_str(x + used, y, text, fg=color or fg, bg=bg, max_width=max_width - used)
        used += len(text)


def _tail_len(description: Cell, footer: Cell) -> int:
    """Width of a row's muted description plus its optional footer."""
    footer_len = len(cell_text(footer))
    return len(cell_text(description)) + (2 + footer_len if footer_len else 0)


def sort_items(items: List[str]) -> List[str]:
    """The default menu order: case-insensitive alphabetical."""
    return sorted(items, key=lambda item: (item.lower(), item))


class SelectionMenu(Component):
    """A floating menu component for autocomplete/suggestions.
    
    Pure display and navigation component - doesn't know about commands,
    triggers, or business logic. Just displays items and handles selection.
    """
    
    def __init__(self, 
                 compositor: Optional[Any] = None,
                 id: Optional[str] = None, 
                 frame_color: RGB = None,
                 content_color: RGB = None,
                 left_pad: int = 1,
                 right_pad: int = 1,
                 max_height: int = 12):
        super().__init__(id)
        self.items: List[str] = []
        self.selected_index = 0
        self.frame_color = frame_color if frame_color is not None else theme.DEFAULT
        self.content_color = content_color if content_color is not None else self.frame_color
        # Color of the selected row (falls back to the frame color).
        self.highlight_color = theme.USER
        self.bg = theme.get_bg()
        self.left_pad = left_pad
        self.right_pad = right_pad
        self.max_height = max_height
        self.is_visible = False
        self.display_prefix = ""  # Prefix to show (e.g., "/" for commands)
        # Optional name -> one-line description; drawn muted to the right of the
        # item, aligned into a column.
        self.item_descriptions: dict[str, str] = {}
        # Optional per-item footer (e.g. an "active" tag) drawn after the
        # description in ``footer_color``.
        self.item_footers: dict[str, str] = {}
        self.footer_color = theme.SUCCESS
        # Optional title shown in the top border, and status text (e.g. the
        # current search query) shown in the bottom border.
        self.title = ""
        self.status_text = ""
        # Minimum popup width (0 = shrink to content).
        self.min_width = 0
        # When True, use the full available width instead of shrinking to the
        # longest item (used by the @ file picker for long paths).
        self.fill_width = False
        
        # Called after a click selects an item (e.g. to accept it).
        self.on_click: Optional[Callable[[], Any]] = None
        # First item row drawn (the scroll window), for mapping clicks.
        self._first = 0

        # Compositor integration for auto-registration
        self.compositor = compositor
        self._registered_with_compositor = False


    def set_fill_width(self, fill_width: bool):
        if self.fill_width != fill_width:
            self.fill_width = fill_width
            self.mark_changed()

    def apply_theme(self) -> None:
        """Re-resolve theme-derived colors after a theme switch."""
        self.frame_color = theme.DEFAULT
        self.content_color = self.frame_color
        self.highlight_color = theme.USER
        self.footer_color = theme.SUCCESS
        self.bg = theme.get_bg()
        self.mark_changed()

    def set_compositor(self, compositor):
        """Set compositor for auto-registration when menu is shown/hidden."""
        self.compositor = compositor
    
    def _update_compositor_registration(self):
        """Auto-register/unregister with compositor based on visibility."""
        if not self.compositor:
            return
        
        if self.is_visible and not self._registered_with_compositor:
            self.compositor.add_overlay(self)
            self._registered_with_compositor = True
        elif not self.is_visible and self._registered_with_compositor:
            self.compositor.remove_overlay(self)
            self._registered_with_compositor = False
        if hasattr(self.compositor, "request_render"):
            self.compositor.request_render()
    
    def update(self, all_items: List[str], search_term: str = "",
               display_prefix: str = "", descriptions: Optional[dict] = None,
               footers: Optional[dict] = None, ordered: bool = False):
        """Update menu with new items and optional search filter.

        Args:
            all_items: Complete list of items to choose from (raw, without prefixes)
            search_term: Optional search term to fuzzy filter items
            display_prefix: Prefix to show when rendering (e.g., "/" for commands)
            descriptions: Optional item -> one-line description mapping
            ordered: The source's order is meaningful (e.g. effort levels);
                otherwise items are sorted (case-insensitive) while nothing
                is typed. A search ranks by match either way.
        """
        if descriptions is not None:
            self.item_descriptions = dict(descriptions)
        if footers is not None:
            self.item_footers = dict(footers)
        self.display_prefix = display_prefix
        if not search_term:
            self.items = list(all_items) if ordered else sort_items(all_items)
        else:
            # Fuzzy search
            results = fuzzy_search(search_term, all_items, threshold=0.01)
            self.items = [res[0] for res in results]

        # Show menu if we have items
        self.is_visible = len(self.items) > 0
        
        # Reset selection if out of bounds
        if self.selected_index >= len(self.items):
            self.selected_index = 0
        
        # Auto-register with compositor
        self._update_compositor_registration()

    def set_items(self, items: List[str], display_prefix: str = "",
                  descriptions: Optional[dict] = None,
                  footers: Optional[dict] = None):
        """Replace items with an already-filtered/ranked list.

        Unlike :meth:`update`, no fuzzy filtering or sorting is applied.
        """
        if descriptions is not None:
            self.item_descriptions = dict(descriptions)
        if footers is not None:
            self.item_footers = dict(footers)
        self.display_prefix = display_prefix
        self.items = list(items)
        self.is_visible = len(self.items) > 0
        if self.selected_index >= len(self.items):
            self.selected_index = 0
        self._update_compositor_registration()
    
    def hide(self):
        """Hide the menu."""
        self.is_visible = False
        self.items = []
        self.selected_index = 0
        self.display_prefix = ""
        
        # Auto-unregister with compositor
        self._update_compositor_registration()
    
    def get_selected(self) -> Optional[str]:
        """Get the currently selected item."""
        if self.items and 0 <= self.selected_index < len(self.items):
            return self.items[self.selected_index]
        return None
    

    def measure_width(self, available: int) -> int:
        """Width the popup box will occupy, given the available columns."""
        if self.fill_width:
            inner = max(15, self.width or available)
        else:
            name_col = max(
                (len(self.display_prefix) + len(item) for item in self.items),
                default=0,
            )
            desc_col = max(
                (_tail_len(self.item_descriptions.get(item, ""),
                           self.item_footers.get(item, ""))
                 for item in self.items),
                default=0,
            )
            raw_len = name_col + (2 + desc_col if desc_col > 0 else 0)
            inner = max(15, self.min_width,
                        raw_len + self.left_pad + self.right_pad + 2)
        return min(inner, self.width or available, available)

    def render(self, buffer: Buffer):
        """Render the menu at its current position."""
        if not self.is_visible or not self.items:
            return

        menu_width = self.measure_width(buffer.width - self.x)

        name_col = max(
            (len(self.display_prefix) + len(item) for item in self.items),
            default=0,
        )
        desc_col = max(
            (_tail_len(self.item_descriptions.get(item, ""),
                       self.item_footers.get(item, ""))
             for item in self.items),
            default=0,
        )
        has_desc = desc_col > 0

        # Visible rows from the height, capped by max_height.
        max_visible = max(1, min(self.max_height - 2, max(0, self.height - 2)))
        visible_count = min(len(self.items), max_visible)

        # Scroll window that keeps the selection visible.
        first = 0
        if self.selected_index >= visible_count:
            first = self.selected_index - visible_count + 1
        first = max(0, min(first, len(self.items) - visible_count))
        self._first = first

        menu_height = visible_count + 2  # +2 for top and bottom borders

        # Clear the popup rectangle first so it overwrites whatever is behind
        # it (e.g. the action bar) instead of letting it show through the gaps.
        for yy in range(menu_height):
            for xx in range(menu_width):
                buffer.set(self.x + xx, self.y + yy, " ", bg=self.bg)

        # Draw the box with background
        buffer.set(self.x, self.y, "┌", fg=self.frame_color, bg=self.bg)
        for i in range(1, menu_width - 1):
            buffer.set(self.x + i, self.y, "─", fg=self.frame_color, bg=self.bg)
        buffer.set(self.x + menu_width - 1, self.y, "┐", fg=self.frame_color, bg=self.bg)

        # Optional title, inline in the top border.
        if self.title:
            title_str = f" {self.title[:max(0, menu_width - 4)]} "
            buffer.write_str(self.x + 2, self.y, title_str,
                             fg=self.frame_color, bg=self.bg,
                             max_width=max(0, menu_width - 3))

        inner_width = menu_width - 2  # Minus borders
        content_area = max(0, inner_width - self.left_pad - self.right_pad)

        # Render visible items (windowed around the selection).
        for row in range(visible_count):
            idx = first + row
            item = self.items[idx]
            curr_y = self.y + 1 + row

            buffer.set(self.x, curr_y, "│", fg=self.frame_color, bg=self.bg)

            is_selected = (idx == self.selected_index)
            display_text = f"{self.display_prefix}{item}"

            for p in range(self.left_pad):
                buffer.set(self.x + 1 + p, curr_y, " ", bg=self.bg)

            content_x = self.x + 1 + self.left_pad
            selected_fg = self.highlight_color or self.frame_color
            name_text = display_text.ljust(name_col)[:content_area]
            buffer.write_str(content_x, curr_y, name_text,
                             fg=selected_fg if is_selected else self.content_color,
                             bg=self.bg, bold=is_selected, max_width=content_area)

            if has_desc:
                desc = self.item_descriptions.get(item, "")
                footer = self.item_footers.get(item, "")
                tail_x = content_x + name_col + 2
                tail_area = content_area - name_col - 2
                desc_len = len(cell_text(desc))
                if desc_len and tail_area > 0:
                    _write_cell(buffer, tail_x, curr_y, desc,
                                theme.MUTED, self.bg, tail_area)
                if footer:
                    footer_x = tail_x + desc_len + 2
                    footer_area = content_area - (name_col + 2 + desc_len + 2)
                    if footer_area > 0:
                        _write_cell(buffer, footer_x, curr_y, footer,
                                    self.footer_color, self.bg, footer_area)

            for p in range(self.right_pad):
                buffer.set(self.x + 1 + self.left_pad + content_area + p, curr_y, " ",
                           bg=self.bg)

            buffer.set(self.x + menu_width - 1, curr_y, "│", fg=self.frame_color, bg=self.bg)

        # Draw bottom border
        buffer.set(self.x, self.y + menu_height - 1, "└", fg=self.frame_color, bg=self.bg)
        for i in range(1, menu_width - 1):
            buffer.set(self.x + i, self.y + menu_height - 1, "─", fg=self.frame_color, bg=self.bg)
        buffer.set(self.x + menu_width - 1, self.y + menu_height - 1, "┘", fg=self.frame_color, bg=self.bg)

        # Scroll indicator: "n/m" when there are more items than fit.
        counter = ""
        if len(self.items) > visible_count:
            counter = f" {self.selected_index + 1}/{len(self.items)} "
            cx = self.x + menu_width - 1 - len(counter)
            if cx > self.x:
                buffer.write_str(cx, self.y + menu_height - 1, counter,
                                 fg=theme.MUTED, bg=self.bg)

        # Status text (e.g. the current search query) at the bottom-left.
        if self.status_text:
            avail = menu_width - 2 - len(counter)
            if avail > 0:
                buffer.write_str(self.x + 1, self.y + menu_height - 1, self.status_text,
                                 fg=theme.MUTED, bg=self.bg, max_width=avail)


    def handle_input(self, event: Any) -> bool:
        """A click on an item selects it and calls ``on_click``; the wheel
        moves the selection. Any mouse event inside the menu is consumed."""
        if not (isinstance(event, MouseEvent) and self.is_visible and self.items):
            return False
        if not (self.x <= event.x < self.x + self.width
                and self.y <= event.y < self.y + self.height):
            return False
        if event.button == 64:
            self.action_up()
        elif event.button == 65:
            self.action_down()
        elif event.button == 0 and event.pressed and not event.drag:
            index = self._first + event.y - self.y - 1
            if self.y < event.y < self.y + self.height - 1 and 0 <= index < len(self.items):
                self.selected_index = index
                if self.on_click is not None:
                    self.on_click()
        self.mark_changed()
        return True

    def action_up(self):
        """Move selection up."""
        if self.items:
            self.selected_index = (self.selected_index - 1) % len(self.items)
    
    def action_down(self):
        """Move selection down."""
        if self.items:
            self.selected_index = (self.selected_index + 1) % len(self.items)
    
