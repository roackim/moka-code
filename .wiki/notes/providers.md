# Provider contract

> **Status: APPROVED (2026-10-01), not implemented.** This page describes the
> target of `PLAN.md` step 1. Until it is built,
> `tree/harness.md` and `architecture.md` describe what exists. Once built,
> this page becomes the state document and this banner goes.

A *provider* is a server family moka can talk to: llama.cpp, OpenRouter,
OpenAI and OpenAI-compatible servers (Ollama was removed on 2026-10-01,
§9.11). Today their differences are about two dozen `type == ...` branches spread over `endpoint.py`,
`endpoint_discovery.py`, `endpoint_openai.py`, `settings.py` and
`unserved_model`. This page defines the one seam that replaces them, and what
each side promises the other.

The slot table (§4) is the checklist of what a provider must define; the
class structure is the one decided in `PLAN.md`.

---

## 1. Shape

An `Endpoint` *is* an instance of its provider's class. The base class holds
the contract and the per-server state; a registry maps `type` to class.

```
Endpoint                      contract + per-server config and state
├─ OpenAICompatible           type "openai-compatible": /chat/completions,
│  │                          /models, SSE (OpenAI itself, vLLM, LM Studio,
│  │                          your own proxy)
│  ├─ LlamaCpp                type "llamacpp": adds /props, default URL
   └─ OpenRouter              type "openrouter": fixed URL, model whitelist,
                              provider routing

REGISTRY = {"openai-compatible": OpenAICompatible, "llamacpp": LlamaCpp,
            "openrouter": OpenRouter}
```

- No `if type == ...` outside the registry lookup. The harness, the UI and
  `settings` ask the class (attributes below), never the type string.
- The registry drives: which `type` values are valid, which keys a server
  table may have, the servers template, and the `ServerType` annotation.
- Layout (open, see §10): one file per provider in `harness/providers/`;
  `endpoint.py` keeps the base class, the factory and the catalog.
- `providers/*` import `settings` lazily only, because `settings` imports the
  registry at load time.

---

## 2. Class attributes (the config spec)

Declarative, read by config validation and by the template generator.

| Attribute | Meaning |
|---|---|
| `type` | the `type = "..."` value |
| `default_url` | optional `base_url`, used when absent (llama.cpp only) |
| `fixed_url` | `base_url` is forbidden, this is the URL (OpenRouter only) |
| `extra_keys` | server-table keys beyond the common ones |
| `template` | the `servers.toml` block for this provider |

`base_url` rule, derived: `fixed_url` set → forbidden; `default_url` set →
optional; neither → required.

Common keys (all providers): `type`, `base_url` (per the rule above),
`api_key` or `api_key_env`, and the advanced `max_context`, `timeout`,
`retry_attempts`, `retry_delay`.

| Provider | `base_url` | `extra_keys` |
|---|---|---|
| `openai-compatible` | required | — |
| `llamacpp` | optional, default `http://localhost:8080/v1` | — |
| `openrouter` | forbidden, fixed `https://openrouter.ai/api/v1` | `models`, `providers`, `providers_by_model` |

Every provider sends the selected `model` id exactly as listed. There is no
"one served model wins" mode: llama.cpp ignores `model` in single-model mode
and requires it in router mode (its README), so always sending it is correct
for both.

### 2.1 The `servers.toml` shape (decided 2026-10-01)

```toml
## One table per server. The name is yours (shown by /model).

[servers.openrouter]
type = "openrouter"
api_key_env = "OPENROUTER_API_KEY"
models = ["deepseek/deepseek-v4.1-flash"]     # only these are offered
providers = ["deepseek"]                      # default routing: only these, in order

[servers.openrouter.providers_by_model]       # optional, per-model override
"deepseek/deepseek-v4.1-flash" = ["deepseek", "parasail", "krea", "novita"]

[servers.local]
type = "openai-compatible"
base_url = "http://localhost:8010/openai/v1"
```

- **`type` is always written**, in every table. The "table named after its
  type" shortcut is deleted. Valid types come from the registry.
- **Keys:** the common keys above. A keyless server has no key line. A set
  `api_key_env` whose variable is unset is an error notice, as today.
- **OpenRouter:** `models` is the whitelist (what `/model` lists; absent or
  empty offers nothing, reported by the existing "lists no models" notice).
  `providers` is the default routing: only those, in order, no fallbacks.
  `providers_by_model` overrides it per model; `[]` means OpenRouter's own
  routing; a model not listed there uses the default. The per-model
  `[servers.<name>.models."<id>"]` tables are deleted.
