"""Ollama native chat transport for :class:`Endpoint`.

Ollama's native ``/api/chat`` API is used instead of the OpenAI-compatible
shim so final usage counters are retained. Free functions take the endpoint.
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, AsyncGenerator, Dict, Optional

import httpx

from moka_chat.harness.endpoint import ModelInfo

if TYPE_CHECKING:
    from moka_chat.harness.endpoint import Endpoint


logger = logging.getLogger(__name__)


def native_base_url(endpoint: "Endpoint") -> str:
    return endpoint.base_url.removesuffix("/v1").rstrip("/")


async def check_connection(endpoint: "Endpoint") -> bool:
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{native_base_url(endpoint)}/api/tags",
                timeout=endpoint.timeout,
            )
            return response.is_success
    except Exception:
        return False


def ollama_messages(messages: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """Normalize OpenAI-style history for Ollama's native chat API.

    OpenAI allows ``content=None`` on tool-call-only assistant turns, but
    Ollama (and proxies in front of it) validate content as a string and
    reject the request with HTTP 422 otherwise. Content parts (a message with
    images) become text plus Ollama's ``images: [base64]`` field. Reasoning
    sent back goes in Ollama's ``thinking`` field.
    """
    normalized = []
    for message in messages:
        msg = dict(message)
        msg.pop("reasoning_details", None)
        reasoning = msg.pop("reasoning", None)
        if reasoning:
            msg["thinking"] = reasoning
        if isinstance(msg.get("content"), list):
            texts, images = [], []
            for part in msg["content"]:
                if part.get("type") == "image_url":
                    images.append(part["image_url"]["url"].split(",", 1)[1])
                else:
                    texts.append(part.get("text", ""))
            msg["content"] = "\n".join(texts)
            if images:
                msg["images"] = images
        if msg.get("content") is None:
            msg["content"] = ""
        if msg.get("tool_calls") is None:
            msg.pop("tool_calls", None)
        normalized.append(msg)
    return normalized


async def create_completion(
    endpoint: "Endpoint",
    messages: list[Dict[str, Any]],
    tools: Optional[list[Dict[str, Any]]],
    stream: bool,
) -> AsyncGenerator[Any, None]:
    payload: Dict[str, Any] = {
        "model": await endpoint.get_model_name(),
        "messages": ollama_messages(messages),
        "stream": stream,
    }
    if tools:
        payload["tools"] = tools

    async with httpx.AsyncClient() as client:
        async with client.stream(
            "POST",
            f"{native_base_url(endpoint)}/api/chat",
            json=payload,
            timeout=None,
        ) as response:
            response.raise_for_status()
            if not stream:
                data = await response.json()
                yield native_response(data)
                return
            async for line in response.aiter_lines():
                if not line:
                    continue
                yield native_response(json.loads(line))


def native_response(data: Dict[str, Any]) -> Any:
    """Adapt one native Ollama response to the OpenAI chunk shape."""
    message = data.get("message") or {}
    content = message.get("content")
    reasoning = message.get("thinking")
    tool_calls = []
    for index, call in enumerate(message.get("tool_calls") or []):
        function = call.get("function") or {}
        arguments = function.get("arguments", {})
        tool_calls.append(SimpleNamespace(
            index=index,
            id=call.get("id"),
            function=SimpleNamespace(
                name=function.get("name"),
                arguments=json.dumps(arguments) if isinstance(arguments, dict) else arguments,
            ),
        ))
    delta = SimpleNamespace(
        content=content,
        reasoning_content=reasoning,
        tool_calls=tool_calls,
    )
    choice = SimpleNamespace(
        delta=delta,
        finish_reason="stop" if data.get("done") else None,
    )
    usage = {
        "prompt_eval_count": data.get("prompt_eval_count"),
        "eval_count": data.get("eval_count"),
    }
    return SimpleNamespace(
        choices=[] if data.get("done") else [choice],
        usage=usage,
    )


async def list_models(endpoint: "Endpoint") -> list[ModelInfo]:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{native_base_url(endpoint)}/api/tags",
            timeout=endpoint.timeout,
        )
        response.raise_for_status()
        data = response.json()
    return [
        ModelInfo(
            id=model.get("name", model.get("model", "")),
            context_window=model.get("context_length"),
            metadata=model,
        )
        for model in data.get("models", [])
        if model.get("name", model.get("model"))
    ]


async def context_window(endpoint: "Endpoint", model_name: str) -> int:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{native_base_url(endpoint)}/api/show",
            json={"name": model_name},
            timeout=endpoint.timeout,
        )
        response.raise_for_status()
        data = response.json()
    for key, value in data.get("model_info", {}).items():
        if key.lower().endswith(("context_length", "context", "n_ctx")) and isinstance(value, int):
            return value
    parameters = data.get("parameters", "")
    for line in str(parameters).splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in {"num_ctx", "n_ctx"}:
            return int(parts[1])
    raise RuntimeError(f"Could not determine context window for Ollama model: {model_name}")
