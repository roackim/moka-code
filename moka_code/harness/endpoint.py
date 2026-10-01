"""LLM endpoints: the provider base class, the factory and the catalog.

An :class:`Endpoint` is both the connection description (base_url, api_key,
model) and the live client. Each server family is a subclass in
``harness/providers/`` (contract: ``.wiki/notes/providers.md``); the registry
there maps a ``type`` to its class, and nothing outside a provider branches on
the type.

Transports are implemented directly on httpx (no SDK): moka fully owns the
connection, DNS/IP, timeouts and retries, and exposes precise timing.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, Optional
from urllib.parse import urlsplit

import httpx

from moka_code.harness.usage import TokenUsage
from moka_code.harness.endpoint_local import (
    _local_cache,
    _resolve_local_hostname,
    _resolve_local_hostname_async,
    _resolve_local_hostname_await,
    invalidate_local_hostname,
    is_local_resolution_pending,
    prewarm_local_resolution,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model metadata
# ---------------------------------------------------------------------------

@dataclass
class ModelInfo:
    """Metadata for one model exposed by an endpoint."""

    id: str
    context_window: int | None = None
    owned_by: str | None = None
    metadata: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = {}

    # Convenience accessors for the most useful Ollama metadata fields.
    @property
    def size(self) -> int | None:
        return self.metadata.get("size")

    @property
    def family(self) -> str | None:
        return self.metadata.get("family")

    @property
    def modified_at(self) -> str | None:
        return self.metadata.get("modified_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "context_window": self.context_window,
            "owned_by": self.owned_by,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelInfo":
        return cls(
            id=data.get("id", ""),
            context_window=data.get("context_window"),
            owned_by=data.get("owned_by"),
            metadata=data.get("metadata") or {},
        )


# ---------------------------------------------------------------------------
# Stream chunks
# ---------------------------------------------------------------------------

@dataclass
class ToolCallPiece:
    """A fragment of one streamed tool call."""

    index: int
    id: Optional[str] = None
    name: Optional[str] = None
    arguments: str = ""     # a JSON text fragment (dict arguments are dumped)


@dataclass
class Chunk:
    """One piece of a streamed answer, in no provider's vocabulary."""

    text: str = ""                          # answer text, verbatim
    reasoning: str = ""                     # reasoning text, as the server sent it
    # Opaque reasoning blocks, yielded once at the end, already assembled by
    # the provider (OpenRouter's ``reasoning_details``).
    reasoning_native: Optional[list] = None
    tool_calls: list[ToolCallPiece] = field(default_factory=list)
    usage: Optional[TokenUsage] = None
    finish: Optional[str] = None            # the server's finish reason


# ---------------------------------------------------------------------------
# HTTP client / proxy handling
# ---------------------------------------------------------------------------

# httpx reads HTTP(S)_PROXY / ALL_PROXY / NO_PROXY by default but, unlike curl,
# does NOT honor NO_PROXY beyond exact matches. WSL setups commonly inherit a
# Windows HTTP_PROXY that routes LAN/localhost traffic through a bogus proxy —
# "curl works, moka doesn't". For local/LAN targets we pin trust_env=False.
ALL_PROXY_KEYS = ("http_proxy", "https_proxy", "all_proxy")


def _is_local_target(url: str) -> bool:
    """True if the URL targets a local/LAN host that should bypass any proxy."""
    hostname = urlsplit(url).hostname or ""
    if hostname in ("localhost", "127.0.0.1", "::1"):
        return True
    if hostname.endswith(".local"):
        return True
    # RFC1918 private ranges + link-local.
    return hostname.startswith(("192.168.", "10.", "172."))


def _new_http_client(endpoint: "Endpoint") -> httpx.AsyncClient:
    """Build the single owned httpx client for an endpoint.

    - No SDK magic: we control base_url (already the resolved IPv4 for .local),
      timeout and trust_env.
    - ``trust_env=False`` for local/LAN targets blocks inherited proxy vars.
    - ``limits`` keeps keep-alive connections open across messages in one convo
      (no re-TCP-handshake per message).
    """
    kwargs: Dict[str, Any] = {
        "base_url": endpoint.base_url,
        "timeout": httpx.Timeout(endpoint.timeout, connect=endpoint.timeout),
        "trust_env": not _is_local_target(endpoint.base_url),
        "limits": httpx.Limits(max_keepalive_connections=5, keepalive_expiry=30.0),
    }
    if endpoint.api_key:
        kwargs["headers"] = {"Authorization": f"Bearer {endpoint.api_key}"}
    return httpx.AsyncClient(**kwargs)


