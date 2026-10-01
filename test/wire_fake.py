"""In-process fake servers for wire-level provider tests.

Every request a provider makes, through its own pooled client or a throwaway
``httpx.AsyncClient()``, goes to one recording ``httpx.MockTransport``. Tests
assert on what was *sent* (method, URL, headers, JSON body), not on the UI.

Samples below are copied from the real APIs, never invented (PROCESS.md, rule
6); each notes its source and the date it was read. A field a provider does
not read may be trimmed, but no value is made up.
"""

import json
from dataclasses import dataclass
from typing import Any, Callable

import httpx

_REAL_CLIENT = httpx.AsyncClient


@dataclass
class Recorded:
    method: str
    url: str
    headers: dict
    body: Any                       # parsed JSON, or None

    @property
    def path(self) -> str:
        return httpx.URL(self.url).path


def json_response(obj: Any, status: int = 200) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(status, json=obj)


def text_response(text: str, status: int, content_type: str) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(
        status, headers={"content-type": content_type}, content=text.encode())


class FakeServer:
    """Records every request; answers from the routes registered with ``on``."""

    def __init__(self) -> None:
        self.requests: list[Recorded] = []
        self._routes: list[tuple[str, str, list]] = []

    def on(self, method: str, path_suffix: str, *responders: Callable) -> "FakeServer":
        """Answer ``method`` requests whose path ends with ``path_suffix``.
        Several responders are used in order, the last one repeating."""
        self._routes.append((method, path_suffix, list(responders)))
        return self

    def __call__(self, request: httpx.Request) -> httpx.Response:
        content = request.content
        self.requests.append(Recorded(
            method=request.method, url=str(request.url),
            headers={k.lower(): v for k, v in request.headers.items()},
            body=json.loads(content) if content else None))
        for method, suffix, responders in self._routes:
            if request.method == method and request.url.path.endswith(suffix):
                responder = responders.pop(0) if len(responders) > 1 else responders[0]
                return responder(request)
        return httpx.Response(404, json={"error": "no fake route for " + request.url.path})

    def sent(self, method: str | None = None, path_suffix: str = "") -> list[Recorded]:
        return [r for r in self.requests
                if (method is None or r.method == method) and r.path.endswith(path_suffix)]

    def calls(self) -> list[tuple[str, str]]:
        """``[(method, path)]`` in order: the request sequence."""
        return [(r.method, r.path) for r in self.requests]


def install(monkeypatch, server: FakeServer) -> None:
    """Route every ``httpx.AsyncClient`` created from now on to ``server``."""
    transport = httpx.MockTransport(server)

    class _FakeClient(_REAL_CLIENT):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)


# -- OpenAI-compatible chat (streaming) ---------------------------------------

# A ``chat.completion.chunk``: developers.openai.com/api/reference/resources/
# chat/subresources/completions/streaming-events, read 2026-10-01.
OPENAI_FIRST_CHUNK = json.loads(
    '{"id":"chatcmpl-123","object":"chat.completion.chunk","created":1694268190,'
    '"model":"gpt-6-astra", "system_fingerprint": "fp_44709d6fcb", "choices":[{"index":0,'
    '"delta":{"role":"assistant","content":""},"logprobs":null,"finish_reason":null}],'
    '"obfuscation":"r4N7vQ2m"}')


