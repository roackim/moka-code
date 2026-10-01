"""Reasoning effort: levels detected from the catalog, ``/effort``, request
fields. Decided 2026-09-30: no ``efforts`` key in servers.toml; levels come
only from detection."""

import asyncio
from types import SimpleNamespace

from moka_code import settings
from moka_code.harness.endpoint import efforts_from_metadata
from moka_code.settings import Config
from moka_code.ui.commands import models
from moka_code.harness.providers import REGISTRY, Ollama, OpenAICompatible


def _endpoint(type_, **kw):
    return REGISTRY[type_](name="s", base_url="http://s/v1", model="m", **kw)


def test_effort_payload_uses_each_servers_field():
    for type_, expected in [
        ("llamacpp", {"reasoning_effort": "high"}),
        ("openai", {"reasoning_effort": "high"}),
        ("openrouter", {"reasoning": {"effort": "high"}}),
        ("ollama", {"think": "high"}),
    ]:
        endpoint = _endpoint(type_)
        endpoint.effort = "high"
        assert endpoint.effort_payload() == expected


def test_effort_payload_is_never_dropped():
    endpoint = _endpoint("ollama")
    assert endpoint.effort_payload() == {}            # nothing chosen
    endpoint.effort = "high"          # not (or no longer) declared: still sent
    assert endpoint.effort_payload() == {"think": "high"}
    endpoint.effort = "none"
    assert endpoint.effort_payload() == {"think": False}


def test_efforts_key_in_a_model_table_is_reported(tmp_path):
    (tmp_path / "servers.toml").write_text(
        '[servers.openrouter.models."m"]\nefforts = ["max"]\n')
    config = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")
    assert any("unknown key 'efforts'" in e for e in config.load_errors)


def test_effort_state_round_trip(tmp_path):
    config = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")
    config.save_effort("ollama", "qwen:low", "high")
    config.save_effort("local", "", "low")
    reloaded = Config(config_dir=tmp_path, state_path=tmp_path / "state.toml")
    assert reloaded.load_errors == []
    assert reloaded.get_effort("ollama", "qwen:low") == "high"
    assert reloaded.get_effort("local", None) == "low"
    reloaded.save_effort("ollama", "qwen:low", None)
    assert "ollama" not in reloaded.efforts


def _ui(endpoint):
    messages = []
    panel = SimpleNamespace(add_message=lambda text, **k: messages.append(text))
    return SimpleNamespace(agent=SimpleNamespace(endpoint=endpoint),
                           chat_history_panel=panel), messages


def _config(monkeypatch, servers, catalog=None, efforts=None):
    monkeypatch.setattr(settings.config, "servers", servers)
    monkeypatch.setattr(settings.config, "models_by_server", catalog or {})
    monkeypatch.setattr(settings.config, "efforts", efforts or {})
    monkeypatch.setattr(settings.config, "active_server", "s")
    monkeypatch.setattr(settings.config, "model_selection", {"s": "m"})
    monkeypatch.setattr(settings.config, "_save_state", lambda: None)

    async def no_network(names=None):
        pass
    monkeypatch.setattr("moka_code.harness.endpoint.refresh_catalog", no_network)


def test_effort_command_sets_and_persists(monkeypatch):
    _config(monkeypatch, {"s": {"type": "ollama", "base_url": "http://s/v1"}},
            catalog={"s": [{"id": "m", "metadata": {"reasoning": {"supported_efforts": ["low", "high"]}}}]})
    endpoint = _endpoint("ollama")
    ui, messages = _ui(endpoint)

    asyncio.run(models.effort_command(ui, ["high"]))
    assert endpoint.effort == "high"
    assert settings.config.get_effort("s", "m") == "high"

    asyncio.run(models.effort_command(ui, ["default"]))
    assert endpoint.effort is None

    asyncio.run(models.effort_command(ui, ["max"]))
    assert "Unknown effort" in messages[-1]


def test_effort_command_without_levels_explains(monkeypatch):
    _config(monkeypatch, {"s": {"type": "ollama", "base_url": "http://s/v1"}})
    ui, messages = _ui(_endpoint("ollama"))
    asyncio.run(models.effort_command(ui, []))
    assert "No reasoning effort detected" in messages[-1]


def test_effort_inline_menu_and_picker_mark_the_active_level(monkeypatch):
    _config(monkeypatch, {"s": {"type": "ollama", "base_url": "http://s/v1"}},
            catalog={"s": [{"id": "m", "metadata": {"reasoning": {"supported_efforts": ["low", "high"]}}}]},
            efforts={"s": {"m": "high"}})
    assert models.effort_completions() == ["default", "low", "high"]
    assert models.effort_descriptions()["high"] == "active"

    ui, _ = _ui(_endpoint("ollama"))
    captured = {}
    ui.show_search_modal = lambda title, items, **k: captured.update(items=items, **k)
    asyncio.run(models.effort_command(ui, []))
    assert captured["footers"] == {"high": "active"}
    assert captured["initial_index"] == 2


