"""Provider-neutral token usage normalization."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from moka_code.harness import events


@dataclass(frozen=True)
class TokenUsage:
    """Authoritative token counts reported by an LLM provider."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    # Price of the request in USD, when the provider reports it (OpenRouter).
    cost: float | None = None
    raw: Any = None

    @property
    def is_empty(self) -> bool:
        return all(value is None for value in (
            self.prompt_tokens,
            self.completion_tokens,
            self.total_tokens,
            self.reasoning_tokens,
            self.cached_prompt_tokens,
        ))


@dataclass
class MetricsState:
    """Tracks generation metrics for periodic emission."""
    generation_start_time: Optional[float] = None
    total_tokens: int = 0
    last_update: float = 0.0
    ttft_ms: Optional[float] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_usage_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None

    def ensure_started(self):
        """Mark the generation start time on first content."""
        if self.generation_start_time is None:
            self.generation_start_time = time.perf_counter()

    def set_usage(self, usage):
        """Replace estimates with authoritative provider usage when available."""
        self.prompt_tokens = usage.prompt_tokens
        self.completion_tokens = usage.completion_tokens
        self.total_usage_tokens = usage.total_tokens
        self.reasoning_tokens = usage.reasoning_tokens
        if usage.completion_tokens is not None:
            self.total_tokens = usage.completion_tokens

    def maybe_metrics(self, interval: float) -> Optional[events.Usage]:
        """Return a Usage event if enough time has elapsed, else None."""
        if self.generation_start_time is None:
            return None
        current = time.perf_counter()
        if current - self.last_update >= interval:
            duration = current - self.generation_start_time
            tps = self.total_tokens / duration if duration > 0 else 0
            self.last_update = current
            return events.Usage(
                tokens=self.total_tokens,
                tokens_per_second=tps,
                ttft_ms=self.ttft_ms,
                prompt_tokens=self.prompt_tokens,
                completion_tokens=self.completion_tokens,
                total_tokens=self.total_usage_tokens,
                reasoning_tokens=self.reasoning_tokens,
                estimated=self.completion_tokens is None,
            )
        return None

    def final_metrics(self) -> Optional[events.Usage]:
        """Return the final Usage event with duration_ms, or None."""
        if self.generation_start_time is None:
            return None
        duration = time.perf_counter() - self.generation_start_time
        tps = self.total_tokens / duration if duration > 0 else 0
        return events.Usage(
            tokens=self.total_tokens,
            tokens_per_second=tps,
            ttft_ms=self.ttft_ms,
            duration_ms=duration * 1000,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            total_tokens=self.total_usage_tokens,
            reasoning_tokens=self.reasoning_tokens,
            estimated=self.completion_tokens is None,
        )
