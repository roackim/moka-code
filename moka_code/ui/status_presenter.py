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
from moka_code.ui.tui.colors import theme, resolve_color
from moka_code.ui.tui.components.box import SPINNER_FRAMES


def _format_cost(cost: float | None) -> str:
    """``$0.12`` for this conversation (to the cent); empty when unreported."""
    if cost is None:
        return ""
    return f"${cost:.2f}"


def _format_model(server: str, model: str, effort: str | None = None) -> str:
    """Compose ``[server<sep>]model[<sep>effort]`` for the status field."""
    parts = [server] if settings.config.ui_show_provider else []
    parts.append(model)
    label = settings.config.ui_provider_separator.join(parts)
    if effort and settings.config.ui_show_effort:
        label += settings.config.ui_effort_separator + effort
    return label


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
        if name in settings.config.server_errors:
            url = getattr(endpoint, "_original_base_url", "") or endpoint.base_url
            errors.append(f"{name} cannot be joined: {settings.config.server_errors[name]} "
                          f"({url}) → start it, or /config servers")
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
        errors.append("no role file loads → /role create agent")
    elif problem == "gone":
        warnings.append(f"role {role.name}: its file is gone, running from memory "
                        f"→ /role create {role.name}")
    elif problem == "changed":
        warnings.append(f"role {role.name} changed on disk → /reload")
    preserve = getattr(role, "preserve_thinking", None)
    if (endpoint is not None and preserve is False
            and endpoint.needs_preserved_thinking(bool(getattr(agent, "tool_schemas", None)))):
        warnings.append(
            f"{name} needs reasoning sent back, role {role.name} sends none "
            f"→ preserve_thinking (/role edit {role.name})")
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
        elif server_name in settings.config.server_errors:
            other.append(f"{server_name} cannot be joined: "
                         f"{settings.config.server_errors[server_name]} → /config servers")
    sandbox_required = getattr(agent, "sandbox_required", None)
    if callable(sandbox_required) and sandbox_required():
        role = getattr(getattr(agent, "role", None), "name", "this role")
        errors.append(f"role {role} requires a sandbox → /sandbox start <id>")
    problem = getattr(agent, "sandbox_problem", None)
    if problem:
        warnings.append(problem)
    window = getattr(endpoint, "context_window", None)
    if (endpoint is not None and callable(window) and window() is None
            and (settings.config.context_compact_at > 0 or settings.config.context_compact_stop_at > 0)):
        warnings.append("context size unknown: auto-compact is off (/compact works by hand)")
    drift = getattr(agent, "sandbox_drift", None)
    drift = drift() if callable(drift) else None
    sandbox_name = getattr(agent, "sandbox_name", None)
    if drift == "gone":
        warnings.append(f"sandbox {sandbox_name}: its file is gone, still running from "
                        "memory → /sandbox stop")
    elif drift == "changed":
        warnings.append(f"sandbox {sandbox_name} changed on disk → /reload")
    stale = getattr(agent, "sandbox_stale", None)
    if callable(stale) and stale():
        warnings.append(f"sandbox {sandbox_name}: its image is older than its dockerfile "
                        f"→ /sandbox build {sandbox_name}")
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
    # ``/reload`` may have changed the field list or separator; StatusBar was
    # built once at startup, so re-apply both here on every refresh.
    bar = getattr(app, "status_bar", None)
    if bar is not None and hasattr(bar, "set_fields"):
        separator = settings.config.ui_status_bar_separator
        if getattr(bar, "separator", None) != separator:
            bar.separator = separator
            bar.mark_changed()
        fields = list(settings.config.ui_status_bar_fields)
        if getattr(bar, "fields", None) != fields:
            bar.set_fields(fields)
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
            effort = settings.config.efforts.get(selected, {}).get(model)
            label = _format_model(selected, model, effort)
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
    endpoint_model = _format_model(endpoint.name, model, effort)

    # Show an animated spinner while .local hostname resolution or the
    # connection check is pending.
    from moka_code.harness.endpoint import is_local_resolution_pending, unserved_model
    if is_local_resolution_pending(endpoint._original_base_url) or getattr(endpoint, "_connection_state", None) == "checking":
        frame = SPINNER_FRAMES[app._status_spinner_frame % len(SPINNER_FRAMES)]
        endpoint_model = f"{frame} {endpoint_model}"

    # Color the server/model field by the latest probe of the server (model
    # listing, connection check or chat request): checking -> orange, failed
    # -> red, ok -> green.
    conn = getattr(endpoint, "_connection_state", "unknown")
    lists_nothing = (settings.config.models_by_server.get(endpoint.name) == []
                     and endpoint.name not in settings.config.stale_servers)
    if lists_nothing or unserved_model(endpoint.name,
                                       getattr(endpoint, "selected_model", None)) is not None:
        server_color = theme.ERROR      # kept, but not joinable: never replaced
    elif conn == "checking":
        server_color = theme.WARNING
    elif endpoint.name in settings.config.server_errors or conn == "error":
        server_color = theme.ERROR
    elif conn == "ok":
        server_color = theme.SUCCESS
    else:
        server_color = theme.DEFAULT
    _set_fields(app, agent, endpoint_model, endpoint.name, model,
                server_color, role, state, endpoint)