- **Template:** a legend, then one block per provider (from its `template`)
  showing only what a server needs. The advanced keys are listed once in the
  header, not under every provider.
- **Old shapes are reported, never aliased:** a per-model `models` table, a
  `type` named `openai` (renamed `openai-compatible`), or a missing `type`
  gives a load error with the replacement, and that server is skipped (the
  mechanism of `_RETIRED_SERVER_KEYS`). moka never rewrites `servers.toml`.

---

## 3. Methods

### `list_models() -> list[ModelInfo]`

What the server offers now. The provider does whatever requests it needs
(llama.cpp: `/models` and `/props`; OpenRouter: the public catalog filtered
to the `models` tables).

```
ModelInfo: id, context_window, images, efforts, owned_by, raw
  context_window  int | None      None = the server did not say
  images          bool | None     None = unknown (never treated as "no")
  efforts         list[str]       effort levels the model takes; [] = none known
  raw             dict            the server's entry, read only by its provider
```

- Facts are filled in **here**, by the provider that understands its own
  server. Nothing outside a provider reads `raw` or knows a vendor field
  name (done 2026-10-01: `stated_efforts` / `stated_images` in
  `openai_compatible.py` read the shared `/models` shape).
- The harness stores the result in `Config.models_by_server` (memory only,
  never persisted), marks a server stale when a refresh fails, keeps its last
  good list.
- Replaced `list_models` + `discover_models` + `query_context_window` +
  `probe_image_input` (decision 1, §10; done 2026-10-01).

### `stream(messages, tools) -> AsyncIterator[Chunk]`

The only request path. Compaction uses it too (collects the text); the
non-streaming path is deleted. The model and effort are the instance's
(`selected_model`, `effort`): per-server state lives in the endpoint
(`PLAN.md` Decisions; signature decided 2026-10-01). The base class runs the
retry loop (§7) around the provider's one-request `_stream()`.

```
Chunk: text, reasoning, reasoning_native, tool_calls, usage, finish
  text              str              answer text, verbatim
  reasoning         str              reasoning text, as the server sent it
  reasoning_native  list | None      opaque; emitted once, at the end, already
                                     assembled by the provider (OpenRouter's
                                     reasoning_details, merged per index)
  tool_calls        list[Piece]      index, id, name, arguments (a JSON text
                                     fragment; dict arguments are dumped here)
  usage             TokenUsage | None
  finish            str | None       the server's finish reason
```

- The harness never sees `choices`, `delta`, `timings` or any vendor
  field. Chunks replace today's SDK-shaped
  `SimpleNamespace` objects.
- The provider builds the request: OpenAI-compatible payload
  (`stream_options.include_usage`, `tools`), OpenRouter's `provider` routing,
  and `effort_payload(effort)`.
- Retries and errors: §7.

### `effort_payload(level) -> dict`

Pure mapping of the chosen level to this server's request field.

| Provider | Field |
|---|---|
| `llamacpp`, `openai-compatible` | `{"reasoning_effort": level}` |
| `openrouter` | `{"reasoning": {"effort": level}}` |

`None` (the "default" choice) → `{}`. A chosen level is **always** sent, never
checked against `efforts` (an incomplete catalog must not drop a user choice;
the server decides).

### `replay(entry, target_model) -> dict`

Reasoning fields for an outgoing assistant message. In step 1 every provider
returns `{}`: replay was removed on 2026-09-30 and nothing is sent back. The
hook exists so step 2 fills it per provider without touching the harness
(`reasoning` → `reasoning_content`; `reasoning_native` only to
the model that produced it).

### `ping()`

Raises if the server is unreachable: `GET /models`. The base class wraps it in `diagnose_connection` (proxy
hints, `.local` re-resolution, `_connection_state`).

---

## 4. What each provider defines (the slot table)

Slot labels are row ids only (gaps in the numbering are historical). "Today"
is the current behaviour; step 1 keeps it unless §9 says otherwise (S6 is the
target, see §9.5).

