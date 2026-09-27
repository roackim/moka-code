from types import SimpleNamespace

from moka_chat.harness.usage import normalize_usage, usage_from_response


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