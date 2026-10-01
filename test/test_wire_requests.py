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
from moka_code.harness.endpoint import (
    Endpoint, efforts_from_metadata, image_input_from_metadata, make_endpoint as build,
)
from moka_code.harness.usage import usage_from_response


# -- the three adapters to the provider API -------------------------------------

def make_endpoint(table: dict, effort=None) -> Endpoint:
    """A server built from a ``servers.toml`` table, with model ``m`` selected."""
    endpoint = build("srv", table)
    endpoint._selected_model = "m"
    endpoint.effort = effort
    return endpoint


def run_chat(endpoint, messages, tools=None) -> tuple[str, list]:
    """One streamed chat request; ``(answer text, usage of each chunk)``."""
    async def go():
        text, usages = "", []
        async for chunk in endpoint.create_completion(messages, tools=tools, stream=True):
            for choice in chunk.choices:
                text += choice.delta.content or ""
            usage = usage_from_response(chunk)
            if usage is not None:
                usages.append((usage.prompt_tokens, usage.completion_tokens))
        await endpoint.aclose()
        return text, usages
    return asyncio.run(go())


def learn(endpoint) -> dict:
    """``{model id: (context window, accepts images, effort levels)}`` as the
    app learns them from this server."""
    async def go():
        facts = {}
        for model in await endpoint.discover_models():
            try:
                context = model.context_window or await endpoint.query_context_window(model.id)
            except RuntimeError:
                context = None
            images = image_input_from_metadata(model.metadata)
            if images is None:
                images = await endpoint.query_image_input(model.id)
            facts[model.id] = (context, images, efforts_from_metadata(model.metadata))
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

COMPAT = {"type": "openai", "base_url": "http://h/v1"}
LLAMACPP = {"type": "llamacpp", "base_url": "http://h/v1"}
OPENROUTER = {"type": "openrouter"}
OLLAMA = {"type": "ollama", "base_url": "http://h/v1"}


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


# -- OpenAI-compatible chat -------------------------------------------------------------

def test_openai_compatible_chat_request(fake):
    chat_route(fake)
    text, usages = run_chat(make_endpoint({**COMPAT, "api_key": "K"}), HISTORY, TOOLS)

    [request] = fake.requests
    assert (request.method, request.url) == ("POST", "http://h/v1/chat/completions")
    assert request.headers["authorization"] == "Bearer K"
    assert request.body == {
        "model": "m", "messages": HISTORY, "tools": TOOLS,
        "stream": True, "stream_options": {"include_usage": True}}
    assert (text, usages) == ("Hi", [(9, 2)])


def test_no_api_key_sends_no_authorization_header(fake):
    chat_route(fake)
    run_chat(make_endpoint(COMPAT), SIMPLE)
    assert "authorization" not in fake.requests[0].headers
    assert chat_body(fake) == {"model": "m", "messages": SIMPLE, "stream": True,
                               "stream_options": {"include_usage": True}}


@pytest.mark.parametrize("table, effort, fragment", [
    (COMPAT, "high", {"reasoning_effort": "high"}),
    (LLAMACPP, "low", {"reasoning_effort": "low"}),
    (OPENROUTER, "max", {"reasoning": {"effort": "max"}}),
    (COMPAT, None, {}),
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
    text, _ = run_chat(make_endpoint({**COMPAT, "retry_delay": 0}), SIMPLE)
    first, second = fake.sent("POST", "/chat/completions")
    assert first.body == second.body and text == "Hi"


# -- OpenRouter -----------------------------------------------------------------------------

def test_openrouter_always_talks_to_openrouter(fake):
    chat_route(fake)
    run_chat(make_endpoint({**OPENROUTER, "api_key": "K"}), SIMPLE)
    [request] = fake.requests
    assert request.url == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer K"


@pytest.mark.parametrize("table, provider", [
    ({"providers": ["deepseek"]}, {"order": ["deepseek"], "allow_fallbacks": False}),
    ({"providers": ["deepseek"], "models": {"m": {"providers": ["a", "b"]}}},
     {"order": ["a", "b"], "allow_fallbacks": False}),       # a model's list replaces the default
    ({"providers": ["deepseek"], "models": {"m": {"providers": []}}}, None),   # OpenRouter's own routing
    ({"providers": ["deepseek"], "models": {"m": {}}}, {"order": ["deepseek"], "allow_fallbacks": False}),
    ({}, None),
])
def test_openrouter_provider_routing_is_a_strict_whitelist(fake, table, provider):
    """§9.7 changes where these are written (``providers_by_model``), not what is sent."""
    chat_route(fake)
    run_chat(make_endpoint({**OPENROUTER, **table}), SIMPLE)
    assert chat_body(fake).get("provider") == provider


# -- llama.cpp ---------------------------------------------------------------------------------

def test_llamacpp_today_asks_the_server_which_model_it_serves(fake):
    """§9.6 / ISSUES P9: the selected ``m`` is replaced by the first listed
    model, after a ``GET /models`` before the chat. After the change: no
    lookup, model ``m`` is sent."""
    fake.on("GET", "/models", wire.json_response(wire.LLAMACPP_MODELS))
    chat_route(fake)
    run_chat(make_endpoint(LLAMACPP), SIMPLE)
    assert fake.calls() == [("GET", "/v1/models"), ("POST", "/v1/chat/completions")]
    assert chat_body(fake)["model"] == "../models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"


# -- Ollama -------------------------------------------------------------------------------------

def test_ollama_chat_request(fake):
    """The body differs from Ollama's API reference in two places, pinned as
    they are today (ISSUES P10): tool-call ``arguments`` is a JSON string (the
    reference shows an object) and the tool result carries ``tool_call_id``
    (the reference shows ``tool_name``). No ``Authorization`` header is sent
    even with an ``api_key`` (§9.3)."""
    fake.on("POST", "/api/chat", wire.ollama_stream())
    text, usages = run_chat(make_endpoint({**OLLAMA, "api_key": "K"}, effort="none"), HISTORY, TOOLS)

    [request] = fake.requests
    assert (request.method, request.url) == ("POST", "http://h/api/chat")
    assert "authorization" not in request.headers
    assert request.body == {
        "model": "m", "stream": True, "tools": TOOLS, "think": False,
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "read", "arguments": "{\"path\": \"a\"}"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "file text"},
            {"role": "user", "content": "look", "images": ["QUJD"]},
        ]}
    assert (text, usages) == ("Hi", [(26, 282)])