| Slot | `llamacpp` | `openai-compatible` | `openrouter` |
|---|---|---|---|
| S2 list | `/models` + `/props` | `/models` | openrouter.ai `/models` ∩ the `models` list |
| S4 context | `/props` `n_ctx`, else `/models` `context_length` | `/models` `context_length` | catalog `context_length` |
| S5 images | `/props` `modalities.vision` | `architecture.input_modalities`, else unknown | `architecture.input_modalities` |
| S6 efforts (target) | `reasoning.supported_efforts` if the server advertises it, else none known | same | `reasoning.supported_efforts` only |
| S7 field | `reasoning_effort` | `reasoning_effort` | `reasoning.effort` |
| S8 reasoning out | `reasoning_content` \| `reasoning` \| `reasoning_details[].text` | same | same, plus `reasoning_details` assembled into `reasoning_native` |
| S9 reasoning in | none (step 2) | none | none |
| S11 usage | `include_usage`; cache from `timings.cache_n` | `include_usage` | `include_usage`, `cost` |
| S13 extras | — | — | `providers` / `providers_by_model` routing, `allow_fallbacks: false` |

`max_context` (server table) stays the user's fallback when a server reports
no window. llama.cpp router mode lists several models on `/models`, each with
a `status` and `architecture.input_modalities`; whether `/props` answers per
model there is unverified (⚠), so a missing `/props` answer means unknown
context and images, never a failure.

---

## 5. What stays in the base class

Identical for every provider, not overridden:

- the HTTP client pool and its lifecycle (`_replace_client`, `retire`,
  `aclose`), proxy rules (`trust_env=False` for local targets), auth header;
- `.local` hostname resolution (`endpoint_local.py`);
- per-server state: selected model, effort, connection state, `source`;
- reading the catalog (`catalog_entry`, `unserved_model`, `context_window()`,
  `accepts_images()`), now from `ModelInfo` fields only;
- `diagnose_connection` around `ping()`;
- the retry loop around `stream()` (§7).

---

## 6. What the harness guarantees in return

1. **Messages are API-shaped.** Only `role`, `content`, `tool_calls`,
   `tool_call_id`, and image content parts (base64 data URLs). No moka fields
   (`id`, `source`, image references) and, until step 2, no reasoning.
