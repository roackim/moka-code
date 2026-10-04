"""Renders agent/endpoint state into the status bar and the notice band.

Split out of ``app.py``; reads live agent + endpoint state and pushes formatted
values/colors onto the app's status bar, and what currently needs fixing onto
the notice band (see :func:`notices`).
"""
from __future__ import annotations

import os
import time
from typing import Any

from moka_code import settings
from moka_code.ui.tui.colors import theme, RGB, ANSIColor
from moka_code.ui.tui.components.box import SPINNER_FRAMES


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


def _format_cost(cost: float | None) -> str:
    """``$0.12`` for this conversation (to the cent); empty when unreported."""
    if cost is None:
        return ""
    return f"${cost:.2f}"


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


def notices(agent) -> list[tuple[str, str]]:
    """What is wrong right now, as ``(level, "problem → fix")``, errors first.

    Read from live state on every refresh, so a notice disappears as soon as
    its cause is fixed. Setup problems (config and role files) are the
    banner's (``app.setup_notes``), not these.
    """
    from moka_code.harness.endpoint import catalog_entry, unserved_model

    endpoint = getattr(agent, "endpoint", None)
    name = getattr(endpoint, "name", "")
    conn = getattr(endpoint, "_connection_state", "unknown")
    errors, warnings = [], []
    selected = settings.config.active_server
    if not settings.config.servers:
        errors.append("no server configured → /config servers")
    elif endpoint is None:
        if selected and selected not in settings.config.servers:
            errors.append(f"server '{selected}' is not in servers.toml → /config servers or /model")
        else:
            errors.append("no model selected → /model")
    else:
        server = settings.config.servers.get(name) or {}
        key_env = server.get("api_key_env")
        if key_env and not os.getenv(key_env):
            errors.append(f"${key_env} is not set ({name} API key) → export {key_env}=…")
        if conn == "error":
            url = getattr(endpoint, "_original_base_url", "") or endpoint.base_url
            errors.append(f"{name} unreachable ({url}) → start it, or /config servers")
        elif not getattr(endpoint, "selected_model", None):
            errors.append(f"{name}: no model selected → /model")
        elif (settings.config.models_by_server.get(name) == []
              and name not in settings.config.stale_servers):
            errors.append(f"{name} lists no models → /config servers")
        elif unserved_model(name, getattr(endpoint, "selected_model", None)) is not None:
            errors.append(f"{endpoint.selected_model} is no longer served by {name} → /model")
        elif (name not in settings.config.stale_servers and endpoint.selected_model
              and catalog_entry(name, endpoint.selected_model)
              and endpoint.context_window() is None):
            errors.append(f"{name} states no context window for {endpoint.selected_model} "
                          "→ server broken, or moka reads the wrong route")
    role = getattr(agent, "role", None)
    role_problem = getattr(agent, "role_problem", None)
    problem = role_problem() if callable(role_problem) else None
    if problem == "none":
        errors.append("no role file loads → /config role agent")
    elif problem == "gone":
        warnings.append(f"role {role.name}: its file is gone, running from memory "
                        f"→ /config role {role.name}")
    elif problem == "changed":
        warnings.append(f"role {role.name} changed on disk → /reload")
    depth = getattr(role, "replay_reasoning_depth", None)
    if endpoint is not None and depth is not None:
        needed = endpoint.min_replay_depth(bool(getattr(agent, "tool_schemas", None)))
        if depth < needed:
            warnings.append(
                f"{name} needs reasoning sent back for {'all' if needed >= 999 else needed} "
                f"turn(s), role {role.name} sends {depth} → replay_reasoning_depth "
                f"(/config role {role.name})")
    # Every configured server, not only the active one: a server that cannot
    # be listed would otherwise fail without a word (the active one's own
    # problems are reported above).
    for server_name, table in settings.config.servers.items():
        if server_name == name and endpoint is not None:
            continue
        other = errors if server_name == selected else warnings
        key_env = table.get("api_key_env")
        if key_env and not os.getenv(key_env):
            other.append(f"${key_env} is not set ({server_name} API key) → export {key_env}=…")
        elif server_name in settings.config.discovery_errors:
            other.append(f"{server_name} cannot be listed: "
                         f"{settings.config.discovery_errors[server_name]} → /config servers")
    sandbox_required = getattr(agent, "sandbox_required", None)
    if callable(sandbox_required) and sandbox_required():
        role = getattr(getattr(agent, "role", None), "name", "this role")
        errors.append(f"role {role} requires a sandbox → /sandbox start <id>")
    problem = getattr(agent, "sandbox_problem", None)
    if problem:
        warnings.append(problem)
    return [("error", text) for text in errors] + [("warning", text) for text in warnings]


