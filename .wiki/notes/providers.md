# Provider contract

> **Status:** built (`PLAN.md` step 1, 2a and 2b, 2026-10-01). This page describes what exists; the rows marked 2b /
> 2c in §11 and DeepSeek are not built yet.

A *provider* is a server family moka can talk to: llama.cpp and OpenRouter
(Ollama was removed on 2026-10-01, §9.11; the catch-all `openai-compatible`
type on 2026-10-01, §9.12). Their differences used to be about two dozen `type == ...` branches
spread over `endpoint.py`, `endpoint_discovery.py`, `endpoint_openai.py`,
`settings.py` and `unserved_model`. This page defines the one seam that
replaced them, and what each side promises the other.

The slot table (§4) is the checklist of what a provider must define; the
class structure is the one decided in `PLAN.md`.

---

## 1. Shape

An `Endpoint` *is* an instance of its provider's class. The base class holds
the contract and the per-server state; a registry maps `type` to class.

```
Endpoint                      contract + per-server config and state
├─ OpenAICompatible           abstract, no type: shared /chat/completions,
│  │                          /models, SSE code
│  ├─ LlamaCpp                type "llamacpp": adds /props, default URL
   └─ OpenRouter              type "openrouter": fixed URL, model whitelist,
                              provider routing

REGISTRY = {"llamacpp": LlamaCpp, "openrouter": OpenRouter}
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
`api_key` or `api_key_env`, and the advanced `timeout`, `retry_attempts`,
`retry_delay`.

| Provider | `base_url` | `extra_keys` |
|---|---|---|
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
type = "llamacpp"
base_url = "http://localhost:8080/v1"
```

- **`type` is always written**, in every table. The "table named after its
  type" shortcut is deleted. Valid types come from the registry.
- **Keys:** the common keys above. A keyless server has no key line. A set
  `api_key_env` whose variable is unset is an error notice.
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
  `type` named `openai` or `openai-compatible` (no longer supported), a
  `max_context` key, or a missing `type` gives a load error, and that server
  is skipped (the
  mechanism of `_RETIRED_SERVER_KEYS`). moka never rewrites `servers.toml`.
- **A `providers_by_model` id not in `models`** is reported (entry not used);
  the server still loads (decided 2026-10-01).
- Implemented 2026-10-01 (§9.7).

---

## 3. Methods

### `list_models() -> list[ModelInfo]`

What the server offers now. The provider does whatever requests it needs
(llama.cpp: `/models` and `/props`; OpenRouter: the public catalog filtered
to the `models` tables).

