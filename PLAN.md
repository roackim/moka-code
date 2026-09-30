# Plan: providers, reasoning, compaction

Direction for the next steps (2026-09-30). Each step ends with fewer files,
branches and code paths. Items marked ⚠ are unverified and get checked
against the provider's current docs before they are built on.

---

## Decisions

- **Providers.** An `Endpoint` *is* an instance of its provider's class:
  base `Endpoint` (the contract + per-server config and state) →
  `OpenAICompatible` → `LlamaCpp`, `OpenAI`, `OpenRouter`; `Ollama` on its
  own. A registry `{type: class}` drives config validation and the servers
  template. Per-server state (selected model, connection, effort) lives in
  the instance.
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
   detection. Model tables keep `providers` (OpenRouter).
2. Always auto-select a model: the last used one if still available, else the
   first available. `no model selected` only when nothing is available.
3. Menus are shared components:
   - with nothing typed, every suggestion list is sorted; a source with a
     meaningful order declares it (e.g. effort levels);
   - every picker built on `SelectionMenu` is clickable (completions,
     `/model`, `/session`, `/theme`, ...);
   - the `/config` popup uses the same sorted list.
4. Remove the `$` shell prefix handling.
5. `##` for help lines in the `ui`, `context` and `debug` templates (after
   checking the template sync and its guard test).

## Step 1: provider contract (restructure, no behaviour change)

1. Write `.wiki/notes/providers.md` first, for review:
   - config spec: keys, required/forbidden (e.g. OpenRouter: no `base_url`;
     Ollama/OpenAI: `base_url` required);
   - `stream(messages, tools, model, effort)` → neutral chunks: text,
     reasoning text, `reasoning_native`, tool-call pieces, usage, finish
     reason;
   - `replay(entry, target_model)` → reasoning fields for an outgoing message;
   - `list_models()` → `ModelInfo` (id, context window, images, effort
     levels); `served_model()` for single-model servers;
   - `effort_payload(level)`;
   - capabilities: `selects_model`, `fixed_url`;
   - what the harness guarantees in return;
   - checklist: adding a provider.
2. Move the four providers behind it; delete the ~20 `type ==` branches
   (`endpoint.py`, `endpoint_discovery.py`, `endpoint_openai.py`,
   `settings.py`).
3. One request path per provider: compaction collects from the stream (the
   non-streaming path goes).
4. Server types and the servers template come from the registry.
5. Tests rewritten against the contract.

## Step 2: reasoning

1. Stored on each assistant entry:
   - `content`: verbatim, never parsed;
   - `reasoning`: plain text, if any;
   - `reasoning_native`: opaque, as the provider sent it (today's
     `reasoning_details`; later Anthropic thinking blocks, OpenAI encrypted
     items);
   - `origin`: `{type, model}` of the producing server/model.
2. Remove the tag parser from the stream and from import.
3. `replay` per provider:
   - llama.cpp / OpenAI-compatible: `reasoning` → `reasoning_content`;
   - Ollama: `reasoning` → `thinking`;
   - OpenRouter: `reasoning_native` when `origin.model` is the target,
     else `reasoning` text;
   - default depth: current turn (see Decisions).
4. Plain text replays across models (DeepSeek Flash → Pro works); signed or
   encrypted native blocks only to the model that produced them. When they
   are withheld: one line in `/activity` + a hint-row flash.
5. Old sessions: `reasoning_details` read as `reasoning_native`; an entry
   without `origin` has its native blocks withheld.
6. Cleanup: dead parser state (`full_reasoning`, `detected_open_tag`),
   misleading docstrings, duplicate checks in the stream loop, `MetricsState`
   out of `thinking_parser.py`, `.wiki/notes/reasoning-traces.md` rewritten.
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
  reasoning, e.g. llama.cpp `--reasoning-format`).
- New providers: native Anthropic, DeepSeek, OpenAI Responses API.

## Open questions

- `edit` / `write` one-liners in compaction: is the replacement count enough,
  or keep the edited line ranges?
