"""``type = "openai-compatible"``: any server that speaks the chat-completions API
and states its own facts in ``/models`` (a proxy, vLLM, LM Studio, ...)."""
from __future__ import annotations

from moka_code.harness.providers.openai_compatible import OpenAICompatible


class OpenAICompatibleServer(OpenAICompatible):
    """Nothing of its own: the inherited ``/models`` reader, ``reasoning_content`` in
    and out, flat ``reasoning_effort`` and ``include_usage`` are the whole contract.

    The server must state each model's ``context_length`` in ``/models`` (there is
    no ``/props``, no default and no config key for it); it may state
    ``architecture.input_modalities`` and ``reasoning.supported_efforts``. A model
    that states no window gets the error notice (``providers.md`` §9.12).
    """

    type = "openai-compatible"
    template = """\
## OpenAI-compatible server.
# [servers.custom]
# type = "openai-compatible"
# base_url = "http://localhost:8000/v1"
# api_key_env = "CUSTOM_API_KEY"
"""
