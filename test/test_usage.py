from types import SimpleNamespace

from moka_code.harness.usage import normalize_usage, usage_from_response


def test_normalizes_openai_usage():
    usage = normalize_usage({
        "prompt_tokens": 12400,
        "completion_tokens": 321,
        "total_tokens": 12721,
    })

    assert usage.prompt_tokens == 12400
    assert usage.completion_tokens == 321
    assert usage.total_tokens == 12721


def test_normalizes_ollama_native_usage_counters():
    usage = normalize_usage({
        "prompt_eval_count": 12400,
        "eval_count": 321,
    })

    assert usage.prompt_tokens == 12400
    assert usage.completion_tokens == 321
    assert usage.total_tokens is None


def test_extracts_usage_from_empty_stream_chunk():
    response = SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2, total_tokens=12),
    )

    usage = usage_from_response(response)

    assert usage.total_tokens == 12


def test_extracts_usage_from_ollama_native_done_chunk():
    response = SimpleNamespace(
        choices=[],
        usage={"prompt_eval_count": 12400, "eval_count": 321},
    )

    usage = usage_from_response(response)

    assert usage.prompt_tokens == 12400
    assert usage.completion_tokens == 321
    assert usage.is_empty is False

def test_cached_tokens_found_next_to_completion_details():
    """OpenAI-style usage carries both detail blocks; the cache is in the prompt one."""
    usage = normalize_usage({
        "prompt_tokens": 1000,
        "completion_tokens": 50,
        "completion_tokens_details": {"reasoning_tokens": 10},
        "prompt_tokens_details": {"cached_tokens": 900},
    })

    assert usage.reasoning_tokens == 10
    assert usage.cached_prompt_tokens == 900


def test_cached_tokens_from_anthropic_style_usage():
    usage = normalize_usage({"prompt_tokens": 1000, "cache_read_input_tokens": 800})

    assert usage.cached_prompt_tokens == 800


def test_cached_tokens_from_llamacpp_timings():
    response = SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=5, total_tokens=1005),
        timings={"cache_n": 950, "prompt_n": 50},
    )

    assert usage_from_response(response).cached_prompt_tokens == 950
