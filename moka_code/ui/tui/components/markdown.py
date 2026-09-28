"""Naive markdown parser and style system for moka TUI.

Parses markdown into a list of display lines, each being a list of
StyledSegment objects.  Styles are driven by settings.config.markdown_styles.
Designed for live re-parsing during streaming — full re-parse per update.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import List, Optional

from wcwidth import wcswidth

from moka_code import settings
from moka_code.ui.tui.colors import RGB, theme
from moka_code.ui.tui.graphemes import split_clusters
from moka_code.ui.tui.buffer import Buffer
from moka_code.ui.tui.components.base import Component


# ---------------------------------------------------------------------------
# Style resolution helpers
# ---------------------------------------------------------------------------

def _resolve_color(value: Optional[str]):
    """A style color: ``None``, a theme color name (``"MUTED"`` — follows the
    active theme, ANSI palettes included) or a hex string (an RGB tuple)."""
    if value is None:
        return None
    if not value.startswith("#") and hasattr(theme, value):
        return getattr(theme, value)
    c = RGB(value)
    return (c.r, c.g, c.b)


def _get_style(element: str) -> dict:
    """Return the resolved style dict for a markdown element name.

    Reads from settings.config.markdown_styles, converts hex strings to
    RGB tuples, and fills in missing keys with safe defaults.
    """
    cfg = settings.config.markdown_styles.get(element, {})
    return {
        "fg": _resolve_color(cfg.get("fg")),
        "bg": _resolve_color(cfg.get("bg")),
        "bold": bool(cfg.get("bold", False)),
        "reverse": bool(cfg.get("reverse", False)),
        "italic": bool(cfg.get("italic", False)),
        "underline": bool(cfg.get("underline", False)),
    }


# ---------------------------------------------------------------------------
# StyledSegment
# ---------------------------------------------------------------------------

# Fenced code is drawn indented by this much; mouse selection skips it.
CODE_INDENT = "  "


@dataclass
class StyledSegment:
    text: str
    fg: Optional[tuple[int, int, int]] = None
    bg: Optional[tuple[int, int, int]] = None
    bold: bool = False
    reverse: bool = False
    code_block: bool = False  # If True, skip word-wrap and hard-break instead
    indent: bool = False      # Code-block indent: repeated on wrapped rows, never copied
    italic: bool = False
    underline: bool = False

    @property
    def display_width(self) -> int:
        w = wcswidth(self.text)
        return w if w >= 0 else len(self.text)


@dataclass
class TableSegment(StyledSegment):
    """A whole table, rendered only once the available width is known.

    ``Markdown.parse`` emits it as a one-segment line; ``MarkdownComponent``
    renders it when wrapping, so the table fits the width (cells wrap within
    their column) and is laid out again on resize.
    """
    headers: List[str] = field(default_factory=list)
    rows: List[List[str]] = field(default_factory=list)
    align: dict = field(default_factory=dict)
    style: dict = field(default_factory=dict)

    def render(self, width: Optional[int]) -> List[List[StyledSegment]]:
        from moka_code.ui.tui.ascii_table import AsciiTable, TableStyle

        table = AsciiTable(
            headers=self.headers, rows=self.rows,
            style=TableStyle(style_name="squared", inner_vbar=True, inner_hbar=False, h_padding=1),
            max_width=None, align=self.align, total_width=width,
        )
        # code_block: never re-wrapped (the table is already fitted).
        return [[StyledSegment(line, code_block=True, **self.style)]
                for line in table.to_string().split("\n")]


# ---------------------------------------------------------------------------
# Block types
# ---------------------------------------------------------------------------

@dataclass
class ParagraphLine:
    """A paragraph / text line — will be inline-parsed into segments."""
    raw: str


@dataclass
class HeaderLine:
    """A header line: # to ######."""
    level: int
    text: str


@dataclass
class CodeBlockLine:
    """A line inside a fenced code block. Not inline-parsed."""
    text: str
    lang: str = ""
    highlighted: bool = False  # Whether syntax highlighting was applied


@dataclass
class UnorderedListItemLine:
    """A line belonging to an unordered list."""
    text: str
    indent: int = 0


@dataclass
class OrderedListItemLine:
    """A line belonging to an ordered list."""
    number: int
    text: str
    indent: int = 0


@dataclass
class QuoteLine:
    """A blockquote line."""
    text: str
    indent: int = 0


@dataclass
class HrLine:
    """Horizontal rule: --- or *** or ___."""
    pass


@dataclass
class EmptyLine:
    """Blank line between paragraphs."""
    pass


@dataclass
class TableLine:
    """A line belonging to a markdown table (header, separator, or data row)."""
    cells: List[str]
    is_separator: bool = False  # True for the --- separator row


Block = (
    ParagraphLine
    | HeaderLine
    | CodeBlockLine
    | UnorderedListItemLine
    | OrderedListItemLine
    | QuoteLine
    | HrLine
    | EmptyLine
    | TableLine
)


# ---------------------------------------------------------------------------
# Line splitting
# ---------------------------------------------------------------------------

