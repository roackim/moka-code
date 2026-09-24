from dataclasses import dataclass
from typing import Optional
import re
from wcwidth import wcwidth, wcswidth

from pico_chat.ui.tui.colors import RGB, theme
from pico_chat.ui.tui.graphemes import split_clusters
from pico_chat.ui.tui.terminal import ANSI
from pico_chat import pico_cfg

# Compiled once: write_str runs for every text fragment on every repaint.
_ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


def _text_tokens(s: str):
    """Yield ``(text, is_ansi)`` tokens: ANSI escapes, newlines, grapheme clusters.

    Clustering keeps emoji sequences (variation selectors, ZWJ, keycaps) in one
    cell so their display width is measured correctly.
    """
    i = 0
    n = len(s)
    while i < n:
        match = _ANSI_ESCAPE.match(s, i)
        if match:
            yield match.group(), True
            i = match.end()
            continue
        if s[i] == "\n":
            yield "\n", False
            i += 1
            continue
        j = i
        while j < n and s[j] != "\n" and _ANSI_ESCAPE.match(s, j) is None:
            j += 1
        for cluster in split_clusters(s[i:j]):
            yield cluster, False
        i = j


def _cluster_width(text: str) -> int:
    """Display width of a grapheme cluster (wide emoji = 2, combining = 0)."""
    width = wcswidth(text)
    if width < 0:
        width = wcwidth(text[-1]) if text else 0
        if width < 0:
            width = 1
    return width


@dataclass(slots=True)
class Cell:
    char: str = " "
    fg: Optional[tuple[int, int, int]] = None
    bg: Optional[tuple[int, int, int]] = None
    bold: bool = False
    reverse: bool = False
    underline: bool = False
    is_wide_char_continuation: bool = False  # Marks cells that are part of a wide character

