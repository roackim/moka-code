"""Regression tests for model/endpoint selection and reconciliation.

These cover the failure modes where moka could display one model but send
another: per-server selection not applied on switch and single-model endpoints
(llama.cpp) that ignore the requested model.
"""

import asyncio
from types import SimpleNamespace

import pytest

from moka_code.harness.providers import LlamaCpp


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
        "type": "llamacpp",
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
        "type": "llamacpp",
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
    cfg.config.servers["a"] = {"type": "llamacpp", "base_url": "http://a/v1"}
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
    """§9.2 / ISSUES P4 (2026-10-01): no probe, no 32768, no ``max_context``:
    only what the server's listing states (2a: its absence is a notice)."""
    endpoint = LlamaCpp(name="o", base_url="http://o/v1", model="m")
    assert endpoint.context_window() is None
    cfg.config.models_by_server["o"] = [{"id": "m", "context_window": 131072}]
    assert endpoint.context_window() == 131072


def test_missing_context_window_is_an_error_notice(cfg):
    """2a (2026-10-01): a listed model whose server states no context window
    is flagged; a stale listing or an unlisted model is not this notice."""
    from types import SimpleNamespace
    from moka_code.ui.status_presenter import notices

    cfg.config.servers["o"] = {"type": "llamacpp", "base_url": "http://o/v1"}
    endpoint = LlamaCpp(name="o", base_url="http://o/v1", model="m")
    agent = SimpleNamespace(endpoint=endpoint, role=None)
    cfg.config.models_by_server["o"] = [{"id": "m", "context_window": None}]
    assert any("states no context window for m" in t for _, t in notices(agent))
    cfg.config.models_by_server["o"] = [{"id": "m", "context_window": 8192}]
    assert not any("context window" in t for _, t in notices(agent))
    cfg.config.models_by_server["o"] = [{"id": "m", "context_window": None}]
    cfg.config.stale_servers.add("o")
    assert not any("context window" in t for _, t in notices(agent))


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
    cfg.config.servers["a"] = {"type": "llamacpp", "base_url": "http://old/v1"}
    agent = _ReloadAgent(get_active_endpoint())

    _reapply(agent)
    assert agent.switched == []  # unchanged config keeps the live endpoint

    cfg.config.servers["a"] = {"type": "llamacpp", "base_url": "http://new/v1"}
    _reapply(agent)
    assert len(agent.switched) == 1
    assert agent.endpoint.base_url == "http://new/v1"




def test_needed_preserved_thinking_is_a_warning_from_live_state(cfg):
    """A role that sends no reasoning to a provider that needs it gets a
    warning, from live state (a model or server switch re-evaluates it)."""
    from types import SimpleNamespace
    from moka_code.harness.roles import Role
    from moka_code.ui.status_presenter import notices

    class Strict(LlamaCpp):
        def needs_preserved_thinking(self, has_tools):
            return has_tools

    cfg.config.servers["s"] = {"type": "llamacpp", "base_url": "http://s/v1"}
    cfg.config.models_by_server["s"] = [{"id": "m", "context_window": 8192}]
    endpoint = Strict(name="s", base_url="http://s/v1", model="m")

    def warnings(preserve, tools):
        agent = SimpleNamespace(endpoint=endpoint, tool_schemas=tools,
                                role=Role(name="r", preserve_thinking=preserve))
        return [t for level, t in notices(agent) if level == "warning"]

    assert "role r sends none" in warnings(False, [{"x": 1}])[0]
    assert warnings(True, [{"x": 1}]) == []
    assert warnings(False, None) == []                  # no tools sent: not needed
    other = LlamaCpp(name="s", base_url="http://s/v1", model="m")
    endpoint = other                                    # switching server re-evaluates
    assert warnings(False, [{"x": 1}]) == []


def test_a_server_that_cannot_be_listed_is_reported_even_when_not_active(cfg, monkeypatch):
    """A failed discovery used to reach only the debug log: with the active
    server missing, a broken DeepSeek failed without a word."""
    import asyncio
    from types import SimpleNamespace

    import httpx
    from moka_code.harness.endpoint import refresh_catalog
    from moka_code.ui.status_presenter import notices

    monkeypatch.delenv("DS_KEY", raising=False)
    cfg.config.servers["ds"] = {"type": "llamacpp", "base_url": "http://ds/v1"}
    cfg.config.servers["keyed"] = {"type": "llamacpp", "base_url": "http://k/v1",
                                   "api_key_env": "DS_KEY"}
    cfg.config.active_server = "gone"
    agent = SimpleNamespace(endpoint=None, role=None)

    async def refuse(self):
        request = httpx.Request("GET", "http://ds/v1/models")
        raise httpx.HTTPStatusError("x", request=request, response=httpx.Response(401, request=request))

    monkeypatch.setattr(LlamaCpp, "list_models", refuse)
    asyncio.run(refresh_catalog(["ds"]))

    assert cfg.config.discovery_errors == {"ds": "HTTP 401 (check the API key)"}
    texts = [(level, t) for level, t in notices(agent)]
    assert ("warning", "ds cannot be listed: HTTP 401 (check the API key) → /config servers") in texts
    assert ("warning", "$DS_KEY is not set (keyed API key) → export DS_KEY=…") in texts

    async def fine(self):
        return []

    monkeypatch.setattr(LlamaCpp, "list_models", fine)
    asyncio.run(refresh_catalog(["ds"]))
    assert cfg.config.discovery_errors == {}
    assert not any("cannot be listed" in t for _, t in notices(agent))


def test_server_without_a_selected_model_is_an_error_notice(cfg):
    """ISSUES U7: the status bar's ``?`` always comes with its fix."""
    from moka_code.ui.status_presenter import notices

    cfg.config.servers["o"] = {"type": "llamacpp", "base_url": "http://o/v1"}
    agent = SimpleNamespace(endpoint=LlamaCpp(name="o", base_url="http://o/v1"), role=None)

    assert ("error", "o: no model selected → /model") in notices(agent)


def test_unknown_theme_is_reported(cfg, monkeypatch):
    """ISSUES U5: a typo in ``ui.theme`` (or a vanished saved theme) is named."""
    from moka_code.ui.tui.colors import theme_problems

    monkeypatch.setattr(cfg.config, "ui_theme", "nrod")
    monkeypatch.setattr(cfg.config, "active_theme", "gone")
    assert theme_problems() == ["ui.toml: theme 'nrod' is not a theme (using terminal)",
                                "state.toml: active_theme 'gone' is not a theme (using terminal)"]

    monkeypatch.setattr(cfg.config, "ui_theme", "nord")
    monkeypatch.setattr(cfg.config, "active_theme", None)
    assert theme_problems() == []


def _openrouter_models(monkeypatch, enabled, catalog_ids):
    from moka_code.harness.providers import OpenRouter

    server = OpenRouter(name="or", models=enabled)

    async def catalog():
        return [{"id": cid, "context_length": 1000} for cid in catalog_ids]

    monkeypatch.setattr(server, "_catalog", catalog)
    return asyncio.run(server.list_models())


def test_openrouter_bare_id_matches_one_model(monkeypatch):
    models = _openrouter_models(monkeypatch, ["flash"], ["a/flash", "b/other"])
    assert [m.id for m in models] == ["a/flash"]


def test_openrouter_bare_id_matching_two_models_is_an_error(monkeypatch):
    """ISSUES P5: never a silent pick between ``a/flash`` and ``b/flash``."""
    with pytest.raises(RuntimeError, match="matches a/flash, b/flash → write the full id"):
        _openrouter_models(monkeypatch, ["flash"], ["a/flash", "b/flash"])
