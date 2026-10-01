# Plan: providers, reasoning, compaction

Direction for the next steps (2026-09-30). Each step ends with fewer files,
branches and code paths. Items marked ⚠ are unverified and get checked
against the provider's current docs before they are built on.

---

## Decisions

- **Providers.** An `Endpoint` *is* an instance of its provider's class:
  base `Endpoint` (the contract + per-server config and state) →
  `OpenAICompatible` (type `openai-compatible`) → `LlamaCpp`, `OpenRouter`.
  A registry `{type: class}` drives config validation
  and the servers template. Per-server state (selected model, connection,
  effort) lives in the instance. Contract: `.wiki/notes/providers.md`.
- **Servers config (2026-10-01).** `type` is always written (no table-name
  shortcut); `openai` is renamed `openai-compatible`; `llamacpp` no longer
  assumes one served model (it can serve several): the selected id is always
  sent; OpenRouter uses a `models` list plus `providers` and
  `providers_by_model` instead of per-model tables; templates show only the
  keys a server needs; old shapes are reported with their replacement, never
  aliased.
- **Ollama removed (2026-10-01).** No `ollama` type and no native client; an
  Ollama server can be used as `openai-compatible` (`…:11434/v1`), without
  context window, image or effort facts. The effort-variant switch
  (`X:low` / `X:high` sibling ids) went with it. `providers.md` §9.11.
- **Effort levels are read only where a server states them.** No guessing:
  OpenRouter `reasoning.supported_efforts`; others only if the server
  advertises `reasoning.supported_efforts`. A server that states none gets no `/effort`
  menu. A chosen level is always sent as chosen.
- **Reasoning is kept as the model produced it and sent back the same way.**
  moka never parses, moves or rewrites it.
- **Mode A (inline `<think>` in `content`) is off.** No tag parsing anywhere;
  `content` is stored, shown and sent back verbatim.
- **Replay depth.** Default: the current turn's reasoning (the tool loop in
  progress) is sent back; earlier turns' is not. A provider's `replay` may
  opt into more. ⚠ Basis (general knowledge): Anthropic ignores earlier
  thinking blocks; DeepSeek wants reasoning back only inside a tool loop;
  Qwen3 templates drop earlier thinking; OpenRouter requires it across tool
  calls.
- **No reasoning is sent back until step 2.** The previous replay
  (every turn, every model) was removed on 2026-09-30. No user setting for
  replay depth (not in servers, context or roles); providers decide.
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
   selection ✅; servers config).
3. One request path per provider: compaction collects from the stream (the
   non-streaming path goes). ✅ (2026-10-01, with `Chunk`)
4. Server types and the servers template come from the registry; the new
   servers.toml shape (`providers.md` §2.1).
5. Tests rewritten against the contract.

## Step 2: reasoning

1. Stored on each assistant entry:
   - `content`: verbatim, never parsed;
   - `reasoning`: plain text, if any;
   - `reasoning_native`: opaque, as the provider sent it (today's
     `reasoning_details`; later Anthropic thinking blocks, OpenAI encrypted
     items);
   - `origin`: `{type, model}` of the producing server/model.
2. Remove the tag parser from the stream and from import. ✅ (2026-10-01)
3. `replay` per provider:
   - llama.cpp / OpenAI-compatible: `reasoning` → `reasoning_content`;
   - OpenRouter: `reasoning_native` when `origin.model` is the target,
     else `reasoning` text;
   - default depth: current turn (see Decisions).
4. Plain text replays across models (DeepSeek Flash → Pro works); signed or
   encrypted native blocks only to the model that produced them. When they
   are withheld: one line in `/activity` + a hint-row flash.
5. Old sessions: `reasoning_details` read as `reasoning_native`; an entry
   without `origin` has its native blocks withheld.
6. Cleanup: `.wiki/notes/reasoning-traces.md` rewritten. (Dead parser state,
   duplicate stream-loop checks and `MetricsState` done with item 2.)
7. Verify the ⚠ replay rules against each provider's docs.

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
- New providers: native Anthropic, DeepSeek, OpenAI Responses API.

## Open questions

- `edit` / `write` one-liners in compaction: is the replacement count enough,
  or keep the edited line ranges?
