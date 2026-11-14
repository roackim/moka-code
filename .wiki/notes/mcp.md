# MCP servers

> Describes what exists. Direction lives in `principles.md`.

MCP (Model Context Protocol) is a **tool source**, not a model provider. The
core registry stays the five built-in tools; a configured server's tools are
added dynamically and gated by the same role and permission gate.

## Shape

```
settings.config.mcp_servers          # mcp.toml, connection-only
        │
        ▼
moka_code/mcp.py
  McpClient  ── initialize → tools/list → tools/call
    ├─ _StdioConnection   (child process, newline JSON-RPC)
    └─ _HttpConnection    (Streamable HTTP; JSON or SSE response)
  McpTransport            (ToolTransport: is_sandbox = False)
  McpManager              (live clients + discovery, refresh/close)
        │
        ▼
Harness._rebuild_tools()  ── merges registered tools for role.enabled_mcp_servers()
```

The MCP module is the counterpart of `harness/endpoint.py`: settings holds the
validated tables, the module builds live clients. MCP tools are ordinary
`RegisteredTool` objects, so the loop, permission gate, elision, image handling
and schema plumbing are unchanged.

## Config (`mcp.toml`)

One `[servers.<name>]` table per server, structured and user-authored (never
line-synced), mirroring `servers.toml`:

- `type = "stdio"`: `command` (required), `args` (`[str]`), `env` (table).
- `type = "http"`: `url` (required), `headers` (table).
- Common: `tool_timeout` (seconds, default 60).
- A table named `stdio`/`http` implies `type`; any other name must write it.
- `$NAME` / `${NAME}` in `env` and `headers` read the environment.
- Unknown keys, missing required keys and wrong types are load errors; the bad
  server is skipped, the rest still load.

## Roles

`roles/<name>.toml` has two tables:

```toml
[tools]        # the five built-ins
read = "yes"

[mcps]         # one line per configured MCP server; unset = no
github = "ask"
```

All tools of a server share its `no`/`ask`/`yes`. `Role.permission_for` maps a
qualified `<server>__<tool>` name through `[mcps]`. Legacy flat role files are
migrated on startup: the old top-level tool lines move under a `[tools]` table,
and an `[mcps]` table is added (`_migrate_role_file`).

## Naming

Discovered tools are exposed as `<server>__<tool>`, sanitized to
`[A-Za-z0-9_-]` and capped at 64 chars, so two servers cannot collide and the
name is a valid OpenAI function name. The original tool name is sent back in
`tools/call`.

## Lifecycle

- Connection is lazy: the first `Harness.chat()`/`resume()` calls
  `mcp.ensure_ready()`, then rebuilds the tool map. A slow/broken server never
  delays startup or blocks a turn.
- `/reload` marks the manager stale and schedules a background refresh; the next
  turn rebuilds the tools.
- Failures are per server (`mcp.status()`), shown in the status notices.
- `main()` calls `mcp.close_sync()` on exit so stdio child processes are
  terminated even after the event loop closed.

## Security

MCP servers run on the host, outside any moka sandbox (`McpTransport.is_sandbox
= False`). The server is its own trust boundary; the role's `[mcps]` line is the
approval gate. See `security.md`.

## UI

`/mcp` is a viewer (servers, transport, state, tool count, active role setting);
`/mcp <server>` lists that server's tools. `mcp.toml` is edited with
`/config mcp`.