@dataclass
class ConnectionDiagnosis:
    """Result of a connection attempt, with diagnostics on failure."""
    ok: bool
    url: str
    error: Optional[Exception] = None
    original_url: Optional[str] = None
    hostname: Optional[str] = None

    def message(self) -> str:
        """Human-readable summary for the /server diagnose command."""
        lines = [f"URL     : {self.url}"]
        if self.original_url and self.original_url != self.url:
            lines.append(f"Orig    : {self.original_url}")
        if self.ok:
            lines.append("Status  : ONLINE")
            return "\n".join(lines)

        lines.append("Status  : UNREACHABLE")
        line = f"Error   : {self.error}"
        if self.error is not None:
            line += f" ({type(self.error).__name__})"
        lines.append(line)

        # Proxy hints — the most common silent killer.
        active = {
            k: os.environ.get(k) or os.environ.get(k.upper())
            for k in ALL_PROXY_KEYS
            if os.environ.get(k) or os.environ.get(k.upper())
        }
        no_proxy = os.environ.get("no_proxy") or os.environ.get("NO_PROXY")
        if active:
            lines.append("Proxy env: " + ", ".join(f"{k}={v}" for k, v in active.items()))
            lines.append(
                "Hint     : httpx may route through these proxy vars. If the "
                "server is on your LAN, unset them or ensure it bypasses the proxy."
            )
        if no_proxy:
            lines.append(f"NO_PROXY : {no_proxy}")

        # DNS hint for un-resolvable .local hosts.
        if self.hostname and self.hostname.endswith(".local") and not self.ok:
            lines.append(
                "Hint     : .local hostname resolution happens via `getent`. "
                "Run `getent hosts " + self.hostname + "` to verify it resolves to an IP."
            )

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

