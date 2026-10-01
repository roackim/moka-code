"""Shared test fixtures for moka.

Centralises the stubs and helpers that were previously duplicated across
test_permissions.py, test_compaction.py, and test_ui_permission_submit.py.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import pytest

from moka_code.harness.harness import Harness
from moka_code.harness.llm_status import AgentState
from moka_code.harness.permissions import PermissionGate


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class NoopDebugStream:
    """DebugStream stub that silently absorbs all log calls."""

    def log(self, *args, **kwargs):
        return None


class FakeServer:
    """Minimal LLM server stub for tests that don't need a real connection.

    Yields a single completion response with configurable content.
    """

    def __init__(self, response_content: str = "Decisions: keep latest thread.\nOpen Tasks: continue implementation."):
        self.last_messages = None
        self._response_content = response_content

    async def get_model_name(self):
        return "test-model"

    async def get_context_window(self):
        return 32768

    async def stream(self, messages, tools=None):
        from moka_code.harness.endpoint import Chunk

        self.last_messages = messages
        yield Chunk(text=self._response_content)


class StubReadTool:
    """Minimal read-tool stub for permission-flow tests."""

    def __init__(self, result: str = "ok"):
        self.result = result
        self.called = False

    def execute(self, **kwargs):
        self.called = True
        return self.result


class StubAgent:
    """Minimal agent stub for UI tests."""

    def list_files_and_folders(self):
        return []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def noop_debug_stream():
    return NoopDebugStream()


@pytest.fixture
def fake_server():
    return FakeServer()


@pytest.fixture
def stub_read_tool():
    return StubReadTool()


@pytest.fixture
def stub_agent():
    return StubAgent()


@pytest.fixture
def harness_stub(tmp_path, stub_read_tool):
    """A partially-initialised Harness for tool-execution tests.

    Uses Harness.__new__ to skip the full __init__ (which would try to
    connect to a real LLM server).  Only the attributes needed by
    _execute_tool_calls are set.
    """
    harness = Harness.__new__(Harness)
    harness.debug_stream = NoopDebugStream()
    harness.state = AgentState.IDLE
    harness.history = []
    harness.workspace = str(tmp_path)
    harness.tools_map = {"read": stub_read_tool}
    from moka_code.harness.roles import Role

    harness._permission_gate = PermissionGate(role=Role(name="test", tools={"read": "yes"}))
    return harness


@pytest.fixture
def harness_stub_compaction():
    """A partially-initialised Harness for compaction tests.

    Uses a FakeServer so compaction can produce a canned response.
    """
    harness = Harness.__new__(Harness)
    harness.debug_stream = NoopDebugStream()
    harness.state = AgentState.IDLE
    harness.history = []
    harness.workspace = "."
    harness.project_context = "Project Root: .\nFiles:"
    harness.endpoint = FakeServer()
    return harness


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_harness_tool_call(harness: Harness, tool_call: dict):
    """Run a single tool call through harness._execute_tool_calls and collect events."""
    async def _collect():
        messages = []
        events = []
        async for event in harness._execute_tool_calls([tool_call], messages):
            events.append(event)
        return events, messages

    return asyncio.run(_collect())


def make_chunk_stream(*chunk_list):
    """Return an async generator that yields the given events."""
    async def _gen():
        for chunk in chunk_list:
            yield chunk
    return _gen()
