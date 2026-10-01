"""Wire-level golden tests: what each provider sends, and what it learns.

PLAN.md step 1.0 (providers.md §8): these record the request bodies and
discovery results of the code as it is, through the in-process fake server of
``wire_fake``. Step 1 restructures the providers; these tests must pass
unchanged afterwards, apart from the deliberate changes of providers.md §9
(marked "§9.x" below), whose expectations change on purpose in the same commit.

Only the three helpers ``make_endpoint``, ``run_chat`` and ``learn`` touch the
provider API; they are the only lines that change with the restructure.
"""

import asyncio

import pytest

import wire_fake as wire
from moka_code.harness.endpoint import Endpoint, make_endpoint as build


# -- the three adapters to the provider API -------------------------------------

def make_endpoint(table: dict, effort=None) -> Endpoint:
    """A server built from a ``servers.toml`` table, with model ``m`` selected."""
    endpoint = build("srv", table)
    endpoint._selected_model = "m"
    endpoint.effort = effort
    return endpoint


def run_chat(endpoint, messages, tools=None, chunks=None) -> tuple[str, list]:
    """One streamed chat request; ``(answer text, usage of each chunk)``.
    Every chunk is also appended to ``chunks`` when given."""
    async def go():
        text, usages = "", []
        async for chunk in endpoint.stream(messages, tools=tools):
            if chunks is not None:
                chunks.append(chunk)
            text += chunk.text
            if chunk.usage is not None:
                usages.append((chunk.usage.prompt_tokens, chunk.usage.completion_tokens))
        await endpoint.aclose()
        return text, usages
    return asyncio.run(go())


def learn(endpoint) -> dict:
    """``{model id: (context window, accepts images, effort levels)}`` as the
    app learns them from this server."""
    async def go():
        facts = {m.id: (m.context_window, m.images, m.efforts)
                 for m in await endpoint.list_models()}
        await endpoint.aclose()
        return facts
    return asyncio.run(go())


# -- shared input ------------------------------------------------------------------

HISTORY = [
    {"role": "system", "content": "be brief"},
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "read", "arguments": "{\"path\": \"a\"}"}}]},
    {"role": "tool", "tool_call_id": "call_1", "content": "file text"},
    {"role": "user", "content": [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}]},
]
TOOLS = [{"type": "function", "function": {
    "name": "read", "description": "d", "parameters": {"type": "object", "properties": {}}}}]
SIMPLE = HISTORY[:2]

LLAMACPP = {"type": "llamacpp", "base_url": "http://h/v1"}
OPENROUTER = {"type": "openrouter"}


@pytest.fixture
def fake(monkeypatch):
    server = wire.FakeServer()
    wire.install(monkeypatch, server)
    return server


def chat_route(server):
    return server.on("POST", "/chat/completions", wire.openai_stream())


def chat_body(server):
    [request] = server.sent("POST", "/chat/completions")
    return request.body


# -- chat completions -------------------------------------------------------------

def test_chat_completions_request(fake):
    chat_route(fake)
    text, usages = run_chat(make_endpoint({**LLAMACPP, "api_key": "K"}), HISTORY, TOOLS)

    [request] = fake.requests
    assert (request.method, request.url) == ("POST", "http://h/v1/chat/completions")
    assert request.headers["authorization"] == "Bearer K"
    assert request.body == {
        "model": "m", "messages": HISTORY, "tools": TOOLS,
        "stream": True, "stream_options": {"include_usage": True}}
    assert (text, usages) == ("Hi", [(9, 2)])


def test_no_api_key_sends_no_authorization_header(fake):
    chat_route(fake)
    run_chat(make_endpoint(LLAMACPP), SIMPLE)
    assert "authorization" not in fake.requests[0].headers
    assert chat_body(fake) == {"model": "m", "messages": SIMPLE, "stream": True,
                               "stream_options": {"include_usage": True}}