class Endpoint:
    """A single LLM endpoint: connection config plus live transport.

    The base of every provider class: per-server config and state, the
    connection pool, the catalog readers. A provider sets the class
    attributes below and implements the discovery and completion methods.
    """

    # The ``type = "..."`` value in ``servers.toml``.
    type: str = ""
    # ``base_url`` when the table has none (llama.cpp only).
    default_url: Optional[str] = None
    # ``base_url`` is forbidden and this is the URL (OpenRouter only).
    fixed_url: Optional[str] = None
    # Server-table keys beyond the common ones, passed to the constructor.
    extra_keys: frozenset[str] = frozenset()
    # Loads one model and ignores the request's ``model`` (llama.cpp).
    serves_one_model: bool = False

    def __init__(
        self,
        name: str,
        base_url: Optional[str] = None,
        api_key: str = "",
        model: Optional[str] = None,
        max_context: Optional[int] = None,
        timeout: float = 30.0,
        retry_attempts: int = 3,
        retry_delay: float = 2.0,
    ):
        base_url = self.fixed_url or base_url or self.default_url
        if not base_url:
            raise ValueError(f"{self.type} servers need a base_url")
        self.name = name
        self.base_url = base_url
        self.api_key = api_key
        self.max_context = max_context
        self.timeout = timeout
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        # The chosen reasoning effort (levels: ``effort_levels``), sent in this
        # server's own field.
        self.effort: Optional[str] = None

        # .local hosts are rewritten to a routable IP. Resolution can block for
        # seconds on an offline mDNS host, so it is kicked off in the background
        # and we fall back to the original URL until the address is ready.
        self._original_base_url = base_url
        self._hostname = urlsplit(base_url).hostname
        if self._hostname and self._hostname.endswith(".local"):
            self.base_url = _resolve_local_hostname_async(base_url)
        self.client = _new_http_client(self)
        # Connection pools no longer in use, closed once no request is in
        # flight (a switch can happen while a reply is still streaming).
        self._in_flight = 0
        self._stale_clients: list[httpx.AsyncClient] = []

        # Runtime caches.
        # ``_selected_model`` is the one model id requests use; resolved once
        # confirmed (llama.cpp: replaced by the model it actually serves).
        self._model_resolved: bool = False
        self._cached_context_window: Optional[int] = None
        # Facts probed from the server per model (context window, image
        # support) when the catalog lacks them; dropped whenever this server's
        # catalog is refreshed (see :meth:`_probed`).
        self._probed_facts: dict[str, dict] = {}
        self._probed_generation = 0
        self._selected_model: Optional[str] = model
        self._model_name_pending: bool = False
        self._connection_state: str = "unknown"  # unknown|checking|ok|error
        # ``(server table, selected model)`` this endpoint was built from, so a
        # reload can tell whether the live endpoint is stale.
        self.source: Optional[tuple] = None

    # -- model selection -----------------------------------------------------

    @property
    def selected_model(self) -> Optional[str]:
        return self._selected_model

    async def prewarm_model_name(self) -> None:
        """Probe the connection and cache model name/context in the background."""
        if self._model_resolved and self._connection_state == "ok":
            await self.probe_image_input()
            return
        self._model_name_pending = True
        self._connection_state = "checking"
        try:
            if self._hostname and self._hostname.endswith(".local"):
                new_url = _resolve_local_hostname_async(self._original_base_url)
                if new_url != self._original_base_url and new_url != self.base_url:
                    self.base_url = new_url
                    self._replace_client()
            diagnosis = await self.diagnose_connection()
            if not diagnosis.ok:
                self._connection_state = "error"
                return
            if self.serves_one_model:
                try:
                    actual = await self.query_model_name()
                    if actual and actual != self._selected_model:
                        logger.warning(
                            "Endpoint '%s' serves '%s' (requested '%s'); using "
                            "the served model.", self.name, actual, self._selected_model,
                        )
                    if actual:
                        self._selected_model = actual
                        self._model_resolved = True
                except Exception as e:
                    logger.warning("Could not resolve served model: %s", e)
            if not self._model_resolved:
                try:
                    await self.get_model_name()
                    self._connection_state = "ok"
                except Exception as e:
                    logger.warning("prewarm model name failed: %s", e)
                    self._connection_state = "error"
            else:
                self._connection_state = "ok"
            try:
                await self.get_context_window()
            except Exception as e:
                logger.warning("prewarm context window failed: %s", e)
            await self.probe_image_input()
        finally:
            self._model_name_pending = False

    def context_window(self) -> Optional[int]:
        """The current model's context window as known now: the live
        catalog's, else the last one resolved by :meth:`get_context_window`."""
        ctx = catalog_entry(self.name, self._selected_model).get(
            "context_window")
        return ctx if isinstance(ctx, int) and ctx > 0 else self._cached_context_window

    def _probed(self, model_name: Optional[str]) -> dict:
        """This model's probed facts, valid until this server's catalog is
        refreshed again (a refresh may mean the server changed)."""
        generation = _refresh_generation.get(self.name, 0)
        if generation != self._probed_generation:
            self._probed_generation, self._probed_facts = generation, {}
        return self._probed_facts.setdefault(model_name or "", {})

    def accepts_images(self) -> Optional[bool]:
        """Whether the current model reads images; ``None`` when unknown."""
        model_name = self._selected_model or ""
        known = image_input_from_metadata(self._catalog_metadata(model_name))
        return known if known is not None else self._probed(model_name).get("image_input")

    async def probe_image_input(self) -> None:
        """Learn whether the current model reads images (no-op once known)."""
        model_name = self._selected_model
        if not model_name or self.accepts_images() is not None:
            return
        try:
            known = await self.query_image_input(model_name)
        except Exception as e:
            logger.debug("image input probe failed: %s", e)
            return
        if known is not None:
            self._probed(model_name)["image_input"] = known

    # -- discovery (implemented by each provider) ----------------------------

    async def list_models(self) -> list[ModelInfo]:
        """List models exposed by this endpoint."""
        raise NotImplementedError

    async def discover_models(self) -> list[ModelInfo]:
        """The models this endpoint surfaces (default: every listed one)."""
        return await self.list_models()

    async def query_model_name(self) -> str:
        """The model id requests use, asked of the server if needed."""
        raise NotImplementedError

    async def query_context_window(self, model_name: str) -> int:
        """The model's context window as the server reports it."""
        raise NotImplementedError

    async def query_image_input(self, model_name: str) -> Optional[bool]:
        """Ask the server whether *model_name* reads images; ``None`` when unknown."""
        return None

    async def get_model_name(self) -> str:
        """Get the model name (cached or queried)."""
        if self._model_resolved and self._selected_model:
            return self._selected_model
        try:
            self._selected_model = await self.query_model_name()
            self._model_resolved = True
            logger.info("Queried model name: %s", self._selected_model)
            return self._selected_model
        except Exception as e:
            logger.warning("Failed to query model name: %s", e)
        if self._selected_model:
            self._model_resolved = True
            logger.info("Using model from config: %s", self._selected_model)
            return self._selected_model
        # Not cached: an offline server must be re-queried once it is back.
        logger.warning("Model name unknown, using 'unknown'")
        return "unknown"

    async def get_context_window(self) -> int:
        """Get the context window size (cached or queried).

        The live catalog's value wins; a queried value is kept per model
        until this server's catalog is refreshed (:meth:`_probed`).
        The fallback is only shown, not memoized, so a later probe can still
        find the real window after a transient failure.
        """
        model_name = await self.get_model_name()
        ctx = catalog_entry(self.name, model_name).get("context_window")
        if isinstance(ctx, int) and ctx > 0:
            self._cached_context_window = ctx
            return ctx
        probed = self._probed(model_name)
        if "context_window" in probed:
            self._cached_context_window = probed["context_window"]
            return self._cached_context_window
        try:
            self._cached_context_window = await self.query_context_window(model_name)
            probed["context_window"] = self._cached_context_window
            logger.info("Queried context window: %s", self._cached_context_window)
            return self._cached_context_window
        except Exception as e:
            logger.warning("Failed to query context window: %s", e)
        if self.max_context:
            self._cached_context_window = self.max_context
        else:
            self._cached_context_window = 32768
        logger.warning("Context window unknown, using default: %s", self._cached_context_window)
        return self._cached_context_window

    # -- connection ----------------------------------------------------------

    async def check_connection(self) -> bool:
        """Check if the endpoint is reachable."""
        return (await self.diagnose_connection()).ok

    async def diagnose_connection(self) -> "ConnectionDiagnosis":
        """Attempt a connection and return detailed diagnostics on failure."""
        error = None
        self._connection_state = "checking"
        try:
            await asyncio.wait_for(self.client.get("/models"), timeout=self.timeout)
            self._connection_state = "ok"
            return ConnectionDiagnosis(ok=True, url=self.base_url, error=None)
        except Exception as e:
            error = e

        if self._hostname and self._hostname.endswith(".local"):
            invalidate_local_hostname(self._original_base_url)
            new_url = await _resolve_local_hostname_await(self._original_base_url)
            if new_url != self._original_base_url:
                self.base_url = new_url
                self._replace_client()
                try:
                    await asyncio.wait_for(self.client.get("/models"), timeout=self.timeout)
                    self._connection_state = "ok"
                    return ConnectionDiagnosis(ok=True, url=self.base_url, error=None)
                except Exception as e2:
                    error = e2

        self._connection_state = "error"
        return ConnectionDiagnosis(
            ok=False,
            url=self.base_url,
            error=error,
            original_url=self._original_base_url,
            hostname=self._hostname,
        )

    # -- chat completion -----------------------------------------------------

    async def stream(
        self,
        messages: list[Dict[str, Any]],
        tools: Optional[list[Dict[str, Any]]] = None,
    ) -> AsyncGenerator[Chunk, None]:
        """Stream a chat completion for the selected model and effort.

        A 503 (model loading) or a network error retries the whole request
        with backoff, up to ``retry_attempts``; any other HTTP error is raised.
        """
        self._in_flight += 1
        try:
            max_retries = self.retry_attempts
            retry_delay = self.retry_delay
            for attempt in range(max_retries):
                try:
                    async for chunk in self._stream(messages, tools):
                        yield chunk
                    return
                except (httpx.HTTPStatusError, httpx.RequestError,
                        asyncio.TimeoutError, httpx.TimeoutException) as e:
                    loading = isinstance(e, httpx.HTTPStatusError)
                    if loading and e.response.status_code != 503:
                        raise
                    if attempt < max_retries - 1:
                        logger.warning(
                            "%s, retrying in %.1fs (attempt %d/%d)",
                            "Model loading (503)" if loading else f"Request failed ({type(e).__name__})",
                            retry_delay, attempt + 1, max_retries,
                        )
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 1.5
                        continue
                    if loading:
                        raise httpx.HTTPStatusError(
                            f"Model loading timeout after {max_retries} attempts: {e}",
                            request=e.request, response=e.response,
                        ) from e
                    raise
        finally:
            self._in_flight -= 1
            await self._close_stale_clients()

    def _stream(
        self,
        messages: list[Dict[str, Any]],
        tools: Optional[list[Dict[str, Any]]],
    ) -> AsyncGenerator[Chunk, None]:
        """One streamed request (no retries), as :class:`Chunk` objects."""
        raise NotImplementedError

    @staticmethod
    async def _raise_for_status(response: httpx.Response) -> None:
        """Raise on an HTTP error; a 503's message is the server's body."""
        if response.status_code == 503:
            body = (await response.aread()).decode(errors="replace")
            raise httpx.HTTPStatusError(body, request=response.request, response=response)
        response.raise_for_status()

    def effort_payload(self) -> dict[str, Any]:
        """The chosen effort in this server's request field (``{}`` if none).

        Always sent as chosen, never checked against the discovered levels:
        a stale or incomplete catalog must not silently drop it. The server
        decides (metallama rejects an unknown level with a 400).
        """
        raise NotImplementedError

    # -- connection pool lifecycle -------------------------------------------

    def _replace_client(self) -> None:
        """Swap in a fresh client (new base URL); the old one is retired."""
        self._stale_clients.append(self.client)
        self.client = _new_http_client(self)
        self._schedule_stale_close()

    def retire(self) -> None:
        """This endpoint is being replaced: close its connections once idle."""
        self._stale_clients.append(self.client)
        self._schedule_stale_close()

    async def aclose(self) -> None:
        """Close every connection now (a throwaway endpoint, e.g. discovery)."""
        self._stale_clients.append(self.client)
        await self._close_stale_clients()

    def _schedule_stale_close(self) -> None:
        try:
            asyncio.get_running_loop().create_task(self._close_stale_clients())
        except RuntimeError:        # no loop (tests, shutdown): nothing to close on
            pass

    async def _close_stale_clients(self) -> None:
        if self._in_flight:
            return                  # the last request in flight closes them
        stale, self._stale_clients = self._stale_clients, []
        for client in stale:
            try:
                await client.aclose()
            except Exception as e:
                logger.debug("closing a retired client failed: %s", e)

    def _catalog_metadata(self, model_name: Optional[str]) -> dict:
        return catalog_entry(self.name, model_name).get("metadata") or {}


