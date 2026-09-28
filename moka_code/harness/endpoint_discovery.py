"""Model listing and context-window discovery for :class:`Endpoint`.

Server-family differences are dispatched here rather than in an ABC. Free
functions take the endpoint.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx

from moka_code.harness import endpoint_ollama
from moka_code.harness.endpoint import ModelInfo

if TYPE_CHECKING:
    from moka_code.harness.endpoint import Endpoint


logger = logging.getLogger(__name__)


async def list_models(endpoint: "Endpoint") -> list[ModelInfo]:
    """List models exposed by this endpoint."""
    if endpoint.type == "ollama":
        return await endpoint_ollama.list_models(endpoint)
    response = await asyncio.wait_for(
        endpoint.client.get("/models"), timeout=endpoint.timeout
    )
    response.raise_for_status()
    data = response.json()
    result = []
    for model in data.get("data", []) or []:
        result.append(ModelInfo(
            id=model.get("id"),
            context_window=model.get("context_length"),
            owned_by=model.get("owned_by"),
        ))
    return result


async def discover_models(endpoint: "Endpoint") -> list[ModelInfo]:
    """Discover the models surfaced by this endpoint.

    OpenRouter exposes thousands of models, so only explicitly-enabled ids
    are surfaced. Ollama models are enriched with their context window.
    """
    if endpoint.type == "openrouter":
        return await discover_openrouter_models(endpoint)
    if endpoint.type == "ollama":
        return await discover_ollama_models(endpoint)
    return await list_models(endpoint)


async def query_model_name(endpoint: "Endpoint") -> str:
    if endpoint.type in ("openrouter", "openai"):
        if endpoint._selected_model:
            return endpoint._selected_model
        raise RuntimeError(f"{endpoint.type} requires a model to be configured")
    if endpoint.type == "ollama" and endpoint._selected_model:
        return endpoint._selected_model
    models = await list_models(endpoint)
    if models:
        return models[0].id
    raise RuntimeError(f"No models available on endpoint '{endpoint.name}'")


async def query_context_window(endpoint: "Endpoint", model_name: str) -> int:
    if endpoint.type == "ollama":
        return await endpoint_ollama.context_window(endpoint, model_name)
    if endpoint.type == "openrouter":
        return await openrouter_context_window(endpoint, model_name)
    if endpoint.type == "openai":
        return openai_context_window(model_name)
    return await llamacpp_context_window(endpoint, model_name)


async def discover_ollama_models(endpoint: "Endpoint") -> list[ModelInfo]:
    """Discover Ollama models, enriching each with its context window."""
    models = await endpoint_ollama.list_models(endpoint)
    enriched = []
    for model in models:
        try:
            ctx = await endpoint_ollama.context_window(endpoint, model.id)
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


async def discover_openrouter_models(endpoint: "Endpoint") -> list[ModelInfo]:
    """Return only the enabled models for this OpenRouter endpoint."""
    enabled = endpoint._enabled_ids()
    if not enabled:
        return []
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://openrouter.ai/api/v1/models",
            timeout=endpoint.timeout,
        )
        if response.status_code != 200:
            return [ModelInfo(id=e) for e in enabled]
    catalog = response.json().get("data", [])
    by_id = {m.get("id"): m for m in catalog}

    def _match(eid: str) -> dict:
        if eid in by_id:
            return by_id[eid]
        # Accept a bare id (no provider namespace) by suffix match, so
        # ``deepseek-v4-flash`` resolves ``deepseek/deepseek-v4-flash``.
        # Prefer non-alias entries (ids not prefixed with ``~``).
        suffix = "/" + eid
        fallback = None
        for cid, info in by_id.items():
            if cid.endswith(suffix):
                if not cid.startswith("~"):
                    return info
                fallback = fallback or info
        return fallback or {}

    result = []
    for eid in enabled:
        info = _match(eid)
        result.append(ModelInfo(
            id=info.get("id") or eid,
            context_window=info.get("context_length"),
            owned_by=info.get("owned_by"),
            metadata=info,
        ))
    return result


async def openrouter_context_window(endpoint: "Endpoint", model_name: str) -> int:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://openrouter.ai/api/v1/models",
            timeout=endpoint.timeout,
        )
        if response.status_code == 200:
            catalog = response.json().get("data", [])
            suffix = "/" + model_name
            fallback = None
            for model in catalog:
                mid = model.get("id", "")
                if mid != model_name and not mid.endswith(suffix):
                    continue
                ctx = model.get("context_length")
                if not ctx:
                    continue
                if mid == model_name or not mid.startswith("~"):
                    return ctx
                fallback = fallback or ctx
            if fallback:
                return fallback
    raise RuntimeError("Could not determine context window from OpenRouter")


def openai_context_window(model_name: str) -> int:
    context_windows = {
        "gpt-4o": 128000,
        "gpt-4o-mini": 128000,
        "gpt-4-turbo": 128000,
        "gpt-4": 8192,
        "gpt-3.5-turbo": 16385,
        "o1": 200000,
        "o1-mini": 128000,
    }
    for known_model, ctx in context_windows.items():
        if known_model in model_name:
            return ctx
    raise RuntimeError(f"Unknown context window for model: {model_name}")


async def llamacpp_context_window(endpoint: "Endpoint", model_name: str) -> int:
    """Query context window from a llama.cpp server via /props."""
    try:
        async with httpx.AsyncClient() as client:
            url = endpoint.base_url.replace("/v1", "/props")
            response = await client.get(url, timeout=endpoint.timeout)
            if response.status_code == 200:
                ctx = response.json().get("default_generation_settings", {}).get("n_ctx")
                if ctx:
                    return ctx
    except Exception as e:
        logger.debug("Failed to query /props endpoint: %s", e)

    models = await list_models(endpoint)
    for model in models:
        if model.id == model_name and model.context_window:
            return model.context_window
    raise RuntimeError("Could not determine context window from server")


# Reasoning effort words, weakest first (OpenAI / OpenRouter vocabulary). A
# model id suffix ``:<word>`` marks an effort variant (``/effort`` switches
# between sibling ids).
EFFORT_WORDS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def efforts_from_metadata(metadata: dict) -> list[str]:
    """Effort levels a model takes as a request parameter, from catalog
    metadata: OpenRouter ``supported_parameters``; Ollama's ``thinking``
    capability (only gpt-oss takes levels, others just on/off)."""
    params = metadata.get("supported_parameters") or []
    if "reasoning_effort" in params or "reasoning" in params:
        return ["none", "low", "medium", "high"]
    if "thinking" in (metadata.get("capabilities") or []):
        name = str(metadata.get("model") or metadata.get("name") or "")
        return ["none", "low", "medium", "high"] if "gpt-oss" in name else ["none"]
    return []


def effort_levels(model_entry: dict | None, server_efforts: list | None,
                  metadata: dict) -> list[str]:
    """Effort levels a model takes as a request parameter: its model table's
    ``efforts``, else the server's, else detected from catalog metadata."""
    if model_entry and "efforts" in model_entry:
        return list(model_entry["efforts"])
    if server_efforts:
        return list(server_efforts)
    return efforts_from_metadata(metadata or {})


