"""Model discovery and selection commands.

The unit of selection is a ``(server, model)`` pair. Discovery is live: the
cached catalog in ``state.toml`` is only a convenience for completion and
offline fallback, never the source of truth.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import List, Optional, Tuple

from moka_code import settings
from moka_code.ui.tui.msg_types import SysMsg, SysMsgError

from .base import ChatUIProtocol, activate_endpoint

# Cap on live discovery so an unreachable server cannot stall the picker.
_DISCOVERY_TIMEOUT = 8.0


async def _discover_all() -> List[Tuple[str, "ModelInfo"]]:
    """Discover models live from every configured endpoint.

    Offline servers fall back to their cached catalog. Returns ``(server,
    model)`` pairs and refreshes the cache.
    """
    from moka_code.harness.endpoint import ModelInfo, get_endpoint

    pairs: List[Tuple[str, ModelInfo]] = []
    for name in list(settings.config.servers):
        endpoint = get_endpoint(name)
        if endpoint is None:
            continue
        try:
            models = await endpoint.discover_models()
        except Exception:
            models = [ModelInfo.from_dict(m) for m in settings.config.models_by_server.get(name, [])]
        settings.config.models_by_server[name] = [m.to_dict() for m in models]
        pairs.extend((name, model) for model in models)
    settings.config.save_model_catalog()
    return pairs


def _current_selection(ui: ChatUIProtocol) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(active_server_name, selected_model_id)`` from the live agent."""
    endpoint = getattr(ui.agent, "endpoint", None)
    return getattr(endpoint, "name", None), getattr(endpoint, "selected_model", None)


def _format_context(tokens: Optional[int]) -> str:
    """Compact context-window label (e.g. ``32k``, ``1.3M``)."""
    if not tokens:
        return ""
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}M"
    if tokens >= 1024:
        if tokens % 1024 == 0:
            return f"{tokens // 1024}k"
        return f"{tokens / 1000:.0f}k"
    return str(tokens)


def _cached_pairs() -> List[Tuple[str, "ModelInfo"]]:
    """``(server, model)`` pairs from the cached catalog (no network)."""
    from moka_code.harness.endpoint import ModelInfo

    pairs: List[Tuple[str, ModelInfo]] = []
    for name in list(settings.config.servers):
        for raw in settings.config.models_by_server.get(name, []):
            try:
                pairs.append((name, ModelInfo.from_dict(raw)))
            except Exception:
                continue
    return pairs


def _build_rows(pairs, active_name: Optional[str], selected: Optional[str]):
    """Aligned picker rows.

    Returns ``(items, descriptions, footers, item -> (server, model_id))``.
    The primary text is the model id; the muted description is the aligned
    ``server  context`` pair; the current model gets an ``active`` footer.
    """
    server_w = max((len(server) for server, _ in pairs), default=0)
    ctx_w = max((len(_format_context(m.context_window)) for _, m in pairs), default=0)
    counts = Counter(model.id for _, model in pairs)

    items: List[str] = []
    descriptions: dict = {}
    footers: dict = {}
    index: dict = {}
    for server, model in pairs:
        # Disambiguate the rare case of one model id on several servers.
        item = model.id if counts[model.id] == 1 else f"{server}:{model.id}"
        active = server == active_name and model.id == selected
        descriptions[item] = (
            f"{server:<{server_w}}  {_format_context(model.context_window):>{ctx_w}}"
        )
        if active:
            footers[item] = "active"
        items.append(item)
        index[item] = (server, model.id)
    return items, descriptions, footers, index


def _select_pair(ui: ChatUIProtocol, server: str, model: str) -> None:
    try:
        _activate(ui, server, model)
        ui.chat_history_panel.add_message(
            f"Selected {model} on {server}.", msg_type=SysMsg(), title="model")
    except Exception as exc:
        ui.chat_history_panel.add_message(
            f"Could not select model: {exc}", msg_type=SysMsgError(), title="model")


def _list_models_text(ui: ChatUIProtocol, pairs) -> None:
    """Headless fallback when no compositor/picker is available."""
    if not pairs:
        ui.chat_history_panel.add_message(
            "No models discovered.\n\n"
            "Configure a server with '/config servers', then run '/model' "
            "again once it is reachable.",
            msg_type=SysMsg(), title="model")
        return
    active_name, selected = _current_selection(ui)
    ordered = sorted(pairs, key=lambda p: (p[0], p[1].id))
    items, descriptions, footers, _ = _build_rows(ordered, active_name, selected)
    lines = [f"{item}  {descriptions.get(item, '')}  {footers.get(item, '')}".rstrip()
             for item in items]
    lines += ["", "Use '/model <model>' to select."]
    ui.chat_history_panel.add_message("\n".join(lines), msg_type=SysMsg(), title="model")


