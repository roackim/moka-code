"""Tests for the sandbox and context status-bar fields."""

from moka_code import settings
from moka_code.harness.usage import TokenUsage
from types import SimpleNamespace

from moka_code.ui.status_presenter import refresh_status_bar
from moka_code.ui.tui.colors import resolve_color, theme


class _Bar:
    def __init__(self):
        self.values = {}
        self.colors = {}

    def set_values(self, values):
        self.values = values

    def set_field_colors(self, colors):
        self.colors = colors


def _app(sandboxed, runtime="none", name=None, effort=None):
    endpoint = SimpleNamespace(
        name="local",
        model="q",
        selected_model="q",
        effort=effort,
        _connection_state="ok",
        _original_base_url="http://localhost:8080/v1",
    )
    agent = SimpleNamespace(
        endpoint=endpoint,
        _last_usage=None,
        role=None,
        state=None,
        workspace=".",
        _sandbox_runtime=runtime,
        sandbox_name=name,
        sandboxed=lambda: sandboxed,
    )
    return SimpleNamespace(agent=agent, status_bar=_Bar(), _status_spinner_frame=0)


def test_sandbox_field_active_is_green():
    app = _app(True, runtime="podman")
    refresh_status_bar(app)

    assert app.status_bar.values["sandbox"] == "⬢ sandbox:podman"
    assert app.status_bar.colors["sandbox"] == theme.SUCCESS


def test_refresh_reapplies_status_bar_field_config(monkeypatch):
    from moka_code.ui.tui.components.bars import StatusBar

    app = _app(True)
    app.status_bar = StatusBar(fields=["endpoint_model"])
    monkeypatch.setattr(settings.config, "ui_status_bar_fields",
                        ["role", "endpoint_model"])
    monkeypatch.setattr(settings.config, "ui_status_bar_separator", " | ")

    refresh_status_bar(app)

    assert app.status_bar.fields == ["role", "endpoint_model"]
    assert app.status_bar.separator == " | "


def test_model_field_provider_and_effort_are_configurable(monkeypatch):
    app = _app(True, effort="high")
    refresh_status_bar(app)
    assert app.status_bar.values["endpoint_model"] == "local:q:high"

    monkeypatch.setattr(settings.config, "ui_show_provider", False)
    refresh_status_bar(app)
    assert app.status_bar.values["endpoint_model"] == "q:high"

    monkeypatch.setattr(settings.config, "ui_show_provider", True)
    monkeypatch.setattr(settings.config, "ui_provider_separator", "/")
    monkeypatch.setattr(settings.config, "ui_show_effort", False)
    refresh_status_bar(app)
    assert app.status_bar.values["endpoint_model"] == "local/q"

    monkeypatch.setattr(settings.config, "ui_show_effort", True)
    monkeypatch.setattr(settings.config, "ui_effort_separator", "@")
    refresh_status_bar(app)
    assert app.status_bar.values["endpoint_model"] == "local/q@high"


def test_sandbox_field_shows_name_instead_of_runtime():
    app = _app(True, runtime="podman", name="gpu-box")
    refresh_status_bar(app)

    assert app.status_bar.values["sandbox"] == "⬢ sandbox:gpu-box"
    assert app.status_bar.colors["sandbox"] == theme.SUCCESS


def test_sandbox_field_bare_is_orange():
    app = _app(False, runtime="none")
    refresh_status_bar(app)

    assert app.status_bar.values["sandbox"] == "⬢ sandbox:none"
    assert app.status_bar.colors["sandbox"] == theme.WARNING


def test_resolve_color_accepts_name_hex_and_fallback():
    assert resolve_color("SUCCESS", theme.DEFAULT) is theme.SUCCESS
    hexed = resolve_color("#112233", theme.DEFAULT)
    assert (hexed.r, hexed.g, hexed.b) == (0x11, 0x22, 0x33)
    assert resolve_color("NOPE", theme.DEFAULT) == theme.DEFAULT
    assert resolve_color("", theme.WARNING) == theme.WARNING


def test_unknown_context_window_shows_only_the_tokens_used():
    """ISSUES P4 (2026-10-01): no invented 32768 maximum."""
    app = _app(True)
    app.agent.endpoint.context_window = lambda: None
    app.agent._last_usage = TokenUsage(prompt_tokens=1000, completion_tokens=500)
    refresh_status_bar(app)
    assert app.status_bar.values["context"] == "ctx 1.5k"

    app.agent.endpoint.context_window = lambda: 131072
    refresh_status_bar(app)
    assert app.status_bar.values["context"] == "ctx 1.5k/128k"


def test_without_a_server_count_the_status_bar_shows_the_estimate_marked_approximate():
    app = _app(True)
    app.agent.endpoint.context_window = lambda: 131072
    app.agent._last_usage = None
    app.agent.context_estimate = lambda: 3000
    refresh_status_bar(app)
    assert app.status_bar.values["context"] == "ctx ~3.0k/128k"
