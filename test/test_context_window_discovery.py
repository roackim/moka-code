"""Regression tests for OpenRouter context-window lookup.

A bare model id (no ``provider/`` namespace) in ``[models."<id>"]`` used to
fail the exact-id lookup against OpenRouter's catalog, so the context window
fell back to 32k even for 1M-token models.
"""

import asyncio

import moka_code.harness.endpoint as endpoint_mod
from moka_code.harness.endpoint import Endpoint


class _Response:
    status_code = 200

    def __init__(self, data):
        self._data = data

    def json(self):
        return {"data": self._data}


class _Client:
    def __init__(self, data):
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, *args, **kwargs):
        return _Response(self._data)


CATALOG = [
    {"id": "~deepseek/deepseek-v4-flash-0731", "context_length": 1024},
    {"id": "deepseek/deepseek-v4-flash-0731", "context_length": 1310720},
    {"id": "other/model", "context_length": 8192},
]


def _patch_client(monkeypatch, data):
    monkeypatch.setattr(endpoint_mod.httpx, "AsyncClient", lambda *a, **k: _Client(data))


def test_openrouter_context_window_matches_bare_id(monkeypatch):
    _patch_client(monkeypatch, CATALOG)
    endpoint = Endpoint(
        name="or", type="openrouter", base_url="https://openrouter.ai/api/v1",
        api_key="k", model="deepseek/deepseek-v4-flash-0731",
    )

    ctx = asyncio.run(endpoint._openrouter_context_window("deepseek-v4-flash-0731"))

    assert ctx == 1310720  # canonical entry, not the "~" alias


def test_openrouter_context_window_exact_id(monkeypatch):
    _patch_client(monkeypatch, CATALOG)
    endpoint = Endpoint(
        name="or", type="openrouter", base_url="https://openrouter.ai/api/v1",
        api_key="k", model="other/model",
    )

    assert asyncio.run(endpoint._openrouter_context_window("other/model")) == 8192


def test_discover_openrouter_canonicalizes_bare_enabled_id(monkeypatch):
    _patch_client(monkeypatch, CATALOG)
    endpoint = Endpoint(
        name="or", type="openrouter", base_url="https://openrouter.ai/api/v1",
        api_key="k", models={"deepseek-v4-flash-0731": {}},
    )

    models = asyncio.run(endpoint.discover_models())

    assert [m.id for m in models] == ["deepseek/deepseek-v4-flash-0731"]
    assert models[0].context_window == 1310720