```
ModelInfo: id, context_window, images, efforts, owned_by, raw
  context_window  int | None      None = the server did not say: an error
                                  notice for the selected model (§9.12)
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
  field. (Chunks replaced the SDK-shaped `SimpleNamespace` objects.)
- The provider builds the request: OpenAI-compatible payload
  (`stream_options.include_usage`, `tools`), OpenRouter's `provider` routing,
  and `effort_payload()`.
- Retries and errors: §7.

### `effort_payload() -> dict`

Pure mapping of the instance's chosen `effort` to this server's request field.

| Provider | Field |
|---|---|
| `llamacpp` | `{"reasoning_effort": level}` |
| `openrouter` | `{"reasoning": {"effort": level}}` |

`None` (the "default" choice) → `{}`. A chosen level is **always** sent, never
checked against `efforts` (an incomplete catalog must not drop a user choice;
the server decides).

### `min_replay_depth(has_tools) -> int`

The fewest turns of reasoning the server's docs say must be sent back (999 =
all); the base class returns 0. The notice band warns when the active role's
`replay_reasoning_depth` is below it (from live state, so also after a model or
server switch); the configured depth is still sent. No provider overrides it
yet (DeepSeek, 2c).

### `replay(entry) -> dict`

The fields that send a stored assistant entry's reasoning back, in this
server's own field (`{}` when it takes none; the base class). The harness calls
it only for the entries its role's `replay_reasoning_depth` allows
(`reasoning-traces.md`); the provider decides the field and what to do when the
model produced no reasoning. `OpenAICompatible`: `reasoning_content`, sent even
when empty (llama.cpp). `OpenRouter`: `reasoning_details` when `entry.origin`
is `{type, model}` of this endpoint, else the `reasoning` text, nothing if
there is none.

### Connection check

`diagnose_connection()` (base class, every provider): `GET /models`, with
proxy hints and `.local` re-resolution; sets `_connection_state`.
`prewarm_connection()` runs it in the background; `check_connection()`
returns only whether it succeeded.

---

## 4. What each provider defines (the slot table)

Slot labels are row ids only (gaps in the numbering are historical).

| Slot | `llamacpp` | `openrouter` |
|---|---|---|
| S2 list | `/models` + `/props` | openrouter.ai `/models` ∩ the `models` list (catalog unreachable: the listing fails, last good one kept) |
| S4 context | `/props` `n_ctx`, else `/models` `context_length` | catalog `context_length` |
| S5 images | `/props` `modalities.vision` | `architecture.input_modalities` |
| S6 efforts | `reasoning.supported_efforts` if the server advertises it, else none known | `reasoning.supported_efforts` only |
| S7 field | `reasoning_effort` | `reasoning.effort` |
| S8 reasoning out | `reasoning_content` \| `reasoning` \| `reasoning_details[].text` | same, plus `reasoning_details` assembled into `reasoning_native` |
| S9 reasoning in | `reasoning_content`, sent even when empty | `reasoning_details` (same model) else `reasoning`; nothing when empty |
| S11 usage | `include_usage`; cache from `timings.cache_n` | `include_usage`, `cost` |
| S13 extras | — | `providers` / `providers_by_model` routing, `allow_fallbacks: false` |

The context window is required (no `max_context`): a selected model whose
listing states none is an error notice. llama.cpp router mode lists several models on `/models`, each with
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
   (`id`, `source`, image references); reasoning only as `Endpoint.replay` returns
   it, for the entries the role's depth allows.
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
7. **One identity for stored output.** Each assistant entry records
   `origin = {type, model}` from the endpoint's `type` and selected model.
8. **One request builder.** `Harness._request_messages()` builds every request
   (`chat`, `get_current_context`) from history; nothing keeps a second list.

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

## 8. Proof on the wire

Step 1 was a restructure, so it was proven on the wire, not through the UI.
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
The same tests passed after it, except the expectations marked §9.x, which
changed on purpose in the same commit.

Since step 1.5 (2026-10-01), provider behaviour is tested only through the
contract (`make_endpoint`, then `stream`, `list_models`, `effort_payload`, the
connection check), on this fake: usage and cost, reasoning fields, the
assembly of `reasoning_details`, routing, model facts. No test calls a
provider's private helpers. Shapes a doc does not show are marked ⚠ in the
test that uses them.

Each test was checked by breaking the code it covers: it fails.

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
7. **The `servers.toml` shape changes** (§2.1, done 2026-10-01): `type` always written, `openai`
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

12. **`openai-compatible` and `max_context` removed** (2026-10-01, `PLAN.md`
    2a): `OpenAICompatible` is abstract shared code, not a `type`; a server
    without its own provider class is not supported, and `type =
    "openai-compatible"` is reported and the server skipped. The context window
    comes only from the server: a selected model whose listing states none is
    an error notice ("server broken, or moka reads the wrong route"), and
    `max_context` is a load error. OpenRouter's catalog being unreachable is a
    failed listing (stale), not a list of models without facts.

13. **Reasoning is sent back** (2026-10-01, `PLAN.md` 2b): per role
    (`replay_reasoning_depth`), in the provider's own field (`replay`). Before
    2026-09-30 every turn was replayed for every model; from then until 2b
    nothing was.

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

A provider is a subclass of `OpenAICompatible` (chat-completions servers) or
`Endpoint`, one file in `harness/providers/`, one line in the registry. No
`type ==` anywhere; if a change needs one, the contract is missing something
and this page changes first.

**Read the provider's own docs first**, fetched raw (`curl`; OpenRouter docs:
append `.md`), only for a provider being built. Answer every row below from
them, write the source and the date next to the answer (here, in the
provider's test fixtures, in its slot-table column). A shape no doc shows is
marked ⚠ and listed in `ISSUES.md`; it is never assumed. A server that is not
documented well enough to answer the rows is not supported (OpenCode Go,
postponed 2026-10-01: per-model APIs, and a `/models` without a context
window).

*Status:* ✅ built · 2b / 2c = `PLAN.md` steps, not built yet.

### 11.1 The sheet to fill

**A. Identity and config** (§2)

| # | Question | Rule | Status |
|---|---|---|---|
| A1 | `type` string | one class, one `type`; no catch-all type for "any server" | ✅ |
| A2 | Base URL | `fixed_url` (forbidden in the table), `default_url` (optional) or required | ✅ |
| A3 | Auth | `api_key` / `api_key_env`; a keyless server has no key line; a set `api_key_env` whose variable is unset is an error notice | ✅ |
| A4 | Keys beyond the common ones | `extra_keys`; anything else is a load error | ✅ |
| A5 | Template block | `template`, only the keys a server needs; a guard test reads it | ✅ |
| A6 | Registry line | `providers/__init__.py`; drives valid types, keys and the template | ✅ |

**B. Model facts** (§3 `list_models`; from the server only, never a catalog,
a table or a guess)

| # | Question | Rule | Status |
|---|---|---|---|
| B1 | Which route lists the models, and the id sent back | the id is sent exactly as listed | ✅ |
| B2 | Context window: route and field | **required**; a selected model whose listing states none is an error notice ("server broken, or moka reads the wrong route"); no `max_context`, no default | ✅ |
| B3 | Image input | optional; absent = unknown, never "no" | ✅ |
| B4 | Effort levels | optional; read only where the server states them; none stated = no `/effort` menu | ✅ |
| B5 | A fact that needs a second request (e.g. llama.cpp `/props`) | fetched inside `list_models()`; a failed side request means unknown, not a failure | ✅ |
| B6 | Catalog not reachable | the listing fails (last good list kept, flagged stale); never models without facts | ✅ |

**C. The request** (§3 `stream`, `effort_payload`)

| # | Question | Rule | Status |
|---|---|---|---|
| C1 | Chat route and payload | `model`, `messages`, `stream`, `tools`, `stream_options.include_usage` | ✅ |
| C2 | Effort field | a chosen level is always sent as chosen, never checked against B4 | ✅ |
| C3 | Thinking on/off switch | only if the docs have one; send it or rely on the server default (DeepSeek: undecided, 2c) | 2c |
| C4 | Routing or other extras | `_extra_payload` (OpenRouter `provider`) | ✅ |

**D. The response** (§3 `Chunk`)

| # | Question | Rule | Status |
|---|---|---|---|
| D1 | Answer text | verbatim: no tag parsing, no `<think>` splitting, anywhere | ✅ |
| D2 | Reasoning text: which field(s) | read the documented field and its aliases | ✅ |
| D3 | Native reasoning blocks (structured or opaque) | `reasoning_native`, assembled by the provider, emitted once at the end, kept exactly as produced | ✅ OpenRouter |
| D4 | Tool-call pieces | index, id, name, argument fragments | ✅ |
| D5 | Usage: prompt, completion, cache count, cost | read as documented; ⚠ where a shape is not documented (ISSUES P11) | ✅ |
| D6 | Finish reason | the server's, unchanged | ✅ |

**E. Reasoning sent back** (`replay`, `PLAN.md` step 2b)

| # | Question | Rule | Status |
|---|---|---|---|
| E1 | The documented field(s) to send reasoning back in | fixed per provider from its docs, never configurable (llama.cpp, DeepSeek: `reasoning_content`; OpenRouter: `reasoning_details` or `reasoning`) | ✅ |
| E2 | Text or native | native only back to the model that produced it (`origin = {type, model}`); other models get the text | ✅ |
| E3 | When there is no reasoning | decided per provider, in its own `replay()` (llama.cpp, DeepSeek: field sent empty; OpenRouter: nothing); unverified cases go to ISSUES | ✅ |
| E4 | Minimum depth | `min_replay_depth(has_tools)`, default 0; DeepSeek: all turns whenever `tools` is sent (else HTTP 400). A role below it gets a warning in the notice band, from live state (so also after a model or server switch); the configured depth is still sent | ✅ base · 2c DeepSeek |
| E5 | Signed or encrypted reasoning (OpenAI, Anthropic, Gemini) | not supported for now | — |

What the harness does, for every provider: stores `reasoning`,
`reasoning_native` and `origin` on each assistant entry; sends back what the
role's `replay_reasoning_depth` allows (0 = none, 1 = the current turn, N = the
last N turns, capped at what exists; default 1; a turn is one user message and
what the model does until its answer); builds every request from history in one
place; never parses, moves or rewrites reasoning. No compatibility for
sessions saved before step 2b.

**F. Failure and connection** (§7)

| # | Question | Rule | Status |
|---|---|---|---|
| F1 | Connection check route | `GET /models` by default; override only if it does not exist | ✅ |
| F2 | Retries | base class: 503 and network errors; the provider adds none | ✅ |
| F3 | Error body | the server's body is not shown yet (§7 gap) | open |

### 11.2 Proof and docs

1. **Wire tests** (`test_wire_requests.py`) through the contract only (§8):
   one expectation per row that applies, with its fixture copied from the
   docs (source and date in the test), undocumented shapes marked ⚠; each
   test checked by breaking the code it covers.
2. Add the provider's column to the slot table (§4), and any deliberate
   change to §9.
3. Update `reasoning-traces.md` and `config.md` where the provider
   changes them; record every unverified item in `ISSUES.md`.

Not covered, by design: plug-in loading; native Anthropic and the OpenAI
Responses API (`PLAN.md` future polish). `reasoning_native`, `replay` (2b) and
`origin` are the seam they will use.
