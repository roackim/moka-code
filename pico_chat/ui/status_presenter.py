"""Renders agent/endpoint state into the status bar.

Split out of ``app.py``; reads live agent + endpoint state and pushes formatted
values/colors onto the app's status bar.
"""
from __future__ import annotations

from typing import Any

from pico_chat import pico_cfg
from pico_chat.ui.tui.colors import theme, RGB, ANSIColor
from pico_chat.ui.tui.components.box import SPINNER_FRAMES


def _resolve_color(value: str, fallback: Any) -> Any:
    """Resolve a theme palette name or ``#rrggbb`` string to a color.

    Falls back to ``fallback`` for an unknown name or invalid hex so a typo in
    the config never blanks the field.
    """
    if not value:
        return fallback
    named = getattr(theme, value, None)
    if isinstance(named, (RGB, ANSIColor)):
        return named
    if value.startswith("#"):
        try:
            return RGB(value)
        except ValueError:
            return fallback
    return fallback


def _format_tokens(value: int | None) -> str:
    if value is None:
        return "?"
    if value < 1000:
        return str(value)
    # Use binary-sized exact values for common context limits (32k for
    # 32768), while keeping the compact decimal style for live usage.
    if value % 1024 == 0:
        return f"{value // 1024}k"
    return f"{value / 1000:.1f}k"


def _context_color(used: int | None, maximum: int | None) -> Any:
    """Color the context field by how full the window is.

    green < 33%, orange < 66%, red >= 66%.
    """
    if not used or not maximum or maximum <= 0:
        return theme.DEFAULT
    ratio = used / maximum
    if ratio < 0.33:
        return theme.SUCCESS
    if ratio < 0.66:
        return theme.WARNING
    return theme.ERROR


def refresh_status_bar(app) -> None:
    """Refresh local status fields without performing network I/O."""
    agent = app.agent
    endpoint = getattr(agent, "endpoint", None)
    if endpoint is None:
        return

    # Prefer the model the endpoint actually resolved (the one sent in the
    # next request) over a requested selection the endpoint may have
    # ignored. ``_cached_model_name`` is set by ``set_model`` and by the
    # connection probe, so this cannot show a model the request won't use.
    model = (
        getattr(endpoint, "_cached_model_name", None)
        or getattr(endpoint, "selected_model", None)
        or endpoint.model
        or "?"
    )
    # Strip a leading path and common file suffix from a model id
    # (e.g. /data/llm/weights/Qwen3.8-27B-Q4_0.gguf -> Qwen3.8-27B-Q4_0)
    # for a compact status bar.
    if isinstance(model, str):
        if "/" in model:
            model = model.rsplit("/", 1)[-1]
        for suffix in (".gguf", ".bin", ".safetensors"):
            if model.endswith(suffix):
                model = model[: -len(suffix)]
                break
    role = getattr(getattr(agent, "role", None), "name", "agent")
    state = getattr(getattr(agent, "state", None), "name", "IDLE").lower()

    # Show an animated spinner while .local hostname resolution or model
    # name discovery is pending.
    from pico_chat.harness.endpoint import is_local_resolution_pending
    if is_local_resolution_pending(endpoint._original_base_url) or getattr(endpoint, "_model_name_pending", False):
        frame = SPINNER_FRAMES[app._status_spinner_frame % len(SPINNER_FRAMES)]
        model = f"{frame} {model}"

    # Color the server/model field by connection state:
    #   checking -> orange, error -> red, ok -> green.
    conn = getattr(endpoint, "_connection_state", "unknown")
    if conn == "checking":
        server_color = theme.WARNING
    elif conn == "error":
        server_color = theme.ERROR
    elif conn == "ok":
        server_color = theme.SUCCESS
    else:
        server_color = theme.DEFAULT

    usage = getattr(agent, "_last_usage", None)
    context_used = getattr(usage, "prompt_tokens", None)
    context_max = getattr(endpoint, "_cached_context_window", None)
    if context_max is None:
        context_max = endpoint.max_context or 32768
    if context_used is None:
        context_used = 0

    # Sandbox field: [glyph][prefix]runtime, colored green when active and
    # orange when tools run unsandboxed. Always present so it stays visible.
    sandboxed = getattr(agent, "sandboxed", None)
    sandboxed = bool(sandboxed()) if callable(sandboxed) else False
    runtime = getattr(agent, "_sandbox_runtime", "none") or "none"
    if sandboxed and runtime == "none":
        runtime = "sandbox"
    sandbox_text = pico_cfg.config.ui_sandbox_glyph
    if sandbox_text:
        sandbox_text += " "
    sandbox_text += f"{pico_cfg.config.ui_sandbox_prefix}{runtime}"
    sandbox_color = _resolve_color(
        pico_cfg.config.ui_sandbox_active_color if sandboxed
        else pico_cfg.config.ui_sandbox_inactive_color,
        theme.SUCCESS if sandboxed else theme.WARNING,
    )

    app.status_bar.set_values({
        "endpoint_model": f"{endpoint.name}:{model}",
        "context": f"ctx {_format_tokens(context_used)}/{_format_tokens(context_max)}",
        "role": f"role {role}",
        "state": state,
        "endpoint": endpoint.name,
        "model": model,
        "workspace": getattr(agent, "workspace", ""),
        "sandbox": sandbox_text,
    })
    app.status_bar.set_field_colors({
        "context": _context_color(context_used, context_max),
        "endpoint": server_color,
        "model": server_color,
        "endpoint_model": server_color,
        "sandbox": sandbox_color,
    })