class Buffer:
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        # Only use theme background if config allows it
        if pico_cfg.config.ui_use_bg_color:
            self.default_bg = (theme.BACKGROUND.r, theme.BACKGROUND.g, theme.BACKGROUND.b)
        else:
            self.default_bg = None  # Use terminal default background
        
        self.cells = [[Cell(bg=self.default_bg) for _ in range(width)] for _ in range(height)]
        self.cursor_pos: Optional[tuple[int, int]] = None  # (x, y)
        self.clip_rect: Optional[tuple[int, int, int, int]] = None # x, y, w, h

    def set_clip(self, x: int, y: int, w: int, h: int):
        self.clip_rect = (x, y, w, h)

    def clear_clip(self):
        self.clip_rect = None

    def _is_in_clip(self, x: int, y: int) -> bool:
        if self.clip_rect is None:
            return True
        cx, cy, cw, ch = self.clip_rect
        return cx <= x < cx + cw and cy <= y < cy + ch


    def set(self, x: int, y: int, char: str, fg=None, bg=None, bold=False, reverse=False, underline=False):
        if 0 <= x < self.width and 0 <= y < self.height:
            if not self._is_in_clip(x, y):
                return
            # Convert RGB objects to tuples
            if fg is not None and hasattr(fg, 'r'):
                fg = (fg.r, fg.g, fg.b)
            if bg is not None and hasattr(bg, 'r'):
                bg = (bg.r, bg.g, bg.b)
            # Important: Always create a fresh Cell instance to avoid sharing
            self.cells[y][x] = Cell(char, fg, bg, bold, reverse, underline)

    def fill(self, x: int, y: int, width: int, height: int, char: str = " ", fg=None, bg=None):
        """Fill a rectangular area with a character."""
        for iy in range(y, y + height):
            for ix in range(x, x + width):
                self.set(ix, iy, char, fg=fg, bg=bg)

    def write_str(self, x: int, y: int, s: str, fg=None, bg=None, bold=False, reverse=False, underline=False, max_width: Optional[int] = None):
        """
        Writes a string to the buffer starting at (x, y).
        This method is ANSI-aware: escape sequences are stored alongside the next
        printable character in a single cell, ensuring they don't occupy extra
        horizontal space in the grid.
        Iterates grapheme clusters so emoji sequences (variation selectors, ZWJ,
        keycaps) occupy the correct number of columns.
        """
        curr_x = x
        pending_ansi = ""  # Accumulates ANSI sequences to attach to the next glyph
        count = 0          # Visible width written, for clipping

        for text, is_ansi in _text_tokens(s):
            if is_ansi:
                pending_ansi += text
                continue
            if text == "\n":
                break
            if max_width is not None and count >= max_width:
                break

            width = _cluster_width(text)
            if max_width is not None and count + width > max_width:
                break

            if width <= 0:
                # Zero-width (combining mark / lone selector): glue it onto the
                # preceding cell instead of consuming a column.
                if curr_x - 1 >= 0 and 0 <= y < self.height:
                    self.cells[y][curr_x - 1].char += pending_ansi + text
                    pending_ansi = ""
                else:
                    pending_ansi += text
                continue

            if 0 <= curr_x < self.width and 0 <= y < self.height:
                self.set(curr_x, y, pending_ansi + text, fg, bg, bold, reverse, underline)
            fg_tuple = (fg.r, fg.g, fg.b) if fg is not None and hasattr(fg, 'r') else fg
            bg_tuple = (bg.r, bg.g, bg.b) if bg is not None and hasattr(bg, 'r') else bg
            for offset in range(1, width):
                cx = curr_x + offset
                if 0 <= cx < self.width and 0 <= y < self.height and self._is_in_clip(cx, y):
                    self.cells[y][cx] = Cell(
                        char="", fg=fg_tuple, bg=bg_tuple, bold=bold,
                        reverse=reverse, underline=underline,
                        is_wide_char_continuation=True,
                    )
            pending_ansi = ""
            curr_x += width
            count += width

        # Handle any trailing ANSI sequences (e.g., color resets)
        if pending_ansi and (max_width is None or count < max_width):
            if curr_x > x and 0 <= y < self.height:
                # Attach trailing ANSI to the last character written
                prev_cell = self.cells[y][curr_x - 1]
                prev_cell.char += pending_ansi
            elif 0 <= curr_x < self.width and 0 <= y < self.height:
                # If no characters were written, put ANSI in an empty cell
                self.set(curr_x, y, pending_ansi + " ", fg, bg, bold, reverse, underline)

    def clear(self):
        bg = self.default_bg
        for row in self.cells:
            for cell in row:
                cell.char = " "
                cell.fg = None
                cell.bg = bg
                cell.bold = False
                cell.reverse = False
                cell.underline = False
                cell.is_wide_char_continuation = False

    def clear_rect(self, x: int, y: int, width: int, height: int):
        """Clear a rectangular area to default background."""
        start_x = max(0, x)
        start_y = max(0, y)
        end_x = min(self.width, x + width)
        end_y = min(self.height, y + height)

        bg = self.default_bg
        cells = self.cells
        for iy in range(start_y, end_y):
            row = cells[iy]
            for ix in range(start_x, end_x):
                cell = row[ix]
                cell.char = " "
                cell.fg = None
                cell.bg = bg
                cell.bold = False
                cell.reverse = False
                cell.underline = False
                cell.is_wide_char_continuation = False

    def render(self) -> str:
        """
        Renders the entire buffer to a single ANSI string.
        Always performs a full redraw from the top-left corner.
        Properly handles wide characters (emojis) that span multiple columns.
        """
        res = [ANSI.MOVE_HOME]
        
        # Initialize background based on whether we're using theme colors
        if self.default_bg is None:
            # Reset to terminal default background
            res.append("\033[49m")
        else:
            # Set theme background color
            res.append(f"\033[48;2;{self.default_bg[0]};{self.default_bg[1]};{self.default_bg[2]}m")
        
        # Use a sentinel value to force first cell to emit colors
        curr_state = {"fg": object(), "bg": object(), "bold": False, "reverse": False, "underline": False}
        
        for y in range(self.height):
            # Move to start of line
            res.append(ANSI.move_to(y + 1, 1))
            terminal_col = 0  # Track actual terminal column position
            
            for x in range(self.width):
                cell: RGB = self.cells[y][x]
                
                # Skip continuation cells (they're covered by the wide char before them)
                if cell.is_wide_char_continuation:
                    terminal_col += 1  # Still counts as a cell position
                    continue
                
                # If there's a gap between our current terminal column and where we should be,
                # use ANSI positioning to jump to the correct position
                if terminal_col != x:
                    res.append(ANSI.move_to(y + 1, x + 1))
                    terminal_col = x
                
                # Update attributes if changed
                attr_changed = (
                    cell.fg != curr_state["fg"] or 
                    cell.bg != curr_state["bg"] or 
                    cell.bold != curr_state["bold"] or 
                    cell.reverse != curr_state["reverse"] or
                    cell.underline != curr_state["underline"]
                )
                
                if attr_changed:
                    # If turning off bold/reverse, we often need a full reset in some terminals
                    # but we try to be surgical
                    if (curr_state["bold"] and not cell.bold) or (curr_state["reverse"] and not cell.reverse):
                        res.append("\033[0m")
                        curr_state = {"fg": None, "bg": None, "bold": False, "reverse": False, "underline": False}
                    
                    if cell.fg != curr_state["fg"]:
                        if cell.fg:
                            if isinstance(cell.fg, tuple):
                                res.append(f"\033[38;2;{cell.fg[0]};{cell.fg[1]};{cell.fg[2]}m")
                            else:
                                res.append(cell.fg.ansi_fg())
                        else:
                            res.append("\033[39m")
                        curr_state["fg"] = cell.fg

                    if cell.bg != curr_state["bg"]:
                        if cell.bg:
                            if isinstance(cell.bg, tuple):
                                res.append(f"\033[48;2;{cell.bg[0]};{cell.bg[1]};{cell.bg[2]}m")
                            else:
                                res.append(cell.bg.ansi_bg())
                        else:
                            res.append("\033[49m")
                        curr_state["bg"] = cell.bg

                    if cell.bold and not curr_state["bold"]:
                        res.append("\033[1m")
                        curr_state["bold"] = True

                    if cell.reverse and not curr_state["reverse"]:
                        res.append("\033[7m")
                        curr_state["reverse"] = True

                    if cell.underline and not curr_state["underline"]:
                        res.append("\033[4m")
                        curr_state["underline"] = True
                    elif not cell.underline and curr_state["underline"]:
                        res.append("\033[24m")
                        curr_state["underline"] = False

                res.append(cell.char)
                
                # Check if next cell is marked as continuation (meaning this char is wide)
                if x + 1 < self.width and self.cells[y][x + 1].is_wide_char_continuation:
                    terminal_col += 2  # Wide character occupies 2 columns
                else:
                    terminal_col += 1  # Normal character occupies 1 column
            
            # Emit a real newline after every row EXCEPT the last. This lets
            # terminal-native copy preserve line breaks between rows. The last
            # row gets no newline so the terminal never scrolls (a newline on
            # the bottom row would shift the whole screen up).
            if y < self.height - 1:
                res.append("\n")
        
        # Return the cursor to the top-left so it is never left on the bottom
        # row between frames (which could trigger a scroll on the next write).
        res.append(ANSI.MOVE_HOME)
        # Reset color and hide the hardware cursor at the end to avoid flickering.
        # We handle cursor display manually in components to prevent terminal cursor jitter.
        res.append(ANSI.HIDE_CURSOR)
        res.append(ANSI.RESET)
        return "".join(res)


