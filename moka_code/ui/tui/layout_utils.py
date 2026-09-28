"""Text layout and utility functions for moka."""

import re
from typing import Optional, List
from wcwidth import wcswidth

# ANSI escape code pattern for stripping colors when calculating width
ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def strip_ansi(text: str) -> str:
    """Strip ANSI escape codes from text."""
    return ANSI_ESCAPE.sub('', text)

def display_width(text: str) -> int:
    """Calculate the display width of text including emojis and wide characters."""
    clean_text = strip_ansi(text)
    width = wcswidth(clean_text)
    return width if width >= 0 else len(clean_text)


def break_long_word(word: str, max_width: int) -> List[str]:
    """Break a word that's too long into chunks that fit within max_width."""
    chunks = []
    current_chunk = ""
    visible_width = 0
    i = 0
    
    while i < len(word):
        if word[i:i+1] == '\x1b':
            match = ANSI_ESCAPE.match(word[i:])
            if match:
                ansi_code = match.group()
                current_chunk += ansi_code
                i += len(ansi_code)
                continue
        
        char = word[i]
        char_width = display_width(char)
        
        if visible_width + char_width > max_width:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = char
            visible_width = char_width
        else:
            current_chunk += char
            visible_width += char_width
        i += 1
    
    if current_chunk:
        chunks.append(current_chunk)
    return chunks

def wrap_text(text: str, max_width: int, padding_width: int = 0, first_line_padding: bool = True) -> str:
    """Smart word wrapping for terminal text.
    
    Args:
        text: Raw text to wrap
        max_width: Maximum width for each line
        padding_width: Padding for continuation lines
        first_line_padding: Whether to apply padding to the first line
    """
    if max_width <= 0:
        return text
        
    lines = []
    paragraphs = text.split('\n')
    
    for para_idx, paragraph in enumerate(paragraphs):
        is_first_line_of_para = (para_idx == 0)
        
        # Determine if we should start with padding
        if not is_first_line_of_para or first_line_padding:
            current_prefix = " " * padding_width
        else:
            current_prefix = ""

        if not paragraph:
            lines.append(current_prefix)
            continue
            
        words = paragraph.split(' ') # Use single space to preserve intent better than .split()
        if not words:
            lines.append(current_prefix)
            continue
        
        current_line = ""
            
        line_max_width = max_width
        
        for word_idx, word in enumerate(words):
            if not word and word_idx < len(words) - 1: # Handle double spaces
                word = ""
            
            word_display_width = display_width(word)
            current_display_width = display_width(current_line)
            
            # Plus space if not starting a line
            space_needed = 1 if current_line and current_line != current_prefix else 0
            
            # The character/word fits if its width + any required space is within the limit
            if current_display_width + space_needed + word_display_width <= line_max_width:
                if current_line and current_line != current_prefix:
                    current_line += " " + word
                else:
                    if current_line == current_prefix:
                        current_line += word
                    else:
                        current_line = current_prefix + word
            else:
                # Doesn't fit. Push current and start new.
                if current_line:
                    lines.append(current_line)
                
                # New line always gets padding
                current_prefix = " " * padding_width
                
                if word_display_width > line_max_width - padding_width:
                    # Too long word
                    chunks = break_long_word(word, line_max_width - padding_width)
                    for i, chunk in enumerate(chunks[:-1]):
                        lines.append(current_prefix + chunk)
                    current_line = current_prefix + chunks[-1]
                else:
                    current_line = current_prefix + word
                    
        if current_line:
            lines.append(current_line)
        elif not lines or lines[-1] != current_prefix:
            # Ensure we add an empty line if the paragraph was just a newline
            lines.append(current_prefix)
            
    return "\n".join(lines)
