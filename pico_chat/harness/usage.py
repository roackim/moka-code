"""Provider-neutral token usage normalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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


def _value(data: Any, *names: str) -> Any:
    if data is None:
        return None
    if isinstance(data, dict):
        for name in names:
            if name in data:
                return data[name]
        return None
    for name in names:
        value = getattr(data, name, None)
        if value is not None:
            return value
    return None


def normalize_usage(value: Any) -> TokenUsage | None:
    """Normalize OpenAI-compatible, Ollama, or provider-specific usage data."""
    if value is None:
        return None

    prompt = _value(value, "prompt_tokens", "prompt_eval_count")
    completion = _value(value, "completion_tokens", "eval_count")
    total = _value(value, "total_tokens")

    details = _value(value, "completion_tokens_details", "prompt_tokens_details")
    reasoning = _value(details, "reasoning_tokens", "reasoning_token_count")
    cached = _value(details, "cached_tokens", "cache_read_input_tokens")
    cost = _value(value, "cost")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)):
        cost = None

    usage = TokenUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        reasoning_tokens=reasoning,
        cached_prompt_tokens=cached,
        cost=cost,
        raw=value,
    )
    return None if usage.is_empty else usage


def usage_from_response(response: Any) -> TokenUsage | None:
    """Extract usage from an SDK response or a raw response dictionary."""
    usage = _value(response, "usage")
    if usage is not None:
        normalized = normalize_usage(usage)
        if normalized is not None:
            return normalized

    # Ollama's native response puts these counters at the top level.
    normalized = normalize_usage(response)
    return normalized
