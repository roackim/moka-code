"""Tests for the sandbox and context status-bar fields."""

from types import SimpleNamespace

from moka_code.ui.status_presenter import _resolve_color, refresh_status_bar
from moka_code.ui.tui.colors import theme


class _Bar:
    def __init__(self):
        self.values = {}
        self.colors = {}

    def set_values(self, values):
        self.values = values

    def set_field_colors(self, colors):
        self.colors = colors


def _app(sandboxed, runtime="none"):
    endpoint = SimpleNamespace(
        name="local",
        model="q",
        selected_model="q",
        _connection_state="ok",
        max_context=32768,
        _model_name_pending=False,
        _original_base_url="http://localhost:8080/v1",
    )
    agent = SimpleNamespace(
        endpoint=endpoint,
        _last_usage=None,
        role=None,
        state=None,
        workspace=".",
        _sandbox_runtime=runtime,
        sandboxed=lambda: sandboxed,
    )
    return SimpleNamespace(agent=agent, status_bar=_Bar(), _status_spinner_frame=0)


def test_sandbox_field_active_is_green():
    app = _app(True, runtime="podman")
    refresh_status_bar(app)

    assert app.status_bar.values["sandbox"] == "⬢ sandbox:podman"
    assert app.status_bar.colors["sandbox"] == theme.SUCCESS


def test_sandbox_field_bare_is_orange():
    app = _app(False, runtime="none")
    refresh_status_bar(app)

    assert app.status_bar.values["sandbox"] == "⬢ sandbox:none"
    assert app.status_bar.colors["sandbox"] == theme.WARNING


def test_resolve_color_accepts_name_hex_and_fallback():
    assert _resolve_color("SUCCESS", theme.DEFAULT) is theme.SUCCESS
    hexed = _resolve_color("#112233", theme.DEFAULT)
    assert (hexed.r, hexed.g, hexed.b) == (0x11, 0x22, 0x33)
    assert _resolve_color("NOPE", theme.DEFAULT) == theme.DEFAULT
    assert _resolve_color("", theme.WARNING) == theme.WARNING


def test_unknown_context_window_shows_only_the_tokens_used():
    """ISSUES P4 (2026-10-01): no invented 32768 maximum."""
    app = _app(True)
    app.agent.endpoint.context_window = lambda: None
    app.agent._last_usage = SimpleNamespace(prompt_tokens=1500)
    refresh_status_bar(app)
    assert app.status_bar.values["context"] == "ctx 1.5k"

    app.agent.endpoint.context_window = lambda: 131072
    refresh_status_bar(app)
    assert app.status_bar.values["context"] == "ctx 1.5k/128k"