def test_efforts_detected_from_catalog_metadata(monkeypatch):
    assert efforts_from_metadata({"supported_parameters": ["tools", "reasoning"]}) == [
        "none", "low", "medium", "high"]
    assert efforts_from_metadata({"capabilities": ["completion", "thinking"], "model": "qwen3"}) == ["none"]
    assert efforts_from_metadata({"capabilities": ["thinking"], "model": "gpt-oss:20b"})[-1] == "high"
    assert efforts_from_metadata({"capabilities": ["completion"]}) == []

    _config(monkeypatch, {"s": {"type": "openrouter"}},
            catalog={"s": [{"id": "m", "metadata": {"supported_parameters": ["reasoning"]}}]})
    assert models.effort_completions() == ["default", "none", "low", "medium", "high"]


def test_effort_variants_switch_the_model(monkeypatch):
    _config(monkeypatch, {"s": {"type": "ollama", "base_url": "http://s/v1"}}, catalog={"s": [
        {"id": "Qwen:low"}, {"id": "Qwen:high"}, {"id": "Qwen:medium"}, {"id": "other"}]})
    monkeypatch.setattr(settings.config, "model_selection", {"s": "Qwen:low"})
    assert models.effort_completions() == ["low", "medium", "high"]
    assert models.effort_descriptions()["low"] == "Qwen:low  active"

    selected = []
    monkeypatch.setattr(models, "_select_pair", lambda ui, server, model: selected.append((server, model)))
    ui, _ = _ui(Ollama(name="s", base_url="http://s/v1", model="Qwen:low"))
    asyncio.run(models.effort_command(ui, ["high"]))
    assert selected == [("s", "Qwen:high")]


def test_exact_efforts_from_reasoning_supported_efforts():
    # OpenRouter lists strongest first; /effort offers weakest first.
    assert efforts_from_metadata({
        "supported_parameters": ["reasoning", "reasoning_effort"],
        "reasoning": {"mandatory": False, "supported_efforts": ["max", "high", "low"]},
    }) == ["low", "high", "max"]
    # No level list: fall back to the supported_parameters guess.
    assert efforts_from_metadata({
        "supported_parameters": ["reasoning"], "reasoning": {"mandatory": False},
    })[0] == "none"


def test_openai_catalog_keeps_model_metadata():
    entry = {"id": "q", "context_length": 128000,
             "reasoning": {"supported_efforts": ["low", "high"]}}

    class Client:
        async def get(self, path):
            return SimpleNamespace(raise_for_status=lambda: None,
                                   json=lambda: {"data": [entry]})

    endpoint = OpenAICompatible(name="s", base_url="http://s/v1")
    endpoint.client = Client()
    [model] = asyncio.run(endpoint.list_models())
    assert efforts_from_metadata(model.metadata) == ["low", "high"]


def test_effort_levels_read_the_live_catalog_not_a_copy(monkeypatch):
    """A catalog refreshed after the first read is what /effort offers."""
    _config(monkeypatch, {"s": {"type": "openai", "base_url": "http://s/v1"}}, catalog={"s": [{"id": "m"}]})
    assert models.effort_completions() == []

    settings.config.models_by_server["s"] = [
        {"id": "m", "metadata": {"reasoning": {"supported_efforts": ["low", "high"]}}}]
    assert models.effort_completions() == ["default", "low", "high"]


def test_effort_missing_from_the_catalog_is_still_sent_and_shown(monkeypatch):
    """A catalog without the saved level (stale, or the server answered
    without its reasoning object) must not drop or hide the effort."""
    _config(monkeypatch, {"s": {"type": "openai", "base_url": "http://s/v1"}}, catalog={"s": [{"id": "m"}]},
            efforts={"s": {"m": "low"}})
    endpoint = _endpoint("openai")
    endpoint.effort = "low"
    assert endpoint.effort_payload() == {"reasoning_effort": "low"}
    assert models.effort_completions() == ["default", "low"]
    assert models.effort_descriptions()["low"] == "active"


def test_effort_is_sent_while_the_server_is_undiscovered(monkeypatch):
    _config(monkeypatch, {"s": {"type": "openai", "base_url": "http://s/v1"}})
    endpoint = _endpoint("openai")
    endpoint.effort = "low"
    assert endpoint.effort_payload() == {"reasoning_effort": "low"}   # the server decides


def test_effort_command_rediscovers_the_active_server(monkeypatch):
    _config(monkeypatch, {"s": {"type": "openai", "base_url": "http://s/v1"}})
    refreshed = []

    async def discover(names=None):
        refreshed.append(names)
        settings.config.models_by_server["s"] = [
            {"id": "m", "metadata": {"reasoning": {"supported_efforts": ["low", "high"]}}}]
    monkeypatch.setattr("moka_code.harness.endpoint.refresh_catalog", discover)

    endpoint = _endpoint("openai")
    ui, _ = _ui(endpoint)
    asyncio.run(models.effort_command(ui, ["high"]))
    assert refreshed == [["s"]]
    assert endpoint.effort_payload() == {"reasoning_effort": "high"}


