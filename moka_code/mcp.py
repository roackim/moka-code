"""MCP (Model Context Protocol) servers: client, discovery and tool transport.

This is the MCP counterpart of :mod:`moka_code.harness.endpoint`: ``settings``
holds the validated ``mcp.toml`` connection tables, and this module turns them
into live clients. The harness only sees ordinary ``RegisteredTool`` objects
executed through the :class:`~moka_code.harness.tools.ToolTransport` seam, so
MCP tools behave exactly like the built-in ones (same registry, same schemas,
same permission gate).

Two transports:

- ``stdio`` — the server is a subprocess speaking newline-delimited JSON-RPC on
  stdin/stdout (the common local case: ``npx``/``uvx``/server binaries);
- ``http`` — Streamable HTTP (POST JSON-RPC, JSON or SSE response).

MCP servers run on the host, outside any moka sandbox: the server is its own
trust boundary. A role enables a server through its ``[mcps]`` table; the
server's tools are exposed as ``<server>__<tool>``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

#: Protocol version we advertise. Servers answer with the version they support.
PROTOCOL_VERSION = "2025-03-26"

#: Seconds to connect + list a server's tools before it is reported unavailable.
CONNECT_TIMEOUT = 15.0

#: Seconds one ``tools/call`` may take unless the server table overrides it.
DEFAULT_TOOL_TIMEOUT = 60.0

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


class McpError(Exception):
    """A JSON-RPC error or a broken MCP connection."""


def expand_env(value: str) -> str:
    """Expand ``$NAME`` / ``${NAME}`` in a config string (unset -> empty)."""
    def replace(match: re.Match) -> str:
        return os.environ.get(match.group(1) or match.group(2), "")

    return _ENV_RE.sub(replace, value)


def _expand_table(table: dict[str, str]) -> dict[str, str]:
    return {str(key): expand_env(str(value)) for key, value in table.items()}


# ---------------------------------------------------------------------------
# Tool records
# ---------------------------------------------------------------------------

@dataclass
class McpTool:
    """One tool discovered from a server, ready for the LLM."""

    server: str
    name: str              # the server's own name, sent back in tools/call
    qualified: str         # <server>__<tool>, the name the model sees
    description: str
    input_schema: dict


@dataclass
class McpServerState:
    """Runtime state of one configured server."""

    name: str
    config: dict
    client: Optional["McpClient"] = None
    tools: list[McpTool] = field(default_factory=list)
    connected: bool = False
    error: Optional[str] = None


def _sanitize_tool_name(server: str, tool: str) -> str:
    """A valid OpenAI function name (``[A-Za-z0-9_-]``, at most 64 chars)."""
    base = f"{server}__{tool}"
    name = re.sub(r"[^A-Za-z0-9_-]", "_", base)
    if len(name) > 64:
        digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:6]
        name = name[:57] + "_" + digest
    return name


def _make_tools(server: str, raw_tools: list[dict]) -> list[McpTool]:
    tools: list[McpTool] = []
    used: set[str] = set()
    for raw in raw_tools:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        qualified = _sanitize_tool_name(server, name)
        # Two server tools whose sanitized names collide: keep both, suffixing.
        if qualified in used:
            digest = hashlib.sha1(f"{server}/{name}".encode("utf-8")).hexdigest()[:4]
            qualified = (qualified[:59] + "_" + digest)[:64]
        used.add(qualified)
        schema = raw.get("inputSchema")
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}}
        elif schema.get("type") != "object":
            schema = {**schema, "type": "object"}
        tools.append(McpTool(
            server=server,
            name=name,
            qualified=qualified,
            description=str(raw.get("description") or f"{server}: {name}"),
            input_schema=schema,
        ))
    return tools


def _format_result(result: Any) -> str:
    """Turn a ``tools/call`` result into the string the model receives."""
    if not isinstance(result, dict):
        return str(result)
    parts: list[str] = []
    is_error = bool(result.get("isError"))
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            parts.append(str(item))
            continue
        kind = item.get("type")
        if kind == "text":
            parts.append(str(item.get("text") or ""))
        elif kind == "image":
            mime = item.get("mimeType") or "image"
            data = item.get("data") or ""
            parts.append(f"[image: {mime}, {len(data)} base64 chars — not attached]")
        elif kind == "audio":
            mime = item.get("mimeType") or "audio"
            parts.append(f"[audio: {mime} — not attached]")
        elif kind == "resource":
            resource = item.get("resource") or {}
            if isinstance(resource, dict) and resource.get("text") is not None:
                parts.append(str(resource["text"]))
            else:
                uri = resource.get("uri", "?") if isinstance(resource, dict) else "?"
                parts.append(f"[resource: {uri}]")
        else:
            parts.append(json.dumps(item, ensure_ascii=False))
    if not parts and result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
    text = "\n".join(part for part in parts if part != "")
    if not text:
        text = "(no output)"
    return f"[MCP tool error] {text}" if is_error else text


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------

class _StdioConnection:
    """Newline-delimited JSON-RPC over a child process's stdin/stdout."""

    def __init__(self, command: str, args: list[str], env: dict[str, str],
                 on_stderr=None):
        self.command = command
        self.args = list(args)
        self.env = env
        self.on_stderr = on_stderr
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._write_lock = asyncio.Lock()
        self._closed = False

    async def start(self) -> None:
        try:
            self.proc = await asyncio.create_subprocess_exec(
                self.command, *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **self.env},
            )
        except FileNotFoundError as exc:
            raise McpError(f"command not found: {self.command}") from exc
        except OSError as exc:
            raise McpError(f"cannot start {self.command}: {exc}") from exc
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._read_stderr())

    async def _read_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", "replace").rstrip("\n")
            if not text:
                continue
            if self.on_stderr is not None:
                try:
                    self.on_stderr(text)
                except Exception:       # pragma: no cover - logging is best effort
                    pass
            else:
                logger.debug("mcp stderr: %s", text)

    async def _read_loop(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        try:
            while not self._closed:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    logger.debug("mcp: non-JSON output: %s", text)
                    continue
                if not isinstance(message, dict):
                    continue
                if "id" in message and ("result" in message or "error" in message):
                    future = self._pending.pop(message["id"], None)
                    if future is not None and not future.done():
                        if "error" in message:
                            error = message["error"] or {}
                            future.set_exception(McpError(
                                f"{error.get('message', 'MCP error')} "
                                f"(code {error.get('code', '?')})"))
                        else:
                            future.set_result(message.get("result"))
                elif "method" in message and "id" in message:
                    # A server request (sampling/roots). Nothing here needs it;
                    # answer method-not-found rather than leave it hanging.
                    asyncio.create_task(self._reply_method_not_found(message["id"]))
                # Notifications are log-only for now.
        finally:
            self._fail_pending("MCP server closed the connection")

    async def _reply_method_not_found(self, request_id: Any) -> None:
        try:
            await self._write({
                "jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32601, "message": "Method not found"},
            })
        except Exception:               # pragma: no cover - best effort
            pass

    async def _write(self, payload: dict) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise McpError("MCP server is not running")
        async with self._write_lock:
            self.proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
            await self.proc.stdin.drain()

    def _fail_pending(self, message: str) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(McpError(message))
        self._pending.clear()

    async def request(self, method: str, params: Optional[dict] = None,
                      timeout: Optional[float] = None) -> Any:
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        try:
            await self._write(payload)
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise McpError(f"MCP request timed out: {method}") from exc
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: Optional[dict] = None) -> None:
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        await self._write(payload)

    async def close(self) -> None:
        self._closed = True
        self._fail_pending("MCP server closed")
        if self.proc is not None and self.proc.returncode is None:
            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), 3.0)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    self.proc.kill()
                except ProcessLookupError:
                    pass
        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
        self.proc = None

    def terminate_sync(self) -> None:
        """Best-effort shutdown when no event loop is available (app exit)."""
        self._closed = True
        if self.proc is not None and self.proc.returncode is None:
            try:
                self.proc.terminate()
            except ProcessLookupError:
                pass