2. **`model` is the selected id exactly as listed.** `stream` does not map
   bare ids to canonical ones (OpenRouter's suffix matching is P5, to audit).
3. **`effort` is passed as chosen** (`None` = default); never validated.
4. **Streams are consumed to the end or closed.** On cancellation the harness
   closes the iterator; the provider releases its request.
5. **The harness never inspects `type`.** It calls a method or reads a class
   attribute.
6. **Catalog discipline.** `list_models()` is called only through
   `refresh_catalog`; results live in memory; a failed refresh keeps the last
   good list, marked stale; a provider never caches facts of its own.
7. **One identity for stored output.** Step 2 records `origin = {type, model}`
   on each assistant entry from `self.type` and the model used.

---

## 7. Failure behaviour

The base class's `stream()` handles retries for every provider. Gaps are
listed, not fixed.

| Situation | Behaviour | Gap |
|---|---|---|
| 503 (model loading) | retried with backoff, then raised with the body | — |
| network error / timeout | retried (whole request) up to `retry_attempts` | may repeat output already shown |
| other 4xx / 5xx | raised as an HTTP status error | the server's body is not shown |
| server unreachable at refresh | last list kept, marked stale | — |
| model not listed any more | selection kept; status bar red, error notice | — |

Errors are still `httpx` exceptions. A neutral error type (status + server
message), so that showing the server's body is a one-place change, was not
introduced with `stream` (open).

---

## 8. Proof of "no behaviour change"

Step 1 is a restructure, so it is proven on the wire, not through the UI.
Sub-step 1.0 is done: `test/test_wire_requests.py` records the **request
bodies, URLs and headers** the current code sends, and what each provider
learns from its server, through the in-process recording fake of
`test/wire_fake.py` (an `httpx.MockTransport` that every `httpx.AsyncClient`
uses, including the throwaway ones). Its fixtures are copied from the real
APIs (llama.cpp README, OpenAI streaming reference,
OpenRouter's live `/models`), each with its source and date.

Covered: a plain chat, a tool loop with an image part, effort set and default
per provider, the 503 retry, OpenRouter's provider routing and fixed URL,
llama.cpp's model lookup, the connection
checks, and `list_models` facts per provider (context window, images, effort
levels). Only three helpers (`make_endpoint`, `run_chat`, `learn`) touch the
provider API, so they are the only lines that change with the restructure.
The same tests must pass after it, except the expectations marked §9.x, which
change on purpose in the same commit.

Each test was checked by breaking the code it covers (effort field,
`stream_options`, the OpenRouter whitelist): it fails.

---

## 9. Deliberate behaviour changes

Everything else is identical.

1. **Compaction streams** (`PLAN.md` step 1.3): the non-streaming path goes.
2. **Probe state is removed** if decision 1 is accepted: context window and
   image support come from `list_models()` only.
3. *(Withdrawn: Ollama removed, §9.11.)*
4. *(Withdrawn: Ollama removed, §9.11.)*
5. **Effort levels are read only where a server states them** (P7). The two
   guesses go. OpenRouter: `reasoning.supported_efforts` only. llama.cpp,
   OpenAI and other OpenAI-compatible servers: levels only if the server
   advertises `reasoning.supported_efforts`; their docs publish no per-model
   list, so otherwise `/effort` offers nothing. A chosen level is still sent
   as chosen (§3).
6. **`llamacpp` no longer assumes one served model** (done 2026-10-01). It
   used to replace the selection with the first listed model
   (`query_model_name`), overriding your choice on a router-mode server. Now
   the selected id is sent, and a selection the server no longer lists is kept
   and shown red like on every other server (`serves_one_model`,
   `query_model_name` / `get_model_name` and the llama.cpp branch of
   `unserved_model` are deleted). With no selection, `stream` raises "No model
   selected" instead of sending the id `"unknown"`.
7. **The `servers.toml` shape changes** (§2.1): `type` always written, `openai`
   renamed `openai-compatible`, OpenRouter's `models` list plus
   `providers_by_model` instead of per-model tables.
8. **`timings.cache_n` is read only for `llamacpp`** (decided 2026-10-01,
   slot S11): an `openai-compatible` server in front of llama.cpp no longer
   gets it. (llama.cpp's documented usage already carries
   `cached_tokens: 0`, so `cache_n` is not used there either: ISSUES P11.)
9. **`reasoning_details` is assembled only for `openrouter`** (decided
   2026-10-01): other servers' reasoning text is still read.
10. *(Ollama retried like every other server via the base class's loop;
    moot since §9.11.)*
11. **Ollama removed** (decided 2026-10-01): no `ollama` type, no native
    `/api/*` client. `type = "ollama"` is reported as an unknown type and the
    server skipped. An Ollama server can still be configured as
    `openai-compatible` with `base_url = "http://…:11434/v1"` (its
    OpenAI-compatible API: chat, streaming, tools, base64 images,
    `include_usage`, `reasoning_effort`; `docs/api/openai-compatibility.mdx`,
    read 2026-10-01), but its `/v1/models` states no context window, image
    support or effort levels. With it, the effort-variant switch (`X:low` /
    `X:high` sibling ids, `models._effort_variants`) is deleted.

---

## 10. Decisions (all approved by the user on 2026-10-01)

1. **Probes folded into `list_models()`.** Deletes `_probed`, `_probed_facts`,
   `probe_image_input`, `query_context_window`, `_cached_context_window` and
   the fallback chain in `get_context_window`. A fact a server answers only
   through a separate request (llama.cpp `/props`) is fetched inside
   `list_models()`.
2. *(Ollama through the shared client: withdrawn, §9.11.)*
3. **`ModelInfo.images` / `ModelInfo.efforts` are explicit; `raw` is read only
   by its provider.**
4. **Guessed effort levels are deleted** (§9.5): exact levels only; a server
   that states none gets no `/effort` menu.
5. **Layout:** `harness/providers/` (`openai_compatible.py`, `llamacpp.py`,
   `openrouter.py`); the base class, factory, catalog,
   `ModelInfo` and `Chunk` stay in `endpoint.py`; `endpoint_discovery.py`,
   `endpoint_openai.py` and `endpoint_ollama.py` are deleted.
6. **Order:** sub-step 1.0 (golden wire tests on today's code) before
   anything moves.
7. **Servers config redesign** (§2.1, §9.6, §9.7): explicit `type`;
   `openai` renamed `openai-compatible`; `llamacpp` no longer single-model
   (it can serve several); OpenRouter `models` list + `providers_by_model`.
   Answers: rename yes, llama.cpp yes, per-model tables "doesn't matter".

---

## 11. Adding a provider

1. Subclass `OpenAICompatible` (chat-completions servers) or `Endpoint`.
2. Set the class attributes of §2, including its `template` block.
3. Implement `list_models`, `stream`, `effort_payload`, `ping`; override
   `replay` if it takes reasoning back.
4. Add one line to the registry.
5. Add wire-level tests for each row of §4 that applies, and its column of
   the slot table.

Touches one new file and one registry line. No `type ==` anywhere; if a
change needs one, the contract is missing something and this page changes
first.

Not covered, by design: an abstract base class or plug-in loading; native
Anthropic, DeepSeek and the OpenAI Responses API (`PLAN.md` future polish).
`reasoning_native`, `replay` and `origin` are the seam they will use.