@pytest.mark.parametrize("table, effort, fragment", [
    (LLAMACPP, "high", {"reasoning_effort": "high"}),
    (LLAMACPP, "low", {"reasoning_effort": "low"}),
    (OPENROUTER, "max", {"reasoning": {"effort": "max"}}),
    (LLAMACPP, None, {}),
    (OPENROUTER, None, {}),
])
def test_effort_is_sent_in_each_servers_own_field(fake, table, effort, fragment):
    chat_route(fake)
    fake.on("GET", "/models", wire.json_response(wire.LLAMACPP_MODELS))
    run_chat(make_endpoint(table, effort), SIMPLE)
    body = chat_body(fake)
    sent = {k: v for k, v in body.items() if k in ("reasoning_effort", "reasoning")}
    assert sent == fragment


def test_a_503_is_retried_with_the_same_request(fake):
    fake.on("POST", "/chat/completions",
            wire.text_response("loading model", 503, "text/plain"), wire.openai_stream())
    text, _ = run_chat(make_endpoint({**LLAMACPP, "retry_delay": 0}), SIMPLE)
    first, second = fake.sent("POST", "/chat/completions")
    assert first.body == second.body and text == "Hi"


@pytest.mark.parametrize("table, cached", [(LLAMACPP, 0), (LLAMACPP, 0)])
def test_llamacpp_cache_counts_as_reported(fake, table, cached):
    """§9 (2026-10-01): only llama.cpp reads ``timings.cache_n``, and only when
    usage has no cache count. llama.cpp's documented usage already says
    ``cached_tokens: 0``, so ``cache_n`` is never used (ISSUES P11)."""
    fake.on("POST", "/chat/completions", wire.openai_stream(usage=wire.LLAMACPP_FINAL_CHUNK))
    chunks = []
    run_chat(make_endpoint(table), SIMPLE, chunks=chunks)
    [usage] = [c.usage for c in chunks if c.usage]
    assert (usage.prompt_tokens, usage.cached_prompt_tokens) == (44, cached)


@pytest.mark.parametrize("table, native", [
    (OPENROUTER, wire.OPENROUTER_REASONING_DELTA["reasoning_details"]),
    (LLAMACPP, None),
])
def test_only_openrouter_assembles_reasoning_details(fake, table, native):
    """§9 (2026-10-01): ``reasoning_details`` blocks are OpenRouter's; every
    OpenAI-compatible server's reasoning text is still read."""
    fake.on("POST", "/chat/completions", wire.openai_stream(deltas=(wire.OPENROUTER_REASONING_DELTA,)))
    chunks = []
    text, _ = run_chat(make_endpoint(table), SIMPLE, chunks=chunks)
    assert "".join(c.reasoning for c in chunks) == "Let me think about this step by step..."
    assert [c.reasoning_native for c in chunks if c.reasoning_native] == ([native] if native else [])
    assert text == "Hi"


def test_llamacpp_cache_from_timings_when_usage_has_none(fake):
    """⚠ Not a documented shape: llama.cpp's README shows usage *with*
    ``prompt_tokens_details`` (ISSUES P11). Kept so the ``timings.cache_n``
    reading stays pinned until P11 is settled: llama.cpp only (§9.8)."""
    final = {"timings": wire.LLAMACPP_FINAL_CHUNK["timings"],
             "usage": {"completion_tokens": 48, "prompt_tokens": 44, "total_tokens": 92}}
    for table, cached in [(LLAMACPP, 236), (OPENROUTER, None)]:
        fake.on("POST", "/chat/completions", wire.openai_stream(usage=final))
        chunks = []
        run_chat(make_endpoint(table), SIMPLE, chunks=chunks)
        [usage] = [c.usage for c in chunks if c.usage]
        assert usage.cached_prompt_tokens == cached