class SubBuffer:
    """
    A sub-surface buffer for efficient component rendering.
    Components can render to their SubBuffer once and blit it multiple times,
    avoiding expensive re-rendering. Position can be updated independently for
    efficient scrolling.
    """
    
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.x = 0  # Blit position (can be updated independently)
        self.y = 0
        self.has_changed = True  # Needs re-rendering
        self.clip_rect: Optional[tuple[int, int, int, int]] = None
        
        if pico_cfg.config.ui_use_bg_color:
            self.default_bg = (theme.BACKGROUND.r, theme.BACKGROUND.g, theme.BACKGROUND.b)
        else:
            self.default_bg = None
        
        # 2D list of cells for elegant growing
        self.cells = [[Cell(bg=self.default_bg) for _ in range(width)] for _ in range(height)]
    
    def set(self, x: int, y: int, char: str, fg=None, bg=None, bold=False, reverse=False):
        """Set a single cell in the buffer."""
        if 0 <= x < self.width and 0 <= y < self.height and self._is_in_clip(x, y):
            # Convert RGB objects to tuples
            if fg is not None and hasattr(fg, 'r'):
                fg = (fg.r, fg.g, fg.b)
            if bg is not None and hasattr(bg, 'r'):
                bg = (bg.r, bg.g, bg.b)
            self.cells[y][x] = Cell(char, fg, bg, bold, reverse)

    def set_clip(self, x: int, y: int, w: int, h: int):
        self.clip_rect = (x, y, w, h)

    def clear_clip(self):
        self.clip_rect = None

    def _is_in_clip(self, x: int, y: int) -> bool:
        if self.clip_rect is None:
            return True
        cx, cy, cw, ch = self.clip_rect
        return cx <= x < cx + cw and cy <= y < cy + ch
    
    def fill(self, x: int, y: int, width: int, height: int, char: str = " ", fg=None, bg=None):
        """Fill a rectangular area with a character."""
        for iy in range(y, min(y + height, self.height)):
            for ix in range(x, min(x + width, self.width)):
                self.set(ix, iy, char, fg=fg, bg=bg)
    
    def write_str(self, x: int, y: int, s: str, fg=None, bg=None, bold=False, reverse=False, max_width: Optional[int] = None):
        """
        Write a string to the buffer starting at (x, y).
        ANSI-aware; iterates grapheme clusters so emoji sequences occupy the
        correct number of columns.
        """
        curr_x = x
        pending_ansi = ""
        count = 0

        for text, is_ansi in _text_tokens(s):
            if is_ansi:
                pending_ansi += text
                continue
            if text == "\n":
                break
            if max_width is not None and count >= max_width:
                break

            width = _cluster_width(text)
            if max_width is not None and count + width > max_width:
                break

            if width <= 0:
                if curr_x - 1 >= 0 and 0 <= y < self.height:
                    self.cells[y][curr_x - 1].char += pending_ansi + text
                    pending_ansi = ""
                else:
                    pending_ansi += text
                continue

            if 0 <= curr_x < self.width and 0 <= y < self.height:
                self.set(curr_x, y, pending_ansi + text, fg, bg, bold, reverse)
            fg_tuple = (fg.r, fg.g, fg.b) if fg is not None and hasattr(fg, 'r') else fg
            bg_tuple = (bg.r, bg.g, bg.b) if bg is not None and hasattr(bg, 'r') else bg
            for offset in range(1, width):
                cx = curr_x + offset
                if 0 <= cx < self.width and 0 <= y < self.height:
                    self.cells[y][cx] = Cell(
                        char="", fg=fg_tuple, bg=bg_tuple, bold=bold,
                        reverse=reverse, is_wide_char_continuation=True,
                    )
            pending_ansi = ""
            curr_x += width
            count += width

        # Handle trailing ANSI
        if pending_ansi and (max_width is None or count < max_width):
            if curr_x > x and 0 <= y < self.height:
                prev_cell = self.cells[y][curr_x - 1]
                prev_cell.char += pending_ansi
            elif 0 <= curr_x < self.width and 0 <= y < self.height:
                self.set(curr_x, y, pending_ansi + " ", fg, bg, bold, reverse)
    
    def clear(self):
        """Clear all cells in the buffer."""
        bg = self.default_bg
        for row in self.cells:
            for cell in row:
                cell.char = " "
                cell.fg = None
                cell.bg = bg
                cell.bold = False
                cell.reverse = False
                cell.underline = False
                cell.is_wide_char_continuation = False

    def clear_region(self, x: int, y: int, width: int, height: int):
        """Reset a rectangular region in place (used for incremental raster)."""
        bg = self.default_bg
        end_x = min(self.width, x + width)
        end_y = min(self.height, y + height)
        for iy in range(max(0, y), end_y):
            row = self.cells[iy]
            for ix in range(max(0, x), end_x):
                cell = row[ix]
                cell.char = " "
                cell.fg = None
                cell.bg = bg
                cell.bold = False
                cell.reverse = False
                cell.underline = False
                cell.is_wide_char_continuation = False

    def shrink(self, new_height: int):
        """Shrink the buffer from the bottom (inverse of :meth:`grow`)."""
        if new_height < self.height:
            del self.cells[new_height:]
            self.height = new_height
            self.has_changed = True
    
    def grow(self, new_height: int):
        """
        Grow the buffer by appending lines to the bottom.
        Useful for streaming content that grows over time.
        Marks the buffer as changed.
        """
        if new_height > self.height:
            for _ in range(new_height - self.height):
                self.cells.append([Cell(bg=self.default_bg) for _ in range(self.width)])
            self.height = new_height
            self.has_changed = True
    
    def set_position(self, x: int, y: int):
        """
        Update the blit position without marking as changed.
        Perfect for scrolling - position updates are free!
        """
        self.x = x
        self.y = y
    
    def mark_changed(self):
        """Mark this buffer as needing re-rendering."""
        self.has_changed = True
    
    def blit(
        self,
        target: Buffer,
        x_offset: Optional[int] = None,
        y_offset: Optional[int] = None,
        clip_rect: Optional[tuple[int, int, int, int]] = None,
    ):
        """
        Copy cells from this SubBuffer to the target Buffer.
        Uses stored position or provided offsets.
        Handles clipping gracefully - only copies cells that fit in target.
        """
        blit_x = x_offset if x_offset is not None else self.x
        blit_y = y_offset if y_offset is not None else self.y

        # Visible rect in target coordinates = intersection of:
        # target bounds, blit bounds, optional clip rect, and target's own clip.
        visible_x0 = max(0, blit_x)
        visible_y0 = max(0, blit_y)
        visible_x1 = min(target.width, blit_x + self.width)
        visible_y1 = min(target.height, blit_y + self.height)

        if clip_rect is not None:
            cx, cy, cw, ch = clip_rect
            visible_x0 = max(visible_x0, cx)
            visible_y0 = max(visible_y0, cy)
            visible_x1 = min(visible_x1, cx + cw)
            visible_y1 = min(visible_y1, cy + ch)

        target_clip = getattr(target, "clip_rect", None)
        if target_clip is not None:
            cx, cy, cw, ch = target_clip
            visible_x0 = max(visible_x0, cx)
            visible_y0 = max(visible_y0, cy)
            visible_x1 = min(visible_x1, cx + cw)
            visible_y1 = min(visible_y1, cy + ch)

        if visible_x0 >= visible_x1 or visible_y0 >= visible_y1:
            return

        # Map visible target rect back to source rect
        src_start_x = visible_x0 - blit_x
        src_end_x = visible_x1 - blit_x
        src_start_y = visible_y0 - blit_y
        src_end_y = visible_y1 - blit_y

        # Fast path: a real Buffer exposes plain lists, so whole row slices can
        # be replaced at once. Copy cells into freshly-built Cells: they must
        # never be shared with this SubBuffer, because the panel mutates target
        # cells in place for selection highlighting, which would corrupt the
        # cached surface. Non-Buffer targets (a parent Box's SubBufferWrapper)
        # have no assignable cell grid and use the generic path.
        if isinstance(target, Buffer):
            target_cells = target.cells
            for src_y in range(src_start_y, src_end_y):
                target_y = blit_y + src_y
                src_row = self.cells[src_y]
                target_cells[target_y][visible_x0:visible_x1] = [
                    Cell(c.char, c.fg, c.bg, c.bold, c.reverse, c.underline,
                         c.is_wide_char_continuation)
                    for c in src_row[src_start_x:src_end_x]
                ]
            return

        for src_y in range(src_start_y, src_end_y):
            target_y = blit_y + src_y
            src_row = self.cells[src_y]
            target_x = visible_x0
            for cell in src_row[src_start_x:src_end_x]:
                target.set(target_x, target_y, cell.char, fg=cell.fg, bg=cell.bg,
                           bold=cell.bold, reverse=cell.reverse)
                target_x += 1
