# Reasoning Trace Handling in moka

*How moka preserves model reasoning/thinking traces across turns, export and import.*

> **Status**: Reasoning is **always stored** in history (the assistant entry's
> `reasoning` text, plus OpenRouter's structured `reasoning_details` as
> produced). What goes back to the model follows one rule (`_api_history`):
> the **current turn's** reasoning (every assistant message since the last user
> message — the tool loop in progress) is always sent; **earlier turns'** when
> the server/model has `preserve_reasoning` (servers.toml, default true). It is
> sent in each server's own reasoning field, never folded into `content`.

---

## How Reasoning is Streamed

The streaming entry point is `Harness._stream_llm_response()` in `harness.py`. Two code paths handle reasoning:

### 1. `reasoning_content` API field

```python
reasoning = getattr(delta, "reasoning_content", None)
if reasoning:
    full_reasoning += reasoning
    yield events.Reasoning(text=reasoning)
    continue
```

This handles the non-standard `reasoning_content` field that local inference servers (llama.cpp, vLLM) and some cloud APIs (DeepSeek) use to deliver chain-of-thought traces. The reasoning is accumulated into `full_reasoning` **and** yielded to the UI.

Provider field names differ and are normalized by the transport adapters:

| Provider | Field |
|---|---|
| DeepSeek / vLLM / llama.cpp | `delta.reasoning_content` |
| OpenRouter (and others) | `delta.reasoning` |
| OpenRouter structured | `delta.reasoning_details[].text` |
| Ollama native | `message.thinking` |

`endpoint_openai._extract_reasoning()` reads the OpenAI-compatible aliases;
`endpoint_ollama` maps `thinking`. If a provider's field is not listed here it
will be silently dropped before the harness sees it — a missing reasoning field
in an export usually means the adapter, not the harness.

### 2. Inline thinking tags in `content` field

The code parses content for embedded thinking tags:

```python
THINKING_TAGS = [
    ("  thinking", "  response"),
    ("<thinking>", "</thinking>"),
]
```

When an opening tag is found in the content stream, content before the tag goes to `events.Token` (and is accumulated into `full_content`), while content *between* the tags goes to `events.Reasoning` **and** is accumulated into `full_reasoning`. The tag delimiters themselves are consumed and discarded.

Across chunk boundaries the parser withholds only the tail that could still be
a partial tag (`_partial_tag_len`), not a fixed-size suffix. Withholding a fixed
tail delayed the last characters of a message until `flush()` at end-of-stream —
very visible when a tool call followed the content (the tail appeared only after
the call). Ordinary text is now emitted immediately.

---

## How the Assistant Message is Saved

```python
msg = {"id": assistant_msg_id, "role": "assistant", "content": full_content or None}
if tool_calls_list:
    msg["tool_calls"] = tool_calls_list
if full_reasoning:
    msg["reasoning"] = full_reasoning
if self._last_reasoning_details:          # OpenRouter blocks, as produced
    msg["reasoning_details"] = self._last_reasoning_details
```

OpenRouter streams `reasoning_details` in pieces;
`endpoint_openai.merge_reasoning_details` folds pieces sharing an `index` into
one block (`text`/`summary`/`data` concatenate, `signature`/`id`/`format` fill
in), rebuilding the blocks as the model produced them — OpenRouter requires
them back **unmodified**.

---

## How History is Re-sent to the LLM

`Harness._to_api_message(entry, keep_reasoning)` sends only `_API_FIELDS`
(`role`, `content`, `tool_calls`, `tool_call_id`) — moka's `id`, `source`,
`reasoning_tag` of old exports, image references never leave. With
`keep_reasoning` an assistant message carries its reasoning under neutral
`reasoning` / `reasoning_details` keys, which each endpoint moves to its field:

| Server (`type`) | Field sent | Source |
|---|---|---|
| `llamacpp` | `reasoning_content` | llama.cpp `common/chat.cpp` parses it from input messages; `--reasoning-preserve` (default on) + the chat template decide whether earlier turns reach the prompt |
| `openrouter` | `reasoning_details` unmodified when the model produced them, else `reasoning` | OpenRouter reasoning docs; required across tool calls |
| `ollama` | `thinking` | Ollama `/api/chat` message fields |
| `openai` | none | Chat Completions has no such field |

(`endpoint_openai.outgoing_messages`, `endpoint_ollama.ollama_messages`.)

Which messages keep it (`Harness._api_history`): assistant messages after the
last *user* message (a `source: "tool"` image entry does not count) always —
the model continues that turn from its reasoning; the rest only when
`Endpoint.preserves_reasoning()` is true. Inside `chat()` each new assistant turn is
appended with `keep_reasoning=True` (it is by definition in the current turn).

Removed (2026-09-28): folding reasoning into `content` as `<think>…</think>`
(the model saw its old thoughts as answer text and servers could not treat them
as reasoning), `reasoning_tag`, and the uncalled thinking-prefill injection.

---

## Configuration

`servers.toml`, per server and per OpenRouter model table (the model's value
wins; absent means `true`; the template ships it commented):

```toml
[servers.local]
type = "llamacpp"
# preserve_reasoning = true           # llama.cpp also needs --reasoning-preserve

[servers.openrouter.models."anthropic/claude-sonnet-4"]
preserve_reasoning = false            # this model: current turn only
```

On: more context per request (paid input tokens on OpenRouter), better
continuity where the server/template keeps it. Off: only the current tool
loop's reasoning goes back. The former `context.preserve_reasoning_traces`
(global, default off) is retired and removed from `context.toml` on startup.

---

## Verification (2026-09-28)

**Checked against a real provider** — OpenRouter,
`deepseek/deepseek-v4.1-flash`, harness driven directly, two turns, three
requests, no errors:

| What | Result |
|---|---|
| Tool loop: reasoning sent back with the tool call (`reasoning_details`) | accepted |
| Next turn: earlier reasoning re-sent (`preserve_reasoning` default) | accepted, coherent answer |
| API field allow-list (no `id`) | accepted |
| Streamed `reasoning_details` merge | 39 pieces → 1 `reasoning.text` block, same length as the reasoning text |

Also checked with a fake llama.cpp server recording payloads:
`reasoning_content` on the right messages, with preserve on and off.

**Open — possible future investigation**

- **Signed / encrypted reasoning blocks.** DeepSeek sends plain
  `reasoning.text` blocks without a signature, so the case where a wrong merge
  would be rejected (Claude or Gemini through OpenRouter: signed text,
  `reasoning.encrypted`) was not exercised. `merge_reasoning_details` joins
  pieces by `index`, based on OpenRouter's docs ("concatenate chunks in order",
  send back unmodified). To settle: one tool-call turn with a Claude or Gemini
  model on OpenRouter; a rejection names the offending block.
- **Real models never met:** images (content parts, Ollama `images`), the
  text-only refusal, and Ollama's `thinking` field — all checked with fake
  servers only.

---

## Related

- [architecture.md](../notes/architecture.md) — High-level data flow
- [config.md](../notes/config.md) — Configuration reference
- `events.py` — Defines the `Reasoning` and `Token` event types
- `harness.py` — `_stream_llm_response()` and `chat()` methods