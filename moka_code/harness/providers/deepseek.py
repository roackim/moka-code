"""``type = "deepseek"``: OpenAI-compatible, with DeepSeek's own ``/models`` shape."""
from __future__ import annotations

import asyncio

from moka_code.harness.endpoint import ModelInfo
from moka_code.harness.providers.openai_compatible import OpenAICompatible, weakest_first


class DeepSeek(OpenAICompatible):
    """DeepSeek (https://api-docs.deepseek.com), read 2026-10-01.

    Reasoning comes and goes in ``reasoning_content`` (the inherited default).
    Thinking is on by default and moka never sends the ``thinking`` switch.
    """

    type = "deepseek"
    fixed_url = "https://api.deepseek.com"
    template = """\
## DeepSeek (always https://api.deepseek.com)
# [servers.deepseek]
# api_key_env = "DEEPSEEK_API_KEY"
"""

    def min_replay_depth(self, has_tools: bool) -> int:
        """With ``tools``, every earlier turn's ``reasoning_content`` must go
        back or the API answers HTTP 400 (``guides/thinking_mode``)."""
        return 999 if has_tools else 0

    async def list_models(self) -> list[ModelInfo]:
        """``GET /models`` (``api/list-models``): ``context_window``,
        ``input_modalities`` and ``effort.supported_levels``."""
        response = await asyncio.wait_for(self.client.get("/models"), timeout=self.timeout)
        response.raise_for_status()
        models = []
        for entry in response.json().get("data", []) or []:
            modalities = entry.get("input_modalities")
            levels = (entry.get("effort") or {}).get("supported_levels")
            models.append(ModelInfo(
                id=entry.get("id"),
                context_window=entry.get("context_window"),
                images=("image" in modalities) if isinstance(modalities, list) else None,
                efforts=weakest_first(levels) if isinstance(levels, list)
                and all(isinstance(level, str) for level in levels) else [],
                owned_by=entry.get("owned_by"),
                raw=entry,
            ))
        return models
