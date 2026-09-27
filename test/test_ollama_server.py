"""Mocked tests for the Ollama backend adapter."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from moka_chat.harness.endpoint import Endpoint


def make_client() -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def make_server(base_url: str = "http://localhost:11434/v1") -> Endpoint:
    return Endpoint(
        name="ollama-test",
        type="ollama",
        base_url=base_url,
        api_key="EMPTY",
        model=None,
        max_context=None,
        timeout=1.0,
    )


def test_native_base_url_strips_v1_suffix():
    assert make_server()._native_base_url() == "http://localhost:11434"
    assert make_server("http://localhost:11434")._native_base_url() == "http://localhost:11434"


def test_list_models_parses_tags():
    server = make_server()
    fake_response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {
            "models": [
                {"name": "llama3.1:8b", "model": "llama3.1:8b"},
                {"model": "qwen2.5:7b"},
                {"size": 123},
            ]
        },
    )
    mock_client = make_client()
    mock_client.get = AsyncMock(return_value=fake_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        models = asyncio_run(server.list_models())

    ids = [m.id for m in models]
    assert ids == ["llama3.1:8b", "qwen2.5:7b"]


def test_query_model_name_uses_selected_then_first():
    server = make_server()
    server._selected_model = "llama3.1:8b"
    assert asyncio_run(server.query_model_name()) == "llama3.1:8b"

    fresh = make_server()
    fake_response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"models": [{"name": "deepseek-v3"}]},
    )
    mock_client = make_client()
    mock_client.get = AsyncMock(return_value=fake_response)
    with patch("httpx.AsyncClient", return_value=mock_client):
        assert asyncio_run(fresh.query_model_name()) == "deepseek-v3"


def test_query_context_window_parses_model_info():
    server = make_server()
    fake_response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {
            "model_info": {"llama.context_length": 8192},
            "parameters": "",
        },
    )
    mock_client = make_client()
    mock_client.post = AsyncMock(return_value=fake_response)
    with patch("httpx.AsyncClient", return_value=mock_client):
        assert asyncio_run(server.query_context_window("llama3.1:8b")) == 8192


def test_query_context_window_parses_parameters():
    server = make_server()
    fake_response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {
            "model_info": {},
            "parameters": "num_ctx 16384",
        },
    )
    mock_client = make_client()
    mock_client.post = AsyncMock(return_value=fake_response)
    with patch("httpx.AsyncClient", return_value=mock_client):
        assert asyncio_run(server.query_context_window("qwen2.5:7b")) == 16384


def test_native_response_adapts_content_and_usage():
    chunk = Endpoint._native_response({
        "message": {"role": "assistant", "content": "hello"},
        "done": True,
        "prompt_eval_count": 100,
        "eval_count": 12,
    })
    assert chunk.choices == []
    assert chunk.usage["prompt_eval_count"] == 100
    assert chunk.usage["eval_count"] == 12


def test_native_response_adapts_reasoning_and_tool_calls():
    chunk = Endpoint._native_response({
        "message": {
            "role": "assistant",
            "content": "call",
            "thinking": "let me think",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {
                        "name": "read",
                        "arguments": {"path": "a.txt"},
                    },
                }
            ],
        },
        "done": False,
    })
    assert chunk.choices[0].delta.reasoning_content == "let me think"
    call = chunk.choices[0].delta.tool_calls[0]
    assert call.function.name == "read"
    assert '"path"' in call.function.arguments
    assert chunk.choices[0].finish_reason is None


def test_check_connection_reports_success():
    server = make_server()
    fake_response = SimpleNamespace(is_success=True)
    mock_client = make_client()
    mock_client.get = AsyncMock(return_value=fake_response)
    with patch("httpx.AsyncClient", return_value=mock_client):
        assert asyncio_run(server.check_connection()) is True


def test_ollama_messages_coerce_null_content_and_drop_null_tool_calls():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1"}]},
        {"role": "tool", "content": "ok", "tool_call_id": "call_1"},
        {"role": "assistant", "content": "hi", "tool_calls": None},
    ]

    normalized = Endpoint._ollama_messages(messages)

    assert normalized[1]["content"] == ""
    assert normalized[1]["tool_calls"] == [{"id": "call_1"}]
    assert normalized[3]["content"] == "hi"
    assert "tool_calls" not in normalized[3]
    # The caller's history must not be mutated.
    assert messages[1]["content"] is None
    assert messages[3]["tool_calls"] is None


def test_create_ollama_completion_sends_string_content(monkeypatch):
    import json

    server = make_server()
    server._selected_model = "test-model"
    captured = {}

    class _Response:
        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            yield json.dumps({"message": {"content": "ok"}, "done": True})

    class _Stream:
        async def __aenter__(self):
            return _Response()

        async def __aexit__(self, *args):
            return False

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def stream(self, method, url, json=None, timeout=None):
            captured["json"] = json
            return _Stream()

    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: _Client())

    async def run():
        return [
            chunk
            async for chunk in server._create_ollama_completion(
                [{"role": "assistant", "content": None, "tool_calls": [{"id": "call_1"}]}],
                None,
                True,
            )
        ]

    asyncio_run(run())

    assert captured["json"]["messages"][0]["content"] == ""


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)
