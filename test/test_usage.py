"""Usage counters as each provider reports them, read into ``TokenUsage``."""

from moka_code.harness.providers import LlamaCpp, OpenAICompatible
from moka_code.harness.providers.openai_compatible import parse_usage


def test_normalizes_openai_usage():
    usage = parse_usage({
        "prompt_tokens": 12400,
        "completion_tokens": 321,
        "total_tokens": 12721,
    })

    assert usage.prompt_tokens == 12400
    assert usage.completion_tokens == 321
    assert usage.total_tokens == 12721


def test_usage_from_a_stream_object_without_choices():
    endpoint = LlamaCpp(name="l")
    chunk = endpoint._chunk({"choices": [], "usage": {
        "prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}})

    assert chunk.usage.total_tokens == 12
    assert chunk.text == "" and not chunk.tool_calls


def test_cached_tokens_found_next_to_completion_details():
    """OpenAI-style usage carries both detail blocks; the cache is in the prompt one."""
    usage = parse_usage({
        "prompt_tokens": 1000,
        "completion_tokens": 50,
        "completion_tokens_details": {"reasoning_tokens": 10},
        "prompt_tokens_details": {"cached_tokens": 900},
    })

    assert usage.reasoning_tokens == 10
    assert usage.cached_prompt_tokens == 900


def test_cached_tokens_from_anthropic_style_usage():
    usage = parse_usage({"prompt_tokens": 1000, "cache_read_input_tokens": 800})

    assert usage.cached_prompt_tokens == 800


def test_cached_tokens_from_llamacpp_timings():
    chunk = LlamaCpp(name="l")._chunk({
        "choices": [],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 5, "total_tokens": 1005},
        "timings": {"cache_n": 950, "prompt_n": 50},
    })

    assert chunk.usage.cached_prompt_tokens == 950


def test_only_llamacpp_reads_timings():
    """§9 (2026-10-01): ``timings.cache_n`` is llama.cpp's (``providers.md`` §4)."""
    data = {"choices": [], "usage": {"prompt_tokens": 1000}, "timings": {"cache_n": 950}}

    assert OpenAICompatible(name="o", base_url="http://o/v1")._chunk(data).usage.cached_prompt_tokens is None
    assert LlamaCpp(name="l")._chunk(data).usage.cached_prompt_tokens == 950
