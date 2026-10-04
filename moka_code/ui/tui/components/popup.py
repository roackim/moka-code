from dataclasses import dataclass
from typing import Optional, Any, List
from moka_code import settings
from moka_code.ui.tui.components.base import Component
from moka_code.ui.tui.components.box import Box
from moka_code.ui.tui.components.text import TextComponent
from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.events import KeyEvent, MouseEvent
from moka_code.ui.tui.colors import RGB, theme
from moka_code.ui.tui.screen import Screen


@dataclass(frozen=True)
class PopupAction:
    """Lightweight action for popup bottom bar. Compatible with Box action rendering."""
    key: str
    label: str
    
    def format(self) -> str:
        return f"[{self.key}] {self.label}"


_POPUP_CLOSE = PopupAction("Esc", "close")
_POPUP_COPY = PopupAction("c", "copy")


class Popup(Component):
    """A centered overlay popup for displaying scrollable text content.
    
    Reuses Box for borders/title/action bar and TextComponent for content.
    Renders via compositor overlay system.
    
    Features:
    - Bottom action bar with [Esc] close (matches message box style), plus
      [c] copy when the content has a text to copy (``on_copy`` is set by the app)
    - Arrow keys, PgUp/PgDn/Home/End and mouse wheel scroll content (position
      shown on the top border)
    - Clickable action bar (close button)
    - Configurable left/right content padding
    """
    
    def __init__(self,
                 compositor: Optional[Any] = None,
                 id: Optional[str] = None,
                 title: str = "",
                 frame_color: RGB = None,
                 content_color: RGB = None,
                 max_width_ratio: float = 0.7,
                 max_height_ratio: float = 0.7):
        super().__init__(id)
        self.frame_color = frame_color if frame_color is not None else theme.DEFAULT
        self.content_color = content_color if content_color is not None else self.frame_color
        self.max_width_ratio = max_width_ratio
        self.max_height_ratio = max_height_ratio
        self.is_visible = False
        self._lines: List[str] = []
        self._scroll_offset = 0
        self._content_pad = 0  # Box borders provide the content spacing.
        self._fill = False
        self._copy_text: Optional[str] = None
        # ``on_copy(text)``: copies ``[c]``'s text (the app confirms it).
        self.on_copy: Optional[Any] = None
        self._background_focus_scope = None
        self._background_focus_index = None
        
        # Build component tree: Box(title, actions) wrapping TextComponent
        self._text = TextComponent("", fg=self.content_color, bg=theme.get_bg())
        self._box = Box(
            self._text,
            title=title,
            fg=self.frame_color,
            focused=True,
            actions=[_POPUP_CLOSE],
        )
        
        # Compositor integration
        self.compositor = compositor
        self._registered_with_compositor = False
    
    def set_compositor(self, compositor):
        """Set compositor for auto-registration when popup is shown/hidden."""
        self.compositor = compositor

    def refresh_theme(self) -> None:
        """Re-resolve theme-derived colors after a theme switch."""
        self.frame_color = theme.DEFAULT
        self.content_color = self.frame_color
        self._text.fg = self.content_color
        self._text.bg = theme.get_bg()
        self._box.fg = self.frame_color
        self._box.bg = theme.get_bg()
        self._box.mark_changed()
    
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

    def _suspend_background_focus(self):
        if not self.compositor or not hasattr(self.compositor, "event_router"):
            return
        scope = self.compositor.event_router.focus_scope
        if scope is None or not scope.active:
            return
        self._background_focus_scope = scope
        self._background_focus_index = scope.focused_index
        scope.manager.clear()

    def _restore_background_focus(self):
        scope = self._background_focus_scope
        index = self._background_focus_index
        self._background_focus_scope = None
        self._background_focus_index = None
        if scope is not None and index is not None:
            scope.manager.focus(index)
    
    def show(self, title: str, content: str, content_padding: int = 0, *,
             fill: bool = False, tail: bool = False, copy_text: Optional[str] = None):
        """Show the popup with the given title and content.

        ``fill``: cover the whole screen; ``tail``: open scrolled to the end;
        ``copy_text``: adds ``[c] copy`` for that text.
        """
        self._box.title = title
        self._lines = content.split("\n")
        self._content_pad = max(0, content_padding)
        self._fill = fill
        self._copy_text = copy_text
        self._box.actions = [_POPUP_COPY, _POPUP_CLOSE] if copy_text is not None else [_POPUP_CLOSE]
        self._suspend_background_focus()
        self.is_visible = True
        self._scroll_offset = 0
        self._center_popup()       # sets self.width/height first
        if tail:
            self._scroll_offset = self._max_scroll()
        self._update_text()        # now _visible_content_height() is valid
        self._update_compositor_registration()
        if self.compositor:
            self.compositor.request_render()
    
    def hide(self):
        """Hide the popup."""
        was_visible = self.is_visible
        self.is_visible = False
        self._lines = []
        self._scroll_offset = 0
        self._restore_background_focus()
        self._update_compositor_registration()
        if was_visible and self.compositor:
            self.compositor.request_render()
    
    def _update_text(self):
        """Push the currently visible slice of lines to the TextComponent."""
        visible_count = self._visible_content_height()
        start = self._scroll_offset
        end = start + visible_count
        visible = self._lines[start:end]
        # Pad each line on the left so content isn't flush against the border.
        pad = " " * self._content_pad
        self._text.update("\n".join(pad + line for line in visible))
    
    def _max_scroll(self) -> int:
        return max(0, len(self._lines) - self._visible_content_height())

    def _scroll_to(self, offset: int) -> bool:
        """Scroll to ``offset`` (clamped); True when the view moved."""
        offset = max(0, min(self._max_scroll(), offset))
        if offset == self._scroll_offset:
            return False
        self._scroll_offset = offset
        self._update_text()
        return True

    def _copy(self) -> bool:
        if self._copy_text is None or self.on_copy is None:
            return False
        self.on_copy(self._copy_text)
        return True

    def _visible_content_height(self) -> int:
        """Number of content lines the Box interior can display."""
        # Account for both the border and the Box content padding.
        return max(0, self.height - 2 - 2 * self._box.padding_y)
    
    def _center_popup(self):
        """Size and center the popup based on terminal dimensions."""
        if not self.compositor:
            return
        
        term_w = self.compositor.width
        term_h = self.compositor.height

        if self._fill:
            self.x = self.y = 0
            self.width, self.height = term_w, term_h
            self._box.set_layout(self.x, self.y, self.width, self.height)
            return

        # Width: fit content + horizontal padding + borders
        if self._lines:
            longest = max(len(line) for line in self._lines)
            popup_w = min(int(term_w * self.max_width_ratio),
                          longest + 2 * self._content_pad + 2)
        else:
            popup_w = int(term_w * self.max_width_ratio)
        popup_w = max(popup_w, 20)
        
        # Height: content + border + the Box's default content padding.
        popup_h = min(
            int(term_h * self.max_height_ratio),
            len(self._lines) + 2 + 2 * self._box.padding_y,
        )
        popup_h = max(popup_h, 4)
        
        self.x = max(0, (term_w - popup_w) // 2)
        self.y = max(0, (term_h - popup_h) // 2)
        self.width = popup_w
        self.height = popup_h
        
        # Position the Box within our overlay bounds
        self._box.set_layout(self.x, self.y, self.width, self.height)
    
    def handle_input(self, event: Any) -> bool:
        """Handle input while popup is open. Consumes all events."""
        if not self.is_visible:
            return False
        
        # Keyboard
        if isinstance(event, (str, KeyEvent)):
            key = event.key if isinstance(event, KeyEvent) else event
            if key == '\x1b':  # Escape
                self.hide()
                return True
            page = max(1, self._visible_content_height() - 1)
            if key == 'c' and self._copy():
                return True
            if key == '\x1b[A':  # Up
                self._scroll_to(self._scroll_offset - 1)
            elif key == '\x1b[B':  # Down
                self._scroll_to(self._scroll_offset + 1)
            elif key == '\x1b[5~':  # PgUp
                self._scroll_to(self._scroll_offset - page)
            elif key == '\x1b[6~':  # PgDn
                self._scroll_to(self._scroll_offset + page)
            elif key in ('\x1b[H', '\x1b[1~'):  # Home
                self._scroll_to(0)
            elif key in ('\x1b[F', '\x1b[4~'):  # End
                self._scroll_to(self._max_scroll())
        
        # Mouse
        if isinstance(event, MouseEvent):
            if event.pressed and not event.drag:
                # Check action bar click (bottom border row)
                if event.button == 0:  # Left click
                    bottom_y = self.y + self.height - 1
                    if event.y == bottom_y and self.x <= event.x < self.x + self.width:
                        # Check hit regions (populated by Box during render)
                        for start, end, action in self._box._action_hit_regions:
                            abs_start = self.x + start
                            abs_end = self.x + end
                            if abs_start <= event.x < abs_end:
                                if action.key == "c":
                                    self._copy()
                                else:
                                    self.hide()
                                return True
                
                # Mouse scroll
                step = settings.config.ui_scroll_lines_per_notch * getattr(event, "scroll_delta", 1)
                if event.button == 64:  # Scroll up
                    self._scroll_to(self._scroll_offset - step)
                elif event.button == 65:  # Scroll down
                    self._scroll_to(self._scroll_offset + step)
        
        # Consume all input when popup is open
        return True
    
    def render(self, buffer: Buffer):
        """Render the popup overlay."""
        if not self.is_visible:
            return
        
        # Ensure Box layout is current
        self._box.set_layout(self.x, self.y, self.width, self.height)
        
        # Box renders borders, title, content, and action bar
        self._box.render(buffer)
        
        # Scroll position on the top border's right end (the bottom border
        # carries the actions): the last visible line / all lines.
        if self._max_scroll() > 0:
            last = min(len(self._lines), self._scroll_offset + self._visible_content_height())
            scroll_text = f" {last}/{len(self._lines)} "
            sx = self.x + self.width - 1 - len(scroll_text)
            if sx > self.x:
                buffer.write_str(sx, self.y, scroll_text,
                                 fg=self.frame_color, bg=self._box.bg)


class PopupScreen(Screen):
    """Screen lifecycle wrapper for a read-only popup."""

    def __init__(self, popup: Popup, title: str, content: str, content_padding: int = 0,
                 **options):
        super().__init__(popup)
        self.popup = popup
        self.title = title
        self.content = content
        self.content_padding = content_padding
        self.options = options

    def on_enter(self):
        self.popup.show(self.title, self.content, content_padding=self.content_padding,
                        **self.options)

    def on_leave(self):
        self.popup.hide()