def test_refresh_keeps_the_last_discovery_marked_stale(monkeypatch):
    """A busy/down server keeps this session's last good entry, flagged."""
    from moka_code.harness import endpoint as endpoint_mod
    from moka_code.harness.endpoint import ModelInfo

    refresh_catalog = endpoint_mod.refresh_catalog     # before _config stubs it
    _config(monkeypatch, {"s": {"type": "openai", "base_url": "http://s/v1"}, "never": {"type": "openai", "base_url": "http://s/v1"}},
            catalog={"s": [{"id": "m", "context_window": 128000}]})
    monkeypatch.setattr(settings.config, "stale_servers", set())
    up = {"ok": False}

    async def discover(self):
        if not up["ok"]:
            raise ConnectionError("busy")
        return [ModelInfo(id="m", context_window=64000)]
    monkeypatch.setattr(endpoint_mod.Endpoint, "discover_models", discover)

    asyncio.run(refresh_catalog(["s", "never"]))
    assert settings.config.models_by_server["s"] == [{"id": "m", "context_window": 128000}]
    assert settings.config.stale_servers == {"s"}
    assert "never" not in settings.config.models_by_server      # nothing to keep
    assert _endpoint("openai").context_window() == 128000
    assert models.model_descriptions()["m"] == "s  125k  unreachable  active"

    up["ok"] = True
    asyncio.run(refresh_catalog(["s"]))
    assert settings.config.models_by_server["s"][0]["context_window"] == 64000
    assert settings.config.stale_servers == set()


def test_an_older_refresh_finishing_late_does_not_win(monkeypatch):
    from moka_code.harness import endpoint as endpoint_mod
    from moka_code.harness.endpoint import ModelInfo

    refresh_catalog = endpoint_mod.refresh_catalog
    _config(monkeypatch, {"s": {"type": "openai", "base_url": "http://s/v1"}}, catalog={"s": [{"id": "old"}]})
    monkeypatch.setattr(settings.config, "stale_servers", set())
    calls = []

    async def discover(self):
        calls.append(None)
        if len(calls) == 1:                 # the first refresh is slow, then fails
            await asyncio.sleep(0.05)
            raise ConnectionError("busy")
        return [ModelInfo(id="new")]
    monkeypatch.setattr(endpoint_mod.Endpoint, "discover_models", discover)

    async def scenario():
        slow = asyncio.create_task(refresh_catalog(["s"]))
        await asyncio.sleep(0)
        await refresh_catalog(["s"])        # e.g. /effort right after /model
        await slow
    asyncio.run(scenario())
    assert [m["id"] for m in settings.config.models_by_server["s"]] == ["new"]
    assert settings.config.stale_servers == set()


def test_unserved_model_is_reported_only_when_the_list_is_reliable(monkeypatch):
    from moka_code.harness.endpoint import unserved_model

    _config(monkeypatch, {"s": {"type": "ollama", "base_url": "http://s/v1"}, "or": {"type": "openrouter"},
                          "ll": {"type": "llamacpp"}},
            catalog={"s": [{"id": "qwen:latest"}, {"id": "Qwen3.8-27B"}],
                     "or": [{"id": "deepseek/deepseek-v4.1-flash"}],
                     "ll": [{"id": "whatever.gguf"}]})
    monkeypatch.setattr(settings.config, "stale_servers", set())
    assert unserved_model("s", "Qwen3.8-27B:high") == ["qwen:latest", "Qwen3.8-27B"]
    assert unserved_model("s", "qwen") is None                  # implicit :latest
    assert unserved_model("or", "deepseek-v4.1-flash") is None   # bare OpenRouter id
    assert unserved_model("ll", "requested") is None             # llama.cpp serves one
    assert unserved_model("never", "m") is None                  # not discovered
    settings.config.stale_servers.add("s")
    assert unserved_model("s", "Qwen3.8-27B:high") is None       # stale: can't tell


def test_every_reload_rediscovers_every_server(monkeypatch):
    from moka_code.ui.commands import base

    refreshed = []

    async def discover(names=None):
        refreshed.append(names)
    monkeypatch.setattr("moka_code.harness.endpoint.refresh_catalog", discover)
    ui = SimpleNamespace(agent=SimpleNamespace(endpoint=None),
                         chat_history_panel=SimpleNamespace(add_message=lambda *a, **k: None))

    async def scenario():
        base.reapply_endpoint(ui)
        await asyncio.sleep(0)
    asyncio.run(scenario())
    assert refreshed == [None]
