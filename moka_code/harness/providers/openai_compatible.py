"""``type = "openai-compatible"``: ``/chat/completions``, ``/models``, SSE.

OpenAI itself, vLLM, LM Studio, a proxy; the base of llama.cpp and OpenRouter.
Implemented directly on httpx (no SDK). Streamed ``chat.completions`` objects
become :class:`~moka_code.harness.endpoint.Chunk` objects.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, Optional

import httpx

from moka_code.harness.endpoint import Chunk, Endpoint, ModelInfo, ToolCallPiece
from moka_code.harness.usage import TokenUsage


logger = logging.getLogger(__name__)


# Reasoning effort words, weakest first (OpenAI / OpenRouter vocabulary).
EFFORT_WORDS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def stated_efforts(entry: Dict[str, Any]) -> list[str]:
    """The effort levels a ``/models`` entry states in
    ``reasoning.supported_efforts`` (OpenRouter's shape, which any
    OpenAI-compatible server may carry), weakest first; ``[]`` otherwise.
    Never guessed from ``supported_parameters`` (``providers.md`` §9.5)."""
    supported = (entry.get("reasoning") or {}).get("supported_efforts")
    if not isinstance(supported, list) or not all(isinstance(e, str) for e in supported):
        return []
    rank = {w: i for i, w in enumerate(EFFORT_WORDS)}
    return sorted(supported, key=lambda e: rank.get(e, len(rank)))


def stated_images(entry: Dict[str, Any]) -> Optional[bool]:
    """Image input as a ``/models`` entry states it in
    ``architecture.input_modalities``; ``None`` when absent."""
    modalities = (entry.get("architecture") or {}).get("input_modalities")
    if isinstance(modalities, list):
        return "image" in modalities
    return None


def _tool_call_pieces(raw_calls: Any) -> list[ToolCallPiece]:
    """Raw tool-call dicts as pieces (``arguments`` always a JSON text)."""
    pieces = []
    for index, call in enumerate(raw_calls or []):
        function = call.get("function") or {}
        arguments = function.get("arguments")
        # Some providers send arguments as a JSON object already.
        if isinstance(arguments, dict):
            arguments = json.dumps(arguments)
        pieces.append(ToolCallPiece(
            index=index, id=call.get("id"), name=function.get("name"),
            arguments=arguments or "",
        ))
    return pieces


def _extract_reasoning(data: Dict[str, Any]) -> Optional[str]:
    """Read reasoning from whichever field a provider uses.

    DeepSeek/vLLM/llama.cpp stream ``reasoning_content``; OpenRouter and others
    stream ``reasoning`` (and may also expose structured ``reasoning_details``).
    """
    reasoning = data.get("reasoning_content")
    if reasoning is None:
        reasoning = data.get("reasoning")
    if reasoning is None:
        details = data.get("reasoning_details")
        if isinstance(details, list):
            joined = "".join(
                item.get("text") or "" for item in details if isinstance(item, dict)
            )
            reasoning = joined or None
    return reasoning


def _field(data: Any, *names: str) -> Any:
    if not isinstance(data, dict):
        return None
    for name in names:
        if name in data:
            return data[name]
    return None


def parse_usage(value: Any) -> Optional[TokenUsage]:
    """An OpenAI-style ``usage`` object as :class:`TokenUsage` (``None`` if empty)."""
    if not isinstance(value, dict):
        return None
    # Two separate blocks: OpenAI-style usage carries both, so looking them
    # up as alternatives always found the completion one and lost the cache.
    reasoning = _field(_field(value, "completion_tokens_details"), "reasoning_tokens")
    cached = _field(_field(value, "prompt_tokens_details"), "cached_tokens")
    cost = _field(value, "cost")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)):
        cost = None
    usage = TokenUsage(
        prompt_tokens=_field(value, "prompt_tokens"),
        completion_tokens=_field(value, "completion_tokens"),
        total_tokens=_field(value, "total_tokens"),
        reasoning_tokens=reasoning,
        cached_prompt_tokens=cached,
        cost=cost,
        raw=value,
    )
    return None if usage.is_empty else usage


def _delta(data: Dict[str, Any]) -> Dict[str, Any]:
    choices = data.get("choices") or []
    return (choices[0].get("delta") or {}) if choices else {}


async def iter_sse_objects(response: httpx.Response):
    """Parse SSE ``data:`` lines from a streaming response into JSON objects."""
    async for line in response.aiter_lines():
        if not line or not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            break
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            logger.debug("Skipping non-JSON SSE line: %r", data[:80])


class OpenAICompatible(Endpoint):
    """A server speaking the OpenAI chat-completions API."""

    type = "openai-compatible"
    template = """\
