# `.local` (mDNS) Hostname Resolution

moka's endpoint layer (`moka_chat/harness/endpoint.py`, with resolution in
`moka_chat/harness/endpoint_local.py`) has special handling for `*.local`
hostnames (mDNS / Bonjour / Avahi), which are common for local servers on a LAN
(e.g. `http://llm-mini-server.local:8080`).

## Transport: raw httpx (no OpenAI SDK)

The OpenAI-compatible chat transport is implemented **directly on httpx** — the
`openai` SDK was removed. moka owns the connection, DNS/IP resolution, timeouts,
retries and connection pooling, giving full visibility into how requests are
sent (and why they're slow). Only the three supported server families are
covered: llama.cpp / Ollama / OpenRouter (+ generic OpenAI-compatible).

- One `httpx.AsyncClient` per server (`_new_http_client`), reused across
  messages in a conversation (keep-alive connections, no per-message handshake).
- Streaming is done by parsing SSE (`data:` lines) from `POST /chat/completions`;
  chunks are adapted (`_adapt_stream_chunk` / `_adapt_chat_response`) to the
  shape the old SDK produced so downstream harness/UI code is unchanged.
- Ollama uses its native `/api/chat` endpoint (which preserves usage counters).

## The problem

httpx connects via `getaddrinfo`. For a `.local` name this can return a
bare IPv6 link-local address (`fe80::…`) **without an interface scope index**.
Connecting to `fe80::` with no zone fails ("no route to host"), even though the
name *lookup* succeeds — so moka could not reach servers that `ping`/`curl` reach
fine. This is especially common under **WSL2** (NAT'd multicast) and where DNS
is otherwise healthy.

## The fix

For `.local` hosts, moka resolves the name to an IPv4 address and swaps it into
the URL. Resolution is done **in-process via `socket.getaddrinfo`** (the same
libc resolver the shell uses — nsswitch + mDNS), with a `getent hosts` subprocess
as a fallback. A bare `getent` subprocess can hang on mDNS when the process
environment differs from the shell (e.g. under `pixi run` / WSL), which made the
request fall back to the slow `.local` name; in-process resolution avoids that.

- `_resolve_local_hostname(url)` — performs the rewrite synchronously (blocking).
  Used by tests and as the underlying blocking resolver.
- `_resolve_local_hostname_async(url)` — non-blocking variant used by
  `LLMServer.__init__`: returns the resolved URL if already cached, otherwise
  kicks off a background resolution and returns the original URL unchanged, so
  construction never blocks on mDNS.
- `_resolve_local_hostname_await(url)` — awaitable variant that runs the
  blocking resolver in a thread executor (`asyncio.to_thread`), used by
  `diagnose_connection()`'s retry path so the event loop stays responsive even
  when the mDNS lookup stalls on an offline host.
- Non-`.local` hosts and any resolver failure return the original URL unchanged.

## Pre-warming

`prewarm_local_resolution(url)` kicks off `.local` resolution in a background
thread (off the event loop) and populates the cache, so the first message in a
conversation doesn't stall on DNS/mDNS. It's called when the initial agent is
set up and when a new tab/conversation is opened (`ui/app.py`). Non-`.local`
hosts and already-cached hosts are no-ops.

While resolution is in progress, `is_local_resolution_pending(url)` returns
True; the status bar shows an animated spinner next to the model name
(`ui/app.py` advances a `SPINNER_FRAMES` frame on each `TickEvent`).

## Model-name pre-warm

`LLMServer.prewarm_model_name()` discovers and caches the model name (and
context window) in the background at tab/conversation open, so the status bar
shows the real model (e.g. for llama.cpp) instead of `?`. `refresh_status_bar`
falls back to `server._cached_model_name` once populated.

It also drives the status-bar connection color. It first runs a real
`diagnose_connection()` probe so the color reflects true reachability — green
only when the server actually responds, red when it's unreachable, orange while
the probe is in flight. (`get_model_name()` swallows network errors and falls
back to the configured model, so it can't be used to judge reachability.)

## Caching + invalidation

Resolutions are cached per-hostname for **the process lifetime** (no TTL) so a
`getent` subprocess runs at most once per host:
- `_cached_ip_for(hostname)` / `_resolve_once(hostname)`
- `invalidate_local_hostname(url)` drops the cache entry

`LLMServer.check_connection()`/`diagnose_connection()` use this for self-healing:
on a connect failure for a `.local` host they invalidate the cached address,
re-resolve once, rebuild the httpx client, and retry exactly once. So the cache
is effectively permanent while a server stays reachable, and only refreshes when
a failed connection proves the entry stale. Non-`.local` hosts are tried once.

## Proxy-avoidance for local/LAN targets

Even with the hostname resolved to an IP, moka could still fail while `curl`
succeeds — the classic WSL2 gotcha. httpx defaults to `trust_env=True` and
silently consumes `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`, routing LAN traffic
through a bogus inherited proxy.

- `_is_local_target(url)` — detects `localhost`, `*.local`, and RFC1918
  (192.168./10./172.16-31.) plus link-local targets.
- `_new_http_client(config)` — for local targets sets `trust_env=False`, so the
  single owned httpx client reaches the LAN server directly.

## Per-request latency: context-window fetch not cached

`Harness._build_messages()` used to run on **every message** and call
`get_context_window()` to build the system prompt. It is currently cached on the
endpoint — **but `get_context_window()` only cached the successful result**. If
`query_context_window()` failed (e.g. llama.cpp `/props` returns 404 / no
`n_ctx`), the `except` path fell back to a default **without caching**, so a
failing/slow query was re-run (opening a fresh `httpx.AsyncClient` + hitting
`/props` + `list_models()`) on *every message* — the source of "10s before the
server receives the request" within one conversation.

Fix: `get_context_window()` now caches the **fallback** value (`max_context` or
32768) in `_model_context_windows` too, so a failing query is tried exactly once
and later messages use the cached default. (The message-build path no longer
fetches the context window at all — the system prompt is the role's `prompt`.)

## Connection diagnostics

`check_connection()` historically swallowed the real error and returned a bare
bool, hiding *why* a connect failed. Now:

- `Endpoint.diagnose_connection()` → `ConnectionDiagnosis` — returns the
  underlying exception, the resolved URL (vs original), and builds a hint-rich
  report mentioning active proxy env vars and DNS resolution.
- There is no `/server diagnose` command (the `/server` command was removed);
  server configuration is edited via `/config servers`.