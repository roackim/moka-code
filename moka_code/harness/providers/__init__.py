"""Server families moka can talk to, one class each (``providers.md``).

``REGISTRY`` maps a ``servers.toml`` ``type`` to its class; it is the only
place a type string is looked up.
"""
from moka_code.harness.providers.deepseek import DeepSeek
from moka_code.harness.providers.llamacpp import LlamaCpp
from moka_code.harness.providers.openai_compatible import OpenAICompatible
from moka_code.harness.providers.openai_server import OpenAICompatibleServer
from moka_code.harness.providers.openrouter import OpenRouter

REGISTRY = {cls.type: cls for cls in (LlamaCpp, OpenRouter, DeepSeek, OpenAICompatibleServer)}

__all__ = ["REGISTRY", "DeepSeek", "LlamaCpp", "OpenAICompatible", "OpenAICompatibleServer",
           "OpenRouter"]
