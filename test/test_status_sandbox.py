"""Tests for the sandbox and context status-bar fields."""

from moka_code import settings
from moka_code.harness.usage import TokenUsage
from types import SimpleNamespace

from moka_code.ui.status_presenter import refresh_status_bar
from moka_code.ui.tui.colors import resolve_color, theme
from moka_code.ui.tui.components.bars import StatusBar


class _Bar(StatusBar):
    """The real bar, with the default template; ``colors`` for short."""

    def __init__(self):
        super().__init__(template=settings.DEFAULT_STATUS_BAR)

    @property
    def colors(self):
        return self.field_colors


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

    assert "sandbox:podman" in app.status_bar.text()
    assert app.status_bar.colors["sandbox"] == theme.SUCCESS


def test_refresh_reapplies_the_status_bar_template(monkeypatch):
    app = _app(True)
    monkeypatch.setattr(settings.config, "ui_status_bar", "{role} | {model}")

    refresh_status_bar(app)

    assert app.status_bar.template == "{role} | {model}"
    assert app.status_bar.text() == "agent q"


def test_default_template_shows_model_and_effort(monkeypatch):
    app = _app(True, effort="high")
    refresh_status_bar(app)
    # No cost reported yet: its group goes, separator included; no shell
    # running, so the right part is only the sandbox.
    assert app.status_bar.text() == "q:high  mode:agent  ctx 0 sandbox:sandbox"
    assert "".join(t for t, _, _ in app.status_bar.runs()[1]).strip() == "sandbox:sandbox"

    app.agent.endpoint.effort = None
    refresh_status_bar(app)
    assert app.status_bar.text().startswith("q  mode:agent")

    monkeypatch.setattr(settings.config, "ui_status_bar", "{model}[@{effort}]")
    app.agent.endpoint.effort = "low"
    refresh_status_bar(app)
    assert app.status_bar.text() == "q@low"


def test_sandbox_field_shows_name_instead_of_runtime():
    app = _app(True, runtime="podman", name="gpu-box")
    refresh_status_bar(app)

    assert "sandbox:gpu-box" in app.status_bar.text()
    assert app.status_bar.colors["sandbox"] == theme.SUCCESS


def test_sandbox_field_bare_is_orange():
    app = _app(False, runtime="none")
    refresh_status_bar(app)

    assert "sandbox:none" in app.status_bar.text()
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
    assert app.status_bar.values["context"] == "1.5k"

    app.agent.endpoint.context_window = lambda: 131072
    refresh_status_bar(app)
    assert app.status_bar.values["context"] == "1.5k/128k"
    assert "  ctx 1.5k/128k" in app.status_bar.text()


def test_without_a_server_count_the_status_bar_shows_the_estimate_marked_approximate():
    app = _app(True)
    app.agent.endpoint.context_window = lambda: 131072
    app.agent._last_usage = None
    app.agent.context_estimate = lambda: 3000
    refresh_status_bar(app)
    assert app.status_bar.values["context"] == "~3.0k/128k"


def test_focus_takes_the_whole_bracket_block():
    """2026-10-29: a ``[ … ]`` block belongs to its first value: focusing it
    inverts the block (not the separator spaces at its edges) and a click
    anywhere on it picks that value."""
    from moka_code.ui.tui.buffer import Buffer

    bar = StatusBar(template="{model}[  mode:{role}][  ctx {context}]")
    bar.set_values({"model": "q", "role": "agent", "context": "1k"})
    bar.set_layout(0, 0, 40, 1)
    bar.set_focused_value("role")
    buffer = Buffer(40, 1)
    bar.render(buffer)
    row = buffer.cells[0]
    text = "".join(cell.char for cell in row)
    inverted = "".join(cell.char for cell in row if cell.reverse)

    assert text.startswith(" q  mode:agent  ctx 1k")
    assert inverted == "mode:agent"
    assert bar.value_at(text.index("mode")) == "role"
    assert bar.value_at(text.index("ctx")) == "context"
    assert bar.value_at(text.index("q")) == "model"