class _HttpConnection:
    """Streamable HTTP: one POST per JSON-RPC message, JSON or SSE response."""

    def __init__(self, url: str, headers: dict[str, str],
                 timeout: float = DEFAULT_TOOL_TIMEOUT):
        self.url = url
        self.headers = dict(headers)
        self.timeout = timeout
        self.session_id: Optional[str] = None
        self.protocol_version = PROTOCOL_VERSION
        self._client: Optional[httpx.AsyncClient] = None
        self._next_id = 0

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=self.timeout)

    def _request_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self.protocol_version,
            **self.headers,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    @staticmethod
    def _parse_sse(text: str, request_id: Any) -> Any:
        """Return the JSON-RPC message in an SSE body whose id matches."""
        data_lines: list[str] = []
        messages: list[dict] = []
        for line in text.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            elif not line.strip() and data_lines:
                raw = "\n".join(data_lines)
                data_lines = []
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict):
                    messages.append(message)
        if data_lines:
            try:
                message = json.loads("\n".join(data_lines))
                if isinstance(message, dict):
                    messages.append(message)
            except json.JSONDecodeError:
                pass
        for message in messages:
            if message.get("id") == request_id:
                return message
        return messages[0] if messages else None

    async def _post(self, payload: dict, timeout: Optional[float] = None) -> Any:
        if self._client is None:
            raise McpError("MCP HTTP client is not started")
        response = await self._client.post(
            self.url, json=payload, headers=self._request_headers(),
            timeout=timeout or self.timeout,
        )
        if response.status_code >= 400:
            raise McpError(f"HTTP {response.status_code}: {response.text[:200]}")
        session_id = response.headers.get("Mcp-Session-Id")
        if session_id:
            self.session_id = session_id
        if not response.content:
            return None
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            return self._parse_sse(response.text, payload.get("id"))
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise McpError(f"invalid JSON response: {response.text[:200]}") from exc

    async def request(self, method: str, params: Optional[dict] = None,
                      timeout: Optional[float] = None) -> Any:
        self._next_id += 1
        request_id = self._next_id
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        message = await self._post(payload, timeout)
        if not isinstance(message, dict):
            raise McpError(f"empty MCP response to {method}")
        if "error" in message:
            error = message["error"] or {}
            raise McpError(f"{error.get('message', 'MCP error')} "
                           f"(code {error.get('code', '?')})")
        return message.get("result")

    async def notify(self, method: str, params: Optional[dict] = None) -> None:
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        await self._post(payload)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def terminate_sync(self) -> None:
        # An async HTTP client has no child process; the event loop closes it.
        self._client = None