@pytest.mark.parametrize("effort, sent", [("high", "high"), ("none", False), (None, "absent")])
def test_ollama_effort_goes_in_think(fake, effort, sent):
    fake.on("POST", "/api/chat", wire.ollama_stream())
    run_chat(make_endpoint(OLLAMA, effort), SIMPLE)
    body = fake.requests[0].body
    assert body.get("think", "absent") == sent


# -- connection checks ----------------------------------------------------------------------------

def test_connection_checks_hit_these_paths(fake):
    """§9.4: Ollama's diagnosis moves from ``/v1/models`` to ``/api/tags``."""
    fake.on("GET", "/models", wire.json_response(wire.LLAMACPP_MODELS))
    fake.on("GET", "/api/tags", wire.json_response(wire.OLLAMA_TAGS))

    async def go():
        compat = make_endpoint(COMPAT)
        assert (await compat.diagnose_connection()).ok
        ollama = make_endpoint(OLLAMA)
        assert await ollama.check_connection()
        assert (await ollama.diagnose_connection()).ok
        await compat.aclose()
        await ollama.aclose()
    asyncio.run(go())
    assert fake.calls() == [("GET", "/v1/models"), ("GET", "/api/tags"), ("GET", "/v1/models")]


# -- what each provider learns about its models ---------------------------------------------------------

def test_llamacpp_learns_context_and_vision_from_props(fake):
    fake.on("GET", "/models", wire.json_response(wire.LLAMACPP_MODELS))
    fake.on("GET", "/props", wire.json_response(wire.LLAMACPP_PROPS))
    assert learn(make_endpoint(LLAMACPP)) == {
        "../models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf": (1024, False, [])}


def test_openai_compatible_reports_only_what_the_server_states(fake):
    """llama.cpp's ``/models`` lists no context length or modalities: unknown
    (``None``), never invented."""
    fake.on("GET", "/models", wire.json_response(wire.LLAMACPP_MODELS))
    assert learn(make_endpoint(COMPAT)) == {
        "../models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf": (None, None, [])}


def test_openrouter_offers_only_whitelisted_models_with_their_stated_facts(fake):
    fake.on("GET", "/models", wire.json_response(wire.OPENROUTER_MODELS))
    table = {**OPENROUTER, "models": {"deepseek/deepseek-v4.1-flash": {}}}
    assert learn(make_endpoint(table)) == {
        "deepseek/deepseek-v4.1-flash": (1048576, True, ["low", "high", "max"])}


def test_ollama_learns_context_and_vision_from_show(fake):
    """§9.x/ISSUES P8: ``/api/show`` is asked with ``name`` today; Ollama's
    reference lists ``model``."""
    fake.on("GET", "/api/tags", wire.json_response(wire.OLLAMA_TAGS))
    fake.on("POST", "/api/show", wire.json_response(wire.OLLAMA_SHOW))
    assert learn(make_endpoint(OLLAMA)) == {"gemma4": (8192, True, [])}
    assert {r.body["name"] for r in fake.sent("POST", "/api/show")} == {"gemma4"}
