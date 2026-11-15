"""A steered message joins the running turn between two requests."""

import asyncio
import json
from unittest.mock import patch

from moka_code.harness import events
from moka_code.harness.endpoint import Chunk, ToolCallPiece
from moka_code.harness.harness import Harness
from moka_code.harness.providers import LlamaCpp
from moka_code.harness.roles import Role


def _harness(tmp_path):
    with patch("moka_code.harness.harness.get_active_endpoint",
               return_value=LlamaCpp(name="t")):
        harness = Harness(workspace_path=str(tmp_path))
    harness.set_role(Role(name="t", tools={"bash": "yes"}))
    requests = []

    async def completion(messages, tools=None):
        requests.append([m.get("content") for m in messages if m.get("role") == "user"])
        if len(requests) == 1:
            harness.steer("also this", key="k")      # sent while the turn runs
            yield Chunk(tool_calls=[ToolCallPiece(
                index=0, id="c1", name="bash", arguments=json.dumps({"command": "true"}))])
        else:
            yield Chunk(text="done")

    harness.endpoint.stream = completion
    return harness, requests


def test_a_steer_joins_before_the_next_request(tmp_path):
    harness, requests = _harness(tmp_path)

    async def run():
        return [e async for e in harness.chat("go")]

    out = asyncio.run(run())
    steered = [e for e in out if isinstance(e, events.Steered)]
    assert [e.key for e in steered] == ["k"]
    # (the first user message carries moka's role notice)
    assert [[u.rsplit("\n", 1)[-1] for u in r] for r in requests] == [["go"], ["go", "also this"]]
    turn = [m for m in harness.history if m["role"] != "system"]
    assert [m["role"] for m in turn] == ["user", "assistant", "tool", "user", "assistant"]
    assert turn[3]["id"] == steered[0].message_id
    assert harness._steers == []


def test_a_steer_the_turn_did_not_take_is_dropped(tmp_path):
    harness, _ = _harness(tmp_path)

    async def answer(messages, tools=None):
        harness.steer("late", key="k")               # the final answer is already out
        yield Chunk(text="done")

    harness.endpoint.stream = answer
    out = asyncio.run(_collect(harness))
    assert not any(isinstance(e, events.Steered) for e in out)
    assert harness._steers == [] and [m["content"] for m in harness.history
                                      if m["role"] == "user"] == ["go"]


async def _collect(harness):
    return [e async for e in harness.chat("go")]
