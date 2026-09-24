"""
Patch parser for replace-block format.

Implements Aider-style search/replace blocks:

    filename.py
    <<<<<<< SEARCH
    old code
    =======
    new code
    >>>>>>> REPLACE
"""
from dataclasses import dataclass
from typing import Tuple, Callable
import re


@dataclass
class PatchBlock:
    """Parsed patch block"""
    filename: str
    search_text: str
    replace_text: str


class PatchParseError(Exception):
    """Error parsing patch block"""
    pass


def parse_patch(content: str) -> PatchBlock:
    """
    Parse a replace-block format patch.
    
    Args:
        content: Patch content with markers
        
    Returns:
        PatchBlock with filename, search, and replace text
        
    Raises:
        PatchParseError: If patch format is invalid
    """
    lines = content.strip().split('\n')
    
    if not lines:
        raise PatchParseError("Empty patch content")
    
    # Markers
    search_marker = '<<<<<<< SEARCH'
    divider_marker = '======='
    replace_marker = '>>>>>>> REPLACE'
    
    search_start = None
    divider = None
    replace_end = None
    
    # 1. Find the SEARCH marker first (it might be indented or have preamble lines before it)
    for i, line in enumerate(lines):
        if search_marker in line:
            search_start = i
            break
            
    if search_start is None:
        raise PatchParseError(f"Missing '{search_marker}' marker")
    
    # 2. Extract filename from the line immediately before SEARCH marker
    if search_start == 0:
        raise PatchParseError("Missing filename before SEARCH marker")
        
    filename = lines[search_start - 1].strip()
    # Basic sanity check for filename (shouldn't have markers or be too long)
    if not filename or any(m in filename for m in [search_marker, divider_marker, replace_marker]):
         raise PatchParseError("Invalid or missing filename immediately before SEARCH marker")

    # 3. Find remaining markers after search_start
    for i in range(search_start + 1, len(lines)):
        line = lines[i]
        if divider_marker in line:
            if divider is not None:
                raise PatchParseError(f"Multiple {divider_marker} markers found")
            divider = i
        elif replace_marker in line:
            if replace_end is not None:
                raise PatchParseError(f"Multiple {replace_marker} markers found")
            replace_end = i
            break # Stop at first REPLACE marker
    
    # Validate markers found and order
    if divider is None:
        raise PatchParseError(f"Missing '{divider_marker}' marker")
    if replace_end is None:
        raise PatchParseError(f"Missing '{replace_marker}' marker")
    
    # 4. Extract content
    search_lines = lines[search_start + 1:divider]
    replace_lines = lines[divider + 1:replace_end]
    
    search_text = '\n'.join(search_lines)
    replace_text = '\n'.join(replace_lines)
    
    return PatchBlock(
        filename=filename,
        search_text=search_text,
        replace_text=replace_text
    )


def apply_patch(file_content: str, patch: PatchBlock) -> Tuple[str, str]:
    """
    Apply a patch to file content.
    
    Args:
        file_content: Original file content
        patch: Parsed patch block
        
    Returns:
        Tuple of (new_content, message)
        - new_content: Modified content (or original if no match)
        - message: Success or error message
        
    Examples:
        >>> content = "def foo():\\n    pass"
        >>> patch = PatchBlock("test.py", "def foo():\\n    pass", "def foo():\\n    return 42")
        >>> new, msg = apply_patch(content, patch)
        >>> "return 42" in new
        True
    """
    def _exact_positions(content: str, needle: str) -> list[tuple[int, int]]:
        if not needle:
            return []
        positions: list[tuple[int, int]] = []
        start = 0
        while True:
            idx = content.find(needle, start)
            if idx == -1:
                break
            positions.append((idx, idx + len(needle)))
            start = idx + 1
        return positions

    def _line_offsets(text: str) -> list[int]:
        offsets = [0]
        for i, ch in enumerate(text):
            if ch == '\n':
                offsets.append(i + 1)
        return offsets

    def _line_spans(
        content: str,
        search_text: str,
        normalizer: Callable[[str], str],
    ) -> list[tuple[int, int]]:
        content_lines = content.split('\n')
        search_lines = search_text.split('\n')
        if not search_lines:
            return []

        n = len(search_lines)
        if n > len(content_lines):
            return []

        normalized_search = [normalizer(line) for line in search_lines]
        offsets = _line_offsets(content)
        spans: list[tuple[int, int]] = []

        for i in range(0, len(content_lines) - n + 1):
            window = content_lines[i:i + n]
            normalized_window = [normalizer(line) for line in window]
            if normalized_window == normalized_search:
                start_idx = offsets[i]
                end_line = i + n
                end_idx = offsets[end_line] if end_line < len(offsets) else len(content)
                spans.append((start_idx, end_idx))
        return spans

    def _norm_whitespace(line: str) -> str:
        return re.sub(r'\s+', ' ', line).strip()

    def _norm_indentation(line: str) -> str:
        return line.lstrip().rstrip()

    modes: list[tuple[str, list[tuple[int, int]]]] = [
        ("exact", _exact_positions(file_content, patch.search_text)),
        ("whitespace-normalized", _line_spans(file_content, patch.search_text, _norm_whitespace)),
        ("indentation-normalized", _line_spans(file_content, patch.search_text, _norm_indentation)),
    ]

    for mode, spans in modes:
        if not spans:
            continue

        if len(spans) > 1:
            return file_content, (
                f"[ERROR] Search block is ambiguous in {mode} mode "
                f"({len(spans)} matches). Add more unique context."
            )

        start_idx, end_idx = spans[0]
        new_content = file_content[:start_idx] + patch.replace_text + file_content[end_idx:]
        return new_content, f"[OK] Applied edit to {patch.filename} (1 replacement, mode={mode})"

    search_lines = patch.search_text.split('\n')
    if search_lines:
        first_line = search_lines[0].strip()
        if first_line:
            for i, line in enumerate(file_content.split('\n'), start=1):
                if first_line in line:
                    return file_content, f"[ERROR] Search block not found. Similar content at line {i}: '{line.strip()}'"

    return file_content, "[ERROR] Search block not found in file. Tried exact, whitespace-normalized, indentation-normalized."
