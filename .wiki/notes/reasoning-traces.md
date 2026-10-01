# Reasoning Trace Handling in moka

*How moka stores model reasoning and sends it back, per provider and per role.*

> **Status** (2026-10-01, `PLAN.md` step 2b): reasoning is stored as the model
> produced it and sent back in the provider's own field, as deep as the role's
> `replay_reasoning_depth` allows. `content` is never parsed: inline `<think>`
> tags stay in the answer. DeepSeek (2c) is the first provider with a documented
> minimum depth (`Endpoint.min_replay_depth`: all turns whenever tools are sent;
> a role below it gets a warning in the notice band).

---

## Streaming

`Endpoint.stream()` yields neutral `Chunk`s; `Harness._stream_llm_response()`
accumulates `text`, `reasoning` and `reasoning_native` and forwards events.
Each provider reads its documented reasoning fields (`providers.md` §4 S8):
llama.cpp and DeepSeek `reasoning_content`; OpenRouter `reasoning`, and its
`reasoning_details` blocks, which `providers/openrouter.merge_reasoning_details`
folds per `index` (`text`/`summary`/`data` concatenate, `signature`/`id`/`format`
fill in) into the blocks the model produced. A field missing from the reader is
dropped before the harness sees it: a missing reasoning field in an export
usually means the provider class, not the harness.

`content` deltas are answer tokens, stored and shown exactly as sent. A server
that does not split reasoning out (llama.cpp `--reasoning-format none` or
`deepseek-legacy` while streaming, or a template it does not recognize) shows
raw `<think>` tags in the answer.

---

## What is stored

Each assistant entry in history:

| Key | Content |
|---|---|
| `content` | the answer, verbatim (`None` for a tool-call-only step) |
| `tool_calls` | the calls, when any |
| `reasoning` | the reasoning text, when the model gave any |
| `reasoning_native` | opaque blocks as produced (OpenRouter `reasoning_details`), when any |
| `origin` | `{type, model}` of the endpoint that produced it, always |

Saved sessions and `/export` carry all of it. Sessions written before 2b have
no compatibility: `reasoning_details` is not read, and an entry without
`origin` counts as foreign (its text is replayed, never its native blocks).

---

## What is sent back

Every request is built in one place, `Harness._request_messages()` (system
prompt + effective history, from the last compaction marker). `_to_api_message`
keeps only `role`, `content`, `tool_calls`, `tool_call_id` and image parts;
moka's `id`, `source`, `origin`, `reasoning*` never leave as such.

**Which entries:** the role's `replay_reasoning_depth` (default 1; a role file
sets it, a new role's template shows it commented). A *turn* is one user message
(not a tool's returned images) and what the model does until its answer.

| Depth | Assistant entries that carry reasoning |
|---|---|
| 0 | none |
| 1 | the current turn: the tool loop in progress |
| N | the last N turns, capped at what exists (999 = all) |

The compaction marker is never a turn. moka sends what is configured, never
more or less, whatever the model needs.

**In which field** (`Endpoint.replay(entry)`, fixed per provider from its docs,
never configurable):

| Provider | Sent | When the model gave no reasoning |
|---|---|---|
| `llamacpp` | `reasoning_content` = the text. llama.cpp uses it if the chat template supports it, else ignores it (PR #18994; `--reasoning-preserve`, default on) | the field is sent empty (⚠ not documented; opencode does the same) |
| `deepseek` | `reasoning_content`, always (with `tools`, a missing field is HTTP 400; `guides/thinking_mode`) | the field is sent empty |
| `openrouter` | `reasoning_details` unmodified when `origin` is this server and this model (OpenRouter: blocks must match what the model produced), else the `reasoning` text | nothing is sent (a decision: the docs are silent; ⚠ DeepSeek behind OpenRouter requires the field, unverified without a key) |

Not supported for now: signed or encrypted reasoning (OpenAI, Anthropic, Gemini
through OpenRouter). Their `reasoning_native` blocks are stored and sent back
only to the same model, but the signed case was never exercised.

---

## Configuration

`replay_reasoning_depth` in the role file only. There is no server or global
key (`preserve_reasoning` was removed 2026-09-30). llama.cpp still needs
`--reasoning-preserve` (default on) for earlier turns to reach the prompt.

---

## Verification

By tests only (fake servers, `test_wire_requests.py`, `test_reasoning_history.py`):
the field and the depth per provider, native blocks only to their model, empty
reasoning. Not checked on a real server since the 2b rebuild.

Open: signed or encrypted blocks (Claude or Gemini through OpenRouter) need one
tool-call turn on a real model; a rejection names the offending block. Images
(content parts) and the text-only refusal were only ever checked with fakes.

---

## Related

- [providers.md](./providers.md) — the provider contract (`replay`, slot S9)
- [architecture.md](../notes/architecture.md) — data flow
- [config.md](../notes/config.md) — configuration reference
- `harness.py` — `_request_messages`, `_replay_start`, `_api_history`, `chat()`
