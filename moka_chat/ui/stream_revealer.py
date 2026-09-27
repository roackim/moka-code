"""Smooths the reveal rate of streamed text.

A ``StreamRevealer`` buffers arrived text and releases it in grapheme-cluster
grains spread over at most ``max_window`` seconds, decoupling the rate text
*arrives* from the rate it *appears*. All time is passed in by the caller, so
the controller is deterministic and unit-testable without a clock.

Design:

- Each buffered stretch is anchored to the arrival time of its **oldest**
  character; the deadline is ``oldest + max_window``. A character is therefore
  never held longer than ``max_window`` (default 250 ms), regardless of how many
  newer chunks merge into the buffer.
- Grain = non-whitespace grapheme clusters; intervening whitespace is released
  for free. A small chunk reveals a cluster per frame; a large chunk spreads
  across the window.
- Behind schedule (``now >= deadline``) \u2192 release everything (catch-up).
"""

from __future__ import annotations

import math

from moka_chat.ui.tui.graphemes import advance_nonws, count_nonws

max_window = 0.250
min_cluster_grain = 1


class StreamRevealer:
    def __init__(self, target_fps: int, max_window: float = max_window):
        self.target_fps = target_fps
        self.max_window = max_window
        self._step_interval = (1.0 / target_fps) if target_fps > 0 else 0.0
        self._pending = ""
        self._oldest: float | None = None
        self._last_step: float | None = None

    def ingest(self, text: str, now: float) -> None:
        """Accept newly arrived text.

        The reveal deadline stays anchored to the oldest buffered text, so a
        fast producer cannot push already-buffered text past ``max_window``.
        """
        if not text:
            return
        if not self._pending:
            self._oldest = now
        self._pending += text

    def pending(self) -> int:
        """Non-whitespace grapheme clusters still waiting to be released."""
        return count_nonws(self._pending)

    def active(self) -> bool:
        return bool(self._pending)

    def tick(self, now: float) -> str:
        """Release the next grain; returns ``""`` when nothing is released."""
        if not self._pending:
            self._oldest = None
            return ""

        # Reveal at most once per step interval.
        if self._last_step is not None and now < self._last_step + self._step_interval:
            return ""

        remaining = count_nonws(self._pending)
        self._last_step = now

        # Whitespace-only (or nothing counted) → flush it for free.
        if remaining == 0:
            return self._release(len(self._pending))

        # Behind schedule (or at it): stop being pretty and release everything.
        anchor = self._oldest if self._oldest is not None else now
        deadline = anchor + self.max_window
        if now >= deadline:
            return self._release(len(self._pending))

        steps_left = max(1, round((deadline - now) * self.target_fps))
        grain = max(min_cluster_grain, math.ceil(remaining / steps_left))
        offset = advance_nonws(self._pending, grain)
        return self._release(offset)

    def drain(self) -> str:
        """Release all pending text immediately."""
        if not self._pending:
            return ""
        return self._release(len(self._pending))

    def _release(self, offset: int) -> str:
        released = self._pending[:offset]
        self._pending = self._pending[offset:]
        if not self._pending:
            self._oldest = None
        return released
