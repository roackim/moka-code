"""``type = "llamacpp"``: OpenAI-compatible, plus ``/props`` and a default URL."""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Dict, Optional

import httpx

from moka_code.harness.providers.openai_compatible import OpenAICompatible
from moka_code.harness.usage import TokenUsage


logger = logging.getLogger(__name__)


class LlamaCpp(OpenAICompatible):
    """A llama.cpp server (``llama-server``)."""

    type = "llamacpp"
    default_url = "http://localhost:8080/v1"
    serves_one_model = True

    def _props_url(self) -> str:
        return self.base_url.replace("/v1", "/props")

    def _usage(self, data: Dict[str, Any]) -> Optional[TokenUsage]:
        usage = super()._usage(data)
        if usage is not None and usage.cached_prompt_tokens is None:
            # llama.cpp reports KV-cache reuse in ``timings``, next to usage.
            cache_n = (data.get("timings") or {}).get("cache_n")
            if isinstance(cache_n, int):
                usage = replace(usage, cached_prompt_tokens=cache_n)
        return usage

    async def query_model_name(self) -> str:
        models = await self.list_models()
        if models:
            return models[0].id
        raise RuntimeError(f"No models available on endpoint '{self.name}'")

    async def query_context_window(self, model_name: str) -> int:
        """Query context window from a llama.cpp server via /props."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(self._props_url(), timeout=self.timeout)
                if response.status_code == 200:
                    ctx = response.json().get("default_generation_settings", {}).get("n_ctx")
                    if ctx:
                        return ctx
        except Exception as e:
            logger.debug("Failed to query /props endpoint: %s", e)

        models = await self.list_models()
        for model in models:
            if model.id == model_name and model.context_window:
                return model.context_window
        raise RuntimeError("Could not determine context window from server")

    async def query_image_input(self, model_name: str) -> Optional[bool]:
        async with httpx.AsyncClient() as client:
            response = await client.get(self._props_url(), timeout=self.timeout)
            response.raise_for_status()
            vision = (response.json().get("modalities") or {}).get("vision")
            return vision if isinstance(vision, bool) else None