def _split_lines(text: str) -> List[str]:
    """Split text into logical lines, dropping the phantom trailing element.

    A trailing newline does not start a new (empty) line; dropping it keeps a
    full parse identical to a line-by-line incremental parse.
    """
    lines = text.split("\n")
    if len(lines) > 1 and lines[-1] == "" and text.endswith("\n"):
        lines.pop()
    return lines


# ---------------------------------------------------------------------------
# Block-level parser
# ---------------------------------------------------------------------------

class BlockParser:
    """Parse markdown text into a list of Block objects (one per line)."""

    def parse(self, text: str) -> List[Block]:
        return self.parse_with_lines(text)[0]

    def parse_with_lines(self, text: str) -> tuple[List[Block], List[int]]:
        """Like :meth:`parse`, but also return the source line index per block."""
        blocks: List[Block] = []
        block_lines: List[int] = []
        lines = _split_lines(text)
        i = 0
        in_code_block = False
        code_fence = ""  # the opening fence (``` or ~~~) optionally with lang
        _current_code_lang = ""

        # --- Table state machine ---
        in_table = False

        def emit(block: Block, line_index: int) -> None:
            blocks.append(block)
            block_lines.append(line_index)

        while i < len(lines):
            line = lines[i]

            # --- Table state machine ---
            if in_table:
                tbl = self._parse_table_line(line)
                if tbl is not None:
                    emit(tbl, i)
                    i += 1
                    continue
                else:
                    in_table = False
                    # Fall through to normal parsing

            # --- Code block state machine ---
            if in_code_block:
                # Check for closing fence
                stripped = line.strip()
                if stripped.startswith(code_fence.rstrip()):
                    in_code_block = False
                    i += 1
                    continue
                # Preserve raw line (including leading whitespace) for code blocks
                emit(CodeBlockLine(text=line, lang=_current_code_lang), i)
                i += 1
                continue

            # Check for opening fence
            stripped = line.strip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                fence_char = stripped[0]
                code_fence = fence_char * 3
                lang = stripped[len(code_fence):].strip()
                _current_code_lang = lang
                in_code_block = True
                i += 1
                continue

            # --- Non-code-block lines ---

            # Empty line
            if stripped == "":
                emit(EmptyLine(), i)
                i += 1
                continue

            # Horizontal rule: --- or *** or ___ (at least 3, optional spaces)
            if self._is_hr(stripped):
                emit(HrLine(), i)
                i += 1
                continue

            # Header
            header = self._parse_header(line)
            if header is not None:
                emit(header, i)
                i += 1
                continue

            # Blockquote
            quote = self._parse_quote(line)
            if quote is not None:
                emit(quote, i)
                i += 1
                continue

            # Unordered list
            ul_item = self._parse_unordered_list(line)
            if ul_item is not None:
                emit(ul_item, i)
                i += 1
                continue

            # Ordered list
            ol_item = self._parse_ordered_list(line)
            if ol_item is not None:
                emit(ol_item, i)
                i += 1
                continue

            # Table: look ahead for header + separator pattern
            tbl = self._parse_table_line(line)
            if tbl is not None and not tbl.is_separator:
                # Peek ahead for separator row
                j = i + 1
                while j < len(lines) and lines[j].strip() == "":
                    j += 1
                if j < len(lines) and self._is_table_separator(lines[j]):
                    in_table = True
                    emit(tbl, i)  # header row
                    i += 1
                    # Consume separator row
                    emit(self._parse_table_line(lines[i]) or TableLine(cells=[""]), i)
                    i += 1
                    continue

            # Default: paragraph
            emit(ParagraphLine(raw=line), i)
            i += 1

        return blocks, block_lines

    def find_commit_line(self, lines: List[str], start: int, last_open: bool = False) -> int:
        """Largest line index ``c`` such that ``lines[:c]`` parses independently.

        A boundary is safe only at a line the parser visits with neutral state
        (outside a fence/table). It additionally holds the last line (it may
        still grow into a table header), any run of trailing blank lines, and a
        potential table header whose lookahead has not yet seen a separator.
        Scanning resumes at ``start`` (a previous safe boundary, neutral state).
        """
        n = len(lines)
        i = start
        in_code = False
        fence = ""
        in_table = False
        table_start = start
        last_start = start
        pending = None
        while i < n:
            if in_code:
                if lines[i].strip().startswith(fence):
                    in_code = False
                i += 1
                continue
            if in_table:
                if self._parse_table_line(lines[i]) is not None:
                    i += 1
                    continue
                # The table is closed by a non-table line. If that line is the
                # still-growing last line it might yet become a row, which would
                # re-group the table across the boundary: hold the whole table.
                if i >= n - 1 and last_open:
                    pending = table_start
                    break
                in_table = False
                # Fall through to neutral handling of this same line.
            # Neutral line start.
            last_start = i
            stripped = lines[i].strip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                fence = stripped[0] * 3
                in_code = True
                i += 1
                continue
            tbl = self._parse_table_line(lines[i])
            if tbl is not None and not tbl.is_separator:
                j = i + 1
                while j < n and lines[j].strip() == "":
                    j += 1
                # The final line may still grow (e.g. into `|---|`), so a
                # candidate separator there cannot resolve the header yet.
                if j >= n or (last_open and j == n - 1):
                    pending = i
                    break
                if self._is_table_separator(lines[j]):
                    in_table = True
                    table_start = i
                    i += 2
                    continue
            i += 1

        c = n - 1 if last_start > n - 1 else last_start
        if pending is not None:
            c = min(c, pending)
        # Keep any trailing blank run in the open region.
        while c > start and lines[c - 1].strip() == "":
            c -= 1
        return max(c, start)


    # --- helpers ---

    def _is_hr(self, s: str) -> bool:
        cleaned = s.replace(" ", "")
        if len(cleaned) < 3:
            return False
        return cleaned in ("---", "___") or (all(c == "-" for c in cleaned)) or (all(c == "*" for c in cleaned)) or (all(c == "_" for c in cleaned))

    def _parse_header(self, line: str) -> Optional[HeaderLine]:
        stripped = line.lstrip()
        if not stripped.startswith("#"):
            return None
        level = 0
        for ch in stripped:
            if ch == "#":
                level += 1
            else:
                break
        if level > 6:
            return None
        # Must have a space after # (or be just #s)
        rest = stripped[level:]
        if rest and rest[0] != " ":
            return None
        text = rest.strip()
        return HeaderLine(level=level, text=text)

    def _parse_quote(self, line: str) -> Optional[QuoteLine]:
        stripped = line.lstrip()
        if not stripped.startswith(">"):
            return None
        # Remove leading > and optional space
        text = stripped[1:]
        if text and text[0] == " ":
            text = text[1:]
        # Count indent (spaces before >)
        indent = len(line) - len(line.lstrip())
        return QuoteLine(text=text, indent=indent)

    def _parse_unordered_list(self, line: str) -> Optional[UnorderedListItemLine]:
        stripped = line.lstrip()
        if len(stripped) < 2:
            return None
        marker = stripped[0]
        if marker not in ("-", "*", "+"):
            return None
        if stripped[1] != " ":
            return None
        indent = len(line) - len(line.lstrip())
        text = stripped[2:]
        return UnorderedListItemLine(text=text, indent=indent)

    def _parse_ordered_list(self, line: str) -> Optional[OrderedListItemLine]:
        stripped = line.lstrip()
        # Match: digits followed by dot and space
        i = 0
        while i < len(stripped) and stripped[i].isdigit():
            i += 1
        if i == 0 or i >= len(stripped):
            return None
        if stripped[i] != ".":
            return None
        if i + 1 >= len(stripped) or stripped[i + 1] != " ":
            return None
        number = int(stripped[:i])
        indent = len(line) - len(line.lstrip())
        text = stripped[i + 2:]
        return OrderedListItemLine(number=number, text=text, indent=indent)

    def _parse_table_line(self, line: str) -> Optional[TableLine]:
        """Try to parse a markdown table line (header, separator, or data row).

        A table line must start and end with '|' and contain at least two cells.
        """
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            return None
        # Remove leading/trailing pipes
        inner = stripped[1:-1].strip()
        if not inner:
            return None
        cells = [c.strip() for c in inner.split("|")]
        if len(cells) < 2:
            return None
        # Check if this is a separator row (only dashes, colons, spaces)
        is_separator = all(
            all(c in " -:=" for c in cell) for cell in cells
        ) and any("-" in cell for cell in cells)
        return TableLine(cells=cells, is_separator=is_separator)

    def _is_table_separator(self, line: str) -> bool:
        """Check if a line is a table separator (--- row)."""
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            return False
        inner = stripped[1:-1].strip()
        cells = [c.strip() for c in inner.split("|")]
        return (
            len(cells) >= 2
            and all(all(c in " -:=" for c in cell) for cell in cells)
            and any("-" in cell for cell in cells)
        )


