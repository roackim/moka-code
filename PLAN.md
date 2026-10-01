# Plan: providers, reasoning, compaction

Direction for the next steps (2026-09-30, revised 2026-10-01). Each step ends with fewer files,
branches and code paths. Items marked ⚠ are unverified and get checked
against the provider's current docs before they are built on.

---

## Decisions

- **Providers.** An `Endpoint` *is* an instance of its provider's class:
  base `Endpoint` (the contract + per-server config and state) →
  `OpenAICompatible` → `LlamaCpp`, `OpenRouter`, `DeepSeek`.
  A registry `{type: class}` drives config validation
  and the servers template. Per-server state (selected model, connection,
  effort) lives in the instance. Contract: `.wiki/notes/providers.md`.
- **One class per API, no catch-all (2026-10-01).** `OpenAICompatible` is
  abstract: shared chat-completions code, not a `type` (the
  `openai-compatible` type goes). Every supported server has its own
  subclass, which reads its facts from its documented routes and sends its
  documented fields. A server without a subclass is not supported (Ollama,
  plain OpenAI, the user's proxy until it has one).
- **Model facts come from the server only (2026-10-01).** No catalog, no
  static data, no guesses. The context window is required: a model whose
  server states none is an error ("server broken, or moka reads the wrong
  route"). Images and effort levels are optional (absent = unknown / none).
  `max_context` (servers.toml) is deleted.
- **Servers config (2026-10-01).** `type` is always written (no table-name
  shortcut); `openai` is renamed `openai-compatible` (deleted since, see above); `llamacpp` no longer
  assumes one served model (it can serve several): the selected id is always
  sent; OpenRouter uses a `models` list plus `providers` and
  `providers_by_model` instead of per-model tables; templates show only the
  keys a server needs; old shapes are reported with their replacement, never
  aliased.
- **Ollama removed (2026-10-01).** No `ollama` type and no native client
  (with `openai-compatible` gone it has no type at all). The effort-variant switch
  (`X:low` / `X:high` sibling ids) went with it. `providers.md` §9.11.
- **Effort levels are read only where a server states them.** No guessing:
  each provider reads its documented field (OpenRouter
  `reasoning.supported_efforts`, DeepSeek `effort.supported_levels`). A server
  that states none gets no `/effort` menu. A chosen level is always sent as
  chosen.
- **Reasoning is kept as the model produced it and sent back the same way.**
  moka never parses, moves or rewrites it.
- **Mode A (inline `<think>` in `content`) is off.** No tag parsing anywhere;
  `content` is stored, shown and sent back verbatim.
- **Replay depth is a role setting (2026-10-01).** `replay_reasoning_depth`
  in the role file: 0 = none, 1 = the current turn (the tool loop in
  progress), N = the last N turns, capped at what exists (999 = all).
  Default 1. A turn is one user message and everything the model does until
  its answer. moka sends what is configured, never more or less.
- **Provider minimum depth (2026-10-01).** A provider may document a minimum
  (DeepSeek: all turns whenever `tools` is sent, else HTTP 400;
  `guides/thinking_mode`, read 2026-10-01). A role below it gets a
  **warning when the role is selected**, nothing else; the configured depth
  is still sent.
- **Replay field is fixed per provider, from its docs (2026-10-01).**
  llama.cpp and DeepSeek: `reasoning_content` (llama.cpp PR #18994: used if
  the chat template supports it, else ignored). OpenRouter:
  `reasoning_details` as produced, or `reasoning` text
  (`reasoning-tokens.md` §Preserving Reasoning). Sent even when empty
  (opencode does the same for DeepSeek). Not configurable.
- **Signed / encrypted reasoning is not supported for now** (OpenAI,
  Anthropic, Gemini through OpenRouter).
- **No reasoning is sent back until step 2.** The previous replay
  (every turn, every model) was removed on 2026-09-30.
- **Compaction is filtered by default.** Reasoning dropped; each tool call
  reduced to one line with its key arguments, no output.

---

## Step 0: answered leftovers

1. Remove `efforts` model tables from the code; effort levels come only from
   detection. Model tables keep `providers` (OpenRouter). ✅
2. Model selection: on a fresh state, auto-select the top of `/model`. A last
   used model is never replaced: it stays, red, with the notice saying why.
   No `model` key in servers.toml (OpenRouter keeps its model tables). ✅
3. Menus are shared components:
   - with nothing typed, every suggestion list is sorted; a source with a
     meaningful order declares it (e.g. effort levels);
   - every picker built on `SelectionMenu` is clickable (completions,
     `/model`, `/session`, `/theme`, ...);
   - the `/config` popup uses the same sorted list. ✅
4. Remove the `$` shell prefix handling. ✅
5. `##` for help lines in the `ui`, `context` and `debug` templates (after
   checking the template sync and its guard test). Also `styles`, `theme`. ✅

## Step 1: provider contract (restructure, no behaviour change)

1. Write `.wiki/notes/providers.md` first, for review:
   - config spec: keys, required/forbidden (e.g. OpenRouter: no `base_url`;
     openai-compatible: `base_url` required);
   - `stream(messages, tools, model, effort)` → neutral chunks: text,
     reasoning text, `reasoning_native`, tool-call pieces, usage, finish
     reason;
   - `replay(entry, target_model)` → reasoning fields for an outgoing message;
   - `list_models()` → `ModelInfo` (id, context window, images, effort
     levels);
   - `effort_payload(level)`;
   - class attributes: `fixed_url`, `default_url`, `extra_keys`, `template`;
   - what the harness guarantees in return;
   - checklist: adding a provider.
   Drafted and approved 2026-10-01. Sub-step 1.0 (before any code moves):
   golden wire tests recording today's request bodies per provider. ✅
   (`test/test_wire_requests.py`, `test/wire_fake.py`)
2. Move the providers behind it; delete the ~20 `type ==` branches
   (`endpoint.py`, `endpoint_discovery.py`, `endpoint_openai.py`,
   `settings.py`). Order agreed 2026-10-01: restructure first (1a classes +
   registry ✅, 1b Ollama into `providers/` ✅), then one commit per §9 change
   (stream + `Chunk` ✅; Ollama removed ✅; `ModelInfo` facts ✅; llama.cpp
   selection ✅; servers config ✅). ✅
3. One request path per provider: compaction collects from the stream (the
   non-streaming path goes). ✅ (2026-10-01, with `Chunk`)
4. Server types and the servers template come from the registry; the new
   servers.toml shape (`providers.md` §2.1). ✅ (2026-10-01)
5. Tests rewritten against the contract. ✅ (2026-10-01)

## Step 2: providers and reasoning

Done before the sub-steps: the tag parser removed from the stream and from
import ✅ (2026-10-01); replay rules checked against the docs of OpenRouter,
llama.cpp and DeepSeek ✅ (2026-10-01, see Decisions).

### 2a. Provider and config

1. `OpenAICompatible` made abstract; the `openai-compatible` type deleted
   (registry, template, docs, tests).
2. Context window required: a listed model whose server states none is an
   error in the notice band. Images and effort levels stay optional.
3. `max_context` deleted from `servers.toml` (spec, template, code).

### 2b. Reasoning replay

1. Stored on each assistant entry: `content` (verbatim), `reasoning` (text),
   `reasoning_native` (opaque, as produced; renamed from
   `reasoning_details`), `origin = {type, model}`. No compatibility for old
   sessions (decided 2026-10-01).
2. `replay_reasoning_depth` in role files (see Decisions), default 1.
3. `replay(entry)` per provider, with its documented field (see Decisions).
   OpenRouter: `reasoning_details` when `origin.model` is the current model,
   else `reasoning` text.
4. A provider's documented minimum depth; a role below it: warning when the
   role is selected.
5. Wire tests per provider (§9.x): tool-loop follow-up carries the field;
   depth 0, 1 and N; empty reasoning still sent.
6. Docs: `.wiki/notes/reasoning-traces.md` rewritten (ISSUES D3);
   `providers.md` §3, §4 S9, §6; ISSUES R8 removed.

### 2c. DeepSeek provider

1. `DeepSeek(OpenAICompatible)`, fixed URL `https://api.deepseek.com`
   (⚠ check the base URL page before building).
2. Facts from `GET /models`: `context_window`, `input_modalities`,
   `effort.supported_levels` (`api/list-models`, read 2026-10-01).
3. Effort as `reasoning_effort`; reasoning out and back in
   `reasoning_content`; minimum depth: all turns when `tools` is sent.
4. ⚠ Whether moka sends `thinking: {type: enabled}` or relies on the default
   (thinking is on by default per `guides/thinking_mode`): to decide.

## Step 3: compaction

1. `context.toml`: `compact_filter_thoughts`, `compact_filter_tool_calls`,
   both on by default.
2. The summarizer gets readable text, never raw stored entries (no ids,
   image references or native blocks).
3. Filtered tool calls become one line with key arguments, no output:
   `read file.py lines 40–120`, `bash: pytest -q → exit 1`,
   `edit file.py (3 replacements)`.

---

## Future polish

- Hint when raw `<think>` shows up in an answer (server not extracting
  reasoning, e.g. llama.cpp `--reasoning-format none` / `deepseek-legacy`
  while streaming; its default extracts into `reasoning_content`).
- New providers: native Anthropic, OpenAI Responses API, OpenCode Go
  (postponed 2026-10-01: per-model APIs — chat completions, Responses,
  Anthropic messages — and its public `/v1/models` states no context
  window; unchecked with an API key).
- llama.cpp router mode: whether `/props` answers per model (unverified).

## Open questions

- `edit` / `write` one-liners in compaction: is the replacement count enough,
  or keep the edited line ranges?
- Minimum-depth warning: only when the role is selected (decided), or also
  when switching to a model/server whose minimum the current role misses?