def image_input_from_metadata(metadata: dict) -> bool | None:
    """Image support recorded in catalog metadata; ``None`` when absent.

    OpenRouter lists ``architecture.input_modalities``; Ollama's ``/api/show``
    lists ``capabilities`` (``"vision"``).
    """
    modalities = (metadata.get("architecture") or {}).get("input_modalities")
    if isinstance(modalities, list):
        return "image" in modalities
    capabilities = metadata.get("capabilities")
    if isinstance(capabilities, list):
        return "vision" in capabilities
    return None


async def query_image_input(endpoint: "Endpoint", model_name: str) -> bool | None:
    """Ask the server whether *model_name* reads images; ``None`` when unknown."""
    async with httpx.AsyncClient() as client:
        if endpoint.type == "ollama":
            response = await client.post(
                f"{endpoint_ollama.native_base_url(endpoint)}/api/show",
                json={"name": model_name}, timeout=endpoint.timeout,
            )
            response.raise_for_status()
            return image_input_from_metadata(response.json())
        if endpoint.type == "openrouter":
            response = await client.get(
                f"https://openrouter.ai/api/v1/models/{model_name}/endpoints",
                timeout=endpoint.timeout,
            )
            response.raise_for_status()
            return image_input_from_metadata(response.json().get("data") or {})
        if endpoint.type == "llamacpp":
            response = await client.get(
                endpoint.base_url.replace("/v1", "/props"), timeout=endpoint.timeout,
            )
            response.raise_for_status()
            vision = (response.json().get("modalities") or {}).get("vision")
            return vision if isinstance(vision, bool) else None
    return None
