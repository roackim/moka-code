"""``type = "openrouter"``: fixed URL, a model whitelist, provider routing."""
from __future__ import annotations

from typing import Any, AsyncGenerator, Dict, Optional

import httpx

from moka_code.harness.endpoint import Chunk, ModelInfo
from moka_code.harness.providers.openai_compatible import (
    OpenAICompatible, _delta, stated_efforts, stated_images,
)


class OpenRouter(OpenAICompatible):
    """OpenRouter (https://openrouter.ai)."""

    type = "openrouter"
    fixed_url = "https://openrouter.ai/api/v1"
    extra_keys = frozenset({"models", "providers", "providers_by_model"})
    template = """\
## OpenRouter (always https://openrouter.ai/api/v1) ----------------------
# [servers.openrouter]
# type = "openrouter"
# api_key_env = "OPENROUTER_API_KEY"
# models = ["deepseek/deepseek-v4.1-flash", "qwen/qwen3-coder"]  # what /model lists
# providers = ["deepseek"]               # optional; only these, in this order
##
## Optional per-model routing: replaces providers for that model; [] means
## OpenRouter's own routing. Slugs are on the model's "Providers" tab.
# [servers.openrouter.providers_by_model]
# "deepseek/deepseek-v4.1-flash" = ["deepseek", "fireworks"]
# "qwen/qwen3-coder" = []
"""

    def __init__(
        self,
        name: str,
        models: Optional[list[str]] = None,
        providers: Optional[list[str]] = None,
        providers_by_model: Optional[dict[str, list[str]]] = None,
        **kwargs: Any,
    ):
        super().__init__(name, **kwargs)
        # The enabled model ids (the whitelist), the default provider
        # whitelist (in order), and per-model replacements of it.
        self.models = list(models or [])
        self.providers = list(providers) if providers is not None else None
        self.providers_by_model = {
            model_id: list(slugs) for model_id, slugs in (providers_by_model or {}).items()}

    async def _catalog(self) -> Optional[list[dict]]:
        """OpenRouter's public model catalog; ``None`` if it did not answer."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.fixed_url}/models", timeout=self.timeout)
            if response.status_code != 200:
                return None
        return response.json().get("data", [])

    async def list_models(self) -> list[ModelInfo]:
        """Only the enabled models (OpenRouter exposes thousands), with the
        facts the public catalog states."""
        enabled = self._enabled_ids()
        if not enabled:
            return []
        catalog = await self._catalog()
        if catalog is None:
            raise RuntimeError("OpenRouter's catalog did not answer")
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
                images=stated_images(info),
                efforts=stated_efforts(info),
                owned_by=info.get("owned_by"),
                raw=info,
            ))
        return result

    def replay(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """``reasoning_details`` as produced, only to the model that produced
        them; else the ``reasoning`` text. Nothing when the model gave none."""
        native = entry.get("reasoning_native")
        origin = entry.get("origin") or {}
        if native and origin == {"type": self.type, "model": self._selected_model}:
            return {"reasoning_details": native}
        if entry.get("reasoning"):
            return {"reasoning": entry["reasoning"]}
        return {}

    def effort_payload(self) -> dict[str, Any]:
        if not self.effort:
            return {}
        return {"reasoning": {"effort": self.effort}}

    async def _stream(
        self,
        messages: list[Dict[str, Any]],
        tools: Optional[list[Dict[str, Any]]],
    ) -> AsyncGenerator[Chunk, None]:
        """The answer, then its ``reasoning_details`` blocks, assembled."""
        blocks: list = []
        async for data in self._sse_objects(messages, tools):
            merge_reasoning_details(blocks, _delta(data).get("reasoning_details"))
            yield self._chunk(data)
        if blocks:
            yield Chunk(reasoning_native=blocks)

    def _extra_payload(self, model_name: str) -> dict[str, Any]:
        provider_spec = self._provider_spec(model_name)
        return {"provider": provider_spec} if provider_spec else {}

    def _enabled_ids(self) -> list[str]:
        """The enabled model ids: only those in ``models``."""
        return list(self.models)

    def _model_providers(self, model_name: str) -> Optional[list[str]]:
        """This model's ``providers_by_model`` entry, or ``None``.

        A bare id (no ``vendor/`` prefix) matches its canonical id, since
        discovery canonicalizes bare ids (ISSUES P5).
        """
        if model_name in self.providers_by_model:
            return self.providers_by_model[model_name]
        for model_id, slugs in self.providers_by_model.items():
            if model_name.endswith("/" + model_id):
                return slugs
        return None

    def _provider_spec(self, model_name: str) -> Optional[dict]:
        """Build the OpenRouter ``provider`` payload for a model.

        ``providers`` is a strict whitelist tried in order: ``order`` alone lets
        OpenRouter fall back to any other host, so fallbacks are disabled. A
        model's ``providers_by_model`` entry replaces the server default; an
        empty list (or none at all) means OpenRouter's own routing (``None``).
        """
        providers = self._model_providers(model_name)
        if providers is None:
            providers = self.providers
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
