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


def test_effort_command_sets_and_persists(monkeypatch):
    saved = []
    monkeypatch.setattr(settings.config, "save_effort", lambda *a: saved.append(a))
    endpoint = _endpoint("ollama", efforts=["low", "high"])
    ui, messages = _ui(endpoint)

    asyncio.run(models.effort_command(ui, ["high"]))
    assert endpoint.effort == "high"
    assert saved == [("s", "m", "high")]

    asyncio.run(models.effort_command(ui, ["default"]))
    assert endpoint.effort is None

    asyncio.run(models.effort_command(ui, ["max"]))
    assert "Unknown effort" in messages[-1]


def test_effort_command_without_levels_explains(monkeypatch):
    ui, messages = _ui(_endpoint("ollama"))
    asyncio.run(models.effort_command(ui, []))
    assert "efforts" in messages[-1]


def test_effort_picker_marks_the_active_level():
    endpoint = _endpoint("ollama", efforts=["low", "high"])
    endpoint.effort = "high"
    ui, _ = _ui(endpoint)
    captured = {}
    ui.show_search_modal = lambda title, items, **k: captured.update(items=items, **k)
    asyncio.run(models.effort_command(ui, []))
    assert captured["items"] == ["default", "low", "high"]
    assert captured["footers"] == {"high": "active"}
    assert captured["initial_index"] == 2