def test_openrouter_usage_is_read_as_documented(fake):
    """Usage accounting (source in ``wire_fake``): counts, cache and cost."""
    fake.on("POST", "/chat/completions",
            wire.openai_stream(usage={"usage": wire.OPENROUTER_USAGE}))
    chunks = []
    run_chat(make_endpoint(OPENROUTER), SIMPLE, chunks=chunks)
    [usage] = [c.usage for c in chunks if c.usage]
    assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (194, 2, 196)
    assert (usage.reasoning_tokens, usage.cached_prompt_tokens, usage.cost) == (0, 0, 0.95)


@pytest.mark.parametrize("table, delta, reasoning", [
    (LLAMACPP, wire.DEEPSEEK_REASONING_DELTA, "9.11 has fewer tenths"),
    (LLAMACPP, wire.DEEPSEEK_REASONING_DELTA, "9.11 has fewer tenths"),
    # ⚠ OpenRouter documents ``message.reasoning`` (non-streamed); the streamed
    # ``delta.reasoning`` is the same field per chunk, not shown in its docs.
    (OPENROUTER, {"reasoning": "pondering"}, "pondering"),
])
def test_reasoning_text_is_read_from_each_servers_field(fake, table, delta, reasoning):
    """Slot S8: the reasoning text reaches ``Chunk.reasoning`` verbatim."""
    fake.on("POST", "/chat/completions", wire.openai_stream(deltas=(delta,)))
    chunks = []
    text, _ = run_chat(make_endpoint(table), SIMPLE, chunks=chunks)
    assert "".join(c.reasoning for c in chunks) == reasoning
    assert text == "Hi"


def test_openrouter_reasoning_blocks_are_assembled_per_index(fake):
    """§9.9: the documented text block, streamed in two pieces sharing its
    ``index``, then the documented encrypted block: rebuilt as two blocks,
    yielded once at the end, as OpenRouter wants them back."""
    [block] = wire.OPENROUTER_REASONING_DELTA["reasoning_details"]
    first = {**block, "text": "Let me think about "}
    second = {"type": "reasoning.text", "text": "this step by step...", "index": 0}
    # ⚠ A signature arriving in a later, empty piece is not shown in the docs
    # (its value is the docs' "Text Type" example); a later non-null field fills in.
    signed = {"type": "reasoning.text", "text": "", "index": 0,
              "signature": "sha256:abc123def456..."}
    deltas = ({"reasoning_details": [first]}, {"reasoning_details": [second]},
              {"reasoning_details": [signed]},
              {"reasoning_details": [wire.OPENROUTER_ENCRYPTED_BLOCK]})
    fake.on("POST", "/chat/completions", wire.openai_stream(deltas=deltas))
    chunks = []
    run_chat(make_endpoint(OPENROUTER), SIMPLE, chunks=chunks)
    assert "".join(c.reasoning for c in chunks) == "Let me think about this step by step..."
    native = [c.reasoning_native for c in chunks if c.reasoning_native]
    assert native == [[{**block, "signature": "sha256:abc123def456..."},
                       wire.OPENROUTER_ENCRYPTED_BLOCK]]


def test_compaction_is_streamed(fake, tmp_path):
    """§9.1 (2026-10-01): one request path; compaction collects the stream."""
    from unittest.mock import patch
    from moka_code.harness.harness import Harness

    fake.on("POST", "/chat/completions", wire.openai_stream("mary", deltas=({"content": "Sum"},)))
    endpoint = make_endpoint(LLAMACPP)
    with patch("moka_code.harness.harness.get_active_endpoint", return_value=endpoint):
        harness = Harness(workspace_path=str(tmp_path))
    harness._add_message_to_history("user", "hello")
    harness._add_message_to_history("assistant", "hi there")
    asyncio.run(harness.compact_history())

    body = chat_body(fake)
    assert (body["stream"], body["stream_options"]) == (True, {"include_usage": True})
    assert "tools" not in body
    assert harness.history[-1]["content"].endswith("\n\nSummary")


