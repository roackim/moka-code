"""Tests for Buffer and SubBuffer rendering components."""

import pytest
from pico_chat.ui.tui.buffer import Buffer, SubBuffer, Cell
from pico_chat.ui.tui.components import TextComponent, Box, InputComponent


class TestSubBuffer:
    """Test SubBuffer functionality."""
    
    def test_creation(self):
        """Test SubBuffer creation."""
        sub = SubBuffer(10, 5)
        assert sub.width == 10
        assert sub.height == 5
        assert sub.has_changed is True
        assert sub.x == 0
        assert sub.y == 0
    
    def test_set_and_write(self):
        """Test set and write_str operations."""
        sub = SubBuffer(20, 10)
        
        # Test set
        sub.set(0, 0, 'A', fg=(255, 0, 0))
        assert sub.cells[0][0].char == 'A'
        assert sub.cells[0][0].fg == (255, 0, 0)
        
        # Test write_str
        sub.write_str(1, 1, 'Hello', fg=(0, 255, 0))
        assert sub.cells[1][1].char == 'H'
        assert sub.cells[1][2].char == 'e'
        assert sub.cells[1][3].char == 'l'
        assert sub.cells[1][4].char == 'l'
        assert sub.cells[1][5].char == 'o'
    
    def test_mark_changed(self):
        """Test mark_changed functionality."""
        sub = SubBuffer(10, 5)
        sub.has_changed = False
        
        sub.mark_changed()
        assert sub.has_changed is True
    
    def test_grow(self):
        """Test buffer growing for streaming content."""
        sub = SubBuffer(10, 5)
        initial_height = sub.height
        
        sub.grow(8)
        assert sub.height == 8
        assert len(sub.cells) == 8
        assert sub.has_changed is True  # Growing marks as changed
    
    def test_set_position(self):
        """Test position updates (free scrolling)."""
        sub = SubBuffer(10, 5)
        sub.has_changed = False
        
        sub.set_position(10, 20)
        assert sub.x == 10
        assert sub.y == 20
        assert sub.has_changed is False  # Position change doesn't mark as changed
    
    def test_blit(self):
        """Test blitting to main buffer."""
        sub = SubBuffer(10, 5)
        main = Buffer(20, 10)
        
        # Write to SubBuffer
        sub.write_str(0, 0, 'Test', fg=(255, 0, 0))
        
        # Blit at position 5, 2
        sub.set_position(5, 2)
        sub.blit(main)
        
        # Verify content appeared in main buffer at correct position
        assert main.cells[2][5].char == 'T'
        assert main.cells[2][6].char == 'e'
        assert main.cells[2][7].char == 's'
        assert main.cells[2][8].char == 't'
    
    def test_blit_clipping(self):
        """Test blitting with clipping at buffer edges."""
        sub = SubBuffer(10, 5)
        main = Buffer(15, 8)
        
        # Fill SubBuffer
        sub.fill(0, 0, 10, 5, 'X')
        
        # Blit partially off-screen
        sub.blit(main, 12, 6)
        
        # Only part that fits should be visible
        assert main.cells[6][12].char == 'X'
        assert main.cells[6][13].char == 'X'
        assert main.cells[6][14].char == 'X'


class TestBoxSubBufferIntegration:
    """Test Box component with SubBuffer integration."""
    
    def test_box_creates_subbuffer(self):
        """Test that Box creates SubBuffer on layout."""
        text = TextComponent('Hello\nWorld!')
        box = Box(text, title='Test', focused=False)
        
        # SubBuffer should be created when layout is set
        box.set_layout(5, 5, 20, 10)
        
        assert box.subbuffer is not None
        assert box.subbuffer.width == 20
        assert box.subbuffer.height == 10
        assert box.subbuffer.has_changed is True
    
    def test_box_renders_to_subbuffer(self):
        """Test Box renders to SubBuffer then blits."""
        text = TextComponent('Test')
        box = Box(text, title='Box', focused=False)
        box.set_layout(0, 0, 20, 5)
        
        # First render should use SubBuffer
        main = Buffer(80, 24)
        box.render(main)
        
        # SubBuffer should be marked clean after render
        assert box.subbuffer.has_changed is False
    
    def test_box_caches_rendering(self):
        """Test Box uses cached SubBuffer on repeated renders."""
        text = TextComponent('Static')
        box = Box(text, title='Cache Test')
        box.set_layout(0, 0, 20, 5)
        
        main = Buffer(80, 24)
        
        # First render
        box.render(main)
        assert box.subbuffer.has_changed is False
        
        # Second render should use cache (not re-render to SubBuffer)
        box.render(main)
        assert box.subbuffer.has_changed is False
    
    def test_box_focus_invalidates(self):
        """Test focus change marks Box as changed."""
        text = TextComponent('Focus')
        box = Box(text, title='Focus Test')
        box.set_layout(0, 0, 20, 5)
        
        main = Buffer(80, 24)
        box.render(main)
        assert box.subbuffer.has_changed is False
        
        # Change focus
        box.set_focused(True)
        assert box.subbuffer.has_changed is True
    
    def test_position_update_preserves_cache(self):
        """Test position updates don't invalidate cache (free scrolling)."""
        text = TextComponent('Scroll')
        box = Box(text, title='Scroll Test')
        box.set_layout(0, 0, 20, 5)
        
        main = Buffer(80, 24)
        box.render(main)
        box.subbuffer.has_changed = False
        
        # Update position (same size)
        box.set_layout(10, 10, 20, 5)
        assert box.subbuffer.has_changed is False  # Position-only change