# ---------------------------------------------------------------------------
# Inline parser
# ---------------------------------------------------------------------------

class InlineParser:
    """Parse a single line of markdown text into a list of StyledSegment.

    Handles: **bold**, *italic*, `code`, [text](url).
    Uses a sequential character scanner — no regex — for controlled boundary
    handling.  Unmatched openers are emitted as plain text.
    """

    def parse(self, text: str) -> List[StyledSegment]:
        segments: List[StyledSegment] = []
        i = 0
        n = len(text)

        while i < n:
            # Inline code: `...`
            if text[i] == "`":
                end = text.find("`", i + 1)
                if end == -1:
                    # Unclosed backtick — emit as plain
                    segments.append(StyledSegment(text[i]))
                    i += 1
                else:
                    code_text = text[i + 1:end]
                    style = _get_style("code")
                    segments.append(StyledSegment(code_text, **style))
                    i = end + 1
                continue

            # Bold: **...**
            if text[i:i + 2] == "**":
                end = text.find("**", i + 2)
                if end == -1:
                    segments.append(StyledSegment(text[i]))
                    i += 1
                else:
                    inner = text[i + 2:end]
                    style = _get_style("bold")
                    segments.append(StyledSegment(inner, **style))
                    i = end + 2
                continue

            # Italic: *...*  (but not **)
            if text[i] == "*" and (i + 1 >= n or text[i + 1] != "*"):
                end = self._find_closing_star(text, i + 1)
                if end is None:
                    segments.append(StyledSegment(text[i]))
                    i += 1
                else:
                    inner = text[i + 1:end]
                    style = _get_style("italic")
                    segments.append(StyledSegment(inner, **style))
                    i = end + 1
                continue

            # Link: [text](url)
            if text[i] == "[":
                result = self._parse_link(text, i)
                if result is not None:
                    seg, consumed = result
                    segments.append(seg)
                    i += consumed
                    continue

            # Plain character
            segments.append(StyledSegment(text[i]))
            i += 1

        return segments

    def _find_closing_star(self, text: str, start: int) -> Optional[int]:
        """Find the first single * at or after start that isn't followed by another *."""
        i = start
        n = len(text)
        while i < n:
            if text[i] == "*":
                # Make sure it's a single star (not **)
                if i + 1 >= n or text[i + 1] != "*":
                    return i
                # Skip past **
                i += 2
            else:
                i += 1
        return None

    def _parse_link(self, text: str, start: int) -> Optional[tuple[StyledSegment, int]]:
        """Try to parse a [text](url) link starting at position start.

        Returns (segment, chars_consumed) or None if not a valid link.
        """
        # Find closing ]
        bracket_end = text.find("]", start + 1)
        if bracket_end == -1:
            return None
        # Must be immediately followed by (
        if bracket_end + 1 >= len(text) or text[bracket_end + 1] != "(":
            return None
        # Find closing )
        paren_end = text.find(")", bracket_end + 2)
        if paren_end == -1:
            return None

        link_text = text[start + 1:bracket_end]
        # url = text[bracket_end + 2:paren_end]  # available but not rendered
        style = _get_style("link")
        seg = StyledSegment(link_text, **style)
        consumed = paren_end - start + 1
        return (seg, consumed)


