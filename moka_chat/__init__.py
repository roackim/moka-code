"""moka: Self-Hosted AI Code Agent.

A CLI chat app for self-hosted LLM agents with native tool calling support.
"""

__version__ = "0.8.0"

from moka_chat import settings
from moka_chat.harness.harness import Harness, get_harness

__all__ = [
    "settings",
    "Harness",
    "get_harness",
]
