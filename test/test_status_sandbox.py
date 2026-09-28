"""Tests for the sandbox status-bar field."""

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
        _cached_model_name="q",
        _connection_state="ok",
        _cached_context_window=32768,
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