class TestInputComponentSubBuffer:
    """Test input component marking parent Box for re-render."""
    
    def test_input_marks_parent_on_text_change(self):
        """Test input marks parent Box as changed when text changes."""
        input_comp = InputComponent('> ')
        box = Box(input_comp, title='Input')
        box.set_layout(0, 0, 40, 5)
        
        main = Buffer(80, 24)
        box.render(main)
        assert box.subbuffer.has_changed is False
        
        # Change text
        input_comp.buffer.text = 'Hello'
        input_comp._on_text_changed()
        
        assert box.subbuffer.has_changed is True
    
    def test_input_marks_parent_on_update(self):
        """Test input marks parent on update() call."""
        input_comp = InputComponent('> ')
        box = Box(input_comp, title='Input')
        box.set_layout(0, 0, 40, 5)
        
        main = Buffer(80, 24)
        box.render(main)
        box.subbuffer.has_changed = False
        
        input_comp.update('New text')
        assert box.subbuffer.has_changed is True
    
    def test_input_marks_parent_on_clear(self):
        """Test input marks parent on clear() call."""
        input_comp = InputComponent('> ')
        box = Box(input_comp, title='Input')
        box.set_layout(0, 0, 40, 5)
        
        main = Buffer(80, 24)
        box.render(main)
        box.subbuffer.has_changed = False
        
        input_comp.clear()
        assert box.subbuffer.has_changed is True
    
    def test_input_cursor_blink_marks_parent(self):
        """Test cursor blink animation marks parent for re-render."""
        import time
        
        input_comp = InputComponent('> ')
        input_comp.focused = True
        box = Box(input_comp, title='Input')
        box.set_layout(0, 0, 40, 5)
        
        main = Buffer(80, 24)
        
        # First render
        box.render(main)
        initial_cursor = input_comp.cursor_renderer.cursor_visible
        box.subbuffer.has_changed = False
        
        # Wait for blink interval (0.5s) + pulse delay (0.5s)
        time.sleep(1.1)
        
        # Render again - cursor should have toggled
        box.render(main)
        final_cursor = input_comp.cursor_renderer.cursor_visible
        
        # Cursor should have toggled
        assert initial_cursor != final_cursor
        
        # Parent should be marked as changed (cursor blinked)
        assert box.subbuffer.has_changed is True

    def test_input_tick_cursor_marks_parent_on_blink(self):
        """Idle tick_cursor should mark the parent box dirty on a blink flip."""
        import time
        input_comp = InputComponent('')
        input_comp.focused = True
        box = Box(input_comp, title='Input')
        box.set_layout(0, 0, 40, 5)

        # First render primes the blink timer.
        main = Buffer(80, 24)
        box.render(main)
        box.subbuffer.has_changed = False

        # tick_cursor right away stays solid (inside pulse delay) -> no change.
        assert input_comp.tick_cursor() is False

        # After pulse-delay + half blink interval the visibility must flip.
        time.sleep(1.1)
        flipped = input_comp.tick_cursor()
        assert flipped is True
        # Advancing again marks the parent box for redraw.
        assert box.subbuffer.has_changed is True

    def test_input_tick_cursor_noop_when_unfocused(self):
        """tick_cursor should do nothing when the input isn't focused."""
        input_comp = InputComponent('')
        input_comp.focused = False
        box = Box(input_comp, title='Input')
        box.set_layout(0, 0, 40, 5)
        box.render(Buffer(80, 24))
        box.subbuffer.has_changed = False
        assert input_comp.tick_cursor() is False
        assert box.subbuffer.has_changed is False

    def test_input_box_prefix_and_child_offset(self):
        """Lines-only input box draws ▸ at col 0, content starts at col 1."""
        input_comp = InputComponent('')
        input_comp.update('hi')
        box = Box(input_comp, title='message', lines_only=True,
                  title_provider=lambda: 'message')
        box.set_layout(0, 0, 40, 3)

        main = Buffer(80, 24)
        box.render(main)

        # No prefix glyph; a 1-space margin precedes the content.
        assert main.cells[1][0].char == ' '
        assert main.cells[1][1].char == 'h'
        assert main.cells[1][2].char == 'i'

    def test_input_box_colors_prefix_and_bars_with_fg(self):
        """Prefix and bars inherit the box fg (e.g. USER), text stays plain."""
        user_color = (10, 20, 30)
        input_comp = InputComponent(' ')
        input_comp.update('hi')
        box = Box(input_comp, title='', lines_only=True, fg=user_color)
        box.set_layout(0, 0, 40, 3)

        main = Buffer(80, 24)
        box.render(main)

        # Rules span the full width and use the box fg color.
        assert main.cells[0][0].char == '╶'
        assert main.cells[0][0].fg == user_color
        # A one-char prompt (space) leaves the text at col 2, like chat gutter.
        assert main.cells[1][1].char == ' '
        assert main.cells[1][2].char == 'h'

    def test_input_box_color_provider_mutes_when_unfocused(self):
        """A color_provider swaps bar/prefix color with the focused state."""
        user_color = (10, 20, 30)
        muted_color = (90, 90, 90)
        input_comp = InputComponent(' ')
        box = Box(
            input_comp, title='', lines_only=True, fg=user_color,
            color_provider=lambda: (user_color if box.focused else muted_color),
        )
        box.set_layout(0, 0, 40, 3)

        main = Buffer(80, 24)
        box.set_focused(True)
        box.render(main)
        assert main.cells[0][0].fg == user_color

        box.set_focused(False)
        box.render(main)
        assert main.cells[0][0].char == '╶'
        assert main.cells[0][0].fg == muted_color

    def test_input_content_color_provider_tints_text(self):
        """A content color provider recolors the typed text (not the bars)."""
        red = (255, 0, 0)
        green = (0, 255, 0)
        input_comp = InputComponent('')
        input_comp.set_content_color_provider(lambda: green if input_comp.text.startswith('/') else red)
        input_comp.update('hello')
        box = Box(input_comp, title='', lines_only=True)
        box.set_layout(0, 0, 40, 3)

        main = Buffer(80, 24)
        box.render(main)

        # Message mode -> red text at col 1.
        assert main.cells[1][1].char == 'h'
        assert main.cells[1][1].fg == red

        # Command mode -> green text.
        input_comp._command_registry = {}
        input_comp.update('/help')
        box.mark_changed()
        box.render(main)
        assert main.cells[1][1].char == '/'
        assert main.cells[1][1].fg == green

    def test_input_box_empty_title_renders_no_section(self):
        """An empty title means no inline section name in the top bar."""
        input_comp = InputComponent('')
        box = Box(input_comp, title='', lines_only=True)
        box.set_layout(0, 0, 40, 3)
        main = Buffer(80, 24)
        box.render(main)
        # Full-width rules ending in half-lines, no inline section.
        assert main.cells[0][0].char == '╶'
        assert main.cells[0][39].char == '╴'
        assert main.cells[2][0].char == '╶'
        assert main.cells[2][39].char == '╴'
        assert 'message' not in "".join(c.char for c in main.cells[0])


class TestEmojiClusterWriting:
    """write_str must keep emoji sequences in one cell and size them correctly."""

    @pytest.mark.parametrize("factory", [lambda: Buffer(20, 2), lambda: SubBuffer(20, 2)])
    def test_keycap_sequence_stays_in_one_cell(self, factory):
        buf = factory()
        buf.write_str(0, 0, "4\uFE0F\u20E3 x")

        assert buf.cells[0][0].char == "4\uFE0F\u20E3"
        assert buf.cells[0][1].is_wide_char_continuation
        # The keycap occupies two columns, then " ", then "x".
        assert "".join(c.char for c in buf.cells[0][:4]) == "4\uFE0F\u20E3 x"

    @pytest.mark.parametrize("factory", [lambda: Buffer(20, 2), lambda: SubBuffer(20, 2)])
    def test_wide_emoji_advances_two_columns(self, factory):
        buf = factory()
        buf.write_str(0, 0, "\U0001F3C6x")

        assert buf.cells[0][0].char == "\U0001F3C6"
        assert buf.cells[0][1].is_wide_char_continuation
        assert buf.cells[0][2].char == "x"
