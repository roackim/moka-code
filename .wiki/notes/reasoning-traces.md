# Reasoning Trace Handling in moka

*How moka preserves model reasoning/thinking traces across turns, export and import.*

> **Status**: Reasoning is **always stored** in history (the assistant entry's
> `reasoning` text, plus OpenRouter's structured `reasoning_details` as
> produced). Every assistant message goes back with its reasoning
> (`_api_history`), in each server's own reasoning field, never folded into
> `content`. Not configurable (pending an audit).

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
| Ollama native | `message.thinking` (llama.cpp-backed proxies: `message.reasoning`) |

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
| `openai` | `reasoning_content` | the de facto field of OpenAI-compatible servers that show reasoning (llama.cpp behind a proxy, e.g. metallama); without it Qwen's template renders an empty `<think></think>` for each earlier step. Real OpenAI Chat Completions returns no reasoning text, so nothing is ever sent there |

(`endpoint_openai.outgoing_messages`, `endpoint_ollama.ollama_messages`.)

`Harness._api_history` keeps it on every assistant message; inside `chat()`
each new assistant turn is appended with `keep_reasoning=True` too.

Removed (2026-09-28): folding reasoning into `content` as `<think>…</think>`
(the model saw its old thoughts as answer text and servers could not treat them
as reasoning), `reasoning_tag`, and the uncalled thinking-prefill injection.

---

## Configuration

None. The servers.toml `preserve_reasoning` key (per server / model table) was
removed (2026-09-30); it is now an unknown key. The former
`context.preserve_reasoning_traces` is retired and removed from `context.toml`
on startup. llama.cpp still needs `--reasoning-preserve` (default on) for
earlier turns to reach the prompt.

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