def make_endpoint(name: str, data: dict[str, Any]) -> Endpoint:
    """Build an endpoint from a ``servers.toml`` ``[servers.<name>]`` table
    (already validated by ``settings``), as an instance of its type's class."""
    from moka_code.harness.providers import REGISTRY

    cls = REGISTRY[data["type"]]
    api_key = data.get("api_key", "")
    api_key_env = data.get("api_key_env")
    if api_key_env:
        api_key = os.getenv(api_key_env, api_key)
    extras = {key: data[key] for key in cls.extra_keys if key in data}
    return cls(
        name=name,
        base_url=data.get("base_url"),
        api_key=api_key,
        max_context=data.get("max_context"),
        timeout=data.get("timeout", 30.0),
        retry_attempts=data.get("retry_attempts", 3),
        retry_delay=data.get("retry_delay", 2.0),
        **extras,
    )


def get_active_endpoint() -> Optional[Endpoint]:
    """Build the endpoint currently selected in ``settings``; ``None`` when
    nothing is selected or the selected server is not configured (never a
    guessed fallback)."""
    from moka_code import settings

    name = settings.config.active_server
    return get_endpoint(name) if name else None


def get_endpoint(name: str) -> Optional[Endpoint]:
    """Build a configured endpoint by name, applying its model selection.

    Model facts (context window, image support, effort levels) are read from
    the live catalog (:func:`refresh_catalog`) when needed, never copied.
    """
    from moka_code import settings

    data = settings.config.servers.get(name)
    if data is None:
        return None
    endpoint = make_endpoint(name, data)
    selected = settings.config.get_model_for_server(name)
    if selected is not None:
        endpoint._selected_model = selected
    endpoint.source = (dict(data), selected)
    endpoint.effort = settings.config.get_effort(name, selected or "")
    return endpoint


