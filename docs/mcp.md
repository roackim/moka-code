# MCP servers

moka can use tools exposed by [Model Context Protocol](https://modelcontextprotocol.io)
servers. MCP is a **tool source**, not a model provider: the core stays five
built-in tools, and a configured server's tools join them.

## Transports

Two standard transports are supported:

- **stdio** — moka launches a local server as a subprocess and talks JSON-RPC
  over stdin/stdout. This is what most local servers use (`npx`, `uvx`, a server
  binary).
- **Streamable HTTP** — a remote server reached over HTTP. The older HTTP+SSE
  transport is not supported.

## Configure

MCP servers live in `~/.config/moka/mcp.toml` (or `$MOKA_CONFIG_DIR/mcp.toml`).
Edit it with `/config mcp`; moka reloads when the editor closes, or run
`/reload` after a hand edit.

Everything is commented out until you uncomment a block:

```toml
## A local server launched as a subprocess.
[servers.filesystem]
type    = "stdio"
command = "npx"
args    = ["-y", "@modelcontextprotocol/server-filesystem", "/home/me"]
# env   = { API_TOKEN = "$MCP_TOKEN" }
# tool_timeout = 60

## A remote server over Streamable HTTP.
[servers.remote]
type = "http"
url  = "https://mcp.example.com/mcp"
# headers = { Authorization = "Bearer $TOKEN" }
```

`$NAME` reads the environment variable when moka starts; use it instead of
pasting secrets. A table named after a transport (`[servers.stdio]`,
`[servers.http]`) may omit `type`; any other name must write it. Each server's
`tool_timeout` caps one `tools/call` (default 60 s).

## Let a role use a server

Tools are only exposed through a role. In `roles/<name>.toml`, the `[mcps]`
table has one line per server, using the same `no` / `ask` / `yes` values as
the built-in `[tools]` table:

```toml
[tools]
read = "yes"
write = "ask"

[mcps]
filesystem = "ask"     # all of this server's tools ask first
github     = "no"      # configured, but disabled for this role
```

A server not listed is `no`, so adding an MCP server to `mcp.toml` never
enables it on its own. Edit the role with `/role edit`.

The model sees each tool as `<server>__<tool>` (for example
`filesystem__read_file`); the prefix keeps two servers with the same tool name
apart.

## Inspect

`/mcp` lists every configured server with its transport, connection state, tool
count and the active role's setting. `/mcp <server>` lists that server's tools.
The viewer never edits configuration.

## Security

MCP servers run on the **host**, not inside a moka sandbox. The server is its
own trust boundary: it decides what its tools can reach. A sandbox selected with
`/sandbox` confines the built-in file and shell tools, not MCP servers. stdio
servers inherit moka's environment (plus their configured `env`), so only add
servers you trust, and use `ask` in the role when you want to approve each call.

## Troubleshooting

- `unavailable` with a start error: check the `command` is on `PATH` and the
  `args` are right; stdio servers are launched themselves.
- `unavailable` with an HTTP error: check `url`, `headers`, and any `$NAME`
  variables (an unset variable expands to empty).
- A server is connected but a role cannot see its tools: add it under `[mcps]`
  in that role and run `/reload`.
- A role errors with `unknown MCP server`: it lists a server that is no longer
  in `mcp.toml`. Remove the line with `/role edit`, or restore the server.
- A tool call fails: the server's `isError` text is returned to the model as
  the tool result, so it can react and retry.
