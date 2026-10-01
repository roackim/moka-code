"""``type = "ollama"``: native ``/api/chat``, ``/api/tags``, ``/api/show``.

Ollama's native ``/api/chat`` API is used instead of the OpenAI-compatible
shim so final usage counters are retained.
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator, Dict, Optional

import httpx

from moka_code.harness.endpoint import (
    Chunk, Endpoint, ModelInfo, ToolCallPiece, image_input_from_metadata,
)
from moka_code.harness.usage import TokenUsage


logger = logging.getLogger(__name__)


def ollama_messages(messages: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """Normalize OpenAI-style history for Ollama's native chat API.

    OpenAI allows ``content=None`` on tool-call-only assistant turns, but
    Ollama (and proxies in front of it) validate content as a string and
    reject the request with HTTP 422 otherwise. Content parts (a message with
    images) become text plus Ollama's ``images: [base64]`` field.
    """
    normalized = []
    for message in messages:
        msg = dict(message)
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


def native_response(data: Dict[str, Any]) -> Chunk:
    """One native Ollama response line as a :class:`Chunk`.

    The final line (``done``) carries only the usage counters.
    """
    if data.get("done"):
        usage = TokenUsage(prompt_tokens=data.get("prompt_eval_count"),
                           completion_tokens=data.get("eval_count"), raw=data)
        return Chunk(usage=None if usage.is_empty else usage)
    message = data.get("message") or {}
    # Ollama streams ``thinking``; llama.cpp-backed proxies may use ``reasoning``.
    reasoning = message.get("thinking") or message.get("reasoning")
    tool_calls = []
    for index, call in enumerate(message.get("tool_calls") or []):
        function = call.get("function") or {}
        arguments = function.get("arguments", {})
        tool_calls.append(ToolCallPiece(
            index=index,
            id=call.get("id"),
            name=function.get("name"),
            arguments=json.dumps(arguments) if isinstance(arguments, dict) else (arguments or ""),
        ))
    return Chunk(text=message.get("content") or "", reasoning=reasoning or "",
                 tool_calls=tool_calls)


class Ollama(Endpoint):
    """An Ollama server, through its native API (keeps final usage counters)."""

    type = "ollama"

    def _native_base_url(self) -> str:
        return self.base_url.removesuffix("/v1").rstrip("/")

    async def list_models(self) -> list[ModelInfo]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self._native_base_url()}/api/tags",
                timeout=self.timeout,
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

    async def discover_models(self) -> list[ModelInfo]:
        """Discover Ollama models, enriching each with its context window."""
        models = await self.list_models()
        enriched = []
        for model in models:
            try:
                ctx = await self.query_context_window(model.id)
                enriched.append(ModelInfo(
                    id=model.id,
                    context_window=ctx,
                    owned_by=model.owned_by,
                    metadata=model.metadata,
                ))
            except Exception as e:
                logger.debug("Could not enrich context for %s: %s", model.id, e)
                enriched.append(model)
        return enriched

    async def query_model_name(self) -> str:
        if self._selected_model:
            return self._selected_model
        models = await self.list_models()
        if models:
            return models[0].id
        raise RuntimeError(f"No models available on endpoint '{self.name}'")

    async def query_context_window(self, model_name: str) -> int:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._native_base_url()}/api/show",
                json={"name": model_name},
                timeout=self.timeout,
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

    async def query_image_input(self, model_name: str) -> bool | None:
        """Whether *model_name* reads images, from ``/api/show``."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._native_base_url()}/api/show",
                json={"name": model_name}, timeout=self.timeout,
            )
            response.raise_for_status()
            return image_input_from_metadata(response.json())

    async def check_connection(self) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self._native_base_url()}/api/tags",
                    timeout=self.timeout,
                )
                return response.is_success
        except Exception:
            return False

    async def _stream(
        self,
        messages: list[Dict[str, Any]],
        tools: Optional[list[Dict[str, Any]]],
    ) -> AsyncGenerator[Chunk, None]:
        payload: Dict[str, Any] = {
            "model": await self.get_model_name(),
            "messages": ollama_messages(messages),
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        payload.update(self.effort_payload())

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self._native_base_url()}/api/chat",
                json=payload,
                timeout=None,
            ) as response:
                await self._raise_for_status(response)
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    yield native_response(json.loads(line))

    def effort_payload(self) -> dict[str, Any]:
        if not self.effort:
            return {}
        return {"think": False if self.effort == "none" else self.effort}