class McpClient:
    """One configured MCP server: connect, discover tools, call them."""

    def __init__(self, name: str, config: dict, on_stderr=None):
        self.name = name
        self.config = dict(config)
        self.on_stderr = on_stderr
        self._conn: Any = None
        self.protocol_version = PROTOCOL_VERSION
        self.connected = False

    @property
    def tool_timeout(self) -> float:
        value = self.config.get("tool_timeout", DEFAULT_TOOL_TIMEOUT)
        return float(value) if value else 0.0

    async def _make_connection(self):
        transport = self.config.get("type")
        if transport == "stdio":
            env = _expand_table(self.config.get("env") or {})
            return _StdioConnection(
                self.config.get("command", ""),
                list(self.config.get("args") or []),
                env,
                on_stderr=self.on_stderr,
            )
        if transport == "http":
            headers = _expand_table(self.config.get("headers") or {})
            return _HttpConnection(
                self.config.get("url", ""), headers,
                timeout=self.tool_timeout or DEFAULT_TOOL_TIMEOUT,
            )
        raise McpError(f"unknown MCP transport '{transport}'")

    async def connect(self) -> None:
        self._conn = await self._make_connection()
        await self._conn.start()
        result = await self._conn.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "moka", "version": "1"},
        }, timeout=CONNECT_TIMEOUT)
        if isinstance(result, dict) and result.get("protocolVersion"):
            self.protocol_version = str(result["protocolVersion"])
            if isinstance(self._conn, _HttpConnection):
                self._conn.protocol_version = self.protocol_version
        await self._conn.notify("notifications/initialized")
        self.connected = True

    async def list_tools(self) -> list[dict]:
        tools: list[dict] = []
        cursor: Optional[str] = None
        while True:
            params = {} if cursor is None else {"cursor": cursor}
            result = await self._conn.request("tools/list", params,
                                              timeout=CONNECT_TIMEOUT)
            if not isinstance(result, dict):
                break
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    async def call_tool(self, name: str, arguments: dict,
                        on_output=None) -> str:
        timeout = self.tool_timeout or None
        result = await self._conn.request(
            "tools/call", {"name": name, "arguments": arguments or {}},
            timeout=timeout,
        )
        return _format_result(result)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
        self.connected = False

    def terminate_sync(self) -> None:
        if self._conn is not None:
            self._conn.terminate_sync()


class McpTransport:
    """The :class:`ToolTransport` for one MCP server's tools."""

    is_sandbox = False

    def __init__(self, client: McpClient, tool_names: dict[str, str]):
        self.client = client
        self.tool_names = dict(tool_names)

    async def execute(self, name: str, args: dict, on_output=None) -> str:
        original = self.tool_names.get(name, name)
        return await self.client.call_tool(original, args, on_output=on_output)

    def cancel_active(self, name: str) -> bool:
        # MCP has a cancellation notification, but the harness cancels the
        # awaiting task; no per-call process is owned here.
        return False


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

def _short_error(exc: Exception) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text if len(text) <= 200 else text[:197] + "..."