# Cap on one server's discovery so an unreachable one cannot stall a refresh.
DISCOVERY_TIMEOUT = 8.0


def catalog_entry(server: Optional[str], model_name: Optional[str]) -> dict:
    """The live catalog's entry for a server's model (``{}`` if unknown)."""
    from moka_code import settings

    for model in settings.config.models_by_server.get(server or "", []):
        if model.get("id") == model_name:
            return model
    return {}


def unserved_model(server: Optional[str], model_name: Optional[str]) -> Optional[list[str]]:
    """The ids ``server`` lists when ``model_name`` is not among them, else
    ``None`` (served, or nothing reliable to compare: never discovered, stale,
    or a llama.cpp server, which serves whatever it loaded)."""
    from moka_code import settings
    from moka_code.harness.providers import REGISTRY

    listed = [m.get("id") for m in settings.config.models_by_server.get(server or "", [])]
    data = settings.config.servers.get(server or "") or {}
    cls = REGISTRY.get(data.get("type"))
    if (not listed or not model_name or server in settings.config.stale_servers
            or (cls is not None and cls.serves_one_model)):
        return None
    for model_id in listed:
        if model_id in (model_name, f"{model_name}:latest") or model_id.endswith("/" + model_name):
            return None     # exact, Ollama's implicit tag, OpenRouter's bare id
    return listed