async def _open_picker(ui: ChatUIProtocol) -> None:
    """Open the searchable model picker.

    The cached catalog is shown immediately; live discovery runs in the
    background and refreshes the open picker (so an unreachable server never
    blocks the UI).
    """
    state: dict = {"index": {}}

    def _show(pairs, modal=None):
        active_name, selected = _current_selection(ui)
        ordered = sorted(pairs, key=lambda p: (p[0], p[1].id))
        items, descriptions, footers, index = _build_rows(ordered, active_name, selected)
        state["index"] = index
        initial = next(
            (i for i, (server, model) in enumerate(ordered)
             if server == active_name and model.id == selected),
            0,
        )

        def _accept(item):
            pair = state["index"].get(item)
            if pair:
                _select_pair(ui, pair[0], pair[1])

        if modal is not None:
            modal.refresh(items, descriptions=descriptions, footers=footers,
                          initial_index=initial)
            return modal
        show = getattr(ui, "show_search_modal", None)
        if show is None:
            return None
        return show("Models", items, descriptions=descriptions, footers=footers,
                    on_accept=_accept, initial_index=initial)

    cached = _cached_pairs()
    modal = _show(cached) if cached else None

    if modal is None and not cached:
        # Nothing cached yet — discover once, briefly, so the first open is
        # not empty (and does not hang on an unreachable server).
        try:
            pairs = await asyncio.wait_for(_discover_all(), _DISCOVERY_TIMEOUT)
        except Exception:
            pairs = []
        if pairs:
            modal = _show(pairs)

    if modal is None:
        _list_models_text(ui, cached)
        return

    async def _refresh():
        try:
            pairs = await asyncio.wait_for(_discover_all(), _DISCOVERY_TIMEOUT)
        except Exception:
            return
        if pairs and getattr(modal, "is_visible", False):
            _show(pairs, modal)

    asyncio.ensure_future(_refresh())


def _split_server_model(raw: str) -> Tuple[Optional[str], str]:
    """Split ``<server>:<model>`` using known server names.

    Model ids may contain colons (e.g. Ollama tags), so only split on the first
    occurrence of a known server name followed by ``:``.
    """
    for server_name in sorted(settings.config.servers, key=len, reverse=True):
        prefix = f"{server_name}:"
        if raw.startswith(prefix) and len(raw) > len(prefix):
            return server_name, raw[len(prefix):]
    return None, raw


def _activate(ui: ChatUIProtocol, server: str, model: str) -> None:
    """Persist the selection and switch to a fresh endpoint that applies it."""
    from moka_code.harness.endpoint import get_endpoint

    settings.config.save_model_selection(server, model)
    settings.config.set_active_server(server)
    endpoint = get_endpoint(server)
    if endpoint is not None:
        activate_endpoint(ui, endpoint)


async def model_use(ui: ChatUIProtocol, args: List[str]):
    if not args:
        await _open_picker(ui)
        return

    raw = " ".join(args).strip()
    server_hint, model = _split_server_model(raw)

    if server_hint is not None:
        if server_hint not in settings.config.servers:
            ui.chat_history_panel.add_message(
                f"Server '{server_hint}' not found.\n\n"
                "Configured servers: "
                + (", ".join(settings.config.servers) or "none")
                + " ('/config servers').",
                msg_type=SysMsgError(), title="model")
            return
        servers = [server_hint]
    else:
        try:
            pairs = await asyncio.wait_for(_discover_all(), _DISCOVERY_TIMEOUT)
        except Exception:
            pairs = _cached_pairs()
        servers = [server for server, info in pairs if info.id == model]

    if not servers:
        ui.chat_history_panel.add_message(
            f"Model '{model}' not found on any configured server.\n\n"
            "Run '/model' to pick from the discovered models.",
            msg_type=SysMsgError(), title="model")
        return

    if len(servers) > 1:
        active_name = getattr(getattr(ui.agent, "endpoint", None), "name", None)
        if active_name in servers:
            server = active_name
        else:
            ui.chat_history_panel.add_message(
                f"Model '{model}' is served by multiple servers:\n"
                + "\n".join(f"  - {s}" for s in servers)
                + "\n\nUse '/model <server>:<model>' to disambiguate.",
                msg_type=SysMsgError(), title="model")
            return
    else:
        server = servers[0]

    try:
        _activate(ui, server, model)
        ui.chat_history_panel.add_message(
            f"Selected {model} on {server}.", msg_type=SysMsg(), title="model")
    except Exception as exc:
        ui.chat_history_panel.add_message(
            f"Could not select model: {exc}", msg_type=SysMsgError(), title="model")


def _active() -> Tuple[Optional[str], Optional[str]]:
    """``(server, model)`` of the persisted selection (the live one)."""
    server = settings.config.active_server
    return server, settings.config.get_model_for_server(server)


def model_completions() -> List[str]:
    """Every cached model, as the ``/model`` picker lists them."""
    pairs = sorted(_cached_pairs(), key=lambda p: (p[0], p[1].id))
    return _build_rows(pairs, *_active())[0]