## OpenAI-compatible (OpenAI, vLLM, LM Studio, a proxy) ------------------
# [servers.openai]
# type = "openai-compatible"
# base_url = "https://api.openai.com/v1"
# api_key_env = "OPENAI_API_KEY"
"""

    async def list_models(self) -> list[ModelInfo]:
        """``/models``, with the facts each entry states."""
        response = await asyncio.wait_for(
            self.client.get("/models"), timeout=self.timeout
        )
        response.raise_for_status()
        return [
            ModelInfo(
                id=model.get("id"),
                context_window=model.get("context_length"),
                images=stated_images(model),
                efforts=stated_efforts(model),
                owned_by=model.get("owned_by"),
                raw=model,
            )
            for model in response.json().get("data", []) or []
        ]

    def effort_payload(self) -> dict[str, Any]:
        if not self.effort:
            return {}
        return {"reasoning_effort": self.effort}

    def _extra_payload(self, model_name: str) -> dict[str, Any]:
        """Provider-specific request fields beyond the standard payload."""
        return {}

    def _usage(self, data: Dict[str, Any]) -> Optional[TokenUsage]:
        """The usage reported in one streamed object, if any."""
        return parse_usage(data.get("usage"))

    def _chunk(self, data: Dict[str, Any]) -> Chunk:
        """One streamed ``chat.completions`` object as a :class:`Chunk`."""
        choices = data.get("choices") or []
        choice = choices[0] if choices else {}
        delta = choice.get("delta") or {}
        return Chunk(
            text=delta.get("content") or "",
            reasoning=_extract_reasoning(delta) or "",
            tool_calls=_tool_call_pieces(delta.get("tool_calls")),
            usage=self._usage(data),
            finish=choice.get("finish_reason"),
        )

    async def _stream(
        self,
        messages: list[Dict[str, Any]],
        tools: Optional[list[Dict[str, Any]]],
    ) -> AsyncGenerator[Chunk, None]:
        async for data in self._sse_objects(messages, tools):
            yield self._chunk(data)

    async def _sse_objects(
        self,
        messages: list[Dict[str, Any]],
        tools: Optional[list[Dict[str, Any]]],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """POST ``/chat/completions`` (streamed) and yield each SSE object."""
        model_name = self._selected_model
        if not model_name:
            # Never a guessed id: the selection is the user's (``/model``).
            raise RuntimeError(f"No model selected on {self.name} (/model)")
        payload: Dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        payload["stream_options"] = {"include_usage": True}
        payload.update(self._extra_payload(model_name))
        payload.update(self.effort_payload())

        if logger.isEnabledFor(logging.DEBUG):
            msg_summary = []
            for msg in messages:
                role = msg.get("role", "?")
                content_len = len(str(msg.get("content", "")))
                tc_count = len(msg.get("tool_calls", []))
                msg_summary.append(f"{role}:{content_len}chars:{tc_count}tools")
            logger.debug(
                "API request: model=%s, messages=[%s], tools=%s",
                model_name, ", ".join(msg_summary), "yes" if tools else "no",
            )

        _t_req = time.perf_counter()
        async with self.client.stream("POST", "/chat/completions", json=payload) as response:
            await self._raise_for_status(response)
            headers_at = time.perf_counter()
            logger.info(
                "[llm] POST /chat/completions headers received in %.0fms",
                (headers_at - _t_req) * 1000,
            )
            count = 0
            async for data in iter_sse_objects(response):
                if count == 0:
                    logger.info(
                        "[llm] first token after %.0fms (headers->first)",
                        (time.perf_counter() - headers_at) * 1000,
                    )
                count += 1
                yield data
            logger.debug("LLM stream complete: %d total chunks", count)
