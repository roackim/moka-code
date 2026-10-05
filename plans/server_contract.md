# Server Contract & Reliability Rules

**Status:** proposed, not implemented · **Owner:** Joackim · **Created:** 2026-09-29
**Companion docs:** `.wiki/notes/principles.md`, `.wiki/notes/reasoning-traces.md`,
`.wiki/tree/harness.md`

This is not an implementation plan with code. It defines the rules, the contract
every server type must fulfil, and the checks that keep moka from silently
talking to a server differently than the user asked. Implementation follows it,
step by step (see *Rollout*).

---

## 1. Why this exists

Between 2026-09-28 and 2026-09-29, a series of bugs made moka unreliable against
a local llama.cpp server (metallama, `type = "openai"`). Qwen3.8 thought for minutes
on `low` effort:

| Bug | Shape |
|---|---|
| A model catalog saved in `state.toml` outlived the server (dead `:low/:high` ids, no metadata) | stale fact |
| The live endpoint held private copies of catalog data; `/model` refreshed a different object | duplicated fact |
| `effort_payload` validated the saved effort and **silently omitted** it when the catalog lacked the level | silent degradation |
| `type = "openai"` stripped all history reasoning, so each tool step showed Qwen an empty `<think></think>` | silent degradation |
| Unknown context window → an invented `32768` shown as fact | invented value |
| `type = "openrouter"` ignored `base_url` for discovery | silent substitution |
| Throwaway endpoints never closed their HTTP pools | leak |

All of them passed the tests. None was visible in the UI or in moka's debug log.
They were found by putting a proxy between moka and the server.

**Three root causes:**
1. moka quietly did something other than what was asked.
2. Server facts were trusted after the server changed.
3. Nothing checked the request actually sent.

---

## 2. Rules

To be added to `.wiki/notes/principles.md`.

**R1 — Never silently degrade user intent.** What the user chose (model, effort,
reasoning preservation, …) is sent as chosen, or refused with a visible message.
Forbidden: "validate, then quietly omit"; clamping; substituting a default.

**R2 — Unknown is not unsupported.** A missing fact (no levels listed, no
context window reported) is *unknown*. Unknown never removes a user choice
(R1), and is never shown as a fact. Invented defaults are marked as defaults.

**R3 — Server facts are never trusted over the server.**
- Server facts (models, levels, context windows, capabilities) are discovered
  live and kept in memory only, never persisted.
- There is one copy, read where it's used, never duplicated into objects.
- A failed refresh keeps the last good value, **marked stale**.
- Anything derived from it expires when it is refreshed.

**R4 — Every piece of state has a ledger entry.** Before adding state, write
down its writer, what invalidates it, and what happens when it is wrong. If
"when wrong" is *silent*, redesign it (see §4).

**R5 — Server errors reach the user verbatim.** A 4xx/5xx shows the server's
own message, not only the status line. R1 relies on this: "the server decides"
only works if the user can read the decision.

