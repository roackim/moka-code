"""``type = "ollama"``: native ``/api/chat``, ``/api/tags``, ``/api/show``."""
from __future__ import annotations

from typing import Any, AsyncGenerator, Dict, Optional

from moka_code.harness import endpoint_ollama as _native
from moka_code.harness.endpoint import Endpoint, ModelInfo


class Ollama(Endpoint):
    """An Ollama server, through its native API (keeps final usage counters)."""

    type = "ollama"

    async def list_models(self) -> list[ModelInfo]:
        return await _native.list_models(self)

    async def discover_models(self) -> list[ModelInfo]:
        return await _native.discover_models(self)

    async def query_model_name(self) -> str:
        if self._selected_model:
            return self._selected_model
        models = await self.list_models()
        if models:
            return models[0].id
        raise RuntimeError(f"No models available on endpoint '{self.name}'")

    async def query_context_window(self, model_name: str) -> int:
        return await _native.context_window(self, model_name)

    async def query_image_input(self, model_name: str) -> Optional[bool]:
        return await _native.image_input(self, model_name)

    async def check_connection(self) -> bool:
        return await _native.check_connection(self)

    def _completion(
        self,
        messages: list[Dict[str, Any]],
        tools: Optional[list[Dict[str, Any]]],
        stream: bool,
    ) -> AsyncGenerator[Any, None]:
        return _native.create_completion(self, messages, tools, stream)

    def effort_payload(self) -> dict[str, Any]:
        if not self.effort:
            return {}
        return {"think": False if self.effort == "none" else self.effort}
