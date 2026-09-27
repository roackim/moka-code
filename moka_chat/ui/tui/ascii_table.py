"""Simple ASCII table renderer — no external dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple, Any

from moka_chat.ui.tui.graphemes import split_clusters
from moka_chat.ui.tui.layout_utils import display_width, wrap_text

# A column squeezed to fit ``total_width`` keeps at least this many cells
# (unless its content is narrower); below that the table overflows instead.
_MIN_FIT_WIDTH = 6


def _truncate_to_width(text: str, width: int) -> str:
    """Truncate ``text`` to at most ``width`` display cells (cluster-aware)."""
    if width <= 0:
        return ""
    out: List[str] = []
    used = 0
    for cluster in split_clusters(text):
        cw = display_width(cluster)
        if used + cw > width:
            break
        out.append(cluster)
        used += cw
    return "".join(out)


def _pad_to_width(text: str, width: int, side: str) -> str:
    """Pad ``text`` to ``width`` display cells (wide chars/emoji aware)."""
    pad = width - display_width(text)
    if pad <= 0:
        return text
    if side == "right":
        return " " * pad + text
    if side == "center":
        left = pad // 2
        return " " * left + text + " " * (pad - left)
    return text + " " * pad


@dataclass
class TableStyle:
    """Style configuration for ASCII table borders."""

    inner_vbar: bool = True
    inner_hbar: bool = False
    h_padding: int = 1
    v_padding: int = 0

    style_name: str = "squared"

    # Derived border characters (populated in __post_init__)
    h: str = ""
    v: str = ""
    tl: str = ""
    tr: str = ""
    bl: str = ""
    br: str = ""
    ml: str = ""
    mr: str = ""
    mt: str = ""
    mb: str = ""
    cr: str = ""

    def __post_init__(self):
        styles: Dict[str, str] = {
            "squared": "─│┌┐└┘├┤┬┴┼",
            "rounded": "─│╭╮╰╯├┤┬┴┼",
            "simple":  "-|+++++++++",
            "double":  "═║╔╗╚╝╠╣╦╩╬",
        }
        s = styles.get(self.style_name, styles["squared"])
        self.h, self.v, self.tl, self.tr, self.bl, self.br = s[0], s[1], s[2], s[3], s[4], s[5]
        self.ml, self.mr, self.mt, self.mb, self.cr = s[6], s[7], s[8], s[9], s[10]


class AsciiTable:
    """Render a 2-D table as an ASCII string.

    Parameters
    ----------
    headers : list[str]
        Column headers.
    rows : list[list]
        Table data rows (each row is a list of values).
    style : TableStyle
        Border / padding configuration.
    max_width : int or None
        Per-column maximum display width (``None`` = unlimited); longer cells
        are truncated with an ellipsis.
    total_width : int or None
        Fit the whole table (borders included) in this many cells: the widest
        columns shrink first and their cells wrap onto several lines. When any
        row spans several lines, rows are separated by horizontal lines.
    align : dict[str, str] or None
        Per-column alignment, e.g. ``{"age": "right"}``.  Accepted values:
        ``"left"`` (default), ``"right"``, ``"center"``.
    """

    def __init__(
        self,
        headers: List[str],
        rows: List[List[Any]],
        style: TableStyle | None = None,
        max_width: int | None = 35,
        align: Dict[str, str] | None = None,
        total_width: int | None = None,
    ):
        self.headers = [str(h) for h in headers]
        self.rows = [[str(v) for v in row] for row in rows]
        self._style = style or TableStyle()
        self.max_width = max_width
        self.align = {k.lower(): v for k, v in (align or {}).items()}

        # Compute column widths
        num_cols = len(self.headers)
        self._col_widths: List[int] = []
        for col_idx in range(num_cols):
            col_values = [self.headers[col_idx]] + [
                row[col_idx] for row in self.rows if col_idx < len(row)
            ]
            max_len = max(display_width(v) for v in col_values) if col_values else 0
            if self.max_width is not None and max_len > self.max_width:
                max_len = self.max_width
            self._col_widths.append(max_len)
        if total_width is not None and self._col_widths:
            s = self._style
            gap = 2 * s.h_padding + 1 if s.inner_vbar else s.h_padding
            overhead = 2 + 2 * s.h_padding + gap * (num_cols - 1)
            self._col_widths = _fit_widths(self._col_widths, total_width - overhead)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def to_string(self) -> str:
        """Return the full ASCII table as a string."""
        lines: List[str] = []
        s = self._style
        hp = s.h_padding
        vp = s.v_padding
        ivb = s.inner_vbar
        widths = self._col_widths

        header = self._cell_lines(self.headers, widths)
        body = [self._cell_lines(row, widths) for row in self.rows]
        # Multi-line rows are hard to tell apart without row separators.
        ihb = s.inner_hbar or any(len(row) > 1 for row in body)

        lines.append(self._separator("top", widths, ivb, hp))
        for _ in range(vp):
            lines.append(self._blank_line(widths, ivb, hp))
        lines += [self._content_line(values, widths, ivb, hp) for values in header]
        for _ in range(vp):
            lines.append(self._blank_line(widths, ivb, hp))
        lines.append(self._separator("mid", widths, ivb, hp))

        for index, row in enumerate(body):
            for _ in range(vp):
                lines.append(self._blank_line(widths, ivb, hp))
            lines += [self._content_line(values, widths, ivb, hp) for values in row]
            for _ in range(vp):
                lines.append(self._blank_line(widths, ivb, hp))
            if ihb and index < len(body) - 1:
                lines.append(self._separator("mid", widths, ivb, hp))

        lines.append(self._separator("bot", widths, ivb, hp))
        return "\n".join(lines)

    def _cell_lines(self, values: List[str], widths: List[int]) -> List[List[str]]:
        """A row as physical lines: each cell word-wrapped to its column.

        Cells only wrap when the table was fitted (``total_width``); with a
        ``max_width`` cap alone they are truncated by ``_content_line``.
        """
        if self.max_width is not None:
            return [values]
        columns = []
        for i, value in enumerate(values[:len(widths)]):
            if display_width(value) <= widths[i]:
                columns.append([value])
            else:
                columns.append([line.rstrip() for line in
                                wrap_text(value, widths[i], 0, False).split("\n")])
        height = max((len(c) for c in columns), default=1)
        return [[c[k] if k < len(c) else "" for c in columns] for k in range(height)]

    def __str__(self) -> str:
        return self.to_string()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _separator(self, pos: str, widths: List[int], ivb: bool, hp: int) -> str:
        s = self._style
        h = s.h
        hhp = h * hp

        if pos == "top":
            corner_l, corner_r = s.tl, s.tr
            mid_char = s.mt if ivb else h
        elif pos == "mid":
            corner_l, corner_r = s.ml, s.mr
            mid_char = s.cr if ivb else h
        else:  # bot
            corner_l, corner_r = s.bl, s.br
            mid_char = s.mb if ivb else h

        if ivb:
            sep = hhp + mid_char + hhp
        else:
            sep = hhp

        mids = [h * w for w in widths]
        return corner_l + hhp + sep.join(mids) + hhp + corner_r

    def _blank_line(self, widths: List[int], ivb: bool, hp: int) -> str:
        s = self._style
        shp = " " * hp
        if ivb:
            sep = shp + s.v + shp
        else:
            sep = shp
        mids = [" " * w for w in widths]
        return s.v + shp + sep.join(mids) + shp + s.v

    def _content_line(self, values: List[str], widths: List[int], ivb: bool, hp: int) -> str:
        s = self._style
        shp = " " * hp
        if ivb:
            sep = shp + s.v + shp
        else:
            sep = shp

        mids: List[str] = []
        for i, val in enumerate(values):
            col_name = self.headers[i].lower() if i < len(self.headers) else ""
            side = self.align.get(col_name, "left")
            if display_width(val) > widths[i]:
                truncated = _truncate_to_width(val, max(0, widths[i] - 1)) + "‥"
            else:
                truncated = val
            mids.append(_pad_to_width(truncated, widths[i], side))

        # Pad or clip row to match column count
        while len(mids) < len(widths):
            mids.append(" " * widths[len(mids)])

        return s.v + shp + sep.join(mids) + shp + s.v


def _fit_widths(natural: List[int], available: int) -> List[int]:
    """Column widths summing to ``available``: the widest columns shrink first.

    Columns already narrower than the common cap keep their natural width;
    leftover space goes back to the columns that were cut the most.
    """
    if sum(natural) <= available:
        return list(natural)
    low, high = 1, max(natural)
    while low < high:  # largest cap whose capped widths still fit
        mid = (low + high + 1) // 2
        if sum(min(n, mid) for n in natural) <= available:
            low = mid
        else:
            high = mid - 1
    cap = max(low, _MIN_FIT_WIDTH)
    widths = [min(n, cap) for n in natural]
    left = available - sum(widths)
    for i in sorted(range(len(widths)), key=lambda i: natural[i] - widths[i], reverse=True):
        if left <= 0:
            break
        extra = min(natural[i] - widths[i], left)
        widths[i] += extra
        left -= extra
    return widths
