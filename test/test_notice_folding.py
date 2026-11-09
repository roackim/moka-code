"""Role/sandbox notices are ``system`` history entries but never go out as
mid-history system messages (templates reject them, and a changed system prompt
would invalidate the provider's prompt cache): they are folded into the next
user message."""

from moka_code.harness.harness import Harness


def _bare():
    harness = Harness.__new__(Harness)
    harness.history = []
    return harness


def test_notices_are_sent_folded_into_the_next_user_message():
    harness = _bare()

    harness._record_sandbox_change("bubblewrap")
    harness._add_message_to_history("user", "hello")
    harness._add_message_to_history("assistant", "hi")
    harness.history.append({"id": "r", "role": "system", "content": "[Role changed from chat to agent]"})
    harness._add_message_to_history("user", "again")

    api = harness._api_history()

    assert [m["role"] for m in api] == ["user", "assistant", "user"]
    assert api[0]["content"] == "[Sandbox: bubblewrap]\n\nhello"
    assert api[2]["content"] == "[Role changed from chat to agent]\n\nagain"
    assert all(m["role"] != "system" for m in api)
