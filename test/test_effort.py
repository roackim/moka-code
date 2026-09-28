"""Reasoning effort: ``efforts`` in servers.toml, ``/effort``, request fields."""

import asyncio
from types import SimpleNamespace

from moka_code import settings
from moka_code.harness.endpoint import Endpoint
from moka_code.settings import Config
from moka_code.ui.commands import models


def _endpoint(type_, **kw):
    return Endpoint(name="s", type=type_, model="m", **kw)


def test_effort_payload_uses_each_servers_field():
    for type_, expected in [
        ("llamacpp", {"reasoning_effort": "high"}),
        ("openai", {"reasoning_effort": "high"}),
        ("openrouter", {"reasoning": {"effort": "high"}}),
        ("ollama", {"think": "high"}),
    ]:
        endpoint = _endpoint(type_, efforts=["low", "high"])
        endpoint.effort = "high"
        assert endpoint.effort_payload() == expected


def test_effort_payload_is_never_dropped():
    endpoint = _endpoint("ollama", efforts=["low"])
    assert endpoint.effort_payload() == {}            # nothing chosen
    endpoint.effort = "high"          # not (or no longer) declared: still sent
    assert endpoint.effort_payload() == {"think": "high"}
    endpoint.effort = "none"
    assert endpoint.effort_payload() == {"think": False}


def test_model_table_efforts_override_the_server():
    endpoint = _endpoint("openrouter", efforts=["low"], models={"m": {"efforts": ["max"]}})
    assert endpoint.effort_levels() == ["max"]


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
    _config(monkeypatch, {"s": {"type": "ollama", "efforts": ["low", "high"]}})
    endpoint = _endpoint("ollama", efforts=["low", "high"])
    ui, messages = _ui(endpoint)

    asyncio.run(models.effort_command(ui, ["high"]))
    assert endpoint.effort == "high"
    assert settings.config.get_effort("s", "m") == "high"

    asyncio.run(models.effort_command(ui, ["default"]))
    assert endpoint.effort is None

    asyncio.run(models.effort_command(ui, ["max"]))
    assert "Unknown effort" in messages[-1]


def test_effort_command_without_levels_explains(monkeypatch):
    _config(monkeypatch, {"s": {"type": "ollama"}})
    ui, messages = _ui(_endpoint("ollama"))
    asyncio.run(models.effort_command(ui, []))
    assert "efforts" in messages[-1]


def test_effort_inline_menu_and_picker_mark_the_active_level(monkeypatch):
    _config(monkeypatch, {"s": {"type": "ollama", "efforts": ["low", "high"]}},
            efforts={"s": {"m": "high"}})
    assert models.effort_completions() == ["default", "low", "high"]
    assert models.effort_descriptions()["high"] == "active"

    ui, _ = _ui(_endpoint("ollama", efforts=["low", "high"]))
    captured = {}
    ui.show_search_modal = lambda title, items, **k: captured.update(items=items, **k)
    asyncio.run(models.effort_command(ui, []))
    assert captured["footers"] == {"high": "active"}
    assert captured["initial_index"] == 2


def test_efforts_detected_from_catalog_metadata(monkeypatch):
    from moka_code.harness.endpoint_discovery import efforts_from_metadata

    assert efforts_from_metadata({"supported_parameters": ["tools", "reasoning"]}) == [
        "none", "low", "medium", "high"]
    assert efforts_from_metadata({"capabilities": ["completion", "thinking"], "model": "qwen3"}) == ["none"]
    assert efforts_from_metadata({"capabilities": ["thinking"], "model": "gpt-oss:20b"})[-1] == "high"
    assert efforts_from_metadata({"capabilities": ["completion"]}) == []

    _config(monkeypatch, {"s": {"type": "openrouter"}},
            catalog={"s": [{"id": "m", "metadata": {"supported_parameters": ["reasoning"]}}]})
    endpoint = _endpoint("openrouter")
    assert endpoint.effort_levels()[0] == "none"
    endpoint.efforts = ["high"]       # explicit servers.toml wins
    assert endpoint.effort_levels() == ["high"]
    assert models.effort_completions() == ["default", "none", "low", "medium", "high"]


