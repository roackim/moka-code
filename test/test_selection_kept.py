"""Model selection: the previous model is kept while it is available;
otherwise the first available one is selected, with a message saying why.

Decided 2026-10-01 (the user, replacing PLAN.md step 0.2's "a last used model
is never replaced"): keep the previous model if available, else the first
available. A server that cannot be listed keeps its selection (red, with the
notice band saying why): a network blip must not switch servers.
"""

from types import SimpleNamespace

import pytest

from moka_code import settings
from moka_code.ui.commands.base import auto_select
from moka_code.ui.status_presenter import notices, refresh_status_bar
from moka_code.ui.tui.colors import theme
from moka_code.harness.providers import LlamaCpp


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setattr(settings.config, "servers", {
        "a": {"type": "llamacpp", "base_url": "http://a/v1"},
        "b": {"type": "llamacpp", "base_url": "http://b/v1"},
    })
    monkeypatch.setattr(settings.config, "models_by_server", {})
    monkeypatch.setattr(settings.config, "stale_servers", set())
    monkeypatch.setattr(settings.config, "active_server", None)
    monkeypatch.setattr(settings.config, "model_selection", {})
    monkeypatch.setattr(settings.config, "efforts", {})
    monkeypatch.setattr(settings.config, "_save_state", lambda: None)
    return settings.config


class _Bar:
    def set_values(self, values):
        self.values = values

    def set_field_colors(self, colors):
        self.colors = colors


def _ui(endpoint=None):
    messages, switched = [], []
    agent = SimpleNamespace(endpoint=endpoint, _last_usage=None, role=None, state=None,
                            workspace=".", _sandbox_runtime="none", sandboxed=lambda: False,
                            switch_server=switched.append)
    panel = SimpleNamespace(add_message=lambda text, **k: messages.append(text))
    ui = SimpleNamespace(agent=agent, chat_history_panel=panel, status_bar=_Bar(),
                         _status_spinner_frame=0)
    return ui, messages, switched


def test_fresh_state_selects_the_top_of_model_list(cfg, monkeypatch):
    monkeypatch.setattr("moka_code.ui.commands.base.activate_endpoint",
                        lambda ui, endpoint: ui.agent.switch_server(endpoint))
    cfg.models_by_server = {"a": [], "b": [{"id": "m1"}, {"id": "m2"}]}
    ui, messages, switched = _ui()

    auto_select(ui)

    assert (cfg.active_server, cfg.model_selection) == ("b", {"b": "m1"})
    assert switched[0].name == "b" and switched[0].selected_model == "m1"
    assert "Selected m1 on b" in messages[-1]


def test_nothing_available_selects_nothing(cfg):
    ui, messages, switched = _ui()
    auto_select(ui)
    assert cfg.active_server is None and not switched and not messages


def _activating(monkeypatch):
    monkeypatch.setattr("moka_code.ui.commands.base.activate_endpoint",
                        lambda ui, endpoint: ui.agent.switch_server(endpoint))


def test_available_previous_model_is_kept(cfg, monkeypatch):
    _activating(monkeypatch)
    cfg.active_server, cfg.model_selection = "b", {"b": "m2"}
    cfg.models_by_server = {"a": [{"id": "x"}], "b": [{"id": "m1"}, {"id": "m2"}]}
    ui, messages, switched = _ui()
    auto_select(ui)
    assert (cfg.active_server, cfg.model_selection) == ("b", {"b": "m2"})
    assert not switched and not messages


def test_model_gone_from_its_live_server_selects_that_servers_first(cfg, monkeypatch):
    _activating(monkeypatch)
    cfg.active_server, cfg.model_selection = "b", {"b": "gone"}
    cfg.models_by_server = {"a": [{"id": "x"}], "b": [{"id": "m2"}, {"id": "m1"}]}
    ui, messages, switched = _ui()
    auto_select(ui)
    assert (cfg.active_server, cfg.model_selection["b"]) == ("b", "m1")
    assert "Selected m1 on b (gone is no longer served by b; /model to change)" in messages[-1]


def test_server_removed_from_config_selects_the_first_available(cfg, monkeypatch):
    _activating(monkeypatch)
    cfg.active_server, cfg.model_selection = "openrouter", {"openrouter": "m"}
    cfg.models_by_server = {"a": [{"id": "x"}], "b": [{"id": "m1"}]}
    ui, messages, switched = _ui()
    auto_select(ui)
    assert (cfg.active_server, cfg.model_selection["a"]) == ("a", "x")
    assert "server 'openrouter' is not in servers.toml" in messages[-1]


def test_a_server_that_cannot_be_listed_keeps_its_selection(cfg, monkeypatch):
    """Stale (down now) or never listed: the server's answer is unknown, so a
    blip never switches servers; stale servers are not a choice either."""
    _activating(monkeypatch)
    cfg.active_server, cfg.model_selection = "a", {"a": "m"}
    cfg.models_by_server = {"a": [{"id": "other"}], "b": [{"id": "m1"}]}
    cfg.stale_servers = {"a"}
    ui, messages, switched = _ui()
    auto_select(ui)
    assert (cfg.active_server, cfg.model_selection) == ("a", {"a": "m"})
    cfg.stale_servers, cfg.models_by_server = set(), {"b": [{"id": "m1"}]}   # never listed
    auto_select(ui)
    assert cfg.active_server == "a" and not switched and not messages


def test_stale_servers_are_never_picked(cfg, monkeypatch):
    _activating(monkeypatch)
    cfg.models_by_server = {"a": [{"id": "x"}], "b": [{"id": "m1"}]}
    cfg.stale_servers = {"a"}
    ui, _, _ = _ui()
    auto_select(ui)
    assert cfg.active_server == "b"


def _endpoint(name, model):
    endpoint = LlamaCpp(name=name, base_url=f"http://{name}/v1", model=model)
    endpoint._connection_state = "ok"
    return endpoint


def test_unlisted_model_is_red_until_auto_select_runs(cfg):
    cfg.active_server, cfg.model_selection = "a", {"a": "gone"}
    cfg.models_by_server = {"a": [{"id": "other"}]}
    ui, _, _ = _ui(_endpoint("a", "gone"))

    refresh_status_bar(ui)

    assert ui.status_bar.values["endpoint_model"] == "a:gone"
    assert ui.status_bar.colors["endpoint_model"] == theme.ERROR
    assert ("error", "gone is no longer served by a → /model") in notices(ui.agent)


def test_missing_server_is_red_until_auto_select_runs(cfg):
    cfg.active_server, cfg.model_selection = "gone", {"gone": "m"}
    ui, _, _ = _ui(None)

    refresh_status_bar(ui)

    assert ui.status_bar.values["endpoint_model"] == "gone:m"
    assert ui.status_bar.colors["endpoint_model"] == theme.ERROR
    assert any("'gone' is not in servers.toml" in text for _, text in notices(ui.agent))
