"""Tests for .local resolution diagnostics and proxy-avoidance in the client."""

import httpx
import pytest

from moka_code.harness.endpoint import (
    ConnectionDiagnosis,
    _is_local_target,
    _new_http_client,
)

from moka_code.harness.providers import LlamaCpp


def make_config(base_url: str):
    return LlamaCpp(
        name="t", base_url=base_url, api_key="EMPTY",
        model=None, max_context=None, timeout=1.0,
    )


# --- Proxy-avoidance for local targets ---


def test_local_targets_are_detected():
    assert _is_local_target("http://localhost:8080")
    assert _is_local_target("http://127.0.0.1:8080")
    assert _is_local_target("http://llm-mini-server.local:8080")
    assert _is_local_target("http://192.168.1.21:8080")
    assert _is_local_target("http://10.0.0.5:8080")
    assert _is_local_target("http://172.16.0.5:8080")
    assert not _is_local_target("https://openrouter.ai/api/v1")


def test_new_client_disables_proxy_for_local(monkeypatch):
    captured = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    _new_http_client(make_config("http://llm-mini-server.local:8080"))
    assert captured["trust_env"] is False
    assert captured["base_url"] == "http://llm-mini-server.local:8080"


def test_new_client_uses_proxy_env_for_remote(monkeypatch):
    captured = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    # Remote host: trust_env stays True so http(s)_proxy env vars are honored.
    _new_http_client(make_config("https://openrouter.ai/api/v1"))
    assert captured["trust_env"] is True

# --- Diagnosis message ---


def test_diagnosis_online_message():
    d = ConnectionDiagnosis(ok=True, url="http://192.168.1.21:8080/v1")
    assert "ONLINE" in d.message()


def test_diagnosis_failure_mentions_proxy_env(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.local:3128")
    d = ConnectionDiagnosis(
        ok=False,
        url="http://192.168.1.21:8080/v1",
        error=ConnectionError("boom"),
        original_url="http://llm-mini-server.local:8080/v1",
        hostname="llm-mini-server.local",
    )
    msg = d.message()
    assert "UNREACHABLE" in msg
    assert "boom" in msg
    assert "Proxy env" in msg
    assert "llm-mini-server.local" in msg


def test_diagnosis_failure_no_proxy_no_hint():
    d = ConnectionDiagnosis(ok=False, url="http://x", error=OSError("refused"))
    assert "Proxy env" not in d.message()