def _set_fields(app, agent, endpoint_model: str, server: str, model: str,
                server_color: Any, role: str, state: str, endpoint) -> None:
    """Push the status fields; ``endpoint`` is ``None`` when nothing is
    selected (no context field then)."""
    usage = getattr(agent, "_last_usage", None)
    context_used = getattr(usage, "context_tokens", None)
    estimated = context_used is None
    if estimated:
        # No server count yet (import, resume, truncate, compaction): the
        # estimate of the next request, shown with a "~".
        estimate = getattr(agent, "context_estimate", None)
        context_used = estimate() if callable(estimate) and endpoint is not None else 0
    context_max = None
    if endpoint is not None:
        context_window = getattr(endpoint, "context_window", None)
        context_max = context_window() if callable(context_window) else None

    # Sandbox field: [glyph][prefix]name, colored green when active and
    # orange when tools run unsandboxed. Always present so it stays visible.
    sandboxed = getattr(agent, "sandboxed", None)
    sandboxed = bool(sandboxed()) if callable(sandboxed) else False
    runtime = getattr(agent, "_sandbox_runtime", "none") or "none"
    # The configured sandbox name is what the user chose; its runtime type is
    # an implementation detail. Fall back to the runtime when no name is known.
    sandbox_label = getattr(agent, "sandbox_name", None) or runtime
    if sandboxed and sandbox_label == "none":
        sandbox_label = "sandbox"
    sandbox_text = settings.config.ui_sandbox_glyph
    if sandbox_text:
        sandbox_text += " "
    sandbox_text += f"{settings.config.ui_sandbox_prefix}{sandbox_label}"
    sandbox_color = resolve_color(
        settings.config.ui_sandbox_active_color if sandboxed
        else settings.config.ui_sandbox_inactive_color,
        theme.SUCCESS if sandboxed else theme.WARNING,
    )

    approx = "~" if estimated and context_used else ""
    app.status_bar.set_values({
        "endpoint_model": endpoint_model,
        # An unknown window shows only what is used (never a guessed size).
        "context": (f"ctx {approx}{_format_tokens(context_used)}/{_format_tokens(context_max)}"
                    if context_max else f"ctx {approx}{_format_tokens(context_used)}"
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
    role_color = resolve_color(getattr(getattr(agent, "role", None), "color", ""), theme.DEFAULT)
    app.status_bar.set_field_colors({
        "role": role_color,
        "context": _context_color(context_used, context_max),
        "endpoint": server_color,
        "model": server_color,
        "endpoint_model": server_color,
        "sandbox": sandbox_color,
    })
