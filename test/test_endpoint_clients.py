"""Endpoint connection pools are closed when no longer used."""

import asyncio

from moka_code import settings
from moka_code.harness import endpoint as endpoint_mod
from moka_code.harness.endpoint import Endpoint
from moka_code.harness.providers import OpenAICompatible


def _endpoint():
    return OpenAICompatible(name="s", base_url="http://s/v1", model="m")


def test_retired_idle_endpoint_closes_its_client():
    async def scenario():
        endpoint = _endpoint()
        client = endpoint.client
        endpoint.retire()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return client.is_closed
    assert asyncio.run(scenario())


def test_retired_endpoint_waits_for_the_reply_in_flight(monkeypatch):
    release = asyncio.Event

    async def scenario():
        gate = release()

        async def fake_stream(self, messages, tools):
            await gate.wait()
            yield "chunk"
        monkeypatch.setattr(OpenAICompatible, "_stream", fake_stream)

        endpoint = _endpoint()
        client = endpoint.client
        chunks = []

        async def consume():
            async for chunk in endpoint.stream([]):
                chunks.append(chunk)
        task = asyncio.create_task(consume())
        await asyncio.sleep(0)
        endpoint.retire()                   # e.g. /model while streaming
        await asyncio.sleep(0)
        open_while_streaming = not client.is_closed
        gate.set()
        await task
        return open_while_streaming, client.is_closed, chunks
    assert asyncio.run(scenario()) == (True, True, ["chunk"])


def test_refresh_catalog_closes_its_throwaway_endpoints(monkeypatch):
    monkeypatch.setattr(settings.config, "servers", {"s": {"type": "openai", "base_url": "http://h/v1"}})
    monkeypatch.setattr(settings.config, "models_by_server", {})
    built = []
    real_get = endpoint_mod.get_endpoint

    def tracking_get(name):
        endpoint = real_get(name)
        built.append(endpoint)
        return endpoint

    async def discover(self):
        return []
    monkeypatch.setattr(endpoint_mod, "get_endpoint", tracking_get)
    monkeypatch.setattr(Endpoint, "discover_models", discover)
    asyncio.run(endpoint_mod.refresh_catalog(["s"]))
    assert built and all(e.client.is_closed for e in built)


def test_probed_facts_expire_when_the_server_is_refreshed(monkeypatch):
    monkeypatch.setattr(settings.config, "models_by_server", {})
    endpoint = _endpoint()
    endpoint._probed("m")["image_input"] = False
    assert endpoint.accepts_images() is False
    monkeypatch.setitem(endpoint_mod._refresh_generation, "s",
                        endpoint_mod._refresh_generation.get("s", 0) + 1)
    assert endpoint.accepts_images() is None        # re-probed after a refresh