# -- OpenRouter -----------------------------------------------------------------------------

def test_openrouter_always_talks_to_openrouter(fake):
    chat_route(fake)
    run_chat(make_endpoint({**OPENROUTER, "api_key": "K"}), SIMPLE)
    [request] = fake.requests
    assert request.url == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer K"


@pytest.mark.parametrize("table, provider", [
    ({"providers": ["deepseek"]}, {"order": ["deepseek"], "allow_fallbacks": False}),
    ({"providers": ["deepseek"], "models": ["m"], "providers_by_model": {"m": ["a", "b"]}},
     {"order": ["a", "b"], "allow_fallbacks": False}),       # a model's list replaces the default
    ({"providers": ["deepseek"], "models": ["m"], "providers_by_model": {"m": []}},
     None),                                                  # OpenRouter's own routing
    ({"providers": ["deepseek"], "models": ["m"], "providers_by_model": {"other": ["a"]}},
     {"order": ["deepseek"], "allow_fallbacks": False}),     # not listed: the default
    ({}, None),
])
def test_openrouter_provider_routing_is_a_strict_whitelist(fake, table, provider):
    """§9.7 (2026-10-01) changed where these are written (``models`` list,
    ``providers_by_model``), not what is sent."""
    chat_route(fake)
    run_chat(make_endpoint({**OPENROUTER, **table}), SIMPLE)
    assert chat_body(fake).get("provider") == provider


# -- llama.cpp ---------------------------------------------------------------------------------

def test_llamacpp_sends_the_selected_model(fake):
    """§9.6 / ISSUES P9 (2026-10-01): the selected ``m`` is sent as selected,
    with no ``GET /models`` lookup first (it used to be replaced by the first
    listed model, losing the choice on a router-mode server)."""
    fake.on("GET", "/models", wire.json_response(wire.LLAMACPP_MODELS))
    chat_route(fake)
    run_chat(make_endpoint(LLAMACPP), SIMPLE)
    assert fake.calls() == [("POST", "/v1/chat/completions")]
    assert chat_body(fake)["model"] == "m"


@pytest.mark.parametrize("table", [LLAMACPP, LLAMACPP, OPENROUTER])
def test_no_selection_sends_nothing(fake, table):
    """§9.6 (2026-10-01): no model selected is an error, never a guessed id
    (``"unknown"`` used to be sent)."""
    endpoint = make_endpoint(table)
    endpoint._selected_model = None
    with pytest.raises(RuntimeError, match="No model selected"):
        run_chat(endpoint, SIMPLE)
    assert fake.requests == []


# -- connection checks ----------------------------------------------------------------------------

def test_connection_checks_hit_these_paths(fake):
    fake.on("GET", "/models", wire.json_response(wire.LLAMACPP_MODELS))

    async def go():
        compat = make_endpoint(LLAMACPP)
        assert await compat.check_connection()
        assert (await compat.diagnose_connection()).ok
        await compat.aclose()
    asyncio.run(go())
    assert fake.calls() == [("GET", "/v1/models"), ("GET", "/v1/models")]


# -- what each provider learns about its models ---------------------------------------------------------

def test_llamacpp_learns_context_and_vision_from_props(fake):
    fake.on("GET", "/models", wire.json_response(wire.LLAMACPP_MODELS))
    fake.on("GET", "/props", wire.json_response(wire.LLAMACPP_PROPS))
    assert learn(make_endpoint(LLAMACPP)) == {
        "../models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf": (1024, False, [])}


def test_llamacpp_reports_only_what_the_server_states(fake):
    """llama.cpp's ``/models`` lists no context length or modalities: unknown
    (``None``), never invented."""
    fake.on("GET", "/models", wire.json_response(wire.LLAMACPP_MODELS))
    assert learn(make_endpoint(LLAMACPP)) == {
        "../models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf": (None, None, [])}


