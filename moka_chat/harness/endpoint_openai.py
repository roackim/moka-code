"""OpenAI-compatible chat transport for :class:`Endpoint`.

Implemented directly on httpx (no SDK). Raw ``chat.completions`` payloads are
adapted to the small SDK-shaped objects the harness consumes. Free functions
take the endpoint so there is no subclass hierarchy.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, AsyncGenerator, Dict, Optional

import httpx

if TYPE_CHECKING:
    from moka_chat.harness.endpoint import Endpoint


logger = logging.getLogger(__name__)


def _adapt_tool_calls(raw_calls: Any) -> list:
    """Adapt raw tool-call dicts to the ``{index,id,function:{name,arguments}}`` shape."""
    if not raw_calls:
        return []
    adapted = []
    for index, call in enumerate(raw_calls):
        function = call.get("function") or {}
        arguments = function.get("arguments")
        # Some providers send arguments as a JSON object already.
        if isinstance(arguments, dict):
            arguments = json.dumps(arguments)
        adapted.append(SimpleNamespace(
            index=index,
            id=call.get("id"),
            function=SimpleNamespace(
                name=function.get("name"),
                arguments=arguments or "",
            ),
        ))
    return adapted


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


def _adapt_stream_chunk(data: Dict[str, Any]) -> Any:
    """Adapt one streaming ``chat.completions`` SSE object to SDK chunk shape."""
    choice = None
    raw_choices = data.get("choices") or []
    if raw_choices:
        rc = raw_choices[0]
        delta = rc.get("delta") or {}
        choice = SimpleNamespace(
            index=rc.get("index", 0),
            delta=SimpleNamespace(
                content=delta.get("content"),
                reasoning_content=_extract_reasoning(delta),
                refusal=delta.get("refusal"),
                tool_calls=_adapt_tool_calls(delta.get("tool_calls")),
            ),
            finish_reason=rc.get("finish_reason"),
        )
    return SimpleNamespace(
        id=data.get("id"),
        choices=[] if choice is None else [choice],
        usage=data.get("usage"),
    )


def _adapt_message(message: Dict[str, Any]) -> SimpleNamespace:
    """Adapt a non-streaming ``choices[0].message`` to SDK message shape."""
    return SimpleNamespace(
        role=message.get("role"),
        content=message.get("content"),
        reasoning_content=_extract_reasoning(message),
        refusal=message.get("refusal"),
        tool_calls=_adapt_tool_calls(message.get("tool_calls")),
    )


def _adapt_chat_response(data: Dict[str, Any]) -> Any:
    """Adapt a non-streaming ``chat.completions`` response to SDK shape."""
    choices = []
    for rc in data.get("choices") or []:
        choices.append(SimpleNamespace(
            index=rc.get("index", 0),
            message=_adapt_message(rc.get("message") or {}),
            finish_reason=rc.get("finish_reason"),
        ))
    return SimpleNamespace(
        id=data.get("id"),
        choices=choices,
        usage=data.get("usage"),
    )


async def iter_sse_chunks(response: httpx.Response):
    """Parse SSE ``data:`` lines from a streaming response into chunks."""
    async for line in response.aiter_lines():
        if not line or not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            break
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            logger.debug("Skipping non-JSON SSE line: %r", data[:80])
            continue
        yield _adapt_stream_chunk(obj)


async def create_completion(
    endpoint: "Endpoint",
    messages: list[Dict[str, Any]],
    tools: Optional[list[Dict[str, Any]]],
    stream: bool,
) -> AsyncGenerator[Any, None]:
    """Stream (or fetch) a completion from an OpenAI-compatible endpoint."""
    _t0 = time.perf_counter()
    model_name = await endpoint.get_model_name()
    logger.info(
        "[llm] model_name resolved in %.0fms (cached=%s)",
        (time.perf_counter() - _t0) * 1000,
        bool(endpoint._cached_model_name),
    )

    max_retries = endpoint.retry_attempts
    retry_delay = endpoint.retry_delay

    for attempt in range(max_retries):
        payload: Dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
        if stream:
            payload["stream_options"] = {"include_usage": True}
        if endpoint.type == "openrouter":
            provider_spec = endpoint._provider_spec(model_name)
            if provider_spec:
                payload["provider"] = provider_spec

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
        try:
            if stream:
                async with endpoint.client.stream("POST", "/chat/completions", json=payload) as response:
                    if response.status_code == 503:
                        error_body = await response.aread()
                        error_message = error_body.decode(errors="replace")
                        if attempt < max_retries - 1:
                            logger.warning(
                                "Model loading (503), retrying in %.1fs (attempt %d/%d)",
                                retry_delay, attempt + 1, max_retries,
                            )
                            await asyncio.sleep(retry_delay)
                            retry_delay *= 1.5
                            continue
                        raise httpx.HTTPStatusError(
                            f"Model loading timeout after {max_retries} attempts: {error_message}",
                            request=response.request, response=response,
                        )
                    response.raise_for_status()
                    headers_at = time.perf_counter()
                    logger.info(
                        "[llm] POST /chat/completions headers received in %.0fms",
                        (headers_at - _t_req) * 1000,
                    )
                    chunk_count = 0
                    first_chunk_at = None
                    async for chunk in iter_sse_chunks(response):
                        if first_chunk_at is None:
                            first_chunk_at = time.perf_counter()
                            logger.info(
                                "[llm] first token after %.0fms (headers->first)",
                                (first_chunk_at - headers_at) * 1000,
                            )
                        chunk_count += 1
                        if chunk_count <= 2 or chunk_count % 50 == 0:
                            logger.debug("LLM chunk %d: choices=%d", chunk_count, len(chunk.choices))
                        yield chunk
                    logger.debug("LLM stream complete: %d total chunks", chunk_count)
                return
            else:
                response = await asyncio.wait_for(
                    endpoint.client.post("/chat/completions", json=payload),
                    timeout=endpoint.timeout,
                )
                if response.status_code == 503:
                    if attempt < max_retries - 1:
                        logger.warning("Model loading (503), retrying in %.1fs", retry_delay)
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 1.5
                        continue
                    response.raise_for_status()
                response.raise_for_status()
                data = response.json()
                yield _adapt_chat_response(data)
                return
        except (httpx.HTTPStatusError, httpx.RequestError, asyncio.TimeoutError, httpx.TimeoutException) as e:
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code != 503:
                raise
            if attempt < max_retries - 1:
                logger.warning(
                    "Request failed (%s) retrying in %.1fs (attempt %d/%d)",
                    type(e).__name__, retry_delay, attempt + 1, max_retries,
                )
                await asyncio.sleep(retry_delay)
                retry_delay *= 1.5
                continue
            raise
