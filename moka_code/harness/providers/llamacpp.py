"""``type = "llamacpp"``: OpenAI-compatible, plus ``/props`` and a default URL."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Any, Dict, Optional

import httpx

from moka_code.harness.endpoint import ModelInfo
from moka_code.harness.providers.openai_compatible import OpenAICompatible
from moka_code.harness.usage import TokenUsage


logger = logging.getLogger(__name__)


class LlamaCpp(OpenAICompatible):
    """A llama.cpp server (``llama-server``)."""

    type = "llamacpp"
    default_url = "http://localhost:8080/v1"
    template = """\
## llama.cpp
# [servers.local]
# type = "llamacpp"
# base_url = "http://localhost:8080/v1"
"""

    def _props_url(self) -> str:
        base = self.base_url.rstrip("/")
        return base.removesuffix("/v1") + "/props"

    def _usage(self, data: Dict[str, Any]) -> Optional[TokenUsage]:
        usage = super()._usage(data)
        if usage is not None and usage.cached_prompt_tokens is None:
            # llama.cpp reports KV-cache reuse in ``timings``, next to usage.
            cache_n = (data.get("timings") or {}).get("cache_n")
            if isinstance(cache_n, int):
                usage = replace(usage, cached_prompt_tokens=cache_n)
        return usage

    async def _props(self, model_id: str) -> Optional[Dict[str, Any]]:
        """``/props`` of one model; ``None`` when it does not answer (unknown,
        not a failure). In router mode ``?model=`` picks the model and
        ``autoload=false`` keeps a listing from loading the unloaded ones (README,
        "Using multiple models"); a model that is not loaded then has no answer."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    self._props_url(), params={"model": model_id, "autoload": "false"},
                    timeout=self.timeout)
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.debug("Failed to query /props for %s: %s", model_id, e)
            return None

    async def list_models(self) -> list[ModelInfo]:
        """``/models``, plus the context window and vision each model's
        ``/props`` states (⚠ ``?model=`` on a single-model server is unverified:
        an unanswered ``/props`` leaves the facts unknown)."""
        models = await super().list_models()
        answers = await asyncio.gather(*(self._props(model.id) for model in models))
        return [self._with_props(model, props) for model, props in zip(models, answers)]

    @staticmethod
    def _with_props(model: ModelInfo, props: Optional[Dict[str, Any]]) -> ModelInfo:
        if props is None:
            return model
        n_ctx = (props.get("default_generation_settings") or {}).get("n_ctx")
        vision = (props.get("modalities") or {}).get("vision")
        return replace(
            model,
            context_window=n_ctx if isinstance(n_ctx, int) and n_ctx > 0 else model.context_window,
            images=vision if isinstance(vision, bool) else model.images,
        )