def test_openrouter_offers_only_whitelisted_models_with_their_stated_facts(fake):
    fake.on("GET", "/models", wire.json_response(wire.OPENROUTER_MODELS))
    table = {**OPENROUTER, "models": ["deepseek/deepseek-v4.1-flash"]}
    assert learn(make_endpoint(table)) == {
        "deepseek/deepseek-v4.1-flash": (1048576, True, ["low", "high", "max"])}


def test_openrouter_catalog_down_is_a_failed_listing(fake):
    """2a (2026-10-01): no catalog means no facts; the listing fails (the last
    good one is kept, flagged stale) instead of offering models without a
    context window."""
    fake.on("GET", "/models", wire.json_response({}, status=503))
    table = {**OPENROUTER, "models": ["deepseek/deepseek-v4.1-flash"]}
    with pytest.raises(RuntimeError):
        learn(make_endpoint(table))


def test_openrouter_effort_levels_are_never_guessed(fake):
    """§9.5 / ISSUES P7 (2026-10-01): this live entry lists ``reasoning`` in
    ``supported_parameters`` but states no ``supported_efforts``. It used to
    get ``none/low/medium/high`` guessed; now it gets none."""
    fake.on("GET", "/models", wire.json_response(wire.OPENROUTER_MODELS))
    table = {**OPENROUTER, "models": ["qwen/qwen3.8-omni-flash"]}
    assert learn(make_endpoint(table)) == {"qwen/qwen3.8-omni-flash": (1000000, True, [])}


def test_openrouter_bare_model_id_resolves_to_the_catalog_id(fake):
    """ISSUES P5 (to audit): a bare id in ``models`` is matched by suffix; its
    ``providers_by_model`` entry routes the canonical id."""
    fake.on("GET", "/models", wire.json_response(wire.OPENROUTER_MODELS))
    table = {**OPENROUTER, "models": ["deepseek-v4.1-flash"],
             "providers_by_model": {"deepseek-v4.1-flash": ["deepseek"]}}
    assert learn(make_endpoint(table)) == {
        "deepseek/deepseek-v4.1-flash": (1048576, True, ["low", "high", "max"])}
    chat_route(fake)
    endpoint = make_endpoint(table)
    endpoint._selected_model = "deepseek/deepseek-v4.1-flash"
    run_chat(endpoint, SIMPLE)
    assert chat_body(fake)["provider"] == {"order": ["deepseek"], "allow_fallbacks": False}


def test_llamacpp_reads_openrouter_shaped_facts(fake):
    """§9.5: a ``/models`` shared by the chat-completions providers may state OpenRouter's
    ``reasoning.supported_efforts`` and ``architecture.input_modalities`` (here
    a proxy in front of OpenRouter, serving its catalog)."""
    fake.on("GET", "/models", wire.json_response(wire.OPENROUTER_MODELS))
    assert learn(make_endpoint(LLAMACPP)) == {
        "deepseek/deepseek-v4.1-flash": (1048576, True, ["low", "high", "max"]),
        "qwen/qwen3.8-omni-flash": (1000000, True, [])}


# -- reasoning sent back (§9.13, PLAN.md 2b, 2026-10-01) --------------------------------

def _tool_loop_request(fake, tmp_path, monkeypatch, endpoint, depth, history):
    """The body of the request a conversation sends after ``history``, built by
    the harness for a role with this replay depth."""
    from unittest.mock import patch
    from moka_code.harness.harness import Harness
    from moka_code.harness.roles import Role

    with patch("moka_code.harness.harness.get_active_endpoint", return_value=endpoint):
        harness = Harness(workspace_path=str(tmp_path))
    harness.role = Role(name="t", replay_reasoning_depth=depth)
    harness.history = history
    chat_route(fake)
    run_chat(endpoint, harness._request_messages())
    return chat_body(fake)["messages"]


