"""What the notice band shows besides the live problems (``status_presenter.notices``):

- **posted messages**: an error or warning stays until its ``✕``; a success
  goes after ``SUCCESS_SECONDS``. The same text posted again moves to the top
  instead of showing twice.
- **jobs**: work moka runs in the background (an image build), with a spinner,
  the time it has run and its latest output line. Not dismissible: it ends.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional

SUCCESS_SECONDS = 6.0
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


@dataclass(eq=False)
class Posted:
    level: str              # "error" | "warning" | "success"
    text: str               # the whole message (its first line is the band line)
    until: Optional[float]  # when it goes by itself (successes), else None

    @property
    def line(self) -> str:
        lines = self.text.strip().splitlines()
        more = " · more" if len(lines) > 1 else ""      # a click opens it whole
        return (lines[0] if lines else "") + more


@dataclass(eq=False)
class Job:
    label: str              # "building godot"
    started: float = field(default_factory=time.monotonic)
    last: str = ""          # its latest output line

    def line(self, now: float) -> str:
        spin = _SPINNER[int(now * 10) % len(_SPINNER)]
        text = f"{spin} {self.label} · {elapsed(now - self.started)}"
        return f"{text} · {self.last}" if self.last else text


def elapsed(seconds: float) -> str:
    """``42s``, ``2m03s``."""
    seconds = int(seconds)
    return f"{seconds // 60}m{seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


class NoticeFeed:
    def __init__(self) -> None:
        self.posted: List[Posted] = []      # newest first
        self.jobs: List[Job] = []

    def post(self, level: str, text: str, now: Optional[float] = None) -> Posted:
        now = time.monotonic() if now is None else now
        text = str(text).strip()
        self.posted = [p for p in self.posted if p.text != text]
        entry = Posted(level, text, now + SUCCESS_SECONDS if level == "success" else None)
        self.posted.insert(0, entry)
        return entry

    def dismiss(self, entry: Posted) -> None:
        if entry in self.posted:
            self.posted.remove(entry)

    def expire(self, now: Optional[float] = None) -> bool:
        """Drop the successes whose time is up; True when one went."""
        now = time.monotonic() if now is None else now
        kept = [p for p in self.posted if p.until is None or p.until > now]
        changed = len(kept) != len(self.posted)
        self.posted = kept
        return changed

    def running(self, label: str) -> Optional[Job]:
        return next((job for job in self.jobs if job.label == label), None)

    def start(self, label: str) -> Job:
        job = Job(label)
        self.jobs.append(job)
        return job

    def finish(self, job: Job) -> None:
        if job in self.jobs:
            self.jobs.remove(job)

    def live(self) -> bool:
        """Whether the band changes by itself (a job's clock, a success's end)."""
        return bool(self.jobs) or any(p.until is not None for p in self.posted)