# ---------------------------------------------------------------------------
# Markdown — high-level wrapper
# ---------------------------------------------------------------------------

class Markdown:
    """Parse markdown text into a list of display lines (each a list of
    StyledSegment).

    Usage:
        md = Markdown()
        lines = md.parse("# Hello **world**")
        # lines -> [ [<StyledSegment("# Hello world") with header1 style> ] ]

        # For live updates during streaming:
        md = Markdown()
        for chunk in stream:
            lines = md.parse(full_text_so_far)
    """

    def __init__(self):
        self._block_parser = BlockParser()
        self._inline_parser = InlineParser()

    def parse(
        self,
        text: str,
        strip_trailing: bool = True,
        open_tail: bool = False,
    ) -> List[List[StyledSegment]]:
        """Return a list of lines; each line is a list of StyledSegment.

        ``strip_trailing`` removes trailing blank lines (the default for a
        complete document). Incremental append parsing passes ``False`` for the
        stable prefix so its separating blank line is retained.

        ``open_tail`` renders the final line with its inline content *plain*
        (no ``InlineParser``). While a line is still being streamed, closed
        spans such as ``**bold**`` stay literal until its newline arrives; this
        keeps per-append cost independent of how much of the line has arrived.
        """
        if not text:
            return []

        blocks, block_lines = self._block_parser.parse_with_lines(text)
        open_line = None
        if open_tail and not text.endswith("\n"):
            open_line = len(_split_lines(text)) - 1
        result: List[List[StyledSegment]] = []

        i = 0
        while i < len(blocks):
            block = blocks[i]

            # Group consecutive TableLine blocks into a table
            if isinstance(block, TableLine):
                table_blocks: List[TableLine] = []
                while i < len(blocks) and isinstance(blocks[i], TableLine):
                    table_blocks.append(blocks[i])
                    i += 1
                rendered = self._render_table(table_blocks)
                result.extend(rendered)
                continue

            plain = open_line is not None and block_lines[i] == open_line
            rendered = self._render_block(block, plain_inline=plain)
            result.extend(rendered)
            i += 1

        # Strip trailing empty lines
        if strip_trailing:
            while result and not result[-1]:
                result.pop()

        return result

    def find_commit_line(self, lines: List[str], start: int, last_open: bool = False) -> int:
        """Line count up to which ``lines`` may be committed (see BlockParser)."""
        return self._block_parser.find_commit_line(lines, start, last_open)

    def _render_block(self, block: Block, plain_inline: bool = False) -> List[List[StyledSegment]]:
        if isinstance(block, EmptyLine):
            return [[]]

        if isinstance(block, HeaderLine):
            element_name = f"header{block.level}"
            style = _get_style(element_name)
            # Add header marker for visual clarity
            marker = self._header_marker(block.level)
            segments = [StyledSegment(marker, **style)]
            # Inline-parse the header text for bold/code/etc inside headers
            inner = [StyledSegment(block.text)] if plain_inline else self._inline_parser.parse(block.text)
            # Merge header style into each segment
            for seg in inner:
                seg.bold = seg.bold or style["bold"]
                seg.reverse = seg.reverse or style["reverse"]
                seg.italic = seg.italic or style["italic"]
                seg.underline = seg.underline or style["underline"]
                if seg.fg is None:
                    seg.fg = style["fg"]
                if seg.bg is None:
                    seg.bg = style["bg"]
            segments.extend(inner)
            return [segments]

        if isinstance(block, CodeBlockLine):
            style = _get_style("code_block")
            base_fg = style["fg"]
            # Apply syntax highlighting
            from moka_code.ui.tui.syntax_highlight import highlight_line, _get_highlight_color
            hl_segments = [(block.text, "")] if plain_inline else highlight_line(block.text, block.lang)
            result_segs = [StyledSegment(CODE_INDENT, code_block=True, indent=True)]
            for text, hl_type in hl_segments:
                seg_fg = _get_highlight_color(hl_type)
                if seg_fg is None:
                    seg_fg = base_fg
                result_segs.append(StyledSegment(text, fg=seg_fg, bg=style["bg"], code_block=True))
            return [result_segs]

        if isinstance(block, QuoteLine):
            style = _get_style("quote")
            segments = [StyledSegment("│ ", **style)]
            inner = [StyledSegment(block.text)] if plain_inline else self._inline_parser.parse(block.text)
            for seg in inner:
                seg.reverse = seg.reverse or style["reverse"]
                if seg.fg is None:
                    seg.fg = style["fg"]
                if seg.bg is None:
                    seg.bg = style["bg"]
            segments.extend(inner)
            return [segments]

        if isinstance(block, UnorderedListItemLine):
            style = _get_style("list")
            indent_str = "  " * block.indent
            # Nesting level (a list line's indent is its leading space count;
            # each level is two spaces). Deeper levels cycle the pastille.
            bullet = ("•", "◦", "▪")[min(block.indent // 2, 2)]
            segments = [StyledSegment(f"{indent_str}{bullet} ")]
            inner = [StyledSegment(block.text)] if plain_inline else self._inline_parser.parse(block.text)
            for seg in inner:
                if seg.fg is None:
                    seg.fg = style["fg"]
                if seg.bg is None:
                    seg.bg = style["bg"]
            segments.extend(inner)
            return [segments]

        if isinstance(block, OrderedListItemLine):
            style = _get_style("list")
            indent_str = "  " * block.indent
            prefix = f"{indent_str}{block.number}. "
            segments = [StyledSegment(prefix)]
            inner = [StyledSegment(block.text)] if plain_inline else self._inline_parser.parse(block.text)
            for seg in inner:
                if seg.fg is None:
                    seg.fg = style["fg"]
                if seg.bg is None:
                    seg.bg = style["bg"]
            segments.extend(inner)
            return [segments]

        if isinstance(block, HrLine):
            style = _get_style("hr")
            # Sentinel segment — MarkdownComponent.render replaces with box-drawing chars
            return [[StyledSegment("hr", **style)]]

        if isinstance(block, ParagraphLine):
            if plain_inline:
                return [[StyledSegment(block.raw)]]
            return [self._inline_parser.parse(block.raw)]

        return [[]]

    def _header_marker(self, level: int) -> str:
        """The source's own ``#`` run, so the level reads as written."""
        return "#" * level + " "

    def _render_table(self, table_blocks: List[TableLine]) -> List[List[StyledSegment]]:
        """Render a group of TableLine blocks as an ASCII table.

        Uses the vendored AsciiTable module.  The first non-separator row
        is treated as headers; subsequent non-separator rows are data.
        """
        headers: List[str] = []
        rows: List[List[str]] = []
        separator: Optional[TableLine] = None
        for tbl in table_blocks:
            if tbl.is_separator:
                if separator is None:
                    separator = tbl
                continue
            if not headers:
                headers = tbl.cells
            else:
                rows.append(tbl.cells)

        if not headers:
            return [[]]

        # Derive per-column alignment from the separator row (`:--`, `--:`, `:-:`).
        align: dict[str, str] = {}
        if separator is not None:
            for i, cell in enumerate(separator.cells):
                if i >= len(headers):
                    break
                marker = cell.strip()
                if marker.startswith(":") and marker.endswith(":"):
                    align[headers[i]] = "center"
                elif marker.endswith(":"):
                    align[headers[i]] = "right"
                elif marker.startswith(":"):
                    align[headers[i]] = "left"

        # Inline markup (`code`, **bold**) would show its markers literally in
        # a cell: keep the text only.
        def plain(cell: str) -> str:
            return "".join(seg.text for seg in self._inline_parser.parse(cell))

        style_cfg = _get_style("table") if "table" in settings.config.markdown_styles else _get_style("paragraph")
        return [[TableSegment(
            "",
            headers=[plain(cell) for cell in headers],
            rows=[[plain(cell) for cell in row] for row in rows],
            align={plain(k): v for k, v in align.items()},
            style=style_cfg,
        )]]


# ---------------------------------------------------------------------------
# MarkdownComponent — TUI component
# ---------------------------------------------------------------------------

def _expand_tables(lines: List[List[StyledSegment]]) -> List[List[StyledSegment]]:
    """Render table placeholders without a width limit (no layout yet)."""
    result: List[List[StyledSegment]] = []
    for line in lines:
        if len(line) == 1 and isinstance(line[0], TableSegment):
            result.extend(line[0].render(None))
        else:
            result.append(line)
    return result


class MarkdownComponent(Component):
    """Renders markdown text as styled segments with segment-aware wrapping.

    Parses on every `update()` call, making it suitable for live streaming
    where the full text changes between calls. Append-only updates commit
    complete lines atomically and re-parse/re-wrap only the open region, so an
    append costs O(open region) rather than O(message length).

    While streaming, the still-open final line is rendered with its inline
    content plain (see :meth:`Markdown.parse`); :meth:`set_streaming` clears the
    rule so a finalized message is styled in full.
    """

    def __init__(self, text: str = "", fg=None, bg=None, id: Optional[str] = None,
                 left_pad: int = 0, streaming: bool = False):
        super().__init__(id)
        self.fg = fg
        self.bg = bg
        self._md = Markdown()
        self._raw_text = text
        self._parsed_lines: List[List[StyledSegment]] = []
        self._wrapped_lines: List[List[StyledSegment]] = []
        self._last_wrap_width = -1
        self.left_pad = left_pad
        self._streaming = streaming
        # Append-only caches: committed lines are parsed/wrapped once and only
        # ever extended. The open region (the uncommitted tail) is re-parsed and
        # re-wrapped on each append and concatenated after the caches.
        self._committed_parsed: List[List[StyledSegment]] = []
        self._committed_wrapped: List[List[StyledSegment]] = []
        self._open_parsed: List[List[StyledSegment]] = []
        self._open_wrapped: List[List[StyledSegment]] = []
        self._commit_line = 0   # number of committed source lines
        self._commit_char = 0   # char length of the committed prefix
        # First visual line that may have changed since the last render
        # (None = full redraw).
        self._dirty_from_line: Optional[int] = None
        self._do_parse_and_wrap(text)

    def take_dirty_from_line(self) -> Optional[int]:
        """Return (and clear) the first changed visual line since last render.

        ``None`` means the caller must do a full redraw.
        """
        dirty = self._dirty_from_line
        self._dirty_from_line = None
        return dirty

    def set_streaming(self, streaming: bool):
        """Declare whether the final line is still open (approach A).

        Clearing it (on finalize) re-parses in full so the previously-open line
        receives its inline styling.
        """
        if self._streaming == streaming:
            return
        self._streaming = streaming
        self._do_parse_and_wrap(self._raw_text)
        self._dirty_from_line = None
        self.mark_changed()

    def update(self, text: str, append: bool = False):
        """Update with new markdown text.

        When ``append`` is true and ``text`` simply extends the previous raw
        text, completed lines are committed and only the open region is
        re-parsed/wrapped.
        """
        old_text = self._raw_text
        eff = self._effective_wrap_width()

        if append and text == old_text:
            return

        if append and eff > 0 and text.startswith(old_text) and old_text != text:
            self._raw_text = text
            self._streaming = True
            self._update_incremental(text, eff)
            self.mark_changed()
            return

        self._raw_text = text
        self._last_wrap_width = -1  # Force re-wrap
        self._do_parse_and_wrap(text)
        self._dirty_from_line = None
        self.mark_changed()

    def _update_incremental(self, text: str, eff: int):
        lines = _split_lines(text)
        prev_open_start = len(self._committed_wrapped)

        new_c = self._md.find_commit_line(
            lines, self._commit_line, last_open=not text.endswith("\n"))
        if new_c > self._commit_line:
            new_char = sum(len(line) + 1 for line in lines[:new_c])
            extension = text[self._commit_char:new_char]
            ext_parsed = self._md.parse(extension, strip_trailing=False)
            self._committed_parsed.extend(ext_parsed)
            self._committed_wrapped.extend(self._wrap_all(ext_parsed, eff))
            self._commit_line = new_c
            self._commit_char = new_char

        open_text = text[self._commit_char:]
        self._open_parsed = self._md.parse(
            open_text, strip_trailing=True, open_tail=not text.endswith("\n"))
        self._open_wrapped = self._wrap_all(self._open_parsed, eff)
        combined_parsed = self._committed_parsed + self._open_parsed
        combined_wrapped = self._committed_wrapped + self._open_wrapped
        # A table render can leave a trailing blank line. When the open region
        # renders to nothing, that blank is document-trailing and must be
        # stripped (the open region's own parse already strips its blanks).
        if not self._open_parsed:
            while combined_parsed and not combined_parsed[-1]:
                combined_parsed.pop()
            while combined_wrapped and not combined_wrapped[-1]:
                combined_wrapped.pop()
        self._parsed_lines = combined_parsed
        self._wrapped_lines = combined_wrapped
        self._last_wrap_width = eff

        dirty = prev_open_start
        if self._dirty_from_line is None or dirty < self._dirty_from_line:
            self._dirty_from_line = dirty

    def _effective_wrap_width(self) -> int:
        """Width available for content after left padding."""
        return max(0, self.width - self.left_pad)

    def _do_parse_and_wrap(self, text: str):
        open_tail = self._streaming and bool(text) and not text.endswith("\n")
        self._parsed_lines = self._md.parse(text, open_tail=open_tail)
        self._committed_parsed = []
        self._committed_wrapped = []
        self._open_parsed = self._parsed_lines
        self._open_wrapped = []
        self._commit_line = 0
        self._commit_char = 0
        self._rebuild_wrapped(self._effective_wrap_width())

    def _rebuild_wrapped(self, eff: int):
        """Re-wrap committed and open segments for a new width (rare)."""
        if eff <= 0:
            # No width yet: tables render at their natural size.
            self._committed_wrapped = _expand_tables(self._committed_parsed)
            self._open_wrapped = _expand_tables(self._open_parsed)
            self._wrapped_lines = self._committed_wrapped + self._open_wrapped
        else:
            self._committed_wrapped = self._wrap_all(self._committed_parsed, eff)
            self._open_wrapped = self._wrap_all(self._open_parsed, eff)
            self._wrapped_lines = self._committed_wrapped + self._open_wrapped
        self._last_wrap_width = eff

    def set_layout(self, x: int, y: int, width: int, height: int):
        super().set_layout(x, y, width, height)
        # Re-wrap on width change
        eff = self._effective_wrap_width()
        if eff > 0 and eff != self._last_wrap_width:
            self._rebuild_wrapped(eff)

    def get_preferred_height(self, width: int) -> int:
        """Calculate height needed for wrapped content."""
        eff = max(0, width - self.left_pad)
        if eff <= 0:
            return 0
        if self._last_wrap_width != eff:
            self._rebuild_wrapped(eff)
        return len(self._wrapped_lines)

    # --- Wrapping ---

    def _wrap_all(self, lines: List[List[StyledSegment]], max_width: int) -> List[List[StyledSegment]]:
        """Wrap all lines to max_width, preserving segment styles."""
        result: List[List[StyledSegment]] = []
        for line in lines:
            if len(line) == 1 and isinstance(line[0], TableSegment):
                result.extend(line[0].render(max_width))
                continue
            wrapped = self._wrap_line(line, max_width)
            result.extend(wrapped)
        return result

    def _wrap_line(self, segments: List[StyledSegment], max_width: int) -> List[List[StyledSegment]]:
        """Wrap a single line of segments to max_width.

        Splits at word boundaries (spaces between segments or within segments).
        For code block lines (segments with code_block=True), hard-breaks
        instead of word-wrapping to preserve indentation.
        Returns a list of wrapped lines, each being a list of segments.
        """
        # Special case: HR sentinel — return as-is, component.render handles it
        if len(segments) == 1 and segments[0].text == "hr":
            return [segments]

        # Code block lines: hard-break to preserve indentation; the block's
        # own indent starts every wrapped row.
        if segments and segments[0].indent:
            rows = self._hard_break_line(segments[1:], max(1, max_width - len(CODE_INDENT)))
            return [[segments[0], *row] for row in rows]
        if segments and segments[0].code_block:
            return self._hard_break_line(segments, max_width)

        # Split segments into "words" (runs of non-space text with their styles)
        words = self._split_into_words(segments)

        # If no words (empty line)
        if not words:
            return [[]]

        wrapped: List[List[StyledSegment]] = []
        current_line: List[StyledSegment] = []
        current_width = 0

        for word, word_width, trailing_spaces in words:
            # Account for trailing spaces in the needed width
            space_width = trailing_spaces if trailing_spaces else (1 if current_line else 0)
            needed = current_width + space_width + word_width
            if needed <= max_width:
                # Fits on current line — add spaces then word
                num_spaces = trailing_spaces if trailing_spaces else (1 if current_line else 0)
                current_line.append(StyledSegment(" " * num_spaces))
                current_width += num_spaces
                current_line.extend(word)
                current_width += word_width
            else:
                # Doesn't fit — push current line and start new
                if current_line:
                    wrapped.append(current_line)
                    current_line = []
                    current_width = 0

                # Try to fit word on new line
                if word_width <= max_width:
                    current_line.extend(word)
                    current_width = word_width
                else:
                    # Word is longer than max_width — hard-break it
                    broken = self._break_segments(word, max_width)
                    for part in broken[:-1]:
                        wrapped.append(part)
                    current_line = broken[-1]
                    current_width = sum(s.display_width for s in current_line)

        if current_line:
            wrapped.append(current_line)

        return wrapped if wrapped else [[]]

    def _hard_break_line(self, segments: List[StyledSegment], max_width: int) -> List[List[StyledSegment]]:
        """Hard-break a line of segments at max_width, preserving all characters
        including spaces (for code blocks with indentation)."""
        result: List[List[StyledSegment]] = []
        current: List[StyledSegment] = []
        current_width = 0

        for seg in segments:
            # Break by grapheme cluster so emoji sequences keep their width.
            for cluster in split_clusters(seg.text):
                ch_width = wcswidth(cluster)
                if ch_width < 0:
                    ch_width = 1

                if current and current_width + ch_width > max_width:
                    # Flush current line
                    result.append(current)
                    current = []
                    current_width = 0

                current.append(replace(seg, text=cluster))
                current_width += ch_width

        if current:
            result.append(current)
        elif not result:
            result.append([])

        return result

    def _split_into_words(self, segments: List[StyledSegment]) -> List[tuple[List[StyledSegment], int, int]]:
        """Split segments into words separated by spaces.

        Returns list of (segments_for_word, word_width, trailing_spaces).
        trailing_spaces records how many consecutive spaces followed this word,
        so that _wrap_line can restore them.
        """
        words: List[tuple[List[StyledSegment], int, int]] = []
        current_word: List[StyledSegment] = []
        trailing = 0

        for seg in segments:
            # Split segment text by spaces, preserving style.
            # idx > 0 means there was a space separator before this part.
            parts = seg.text.split(" ")
            for idx, part in enumerate(parts):
                if idx > 0:
                    # Space separator — flush current word, count the space
                    if current_word:
                        w = sum(s.display_width for s in current_word)
                        words.append((list(current_word), w, trailing))
                        current_word = []
                        trailing = 0
                    trailing += 1
                if part:
                    current_word.append(replace(seg, text=part, code_block=False))

        if current_word:
            w = sum(s.display_width for s in current_word)
            words.append((list(current_word), w, trailing))

        return words

    def _break_segments(self, segments: List[StyledSegment], max_width: int) -> List[List[StyledSegment]]:
        """Hard-break segments that exceed max_width into chunks."""
        result: List[List[StyledSegment]] = []
        current: List[StyledSegment] = []
        current_width = 0

        for seg in segments:
            # Break by grapheme cluster so emoji sequences keep their width.
            clusters = split_clusters(seg.text)
            widths = []
            for cluster in clusters:
                w = wcswidth(cluster)
                widths.append(w if w >= 0 else 1)

            idx = 0
            while idx < len(clusters):
                w = widths[idx]
                if not current or current_width + w <= max_width:
                    # Fits
                    current.append(replace(seg, text=clusters[idx], code_block=False))
                    current_width += w
                    idx += 1
                else:
                    # Flush current
                    result.append(list(current))
                    current = []
                    current_width = 0

        if current:
            result.append(current)
        elif not result:
            result.append([])

        return result

    # --- Rendering ---

    def render(self, buffer: Buffer):
        lines = self._wrapped_lines
        default_fg = self.fg if self.fg is not None else theme.DEFAULT
        default_bg = self.bg if self.bg is not None else theme.get_bg()

        # Restrict to the buffer's clip band (set by Box for tail rastering) so
        # we don't walk lines that cannot be seen.
        first_y = 0
        last_y = min(len(lines), self.height)
        clip = getattr(buffer, "clip_rect", None)
        if clip is not None:
            clip_top = clip[1]
            clip_bottom = clip[1] + clip[3]
            first_y = max(first_y, clip_top - self.y)
            last_y = min(last_y, clip_bottom - self.y)

        for y in range(first_y, last_y):
            line = lines[y]

            # Special case: HR sentinel
            if len(line) == 1 and line[0].text == "hr":
                hr_style = _get_style("hr")
                hr_char = "\u2500"  # box-drawing horizontal
                style_fg = hr_style["fg"] if hr_style["fg"] else (default_fg.r, default_fg.g, default_fg.b) if isinstance(default_fg, RGB) else None
                # Apply left padding to HR too
                buffer.write_str(self.x, self.y + y, " " * self.left_pad, fg=style_fg, max_width=self.width)
                buffer.write_str(self.x + self.left_pad, self.y + y, hr_char * (self.width - self.left_pad), fg=style_fg, max_width=self.width - self.left_pad)
                continue

            # Start each line with left padding
            curr_x = self.left_pad
            for seg in line:
                if curr_x >= self.width:
                    break
                seg_fg = seg.fg if seg.fg is not None else ((default_fg.r, default_fg.g, default_fg.b) if isinstance(default_fg, RGB) else None)
                seg_bg = seg.bg if seg.bg is not None else ((default_bg.r, default_bg.g, default_bg.b) if default_bg and isinstance(default_bg, RGB) else None)
                buffer.write_str(
                    self.x + curr_x,
                    self.y + y,
                    seg.text,
                    fg=seg_fg,
                    bg=seg_bg,
                    bold=seg.bold,
                    reverse=seg.reverse,
                    max_width=self.width - curr_x,
                    underline=seg.underline,
                    italic=seg.italic,
                )
                curr_x += seg.display_width