def _two_turns(first_origin):
    call = {"id": "call_1", "type": "function",
            "function": {"name": "read", "arguments": "{}"}}
    return [
        {"id": "u1", "role": "user", "content": "first"},
        {"id": "a1", "role": "assistant", "content": "old answer",
         "reasoning": "old thought", "origin": first_origin},
        {"id": "u2", "role": "user", "content": "second"},
        {"id": "a2", "role": "assistant", "content": None, "tool_calls": [call],
         "reasoning": "why I call", "origin": {"type": "llamacpp", "model": "m"}},
        {"id": "t1", "role": "tool", "content": "result", "tool_call_id": "call_1"},
    ]


@pytest.mark.parametrize("depth, expected", [
    (0, [None, None]),
    (1, [None, "why I call"]),
    (2, ["old thought", "why I call"]),
    (999, ["old thought", "why I call"]),
])
def test_llamacpp_sends_reasoning_content_by_depth(fake, tmp_path, monkeypatch, depth, expected):
    """llama.cpp accepts ``reasoning_content`` on assistant messages (tools/server/
    README.md and PR #18994, read 2026-10-01); the role's depth picks the turns."""
    endpoint = make_endpoint(LLAMACPP)
    messages = _tool_loop_request(fake, tmp_path, monkeypatch, endpoint, depth,
                                  _two_turns({"type": "llamacpp", "model": "m"}))
    assistant = [m for m in messages if m["role"] == "assistant"]
    assert [m.get("reasoning_content") for m in assistant] == expected
    assert all("reasoning" not in m and "origin" not in m for m in messages)


def test_llamacpp_sends_the_field_even_when_the_model_gave_no_reasoning(fake, tmp_path, monkeypatch):
    """⚠ Not documented; opencode sends the field on every assistant message for
    the same reason (the chat template renders it either way)."""
    history = _two_turns({"type": "llamacpp", "model": "m"})
    history[3].pop("reasoning")
    messages = _tool_loop_request(fake, tmp_path, monkeypatch, make_endpoint(LLAMACPP), 1, history)
    caller = next(m for m in messages if m.get("tool_calls"))
    assert caller["reasoning_content"] == ""


def test_openrouter_sends_native_blocks_only_to_their_model(fake, tmp_path, monkeypatch):
    """openrouter.ai/docs/use-cases/reasoning-tokens.md §Preserving Reasoning
    (read 2026-10-01): ``reasoning_details`` "unmodified", or the ``reasoning``
    string; the blocks must match what the model produced."""
    native = wire.OPENROUTER_REASONING_DELTA["reasoning_details"]
    history = _two_turns({"type": "openrouter", "model": "m"})
    for entry in history[1::2][:2]:
        entry["reasoning_native"] = native
    history[3]["origin"] = {"type": "openrouter", "model": "m"}
    history[1]["origin"] = {"type": "openrouter", "model": "other/model"}
    messages = _tool_loop_request(fake, tmp_path, monkeypatch, make_endpoint(OPENROUTER), 2, history)
    first, second = [m for m in messages if m["role"] == "assistant"]
    assert (first.get("reasoning"), "reasoning_details" in first) == ("old thought", False)
    assert second["reasoning_details"] == native and "reasoning" not in second
    assert all("reasoning_content" not in m for m in messages)


def test_openrouter_sends_nothing_when_the_model_gave_no_reasoning(fake, tmp_path, monkeypatch):
    """A decision, not a documented rule (the docs are silent): an empty field
    would be a value the model never produced. ⚠ DeepSeek behind OpenRouter
    requires the field (its docs); unverified without a key (ISSUES)."""
    history = _two_turns({"type": "openrouter", "model": "m"})
    history[3].pop("reasoning")
    messages = _tool_loop_request(fake, tmp_path, monkeypatch, make_endpoint(OPENROUTER), 1, history)
    assert all(not {"reasoning", "reasoning_details", "reasoning_content"} & m.keys() for m in messages)
