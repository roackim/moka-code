"""``type = "openrouter"``: fixed URL, a model whitelist, provider routing."""
from __future__ import annotations

from typing import Any, Optional

import httpx

from moka_code.harness.endpoint import ModelInfo, image_input_from_metadata
from moka_code.harness.providers.openai_compatible import OpenAICompatible


class OpenRouter(OpenAICompatible):
    """OpenRouter (https://openrouter.ai)."""

    type = "openrouter"
    fixed_url = "https://openrouter.ai/api/v1"
    extra_keys = frozenset({"providers", "models"})

    def __init__(
        self,
        name: str,
        providers: Optional[list[str]] = None,
        models: Optional[dict[str, dict[str, Any]]] = None,
        **kwargs: Any,
    ):
        super().__init__(name, **kwargs)
        # Per-model tables (``providers``; the tables are the enabled models)
        # and the default provider whitelist, in order.
        self.providers = list(providers) if providers is not None else None
        self.models = {model_id: dict(entry) for model_id, entry in (models or {}).items()}

    async def _catalog(self) -> Optional[list[dict]]:
        """OpenRouter's public model catalog; ``None`` if it did not answer."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.fixed_url}/models", timeout=self.timeout)
            if response.status_code != 200:
                return None
        return response.json().get("data", [])

    async def discover_models(self) -> list[ModelInfo]:
        """Only the enabled models: OpenRouter exposes thousands."""
        enabled = self._enabled_ids()
        if not enabled:
            return []
        catalog = await self._catalog()
        if catalog is None:
            return [ModelInfo(id=e) for e in enabled]
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

    async def query_context_window(self, model_name: str) -> int:
        catalog = await self._catalog()
        if catalog is not None:
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

    async def query_image_input(self, model_name: str) -> Optional[bool]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.fixed_url}/models/{model_name}/endpoints", timeout=self.timeout,
            )
            response.raise_for_status()
            return image_input_from_metadata(response.json().get("data") or {})

    def effort_payload(self) -> dict[str, Any]:
        if not self.effort:
            return {}
        return {"reasoning": {"effort": self.effort}}

    def _extra_payload(self, model_name: str) -> dict[str, Any]:
        provider_spec = self._provider_spec(model_name)
        return {"provider": provider_spec} if provider_spec else {}

    def _enabled_ids(self) -> list[str]:
        """Return the explicitly-enabled model ids.

        All OpenRouter models are disabled unless they have a
        ``[models."<id>"]`` table.
        """
        return list(self.models)

    def _model_entry(self, model_name: str) -> Optional[dict]:
        """The ``[models."<id>"]`` table for a model.

        A bare id (no ``vendor/`` prefix) matches its canonical id, since
        discovery canonicalizes bare ids.
        """
        if model_name in self.models:
            return self.models[model_name]
        for model_id, entry in self.models.items():
            if model_name.endswith("/" + model_id):
                return entry
        return None

    def _provider_spec(self, model_name: str) -> Optional[dict]:
        """Build the OpenRouter ``provider`` payload for a model.

        ``providers`` is a strict whitelist tried in order: ``order`` alone lets
        OpenRouter fall back to any other host, so fallbacks are disabled. A
        model's own ``providers`` replaces the server default; an empty list
        (or none at all) means OpenRouter's own routing (``None``).
        """
        entry = self._model_entry(model_name)
        providers = self.providers
        if entry is not None and "providers" in entry:
            providers = entry["providers"]
        if not providers:
            return None
        return {"order": list(providers), "allow_fallbacks": False}


def merge_reasoning_details(blocks: list, pieces: Any) -> None:
    """Fold streamed OpenRouter ``reasoning_details`` pieces into ``blocks``.

    Pieces sharing an ``index`` are one block: their ``text``/``summary``/
    ``data`` strings concatenate and later non-null fields (``signature``,
    ``id``, ``format``) fill in, rebuilding the blocks as the model produced
    them — they must be sent back unmodified.
    """
    if not isinstance(pieces, list):
        return
    for piece in pieces:
        if not isinstance(piece, dict):
            continue
        index = piece.get("index")
        block = next((b for b in blocks if index is not None and b.get("index") == index), None)
        if block is None:
            blocks.append(dict(piece))
            continue
        for key, value in piece.items():
            if key in ("text", "summary", "data") and isinstance(value, str):
                block[key] = (block.get(key) or "") + value
            elif value is not None:
                block[key] = value
