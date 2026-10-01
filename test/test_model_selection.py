"""Regression tests for model/endpoint selection and reconciliation.

These cover the failure modes where moka could display one model but send
another: per-server selection not applied on switch and single-model endpoints
(llama.cpp) that ignore the requested model.
"""

import asyncio
from types import SimpleNamespace

import pytest

from moka_code.harness.providers import LlamaCpp, OpenAICompatible


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
        "type": "openai-compatible",
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
        "type": "openai-compatible",
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


def test_llamacpp_keeps_the_selected_model(cfg, monkeypatch):
    """§9.6 (2026-10-01): the connection check never replaces the selection."""
    endpoint = LlamaCpp(name="local", api_key="EMPTY", model="requested")

    async def _fake_diagnose():
        endpoint._connection_state = "ok"
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(endpoint, "diagnose_connection", _fake_diagnose)
    asyncio.run(endpoint.prewarm_connection())

    assert endpoint.selected_model == "requested"
    assert endpoint._connection_state == "ok"


def test_active_endpoint_uses_only_its_own_server_selection(cfg):
    """A server with no selection must not inherit another server's model."""
    cfg.config.servers["a"] = {"type": "openai-compatible", "base_url": "http://a/v1"}
    cfg.config.servers["b"] = {"type": "openrouter", "base_url": "http://b/v1"}
    cfg.config.model_selection["b"] = "vendor/model-b"
    cfg.config.active_server = "a"

    from moka_code.harness.endpoint import get_active_endpoint

    endpoint = get_active_endpoint()
    assert endpoint.name == "a"
    assert endpoint.selected_model is None


def test_context_window_comes_from_the_catalog(cfg):
    cfg.config.servers["or"] = {"type": "openrouter"}
    cfg.config.model_selection["or"] = "vendor/m"
    cfg.config.models_by_server["or"] = [{"id": "vendor/m", "context_window": 1048576}]

    from moka_code.harness.endpoint import get_endpoint

    assert get_endpoint("or").context_window() == 1048576


def test_unknown_context_window_is_never_invented(cfg):
    """§9.2 / ISSUES P4 (2026-10-01): no probe, no 32768. ``max_context``
    from the server table is the user's own fallback."""
    endpoint = OpenAICompatible(name="o", base_url="http://o/v1", model="m")
    assert endpoint.context_window() is None
    endpoint.max_context = 65536
    assert endpoint.context_window() == 65536
    cfg.config.models_by_server["o"] = [{"id": "m", "context_window": 131072}]
    assert endpoint.context_window() == 131072


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

    monkeypatch.setattr(Endpoint, "prewarm_connection", _no_probe)
    cfg.config.servers["a"] = {"type": "openai-compatible", "base_url": "http://old/v1"}
    agent = _ReloadAgent(get_active_endpoint())

    _reapply(agent)
    assert agent.switched == []  # unchanged config keeps the live endpoint

    cfg.config.servers["a"] = {"type": "openai-compatible", "base_url": "http://new/v1"}
    _reapply(agent)
    assert len(agent.switched) == 1
    assert agent.endpoint.base_url == "http://new/v1"


