"""Split a finished assistant answer into prose / code block / table segments.

Each segment becomes its own transcript message (grouped, no gap between
them), so the ordinary selection and copy actions work per block: ``→`` enters
an answer, ``↑``/``↓`` move between its segments, ``c`` copies one (a code
block without its fences). Splitting happens once the answer is complete, never
while it streams, so a fence or table is never cut mid-way.

Only top-level blocks split: a fence must start at column 0 (a code block
indented inside a list item stays in its prose), and a table needs a header row
followed by a separator row.
"""

from __future__ import annotations

from dataclasses import dataclass

from moka_chat.ui.tui.components.markdown import BlockParser


@dataclass(frozen=True)
class Segment:
    kind: str   # "text" | "code" | "table"
    text: str   # markdown rendered for the segment
    copy: str   # what ``c`` copies (code: the code only)


_FENCES = ("```", "~~~")


def split_answer(text: str) -> list[Segment]:
    """Segments of ``text``; blank lines between segments are dropped."""
    parser = BlockParser()
    lines = text.split("\n")
    segments: list[Segment] = []
    prose: list[str] = []

    def flush_prose() -> None:
        body = "\n".join(prose).strip("\n")
        if body.strip():
            segments.append(Segment("text", body, body))
        prose.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(_FENCES):
            fence = line[:3]
            end = i + 1
            while end < len(lines) and not lines[end].strip().startswith(fence):
                end += 1
            flush_prose()
            segments.append(Segment(
                "code",
                "\n".join(lines[i:end + 1]),
                "\n".join(lines[i + 1:end]),
            ))
            i = end + 1
            continue
        header = parser._parse_table_line(line)
        if header is not None and not header.is_separator and i + 1 < len(lines):
            separator = parser._parse_table_line(lines[i + 1])
            if separator is not None and separator.is_separator:
                end = i + 2
                while end < len(lines) and parser._parse_table_line(lines[end]) is not None:
                    end += 1
                flush_prose()
                table = "\n".join(lines[i:end])
                segments.append(Segment("table", table, table))
                i = end
                continue
        prose.append(line)
        i += 1
    flush_prose()
    return segments


__all__ = ["Segment", "split_answer"]
