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


def test_effort_payload_empty_without_a_declared_level():
    endpoint = _endpoint("ollama", efforts=["low"])
    assert endpoint.effort_payload() == {}
    endpoint.effort = "high"          # not (or no longer) declared
    assert endpoint.effort_payload() == {}
    endpoint.efforts = ["none"]
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

    endpoint = _endpoint("openrouter")
    endpoint._model_metadata["m"] = {"supported_parameters": ["reasoning"]}
    assert endpoint.effort_levels()[0] == "none"
    endpoint.efforts = ["high"]       # explicit servers.toml wins
    assert endpoint.effort_levels() == ["high"]

    _config(monkeypatch, {"s": {"type": "openrouter"}},
            catalog={"s": [{"id": "m", "metadata": {"supported_parameters": ["reasoning"]}}]})
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
