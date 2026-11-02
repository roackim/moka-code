"""moka: Self-Hosted AI Code Agent.

A CLI chat app for self-hosted LLM agents with native tool calling support.
"""

__version__ = "1.3.13"

from moka_code import settings
from moka_code.harness.harness import Harness, get_harness

__all__ = [
    "settings",
    "Harness",
    "get_harness",
]
