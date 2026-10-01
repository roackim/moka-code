"""Regression tests for model/endpoint selection and reconciliation.

These cover the failure modes where moka could display one model but send
another: per-server selection not applied on switch and single-model endpoints
(llama.cpp) that ignore the requested model.
"""

import asyncio
from types import SimpleNamespace

import pytest

from moka_code.harness.providers import LlamaCpp, OpenAICompatible, OpenRouter


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    import moka_code.settings as cfg_mod

    monkeypatch.setattr(cfg_mod, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg_mod, "get_state_path", lambda: tmp_path / "state.toml")
    monkeypatch.setattr(cfg_mod.config, "servers", {}, raising=False)
    monkeypatch.setattr(cfg_mod.config, "model_selection", {}, raising=False)
    monkeypatch.setattr(cfg_mod.config, "models_by_server", {}, raising=False)
    monkeypatch.setattr(cfg_mod.config, "active_server", "a", raising=False)
    return cfg_mod


def test_get_endpoint_applies_per_server_selection(cfg):
    cfg.config.servers["srv"] = {
        "type": "openai",
        "base_url": "http://localhost:8000/v1",
        "api_key": "EMPTY",
    }
    cfg.config.model_selection["srv"] = "selected-model"

    from moka_code.harness.endpoint import get_endpoint

    assert get_endpoint("srv").selected_model == "selected-model"


def test_get_endpoint_without_selection_has_no_model(cfg):
    """Decided 2026-09-30: no ``model`` key in servers.toml; models come
    from discovery (OpenRouter: its model tables) and the selection."""
    cfg.config.servers["srv"] = {
        "type": "openai",
        "base_url": "http://localhost:8000/v1",
    }

    from moka_code.harness.endpoint import get_endpoint

    assert get_endpoint("srv").selected_model is None


def test_model_key_in_servers_toml_is_reported(tmp_path):
    from moka_code.settings import Config

    (tmp_path / "servers.toml").write_text(
        '[servers.openai]\nbase_url = "http://h:8000/v1"\nmodel = "m"\n')
    config = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")
    assert any("unknown key 'model'" in e for e in config.load_errors)


def test_llamacpp_reconciles_requested_selection_with_served_model(cfg, monkeypatch):
    endpoint = LlamaCpp(
        name="local", base_url="http://localhost:8080/v1",
        api_key="EMPTY", model="served-model",
    )

    async def _fake_diagnose():
        return SimpleNamespace(ok=True)

    async def _fake_query_model_name():
        return "served-model"

    monkeypatch.setattr(endpoint, "diagnose_connection", _fake_diagnose)
    monkeypatch.setattr(endpoint, "query_model_name", _fake_query_model_name)

    endpoint._selected_model = "requested-but-ignored"
    asyncio.run(endpoint.prewarm_model_name())

    assert endpoint.selected_model == "served-model"
    assert endpoint._model_resolved




def test_active_endpoint_uses_only_its_own_server_selection(cfg):
    """A server with no selection must not inherit another server's model."""
    cfg.config.servers["a"] = {"type": "openai", "base_url": "http://a/v1"}
    cfg.config.servers["b"] = {"type": "openrouter", "base_url": "http://b/v1"}
    cfg.config.model_selection["b"] = "vendor/model-b"
    cfg.config.active_server = "a"

    from moka_code.harness.endpoint import get_active_endpoint

    endpoint = get_active_endpoint()
    assert endpoint.name == "a"
    assert endpoint.selected_model is None


def test_get_endpoint_seeds_context_window_from_catalog(cfg):
    cfg.config.servers["or"] = {"type": "openrouter", "base_url": "http://or/v1"}
    cfg.config.model_selection["or"] = "vendor/m"
    cfg.config.models_by_server["or"] = [{"id": "vendor/m", "context_window": 1048576}]

    from moka_code.harness.endpoint import get_endpoint

    endpoint = get_endpoint("or")

    async def _no_network(_model):
        raise AssertionError("catalog-known window must not be re-queried")

    endpoint.query_context_window = _no_network
    assert asyncio.run(endpoint.get_context_window()) == 1048576


def test_context_window_fallback_is_not_memoized():
    """A transient failure shows the fallback but a later probe can succeed."""
    endpoint = OpenAICompatible(name="o", base_url="http://o/v1", model="m")
    answers = [RuntimeError("down"), 65536]

    async def _query(_model):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    endpoint.query_context_window = _query
    assert asyncio.run(endpoint.get_context_window()) == 32768
    assert asyncio.run(endpoint.get_context_window()) == 65536


def test_model_name_fallback_is_not_memoized():
    endpoint = OpenAICompatible(name="o", base_url="http://o/v1")
    answers = [RuntimeError("down"), "llama3"]

    async def _query():
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    endpoint.query_model_name = _query
    assert asyncio.run(endpoint.get_model_name()) == "unknown"
    assert asyncio.run(endpoint.get_model_name()) == "llama3"


class _ReloadAgent:
    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.switched = []

    def switch_server(self, endpoint):
        self.endpoint = endpoint
        self.switched.append(endpoint)


def _reapply(agent):
    from moka_code.ui.commands.base import reapply_endpoint

    async def _run():
        reapply_endpoint(SimpleNamespace(agent=agent))

    asyncio.run(_run())


def test_reload_rebuilds_endpoint_when_server_definition_changes(cfg, monkeypatch):
    from moka_code.harness.endpoint import Endpoint, get_active_endpoint

    async def _no_probe(self):
        return None

    monkeypatch.setattr(Endpoint, "prewarm_model_name", _no_probe)
    cfg.config.servers["a"] = {"type": "openai", "base_url": "http://old/v1"}
    agent = _ReloadAgent(get_active_endpoint())

    _reapply(agent)
    assert agent.switched == []  # unchanged config keeps the live endpoint

    cfg.config.servers["a"] = {"type": "openai", "base_url": "http://new/v1"}
    _reapply(agent)
    assert len(agent.switched) == 1
    assert agent.endpoint.base_url == "http://new/v1"


def test_openrouter_providers_are_a_strict_ordered_whitelist():
    """``order`` alone falls back to any host; fallbacks must be disabled."""
    endpoint = OpenRouter(
        name="or", providers=["deepseek"],
        models={
            "deepseek/deepseek-v4.1-flash": {"providers": ["deepseek", "fireworks"]},
            "anthropic/claude-sonnet-4": {},
            "qwen/qwen3-coder": {"providers": []},
        },
    )
    assert endpoint._provider_spec("deepseek/deepseek-v4.1-flash") == {
        "order": ["deepseek", "fireworks"], "allow_fallbacks": False}
    # No per-model list: the server default applies.
    assert endpoint._provider_spec("anthropic/claude-sonnet-4") == {
        "order": ["deepseek"], "allow_fallbacks": False}
    # An explicit empty list means OpenRouter's own routing.
    assert endpoint._provider_spec("qwen/qwen3-coder") is None
    assert OpenRouter(name="or")._provider_spec("any/model") is None


def test_openrouter_bare_model_table_matches_canonical_id():
    endpoint = OpenRouter(name="or", models={"deepseek-v4.1-flash": {"providers": ["deepseek"]}})
    assert endpoint._provider_spec("deepseek/deepseek-v4.1-flash") == {
        "order": ["deepseek"], "allow_fallbacks": False}
    assert endpoint._enabled_ids() == ["deepseek-v4.1-flash"]