def openai_stream(text: str = "Hi", deltas: tuple = (), usage: dict | None = None,
                  ) -> Callable[[httpx.Request], httpx.Response]:
    """A chat stream: the documented first chunk, any extra ``deltas``, one
    content chunk, the finish chunk, then the usage chunk the docs describe for
    ``include_usage`` (empty ``choices`` plus a ``usage`` object; the docs give
    no concrete example). ``usage`` replaces that last chunk's extra fields."""
    def chunk(delta, finish=None):
        return {**OPENAI_FIRST_CHUNK,
                "choices": [{"index": 0, "delta": delta, "logprobs": None, "finish_reason": finish}]}
    last = {**OPENAI_FIRST_CHUNK, "choices": [],
            **(usage or {"usage": {"prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11}})}
    frames = [OPENAI_FIRST_CHUNK, *map(chunk, deltas), chunk({"content": text}),
              chunk({}, "stop"), last]
    body = "".join("data: " + json.dumps(f) + "\n\n" for f in frames) + "data: [DONE]\n\n"
    return text_response(body, 200, "text/event-stream")


# -- llama.cpp ------------------------------------------------------------------

# The last chunk of a /v1/chat/completions answer: its ``timings`` and its
# "standard usage object", both from github.com/ggml-org/llama.cpp,
# tools/server/README.md ("Timings and context usage"), read 2026-10-01.
LLAMACPP_FINAL_CHUNK = json.loads("""
{"timings": {"cache_n": 236, "prompt_n": 1, "prompt_ms": 30.958,
             "prompt_per_token_ms": 30.958, "prompt_per_second": 32.301828283480845,
             "predicted_n": 35, "predicted_ms": 661.064,
             "predicted_per_token_ms": 18.887542857142858,
             "predicted_per_second": 52.94494935437416},
 "usage": {"completion_tokens": 48, "prompt_tokens": 44, "total_tokens": 92,
           "prompt_tokens_details": {"cached_tokens": 0}}}
""")

# GET /models and GET /props: github.com/ggml-org/llama.cpp, tools/server/
# README.md (master), read 2026-10-01. /props trimmed to the fields below.
LLAMACPP_MODELS = json.loads("""
{"object": "list", "data": [{"id": "../models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
 "object": "model", "created": 1735142223, "owned_by": "llamacpp",
 "meta": {"vocab_type": 2, "n_vocab": 128256, "n_ctx_train": 131072, "n_embd": 4096,
          "n_params": 8030261312, "size": 4912898304}}]}
""")
LLAMACPP_PROPS = json.loads("""
{"default_generation_settings": {"id": 0, "id_task": -1, "n_ctx": 1024, "speculative": false,
 "is_processing": false},
 "total_slots": 1,
 "model_path": "../models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
 "modalities": {"vision": false}}
""")


# -- OpenRouter -----------------------------------------------------------------

# A streamed delta carrying ``reasoning_details``: openrouter.ai/docs/use-cases/
# reasoning-tokens ("Streaming Response"), read 2026-10-01.
OPENROUTER_REASONING_DELTA = json.loads("""
{"reasoning_details": [{"type": "reasoning.text",
  "text": "Let me think about this step by step...", "signature": null,
  "id": "reasoning-text-1", "format": "anthropic-claude-v1", "index": 0}]}
""")

# An encrypted ``reasoning_details`` block: openrouter.ai/docs/use-cases/
# reasoning-tokens.md ("Encrypted Type"), read 2026-10-01. The same page says
# streamed blocks are "built by concatenating all chunks in order".
OPENROUTER_ENCRYPTED_BLOCK = json.loads("""
{"type": "reasoning.encrypted",
 "data": "eyJlbmNyeXB0ZWQiOiJ0cnVlIiwiY29udGVudCI6IltSRURBQ1RFRF0ifQ==",
 "id": "reasoning-encrypted-1", "format": "anthropic-claude-v1", "index": 1}
""")

# The ``usage`` of the last SSE message: openrouter.ai/docs/use-cases/
# usage-accounting.md ("Response Format"), read 2026-10-01.
OPENROUTER_USAGE = json.loads("""
{"completion_tokens": 2, "completion_tokens_details": {"reasoning_tokens": 0},
 "cost": 0.95, "cost_details": {"upstream_inference_cost": 19},
 "prompt_tokens": 194,
 "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 100, "audio_tokens": 0},
 "total_tokens": 196}
""")

# -- DeepSeek (an OpenAI-compatible server) ------------------------------------

# Thinking mode streams ``delta.reasoning_content`` next to ``content``:
# api-docs.deepseek.com/guides/thinking_mode, read 2026-10-01.
DEEPSEEK_REASONING_DELTA = {"reasoning_content": "9.11 has fewer tenths"}


# Two entries of GET https://openrouter.ai/api/v1/models (the public list), read
# 2026-10-01, trimmed to the keys moka reads.
OPENROUTER_MODELS = {"data": json.loads("""
[
  {
    "id": "deepseek/deepseek-v4.1-flash",
    "canonical_slug": "deepseek/deepseek-v4.1-flash-20260910",
    "name": "DeepSeek: DeepSeek V4.1 Flash",
    "context_length": 1048576,
    "architecture": {
      "modality": "text+image->text",
      "input_modalities": [
        "text",
        "image"
      ],
      "output_modalities": [
        "text"
      ],
      "tokenizer": "DeepSeek",
      "instruct_type": null
    },
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "reasoning": {
      "mandatory": false,
      "default_enabled": true,
      "supported_efforts": [
        "max",
        "high",
        "low"
      ],
      "default_effort": "high"
    }
  },
  {
    "id": "qwen/qwen3.8-omni-flash",
    "canonical_slug": "qwen/qwen3.8-omni-flash-20260918",
    "name": "Qwen: Qwen3.8 Omni Flash",
    "context_length": 1000000,
    "architecture": {
      "modality": "text+image+audio+video->text",
      "input_modalities": [
        "text",
        "image",
        "audio",
        "video"
      ],
      "output_modalities": [
        "text"
      ],
      "tokenizer": "Qwen",
      "instruct_type": null
    },
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "reasoning": {
      "mandatory": false,
      "default_enabled": true,
      "supports_max_tokens": true
    }
  }
]
""")}
