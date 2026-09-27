"""The status bar shows what this conversation cost, when the provider says."""

from types import SimpleNamespace

from pico_chat.harness.harness import Harness
from pico_chat.harness.usage import normalize_usage
from pico_chat.ui.status_presenter import _format_cost


def test_usage_reads_the_reported_cost():
    usage = normalize_usage({"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.000140035})
    assert usage.cost == 0.000140035
    assert normalize_usage({"prompt_tokens": 10}).cost is None


def test_costs_add_up_per_conversation_and_reset():
    harness = Harness.__new__(Harness)
    harness.history = []
    harness.debug_stream = SimpleNamespace(log=lambda *a, **k: None)
    harness.conversation_cost = None
    harness._add_cost(normalize_usage({"prompt_tokens": 1, "cost": 0.01}))
    harness._add_cost(normalize_usage({"prompt_tokens": 1}))       # local server: no cost
    harness._add_cost(normalize_usage({"prompt_tokens": 1, "cost": 0.0025}))
    assert abs(harness.conversation_cost - 0.0125) < 1e-12
    harness.clear_history()
    assert harness.conversation_cost is None
    harness._add_cost(normalize_usage({"prompt_tokens": 1, "cost": 0.5}))
    harness.load_history([])
    assert harness.conversation_cost is None


def test_cost_field_is_hidden_until_reported():
    assert _format_cost(None) == ""
    assert _format_cost(0.000140035) == "$0.00"
    assert _format_cost(0.0125) == "$0.01"
    assert _format_cost(0.126) == "$0.13"
    assert _format_cost(1.5) == "$1.50"