class McpManager:
    """Live clients for every configured server, refreshed explicitly."""

    def __init__(self):
        self.states: dict[str, McpServerState] = {}
        self.errors: dict[str, str] = {}
        self.stale = True
        self.refresh_task: Optional[asyncio.Task] = None
        self._lock: Optional[asyncio.Lock] = None
        self._lock_loop: Any = None

    def _get_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    def mark_stale(self) -> None:
        self.stale = True

    def request_refresh(self) -> None:
        """Refresh in the background if a loop is running (a sync reload path)."""
        self.stale = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self.refresh_task is None or self.refresh_task.done():
            self.refresh_task = loop.create_task(self.refresh())

    async def _discard(self, name: str) -> None:
        state = self.states.pop(name, None)
        if state is not None and state.client is not None:
            try:
                await state.client.close()
            except Exception:           # pragma: no cover - shutdown is best effort
                logger.debug("closing MCP server %s failed", name, exc_info=True)

    async def refresh(self, force: bool = False) -> None:
        lock = self._get_lock()
        async with lock:
            if not force and not self.stale and self.states:
                return
            from moka_code import settings

            configured = dict(settings.config.mcp_servers)
            for name in list(self.states):
                if name not in configured:
                    await self._discard(name)
            self.errors = {}

            async def one(name: str, config: dict) -> None:
                state = self.states.get(name)
                if (not force and state is not None and state.connected
                        and state.config == config):
                    return
                if state is not None:
                    await self._discard(name)
                state = McpServerState(name=name, config=dict(config))
                self.states[name] = state
                client = McpClient(name, config)
                try:
                    await asyncio.wait_for(client.connect(), CONNECT_TIMEOUT)
                    raw = await asyncio.wait_for(client.list_tools(), CONNECT_TIMEOUT)
                    state.client = client
                    state.tools = _make_tools(name, raw)
                    state.connected = True
                except Exception as exc:
                    state.error = _short_error(exc)
                    self.errors[name] = state.error
                    state.connected = False
                    state.tools = []
                    try:
                        await client.close()
                    except Exception:   # pragma: no cover - best effort
                        pass

            await asyncio.gather(*(one(name, config)
                                   for name, config in configured.items()))
            self.stale = False

    async def ensure_ready(self, force: bool = False) -> None:
        if force or self.stale:
            await self.refresh(force=force)

    async def close_all(self) -> None:
        for name in list(self.states):
            await self._discard(name)
        self.stale = True
        self.errors = {}

    def close_sync(self) -> None:
        """Best-effort teardown after the event loop is gone (app exit)."""
        for state in self.states.values():
            if state.client is not None:
                state.client.terminate_sync()
        self.states = {}
        self.errors = {}
        self.stale = True
        task = self.refresh_task
        if task is not None and not task.done():
            task.cancel()


_manager = McpManager()


# ---------------------------------------------------------------------------
# Public helpers used by the harness and the UI
# ---------------------------------------------------------------------------

def mark_stale() -> None:
    _manager.mark_stale()


def request_refresh() -> None:
    _manager.request_refresh()


async def ensure_ready(force: bool = False) -> None:
    await _manager.ensure_ready(force=force)


async def refresh(force: bool = True) -> None:
    await _manager.refresh(force=force)


async def close_all() -> None:
    await _manager.close_all()


def close_sync() -> None:
    _manager.close_sync()


def configured_names() -> list[str]:
    from moka_code import settings

    return sorted(settings.config.mcp_servers)


def status() -> list[dict]:
    """One record per configured server, for the ``/mcp`` viewer."""
    from moka_code import settings

    records: list[dict] = []
    for name in sorted(settings.config.mcp_servers):
        config = settings.config.mcp_servers.get(name, {})
        state = _manager.states.get(name)
        records.append({
            "name": name,
            "type": config.get("type", "?"),
            "connected": bool(state is not None and state.connected),
            "error": (state.error if state is not None else _manager.errors.get(name)),
            "tools": list(state.tools) if state is not None else [],
        })
    return records


def registered_tools(servers: set[str]) -> dict[str, Any]:
    """``RegisteredTool`` objects for the enabled servers' discovered tools."""
    from moka_code.harness.tools import RegisteredTool, ToolDefinition

    def _unused_handler(toolset, **kwargs):
        raise RuntimeError("MCP tools are executed by their own transport")

    tools: dict[str, RegisteredTool] = {}
    for server in sorted(servers):
        state = _manager.states.get(server)
        if state is None or not state.connected or not state.tools:
            continue
        tool_names = {tool.qualified: tool.name for tool in state.tools}
        transport = McpTransport(state.client, tool_names)
        for tool in state.tools:
            definition = ToolDefinition(
                name=tool.qualified,
                description=tool.description,
                parameters=tool.input_schema,
                handler=_unused_handler,
            )
            tools[tool.qualified] = RegisteredTool(definition, transport)
    return tools


__all__ = [
    "DEFAULT_TOOL_TIMEOUT",
    "McpClient",
    "McpError",
    "McpManager",
    "McpTool",
    "McpTransport",
    "close_all",
    "close_sync",
    "configured_names",
    "ensure_ready",
    "expand_env",
    "mark_stale",
    "refresh",
    "registered_tools",
    "request_refresh",
    "status",
]
