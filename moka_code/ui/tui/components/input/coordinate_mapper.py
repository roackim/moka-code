"""Coordinate mapping and text wrapping for input component."""

from moka_code.ui.tui.layout_utils import display_width, wrap_text


class CoordinateMapper:
    """Handles coordinate conversion and text wrapping calculations."""
    
    def __init__(self, prompt: str, width: int):
        self.prompt = prompt
        self.width = width
        self.prompt_width = display_width(prompt)
        # Cache for wrapped lines to avoid expensive recalculation
        self._cache_text = None
        self._cache_width = None
        self._cache_lines = None
    
    def update_dimensions(self, width: int):
        """Update available width for wrapping."""
        if self.width != width:
            self.width = width
            # Invalidate cache when width changes
            self._cache_width = None
    
    @property
    def available_width(self) -> int:
        """Get available width for text (minus border margins)."""
        return max(1, self.width - 2)
    
    def get_wrapped_lines(self, text: str) -> list[str]:
        """Wrap text into display lines based on width, preserving newlines."""
        # Check cache
        if (self._cache_text == text and 
            self._cache_width == self.width and 
            self._cache_lines is not None):
            return self._cache_lines
        
        available = self.available_width
        
        if available <= self.prompt_width:
            result = [""]
        else:
            paragraphs = text.split('\n')
            all_lines = []
            
            for i, para in enumerate(paragraphs):
                is_first_para = (i == 0)
                if is_first_para:
                    wrapped = wrap_text(para, available, padding_width=self.prompt_width, first_line_padding=False)
                else:
                    wrapped = wrap_text(para, available, padding_width=self.prompt_width, first_line_padding=True)
                
                para_lines = wrapped.split('\n') if wrapped else [""]
                all_lines.extend(para_lines)
            
            result = all_lines
        
        # Update cache
        self._cache_text = text
        self._cache_width = self.width
        self._cache_lines = result
        
        return result
    
    def get_cursor_coords(self, text: str, cursor_pos: int) -> tuple[int, int]:
        """Convert a string index to (row, col) coordinates."""
        available = self.available_width
        
        if available <= 0:
            return 0, 0
        
        # If at position 0, cursor is right after prompt
        if cursor_pos == 0:
            return 0, self.prompt_width
        
        # Split text into paragraphs
        full_paragraphs = text.split('\n')
        text_before_cursor = text[:cursor_pos]
        cursor_paragraphs = text_before_cursor.split('\n')
        
        # Which paragraph contains the cursor?
        cursor_para_idx = len(cursor_paragraphs) - 1
        chars_in_para = len(cursor_paragraphs[-1])
        
        row = 0
        
        # Process all paragraphs up to and including the cursor's paragraph
        for para_idx in range(cursor_para_idx + 1):
            para = full_paragraphs[para_idx] if para_idx < len(full_paragraphs) else ""
            is_first_para = (para_idx == 0)
            
            # Wrap the full paragraph
            if is_first_para:
                wrapped = wrap_text(para, available, padding_width=self.prompt_width, first_line_padding=False)
            else:
                wrapped = wrap_text(para, available, padding_width=self.prompt_width, first_line_padding=True)
            
            para_lines = wrapped.split('\n') if wrapped else [""]
            
            if para_idx < cursor_para_idx:
                # Before cursor paragraph, count all lines
                row += len(para_lines)
            else:
                # Cursor's paragraph - find which wrapped line contains cursor
                char_count = 0
                for line_idx, line in enumerate(para_lines):
                    # Strip padding to get actual text length
                    if is_first_para and line_idx == 0:
                        line_text = line
                    else:
                        line_text = line.lstrip()
                    
                    line_len = len(line_text)
                    
                    if char_count + line_len >= chars_in_para:
                        # Cursor is on this wrapped line
                        row += line_idx
                        chars_in_line = chars_in_para - char_count
                        
                        # Calculate column based on display width
                        col = display_width(line_text[:chars_in_line])
                        
                        # Add prompt width
                        if para_idx == 0 and line_idx == 0:
                            col += self.prompt_width
                        else:
                            col += self.prompt_width
                        
                        return row, col
                    
                    char_count += line_len
                
                # Cursor is at end of paragraph
                row += len(para_lines) - 1
                last_line = para_lines[-1] if para_lines else ""
                col = display_width(last_line.lstrip() if (para_idx > 0 or len(para_lines) > 1) else last_line)
                
                if para_idx == 0 and len(para_lines) == 1:
                    col += self.prompt_width
                else:
                    col += self.prompt_width
                
                return row, col
        
        return 0, self.prompt_width
    
    def get_pos_from_coords(self, text: str, target_row: int, target_col: int) -> int:
        """Find the closest string index for given (row, col) coordinates."""
        if target_row < 0:
            return 0
        
        # Get wrapped lines once (cache this expensive operation)
        lines = self.get_wrapped_lines(text)
        
        # If target row is beyond last row, return end of text
        if target_row >= len(lines):
            return len(text)
        
        # Calculate which character position corresponds to the start of target row
        paragraphs = text.split('\n')
        
        # Track which row we're on and position in text
        current_row = 0
        text_pos = 0
        
        for para_idx, para in enumerate(paragraphs):
            is_first_para = (para_idx == 0)
            
            # Wrap this paragraph
            if is_first_para:
                wrapped = wrap_text(para, self.available_width, padding_width=self.prompt_width, first_line_padding=False)
            else:
                wrapped = wrap_text(para, self.available_width, padding_width=self.prompt_width, first_line_padding=True)
            
            para_lines = wrapped.split('\n') if wrapped else [""]
            
            # Check if target row is in this paragraph
            if current_row + len(para_lines) > target_row:
                # Target row is in this paragraph
                line_in_para = target_row - current_row
                
                # Calculate position within the paragraph
                chars_before_line = 0
                for i in range(line_in_para):
                    line = para_lines[i]
                    # Strip padding to get actual text
                    if is_first_para and i == 0:
                        line_text = line
                    else:
                        line_text = line.lstrip()
                    chars_before_line += len(line_text)
                
                # Now find the position within the target line based on target_col
                target_line = para_lines[line_in_para]
                if is_first_para and line_in_para == 0:
                    target_line_text = target_line
                    col_offset = target_col - self.prompt_width
                else:
                    target_line_text = target_line.lstrip()
                    col_offset = target_col - self.prompt_width
                
                # Convert column to character position (accounting for display width)
                col_offset = max(0, col_offset)
                char_in_line = 0
                current_col = 0
                for char in target_line_text:
                    if current_col >= col_offset:
                        break
                    current_col += display_width(char)
                    char_in_line += 1
                
                return min(text_pos + chars_before_line + char_in_line, len(text))
            
            # Move to next paragraph
            current_row += len(para_lines)
            text_pos += len(para)
            if para_idx < len(paragraphs) - 1:
                text_pos += 1  # +1 for newline
        
        # If we got here, target row doesn't exist - return end of text
        return len(text)