def test_effort_variants_switch_the_model(monkeypatch):
    _config(monkeypatch, {"s": {"type": "ollama"}}, catalog={"s": [
        {"id": "Qwen:low"}, {"id": "Qwen:high"}, {"id": "Qwen:medium"}, {"id": "other"}]})
    monkeypatch.setattr(settings.config, "model_selection", {"s": "Qwen:low"})
    assert models.effort_completions() == ["low", "medium", "high"]
    assert models.effort_descriptions()["low"] == "Qwen:low  active"

    selected = []
    monkeypatch.setattr(models, "_select_pair", lambda ui, server, model: selected.append((server, model)))
    ui, _ = _ui(Endpoint(name="s", type="ollama", model="Qwen:low"))
    asyncio.run(models.effort_command(ui, ["high"]))
    assert selected == [("s", "Qwen:high")]


def test_exact_efforts_from_reasoning_supported_efforts():
    from moka_code.harness.endpoint_discovery import efforts_from_metadata

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
    from moka_code.harness import endpoint_discovery

    entry = {"id": "q", "context_length": 128000,
             "reasoning": {"supported_efforts": ["low", "high"]}}

    class Client:
        async def get(self, path):
            return SimpleNamespace(raise_for_status=lambda: None,
                                   json=lambda: {"data": [entry]})

    endpoint = SimpleNamespace(type="openai", client=Client(), timeout=5)
    [model] = asyncio.run(endpoint_discovery.list_models(endpoint))
    assert endpoint_discovery.efforts_from_metadata(model.metadata) == ["low", "high"]


def test_endpoint_reads_the_live_catalog_not_a_copy(monkeypatch):
    """A catalog refreshed after the endpoint was built is what it reads."""
    _config(monkeypatch, {"s": {"type": "openai"}}, catalog={"s": [{"id": "m"}]})
    endpoint = _endpoint("openai")
    assert endpoint.effort_levels() == []

    settings.config.models_by_server["s"] = [
        {"id": "m", "metadata": {"reasoning": {"supported_efforts": ["low", "high"]}}}]
    assert endpoint.effort_levels() == ["low", "high"]


def test_effort_missing_from_the_catalog_is_still_sent_and_shown(monkeypatch):
    """A catalog without the saved level (stale, or the server answered
    without its reasoning object) must not drop or hide the effort."""
    _config(monkeypatch, {"s": {"type": "openai"}}, catalog={"s": [{"id": "m"}]},
            efforts={"s": {"m": "low"}})
    endpoint = _endpoint("openai")
    endpoint.effort = "low"
    assert endpoint.effort_payload() == {"reasoning_effort": "low"}
    assert models.effort_completions() == ["default", "low"]
    assert models.effort_descriptions()["low"] == "active"


def test_effort_is_sent_while_the_server_is_undiscovered(monkeypatch):
    _config(monkeypatch, {"s": {"type": "openai"}})
    endpoint = _endpoint("openai")
    endpoint.effort = "low"
    assert endpoint.effort_payload() == {"reasoning_effort": "low"}   # the server decides


def test_effort_command_rediscovers_the_active_server(monkeypatch):
    _config(monkeypatch, {"s": {"type": "openai"}})
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
    _config(monkeypatch, {"s": {"type": "openai"}, "never": {"type": "openai"}},
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
    _config(monkeypatch, {"s": {"type": "openai"}}, catalog={"s": [{"id": "old"}]})
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

    _config(monkeypatch, {"s": {"type": "ollama"}, "or": {"type": "openrouter"},
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


def test_only_reload_and_servers_edits_rediscover(monkeypatch):
    from moka_code.ui.commands import base

    refreshed = []

    async def discover(names=None):
        refreshed.append(names)
    monkeypatch.setattr("moka_code.harness.endpoint.refresh_catalog", discover)
    ui = SimpleNamespace(agent=SimpleNamespace(endpoint=None),
                         chat_history_panel=SimpleNamespace(add_message=lambda *a, **k: None))

    async def scenario(**kw):
        base.reapply_endpoint(ui, **kw)
        await asyncio.sleep(0)
    asyncio.run(scenario())
    assert refreshed == []
    asyncio.run(scenario(rediscover=True))
    assert refreshed == [None]