def _refresh_notices(app) -> None:
    """Show the current notices on the band; log each one to the activity
    overlay when it appears and when it clears (``/activity`` lists them all)."""
    band = getattr(app, "notice_band", None)
    if band is None:
        return
    current = notices(app.agent)
    previous = getattr(app, "_notices", [])
    stamp = time.strftime("%H:%M:%S")
    for level, text in current:
        if (level, text) not in previous:
            app.activity(f"{stamp} {text}", level)
    for level, text in previous:
        if (level, text) not in current:
            app.activity(f"{stamp} fixed: {text}")
    app._notices = current
    band.max_lines = settings.config.ui_notice_lines
    lines = [(text, theme.ERROR if level == "error" else theme.WARNING)
             for level, text in current]
    if getattr(app.agent, "private", False):
        lines.insert(0, ("private · nothing is saved · /clear to leave", theme.USER))
    band.set_notices(lines)


def refresh_status_bar(app) -> None:
    """Refresh local status fields without performing network I/O."""
    agent = app.agent
    _refresh_notices(app)
    endpoint = getattr(agent, "endpoint", None)
    role = getattr(getattr(agent, "role", None), "name", "agent")
    state = getattr(getattr(agent, "state", None), "name", "IDLE").lower()
    if endpoint is None:
        # A last used server removed from servers.toml stays shown, red;
        # otherwise nothing is selected. The notice band says how to fix it.
        selected = settings.config.active_server
        if selected:
            model = settings.config.get_model_for_server(selected) or "?"
            label = f"{selected}:{model}"
            _set_fields(app, agent, label, selected, model, theme.ERROR, role, state, None)
            return
        label = "no server" if not settings.config.servers else "no model"
        _set_fields(app, agent, label, label, "", theme.ERROR, role, state, None)
        return

    # ``selected_model`` is the one id the next request uses, as selected.
    model = getattr(endpoint, "selected_model", None) or "?"
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
    effort = getattr(endpoint, "effort", None)
    if effort:      # shown whenever it is sent (always, see effort_payload)
        model = f"{model} · {effort}"

    # Show an animated spinner while .local hostname resolution or the
    # connection check is pending.
    from moka_code.harness.endpoint import is_local_resolution_pending, unserved_model
    if is_local_resolution_pending(endpoint._original_base_url) or getattr(endpoint, "_connection_state", None) == "checking":
        frame = SPINNER_FRAMES[app._status_spinner_frame % len(SPINNER_FRAMES)]
        model = f"{frame} {model}"

    # Color the server/model field by connection state:
    #   checking -> orange, error -> red, ok -> green.
    conn = getattr(endpoint, "_connection_state", "unknown")
    lists_nothing = (settings.config.models_by_server.get(endpoint.name) == []
                     and endpoint.name not in settings.config.stale_servers)
    if lists_nothing or unserved_model(endpoint.name,
                                       getattr(endpoint, "selected_model", None)) is not None:
        server_color = theme.ERROR      # kept, but not joinable: never replaced
    elif conn == "checking":
        server_color = theme.WARNING
    elif conn == "error":
        server_color = theme.ERROR
    elif conn == "ok":
        server_color = theme.SUCCESS
    else:
        server_color = theme.DEFAULT
    _set_fields(app, agent, f"{endpoint.name}:{model}", endpoint.name, model,
                server_color, role, state, endpoint)


def _set_fields(app, agent, endpoint_model: str, server: str, model: str,
                server_color: Any, role: str, state: str, endpoint) -> None:
    """Push the status fields; ``endpoint`` is ``None`` when nothing is
    selected (no context field then)."""
    usage = getattr(agent, "_last_usage", None)
    context_used = getattr(usage, "prompt_tokens", None) or 0
    context_max = None
    if endpoint is not None:
        context_window = getattr(endpoint, "context_window", None)
        context_max = context_window() if callable(context_window) else None

    # Sandbox field: [glyph][prefix]runtime, colored green when active and
    # orange when tools run unsandboxed. Always present so it stays visible.
    sandboxed = getattr(agent, "sandboxed", None)
    sandboxed = bool(sandboxed()) if callable(sandboxed) else False
    runtime = getattr(agent, "_sandbox_runtime", "none") or "none"
    if sandboxed and runtime == "none":
        runtime = "sandbox"
    sandbox_text = settings.config.ui_sandbox_glyph
    if sandbox_text:
        sandbox_text += " "
    sandbox_text += f"{settings.config.ui_sandbox_prefix}{runtime}"
    sandbox_color = _resolve_color(
        settings.config.ui_sandbox_active_color if sandboxed
        else settings.config.ui_sandbox_inactive_color,
        theme.SUCCESS if sandboxed else theme.WARNING,
    )

    app.status_bar.set_values({
        "endpoint_model": endpoint_model,
        # An unknown window shows only what is used (never a guessed size).
        "context": (f"ctx {_format_tokens(context_used)}/{_format_tokens(context_max)}"
                    if context_max else f"ctx {_format_tokens(context_used)}"
                    if endpoint is not None else ""),
        "role": f"role {role}",
        "state": state,
        "endpoint": server,
        "model": model,
        "workspace": getattr(agent, "workspace", ""),
        "sandbox": sandbox_text,
        # Empty (so hidden) until the provider reports a cost.
        "cost": _format_cost(getattr(agent, "conversation_cost", None)),
    })
    app.status_bar.set_field_colors({
        "context": _context_color(context_used, context_max),
        "endpoint": server_color,
        "model": server_color,
        "endpoint_model": server_color,
        "sandbox": sandbox_color,
    })