def model_descriptions() -> dict:
    pairs = sorted(_cached_pairs(), key=lambda p: (p[0], p[1].id))
    items, descriptions, footers, _ = _build_rows(pairs, *_active())
    return {item: f"{descriptions[item]}  {footers.get(item, '')}".rstrip() for item in items}


_DEFAULT_EFFORT = "default"


def _effort_variants(server: Optional[str], model_id: Optional[str]) -> dict:
    """``{level: model id}`` for the effort-variant siblings of ``model_id``
    (``X:low`` / ``X:high``) in the server's catalog; empty unless several."""
    from moka_code.harness.endpoint_discovery import EFFORT_WORDS

    base, sep, suffix = (model_id or "").rpartition(":")
    if not sep or suffix not in EFFORT_WORDS:
        return {}
    ids = {m.get("id") for m in settings.config.models_by_server.get(server, [])}
    variants = {w: f"{base}:{w}" for w in EFFORT_WORDS if f"{base}:{w}" in ids}
    return variants if len(variants) > 1 else {}


def _effort_choices(server: Optional[str], model: Optional[str]):
    """``(choices, current, variants)`` for a server's model.

    Effort variants (``X:low`` / ``X:high``) are chosen by switching model;
    otherwise the choices are ``default`` plus the request-parameter levels
    (``efforts`` in servers.toml, else detected from the catalog). No
    choices: the model has no effort switch.
    """
    from moka_code.harness.endpoint_discovery import effort_levels

    variants = _effort_variants(server, model)
    if variants:
        return list(variants), model.rpartition(":")[2], variants
    table = settings.config.servers.get(server) or {}
    metadata = next((m.get("metadata") or {} for m in settings.config.models_by_server.get(server, [])
                     if m.get("id") == model), {})
    levels = effort_levels((table.get("models") or {}).get(model), table.get("efforts"), metadata)
    if not levels:
        return [], None, {}
    saved = settings.config.get_effort(server, model)
    return [_DEFAULT_EFFORT, *levels], saved if saved in levels else _DEFAULT_EFFORT, {}


def effort_completions() -> List[str]:
    return _effort_choices(*_active())[0]


def effort_descriptions() -> dict:
    choices, current, variants = _effort_choices(*_active())
    descriptions = {c: variants.get(c, "") for c in choices}
    if _DEFAULT_EFFORT in descriptions:
        descriptions[_DEFAULT_EFFORT] = "server default"
    if current:
        descriptions[current] = f"{descriptions[current]}  active".strip()
    return descriptions


def _set_effort(ui: ChatUIProtocol, endpoint, effort: Optional[str]) -> None:
    endpoint.effort = effort
    settings.config.save_effort(endpoint.name, endpoint.selected_model or "", effort)
    ui.chat_history_panel.add_message(
        f"Reasoning effort: {effort or 'server default'}.", msg_type=SysMsg(), title="effort")
    if hasattr(ui, "refresh_status_bar"):
        ui.refresh_status_bar()


async def effort_command(ui: ChatUIProtocol, args: List[str]):
    """``/effort <level>`` sets the current model's reasoning effort; bare
    ``/effort`` opens a picker. See :func:`_effort_choices`."""
    endpoint = getattr(ui.agent, "endpoint", None)
    server = getattr(endpoint, "name", None)
    model = getattr(endpoint, "selected_model", None)
    choices, current, variants = _effort_choices(server, model)
    if not choices:
        ui.chat_history_panel.add_message(
            f"No reasoning effort detected for {model or 'this model'}. "
            "Declare it with efforts = [\"low\", \"medium\", \"high\"] in its "
            "server (or model) table ('/config servers').",
            msg_type=SysMsgError(), title="effort")
        return

    def apply(level):
        if variants:
            _select_pair(ui, server, variants[level])
        else:
            _set_effort(ui, endpoint, None if level == _DEFAULT_EFFORT else level)

    if args:
        if args[0] not in choices:
            ui.chat_history_panel.add_message(
                f"Unknown effort '{args[0]}'. Levels: {', '.join(choices)}.",
                msg_type=SysMsgError(), title="effort")
            return
        apply(args[0])
        return

    show = getattr(ui, "show_search_modal", None)
    if show is None:
        ui.chat_history_panel.add_message(
            f"Effort: {current}. Levels: {', '.join(choices)}.", msg_type=SysMsg(), title="effort")
        return
    show("Effort", choices, footers={current: "active"}, on_accept=apply,
         initial_index=choices.index(current))


async def model_command(ui: ChatUIProtocol, args: List[str]):
    """``/model`` opens the picker; ``/model <model>`` selects directly."""
    await model_use(ui, args)


__all__ = [
    "effort_command", "effort_completions", "effort_descriptions",
    "model_command", "model_completions", "model_descriptions", "model_use",
]