**R6 — API compliance.** moka reads and sends only standard fields or
widely-used de facto ones (OpenAI Chat Completions, OpenRouter's documented
extensions, Ollama's native API, `reasoning_content`). A field specific to one
server (e.g. metallama's `meta.reasoning_efforts`) is never read. Such a server
advertises through the standard/de facto shapes instead.

**R7 — Verify on the wire.** A change that affects what reaches a server is
verified by inspecting the outgoing request (contract tests, §5), not the UI.

---

## 3. Server-type contract

Every server type fills the same **slots**. A type is complete only when each
slot has a defined source, a defined failure behaviour, and a contract test.
For each slot, "none" must be a deliberate, documented answer.

### 3.1 Slots

| # | Slot | The type must define |
|---|---|---|
| S1 | **Base URL** | fixed or configurable; what `base_url` means |
| S2 | **Model listing** | endpoint, id field, which fields are kept as metadata (all of them) |
| S3 | **Model identity** | multi-model (request `model` honoured) or single-model (served model replaces the selection) |
| S4 | **Context window** | source, and what happens when the server reports none |
| S5 | **Image input** | source (metadata or probe); unknown = allowed |
| S6 | **Effort levels** | discovery source(s), in order |
| S7 | **Effort request field** | field name and value mapping (`none`, levels) |
| S8 | **Reasoning out** | streamed field(s) read |
| S9 | **Reasoning in** | history field sent back, and when (current turn / `preserve_reasoning`) |
| S10 | **Tool calls** | request/stream format and adaptation |
| S11 | **Usage** | where prompt/completion tokens come from |
| S12 | **Errors & retries** | what is retried, what is shown, with which text |
| S13 | **Type extras** | anything else (OpenRouter provider routing) |

### 3.2 Current state per type

⚠ marks a known gap, to be fixed or explicitly accepted.

| Slot | `llamacpp` | `openai` (OpenAI-compatible) | `openrouter` | `ollama` |
|---|---|---|---|---|
| S1 Base URL | configurable | configurable | **fixed** `https://openrouter.ai/api/v1`; `base_url` is a load error | configurable (native API derived by stripping `/v1`) |
| S2 Listing | `GET /models` | `GET /models` | openrouter.ai `/models`, only ids enabled in `servers.toml` | `/api/tags` + `/api/show` |
| S3 Identity | single-model (served model wins) | multi | multi (bare id matches `vendor/id`) | multi |
| S4 Context | `/props` `n_ctx`, else `/models` | `/models` `context_length`, else `max_context`, else 32k default ⚠ | catalog `context_length` | `/api/show` `model_info.*context_length` / `num_ctx` |
| S5 Images | `/props` `modalities.vision` | catalog metadata only ⚠ (metallama's `meta.vision` not read, per R6) | `/models/<id>/endpoints` `architecture.input_modalities` | `/api/show` `capabilities` has `vision` |
| S6 Effort levels | `efforts` config → `reasoning.supported_efforts` → `supported_parameters` (guess) | same | same | config → `capabilities: thinking` (levels only for gpt-oss) · `X:low` variant ids |
| S7 Effort field | `reasoning_effort` | `reasoning_effort` | `reasoning.effort` | `think` (`none` → `false`) |
| S8 Reasoning out | `reasoning_content` \| `reasoning` \| `reasoning_details[].text` | same | same | `thinking` \| `reasoning` |
| S9 Reasoning in | `reasoning_content` | `reasoning_content` (only when text exists; real OpenAI never returns any) | `reasoning_details` verbatim, else `reasoning` | `thinking` |
| S10 Tool calls | OpenAI `tool_calls` | same | same | native, adapted |
| S11 Usage | `stream_options.include_usage` | same | same | `prompt_eval_count` / `eval_count` |
| S12 Errors | 503 retried with body; other 4xx/5xx raised **without the server's body** ⚠; network errors and timeouts retry the **whole request**, possibly after partial output ⚠ (to verify) | same | same | to document |
| S13 Extras | — | — | `providers` routing | — |

### 3.3 Contract per slot, independent of type

- **S2:** discovery keeps the full entry as metadata. Nothing is filtered at
  discovery time; readers pick what they need.
- **S4, S5, S6:** unknown is a valid answer (R2). It never removes a user
  choice and is never displayed as a fact.
- **S7:** the saved level is always sent (R1). Only the `default` choice
  sends nothing.
- **S9:** the current turn's reasoning is always sent back where the type has
  a field for it. Earlier turns follow `preserve_reasoning`.
- **S12:** retrying must never repeat output already shown, and must never
  hide the server's error text (R5).

---

## 4. State ledger

All state that affects what moka sends or shows about a server. Every new piece
of state gets a row (R4).

| State | Where | Written by | Invalidated by | When wrong |
|---|---|---|---|---|
| Model catalog | `Config.models_by_server` (memory) | `refresh_catalog` | the next refresh; startup, `/model`, `/effort`, `/reload`, `/config servers` | loud: `/model` shows `unreachable` when stale |
| Stale servers | `Config.stale_servers` | `refresh_catalog` | the next successful refresh | loud (picker marker) |
| Refresh order | `_refresh_generation` | `refresh_catalog` | never (counter) | n/a: the latest refresh wins |
| Probed facts (context, images) | `Endpoint._probed` | `get_context_window`, `probe_image_input` | any refresh of that server | silent until refresh ⚠ acceptable: catalog wins |
| Resolved context window | `Endpoint._cached_context_window` | `get_context_window`, `set_model` | re-read; catalog wins in `context_window()` | display only |
| Selected model | `Endpoint._selected_model` + `_model_resolved` | selection, llama.cpp probe | `/model`, reload | loud: server error, plus the unserved-model warning |
| Connection state | `Endpoint._connection_state` | `diagnose_connection` | the next diagnose | display only |
| `.local` address | `endpoint_local._local_cache` | resolution | a connection failure | self-healing |
| HTTP pools | `Endpoint.client`, `_stale_clients` | endpoint, `_replace_client` | `retire()` / `aclose()` | leak (fixed) |
| Last server/model | `state.toml` `last_server`, `[last_model]` | selection | user action | loud (unserved-model warning) |
| Effort | `state.toml` `[effort]` | `/effort` | user action | loud: the server rejects it (see R5 gap) |

---

## 5. Contract tests (wire level)

**Fixture:** a fake OpenAI-compatible server, in-process, via `httpx.MockTransport`
injected into `Endpoint.client`. No ports, no timing, and deterministic.
- It records every request, with full JSON.
- It serves configurable `/models` responses, including a missing `reasoning`
  object, missing `context_length`, slow responses and errors.
- It streams scripted chunks (content, `reasoning_content`, tool calls, usage).
- It can fail mid-stream.

The same fixture shape covers Ollama's native API.

**Scenarios that must exist** (each asserts on the recorded request, not on the UI):

| Scenario | Asserts |
|---|---|
| Effort saved, catalog lacks the level | the effort field is present with that value |
| Effort `default` | no effort field |
| Stale catalog at startup (dead ids, wrong context) | the live listing wins; nothing from disk |
| Server times out on refresh | model and context stay usable; marked stale |
| Tool loop, `type = "openai"` | each assistant step carries `reasoning_content` |
| `preserve_reasoning = false` | earlier turns carry no reasoning; the current turn does |
| Server switch mid-stream | the stream completes; the old pool closes after |
| 400 with an OpenAI error body | the user sees the server's `error.message` (R5) |
| Network error mid-stream | no duplicated output (S12) |
| OpenRouter server | requests go to openrouter.ai whatever the config says |
| Each type × each slot of §3.2 | the documented field names and mappings |

---

## 6. Observability

The debug log (`debug.toml` `log_enabled`) records a **request envelope** for
every request, so "what did moka send?" is one `grep`.

- **Logged:** server name and type, URL path, `model`, `stream`, the effort
  field(s) as sent, `provider` routing, the tool count, the message count per
  role, and for each assistant message whether it carries reasoning and in
  which field. Response side: status, and on error the body (truncated).
- **Never logged:** message contents, tool arguments, API keys or headers.

---

## 7. One profile per server type

Today a type's behaviour is spread across `endpoint.py`,
`endpoint_discovery.py`, `endpoint_openai.py` and `endpoint_ollama.py`, which
is why gaps like "`openai` sends no reasoning" were invisible.

**Target:** one table (one module) holding, per type, the §3.1 slot values:
the effort field, the reasoning-in and reasoning-out fields, the discovery
functions, the context and image sources, and the fixed base URL. Code reads
the profile instead of branching on `type` in many places.

- **What it removes:** the scattered `if endpoint.type == ...` branches, and
  the hardcoded URLs outside the profile.
- **Principle served:** delete before you design; one place to audit.

This is the last step of the rollout: it restructures code, and the contract
tests (§5) must exist first to prove behaviour is unchanged.

---

## 8. Rules for agents (to add to `AGENTS.md`)

- A change that affects requests to a server is verified on the wire (§5
  fixture, or a local fake server/proxy), not through the UI.
- Never query the user's local LLM server (e.g. metallama on `localhost:8010`)
  without asking; it may be busy with real work.
- Adding state means adding a §4 ledger row. A "when wrong" of *silent* is a
  design error.
- Never write "validate then omit" for user choices (R1). If something must
  be refused, refuse it visibly.
- Server-facing changes list their failure behaviour: server busy, down,
  changed, unknown field, 4xx, mid-stream drop.

---

## 9. Other safeguards

- **Banned patterns (review checklist, grep-able)** in the request path:
  - `return {}` or `pass` as the answer to "invalid" user input;
  - `except Exception:` that continues without a visible message or log;
  - literal defaults (`32768`) returned as if they came from the server;
  - third-party URLs outside the §7 profile.
- **Explicit retry policy (S12):** state which errors are retried, how often,
  and that a retry after partial output is forbidden (or resumes without
  duplicating). Today it's implicit in `endpoint_openai.create_completion`.
- **Drift detection:** when discovery sees a shape it doesn't understand
  (unexpected type, missing `data`), it logs it once rather than silently
  returning nothing.
- **Compatibility notes:** each de facto field in §3.2 records its source
  (e.g. "`reasoning_content`: DeepSeek, llama.cpp"; "`reasoning.supported_efforts`:
  OpenRouter `/models`"), so a later change knows what it may break.
- **Paired changes:** when moka and a server it relies on (metallama) change
  together, the moka side lands with a contract test that pins the agreed shape.

---

## 10. Known gaps (as of 2026-09-29)

| Gap | Slot/rule | Note |
|---|---|---|
| Streaming 4xx shows no server body | S12, R5 | `endpoint_openai.py:254` calls `raise_for_status()` before reading the body |
| Whole-request retry on network error/timeout, possibly after partial output | S12 | unverified; risks duplicated output and double server work |
| `openai` image support undetected unless the metadata uses OpenRouter/Ollama shapes | S5 | accepted per R6 unless a de facto shape exists |
| 32k default when a context window was never learned this session | S4, R2 | should be marked as a default in the status bar |
| No request envelope in the debug log | §6 | |
| No wire-level contract tests | §5 | ad-hoc fake servers/proxies used so far |
| Type behaviour scattered | §7 | |
| One unexplained test-suite failure (1 of 16 full runs) | — | test not captured |

---

## 11. Rollout

1. **Rules:** R1–R7 into `principles.md`; §8 into `AGENTS.md`. No code.
2. **Contract-test fixture and the §5 scenarios.** Scenarios for known gaps
   are written first and marked expected-fail.
3. **Request envelope** in the debug log (§6).
4. **Close the S12 gaps:** server error text shown (R5); a retry policy that
   never repeats output.
5. **Profiles (§7),** with the §5 tests proving unchanged behaviour.
6. **Remaining §10 gaps,** each fixed or explicitly accepted in §3.2.
