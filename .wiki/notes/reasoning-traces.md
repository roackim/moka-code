# Reasoning Trace Handling in pico-chat

*How pico-chat preserves model reasoning/thinking traces across turns, export and import.*

> **Status**: Reasoning is **always stored** in history under the assistant entry's
> `reasoning` field (with the open tag in `reasoning_tag`), so it survives
> multi-turn context, `/export`, `/import` and the transcript — nothing is lost.
> The `preserve_reasoning_traces` config flag (default: `off`) now controls only
> whether that stored reasoning is folded back into the request sent to the
> model as a `<think>...</think>` block; it no longer controls whether reasoning
> is kept.

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

After the stream ends, the assistant message is saved to history with its raw
reasoning kept in a dedicated field:

```python
msg = {"id": assistant_msg_id, "role": "assistant", "content": full_content or None}
if full_reasoning:
    msg["reasoning"] = full_reasoning
    if detected_tag:
        msg["reasoning_tag"] = detected_tag   # e.g. "<think>"
if tool_calls_list:
    msg["tool_calls"] = tool_calls_list
self.history.append(msg)
```

`content` is always the raw answer; reasoning is never folded into it at write
time, so it can be restored exactly.

---

## How History is Re-sent to the LLM

On subsequent turns, `_build_messages()` projects each stored entry through
`Harness._to_api_message()`, which strips `reasoning`/`reasoning_tag` (not valid
API fields) and — only when `preserve_reasoning_traces` is enabled — folds the
reasoning back into `content` using the model's own tag:

```python
messages = [system_msg]
messages.extend(self._to_api_message(m) for m in self._get_effective_history())

def _to_api_message(self, entry):
    msg = {k: v for k, v in entry.items() if k not in ("reasoning", "reasoning_tag")}
    reasoning = entry.get("reasoning")
    if reasoning and pico_cfg.config.preserve_reasoning_traces:
        open_tag, close_tag = self._reasoning_tag_pair(entry.get("reasoning_tag"))
        msg["content"] = f"{open_tag}\n{reasoning}\n{close_tag}\n\n{msg.get('content') or ''}"
    return msg
```

With the flag off (default), the model does not see prior chain-of-thought. With
it on, each assistant message regains its reasoning inline.

---

## Configuration

Enable in `~/.config/pico-chat/context.toml`:

```toml
preserve_reasoning_traces = true
```

The flag defaults to `false`; reasoning is still saved either way.

---

## Impact Assessment

| Scenario | Flag Off | Flag On |
|---|---|---|
| **Stored in history / export / import** | ✅ Always preserved | ✅ Always preserved |
| **Visible in the transcript** | ✅ When reasoned (`▌ thought for Xs`) | ✅ When reasoned |
| **Model sees prior CoT** | ❌ Not re-sent | ✅ Re-sent inline |
| **Multi-turn, non-reasoning model** | No issue | No issue (no reasoning to preserve) |
| **Tool-calling multi-step** | Reasoning kept in history for all steps | ✅ Reasoning between calls re-sent |

---

## Related

- [architecture.md](../notes/architecture.md) — High-level data flow
- [config.md](../notes/config.md) — Configuration reference
- `events.py` — Defines the `Reasoning` and `Token` event types
- `harness.py` — `_stream_llm_response()` and `chat()` methods