# Per server, the number of the latest refresh started: only its result is
# applied, so an older refresh finishing late cannot overwrite a newer one.
_refresh_generation: dict[str, int] = {}


async def refresh_catalog(names: Optional[list[str]] = None) -> None:
    """Rediscover the models of ``names`` (default: every server), in parallel.

    The catalog lives in memory only (never persisted). A server that cannot
    be listed (busy or down) keeps this session's last successful discovery,
    marked stale in ``settings.config.stale_servers``, until a refresh
    succeeds; a server never discovered stays absent.
    """
    from moka_code import settings

    async def one(name: str) -> None:
        generation = _refresh_generation.get(name, 0) + 1
        _refresh_generation[name] = generation
        endpoint = get_endpoint(name)
        try:
            if endpoint is None:
                raise LookupError(name)
            models = await asyncio.wait_for(endpoint.discover_models(), DISCOVERY_TIMEOUT)
        except Exception as e:
            logger.debug("discovery failed for %s: %s", name, e)
            models = None
        finally:
            if endpoint is not None:
                await endpoint.aclose()     # a throwaway: never leave its pool open
        if _refresh_generation.get(name) != generation:
            return                          # a newer refresh owns the result
        if models is None:
            if name in settings.config.models_by_server:
                settings.config.stale_servers.add(name)
            return
        settings.config.models_by_server[name] = [m.to_dict() for m in models]
        settings.config.stale_servers.discard(name)

    await asyncio.gather(*(one(n) for n in (names or list(settings.config.servers))))


# Reasoning effort words, weakest first (OpenAI / OpenRouter vocabulary). A
# model id suffix ``:<word>`` marks an effort variant (``/effort`` switches
# between sibling ids).
EFFORT_WORDS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def efforts_from_metadata(metadata: dict) -> list[str]:
    """Effort levels a model takes as a request parameter, from catalog
    metadata: OpenRouter's ``reasoning.supported_efforts`` (exact levels),
    else ``supported_parameters`` (levels guessed); Ollama's ``thinking``
    capability (only gpt-oss takes levels, others just on/off)."""
    supported = (metadata.get("reasoning") or {}).get("supported_efforts")
    if isinstance(supported, list) and supported and all(isinstance(e, str) for e in supported):
        rank = {w: i for i, w in enumerate(EFFORT_WORDS)}
        return sorted(supported, key=lambda e: rank.get(e, len(rank)))
    params = metadata.get("supported_parameters") or []
    if "reasoning_effort" in params or "reasoning" in params:
        return ["none", "low", "medium", "high"]
    if "thinking" in (metadata.get("capabilities") or []):
        name = str(metadata.get("model") or metadata.get("name") or "")
        return ["none", "low", "medium", "high"] if "gpt-oss" in name else ["none"]
    return []


def image_input_from_metadata(metadata: dict) -> bool | None:
    """Image support recorded in catalog metadata; ``None`` when absent.

    OpenRouter lists ``architecture.input_modalities``; Ollama's ``/api/show``
    lists ``capabilities`` (``"vision"``).
    """
    modalities = (metadata.get("architecture") or {}).get("input_modalities")
    if isinstance(modalities, list):
        return "image" in modalities
    capabilities = metadata.get("capabilities")
    if isinstance(capabilities, list):
        return "vision" in capabilities
    return None


__all__ = [
    "Chunk",
    "Endpoint",
    "ModelInfo",
    "ToolCallPiece",
    "ConnectionDiagnosis",
    "make_endpoint",
    "get_active_endpoint",
    "get_endpoint",
    "_is_local_target",
    "_new_http_client",
    "_local_cache",
    "_resolve_local_hostname",
    "invalidate_local_hostname",
    "prewarm_local_resolution",
    "is_local_resolution_pending",
]

