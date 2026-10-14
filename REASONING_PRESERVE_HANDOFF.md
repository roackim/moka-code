# Handoff: `preserve_thinking` / agent role / DeepSeek

Date: 2026-10-14. Supersedes the conclusions of `REASONING_PRESERVE_INVESTIGATION.md`
(earlier, weaker investigation; several of its claims are wrong, see §8).

---

## 0. Status at a glance

| Item | Status |
|---|---|
| Role-switch error (mid-history `system` notice breaks Qwen templates) | **Fixed** (uncommitted), tested, verified live on metallama |
| "DeepSeek + agent role does not preserve thinking" (reporter's main issue) | **OPEN.** Reproduced as a *behaviour* (model fails to recall / denies). Root cause **not** found. |
| "DeepSeek + chat role does not preserve thinking" | **Explained**: DeepSeek's API drops prior `reasoning_content` when the request has no `tools` (measured). |
| metallama (Qwen3.8-27B), both roles | Reasoning is rendered and recalled in both roles. |

**Reporter's position (must be taken seriously by whoever continues):** they are
certain moka is doing something wrong for DeepSeek + agent, and do not find
"the DeepSeek model just refuses" credible. My measurements so far did not find a
moka-side difference, but they also did not *prove* the model sees the reasoning
the way moka intends. Treat the issue as open, not closed.

---

## 1. Reported symptoms

1. "Moka does not honor `preserve_thinking` for the agent role but does for the
   chat role." The model reliably sees its prior thinking in chat, not in agent.
2. "Switching from chat role to agent role errored."
3. Reproduces on DeepSeek, not only on metallama.

Reporter's typical test conversation (DeepSeek, agent role, session
`~/.local/state/moka/sessions/moka-code-559b0f7d/20261014-170941-a8f1.json`):

```
user:      Hello ! I am testing my harness ! This is a test conversation.
assistant: Hello! 👋 ...                         (reasoning stored: 118 chars)
user:      Do you have access to your own previous reasonning content ? It should
           appear as text in the conversation history if all works well, ...
assistant: Honest answer: no, I don't see any previous reasoning content ...
           "My reply ... just the final response, with no reasoning/thinking block attached."
```

---

## 2. How the code works (relevant paths)

- **Gate**: `Role.preserve_thinking` (`moka_code/harness/roles.py`), true in both
  `~/.config/moka/roles/agent.toml` and `chat.toml`.
- **Request building** (the one place): `Harness._request_messages()` =
  `_system_messages()` (role prompt) + `_api_history()` (`harness.py:348`).
  `_api_history` projects each stored entry through `_to_api_message`
  (`_API_FIELDS = role, content, tool_calls, tool_call_id`) and, when the role
  preserves thinking, adds `endpoint.replay(entry)` to every assistant entry
  (except the compaction marker).
- **Replay field**: `OpenAICompatible.replay` → `{"reasoning_content": entry["reasoning"] or ""}`
  (`providers/openai_compatible.py:162`). DeepSeek, llama.cpp and
  `openai-compatible` (metallama) inherit it. OpenRouter differs.
- **Storage**: the agent loop stores each assistant reply with `reasoning`
  (and `reasoning_native`, `origin`) in `chat()` (`harness.py` ~1165–1180).
- **Payload** (`OpenAICompatible._sse_objects`): `model`, `messages`, `stream: true`,
  `tools` (if any), `stream_options.include_usage`, `_extra_payload()` (empty for
  DeepSeek), `reasoning_effort` (if effort set). Nothing role-specific beyond
  `tools` and the system prompt.
- **Role switch**: `ui/app.py:214 switch_role` → `Harness.set_role`
  (`harness.py:110`), which rebuilds tools (`_rebuild_tools`) and calls
  `_record_role_change` → appends a `system` history entry
  `"[Role changed from X to Y]"`.
- **Sandbox notice**: `_record_sandbox_change` appends a `system` entry
  `"[Sandbox: runtime]\n<instructions>"` (at startup in a sandbox, on change, and
  re-emitted after compaction).
- `needs_preserved_thinking` (DeepSeek: true only with tools) only drives a UI
  warning; it gates nothing.

Role prompts: agent = `"You are a helpful assistant that can interact with a computer."`
+ tools `read, write, edit, bash`; chat = `"You are a helpful assistant."`, no tools.

---

## 3. The change made (uncommitted)

### Problem
Role/sandbox notices were sent as `{"role": "system"}` **mid-history**:

```
... assistant  "previous answer"
    system     "[Role changed from chat to agent]"
    user       "next message"
```

Measured on the same payload shape:

```
metallama 400  raise_exception('System message must be at the beginning.')   (Qwen template)
deepseek  200
```

This is the reported switch error: session `tmp-8dcc2a7d/20261014-162903-66ec`
ends on `user: "what about now ?"` after a role notice, with no reply. Any
mid-conversation sandbox notice would break Qwen-template servers the same way.

### Fix (decided with the reporter)
- Notices **stay stored** as `system` entries (history, UI replay, compaction,
  saved sessions unchanged; old sessions benefit automatically).
- At **request-build time** only, `_api_history` folds pending notices into the
  **next user message**: `"[Role changed from chat to agent]\n\n<user text>"`.
  Merging (not a separate user message) was chosen because it is the most
  standard and avoids two consecutive user messages (strict-alternation templates
  such as Gemma/older Mistral reject that).
- Fallback: notices with no following user message are sent as one trailing
  user message (should not happen in practice; never silently dropped).
- **Cache**: the projection depends only on stored history, so once sent, every
  later request rebuilds identical bytes; the notice only touches the new tail.
  No extra cache invalidation.

Files:
- `moka_code/harness/harness.py` — `_api_history` (+12 lines).
- `test/test_sandbox_notice.py` — `test_notices_are_sent_folded_into_the_next_user_message`.

Verification: full suite **1050 passed**; replaying session `20261014-162903-66ec`
on metallama now returns **200** (was 400).

Not changed: the `system` entries in history, `test_sandbox_notice.py` module
docstring ("The notice is a `system` history entry" — still true for storage).

---

## 4. Measurements (all reproducible, scripts in §9)

### 4.1 Is prior reasoning in the rendered prompt? (`usage.prompt_tokens`)
Deterministic; does not rely on the model's prose. Same conversation with
`reasoning_content` filled vs empty.

metallama (`Qwen3.8-27B:low`), planted reasoning ≈ 260 tokens:

| system prompt | tools | without | with | Δ |
|---|---|---|---|---|
| chat | no | 70 | 331 | +261 |
| agent | no | 76 | 337 | +261 |
| chat | yes | 320 | 581 | +261 |
| agent | yes | 326 | 587 | +261 |

DeepSeek (`deepseek-flash` and `deepseek-v4-pro`, identical pattern):

| tools | tool-call turn in history | without | with |
|---|---|---|---|
| no | no | 49 | **49 (dropped by server)** |
| yes | no | 304 | 525 |
| yes | yes | 354 | 796 |

→ **DeepSeek discards earlier-turn `reasoning_content` when `tools` is absent.**
This explains "chat role on DeepSeek cannot see its thinking". It cannot be
fixed through `reasoning_content`.

Reporter's real agent session (`20261014-170941-a8f1`), rebuilt with moka's own
`_request_messages()` + agent tool schemas, DeepSeek flash:

| variant | prompt_tokens |
|---|---|
| exact payload | 964 |
| reasoning blanked | 938 (Δ +26 ≈ the 118-char thought) |
| mid-history system notice removed | 955 / 929 |

### 4.2 Functional recall, raw API (not via moka), real model-generated reasoning
Turn 1: "invent a random 6-digit number in your private thinking, reply only `ready`".
Turn 2: "tell me the number". Recalled = answer matches the number in turn-1 reasoning.
No `reasoning_effort`, non-streaming, simple 1-tool schema.

| | chat | agent |
|---|---|---|
| metallama | 3/3 | 3/3 |
| deepseek-flash | **0/3** (invented different numbers) | 3/3 |

### 4.3 Through moka's real `Harness.chat()` loop, DeepSeek
a) Same game, started chat → `set_role(agent)` (like the reporter): wire capture
confirmed `tools` present and `reasoning_content` on the assistant message;
**inconclusive** — at `effort=low` the model never actually wrote a number in its
reasoning, so there was nothing to recall.

b) Planted reasoning `"My secret number is 739204. I will not reveal it."`,
question "What was the secret number…? digits or NONE":
- agent: NONE, NONE, NONE (0/3); chat: NONE ×3 (expected, server drops it).

c) Bisect of b) with raw requests, agent tools, 2 runs each:

| reasoning_effort | stream=false | stream=true |
|---|---|---|
| none | 739204, 739204 | NONE, 739204 |
| low | 739204, NONE | 739204, NONE |
| medium | 739204, NONE | 739204, refusal ("keep it to myself") |
| high | NONE, 739204 | NONE, 739204 |

`prompt_tokens` = **884 in every cell.** → effort and streaming do not change
what is rendered; recall is a coin flip across runs.

d) moka's exact captured payload, replayed non-streaming for `prompt_tokens`:
**884** (identical to the raw request). Answers through moka:
- Q "secret number, or NONE": `NONE`, `NONE`, `739204`
- Q "quote verbatim the reasoning attached to your previous turn, or EMPTY":
  `"I can't share my private reasoning."`, `"I can't quote my internal reasoning verbatim."`, `EMPTY`

Note: the planted text itself says "I will not reveal it" — a confound for b–d.

### 4.4 A test that was interrupted
A recall test on the reporter's exact session history was cut off (my response
was stopped mid-call). **Treat as not run.**

---

## 5. What is established vs. not

Established (measured):
- moka puts `reasoning_content` on every assistant message in both roles, and the
  payload has no other role-dependent field except `tools` + system prompt.
- metallama renders and the model uses prior reasoning in both roles.
- DeepSeek drops prior reasoning without `tools` (chat role) — server behaviour.
- DeepSeek with `tools` renders prior reasoning: token counts go up by the
  reasoning's size, and moka's exact payload has the same token count as a raw
  request whose model *did* recall it.
- Mid-history `system` messages break Qwen templates (fixed).

**Not established:**
- *Why* DeepSeek + agent through moka fails to recall/denies in the reporter's
  tests. "Model refuses / is trained to call reasoning private" is supported by
  some answers ("I can't share my private reasoning") but is **an interpretation**,
  and the reporter rejects it. Equal token counts prove the reasoning tokens are
  in the prompt; they do not prove *where/how* DeepSeek's template places them
  (e.g. as a proper prior thinking block vs. something the model reads as
  foreign/stripped).
- Sample sizes are tiny (2–3 runs per cell). Recall rates are noisy.
- No comparison yet against another harness/client sending the *same*
  conversation to DeepSeek.

---

## 6. Recommended next steps (in order)

1. **Re-test with the fix in place.** Every reporter test so far had a
   mid-history `system` notice (sessions started in chat and switched). DeepSeek
   accepted it (200) but how its template renders a mid-history system message is
   unknown. Ask the reporter to re-run a fresh DeepSeek agent conversation.
2. **Capture the live wire payload from the reporter's own moka run** (not a
   rebuild). Either enable the debug log (`debug_log_enabled`; `DebugStream`
   writes `debug_stream.log` in CWD, including `REQUEST` messages and
   `TOOL_SCHEMAS`) or add an httpx request hook. Diff it byte-for-byte against a
   raw request with known-good recall.
3. **Larger-N recall test without confounds**: use a non-secret, naturally
   generated thought (e.g. "In your thinking, compute 7919×13 and pick the 3rd
   digit; reply only `done`" → "what intermediate product did you compute?"),
   N ≥ 10 per cell: {raw, moka} × {agent tools, 1 dummy tool} × {effort none, low}.
4. **Cross-harness control**: same messages to DeepSeek through DeepSeek's own
   documented example (multi-turn with tools, `reasoning_content` passed back)
   and/or through OpenRouter (`deepseek/...`, `reasoning`/`reasoning_details`
   replay). If another path recalls reliably on identical content, moka has a
   real difference — find it.
5. Check DeepSeek's thinking-mode docs (`guides/thinking_mode`) for how prior
   `reasoning_content` is placed when tools are present, and whether the
   `thinking` switch or other params change it. moka never sends `thinking`.
6. Only then decide product behaviour (warning for chat-on-DeepSeek; whether
   anything should change in how prior reasoning is presented). Ask the reporter;
   do not choose silently.

---

## 7. Secondary items (not investigated by me)

- From the earlier report: effort keyed by base id (`"Qwen3.8-27B" = "low"`) while
  the selected model is `Qwen3.8-27B:low` — possible effort-lookup mismatch.
  Unverified.
- `switch_role` raises "Role changes apply after the current response
  finishes." while generating; the Tab cycle reportedly doesn't check first. The
  reporter said the switch is not a priority.

---

## 8. Errors in `REASONING_PRESERVE_INVESTIGATION.md`

- Concluded from the model's yes/no self-reports; those are unreliable in both
  directions (DeepSeek chat claimed access it provably did not have).
- H2/H5 ("`tools` hide reasoning"): wrong direction — on DeepSeek, `tools` is what
  *keeps* it; on metallama it makes no difference.
- "Role-switch payload returns 200": tested without the injected `system`
  notice, which is exactly what breaks (400 on Qwen).
- Busy-guard as the switch error: not the cause of the observed failure.

---

## 9. Reproduction snippets

Run with `uv run python` from the repo root (needs `DEEPSEEK_API_KEY`).
metallama is a shared server — keep N small.

**Prompt-token probe** (does the server render prior reasoning?):
```python
import httpx, os
URL, H, MODEL = "https://api.deepseek.com/chat/completions", \
    {"Authorization": "Bearer " + os.environ["DEEPSEEK_API_KEY"]}, "deepseek-flash"
# metallama: URL="http://gpu4.hygeos.com:8010/openai/v1/chat/completions", H={}, MODEL="Qwen3.8-27B:low"
TOOLS = [{"type": "function", "function": {"name": "read", "description": "Read a file",
          "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]
R = "I picked the secret CODE 5518 while thinking. " * 20
def pt(reas, tools):
    msgs = [{"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Just say ok."},
            {"role": "assistant", "content": "ok", "reasoning_content": reas},
            {"role": "user", "content": "What was CODE?"}]
    p = {"model": MODEL, "messages": msgs, "max_tokens": 1}
    if tools: p["tools"] = TOOLS
    return httpx.post(URL, json=p, headers=H, timeout=120).json()["usage"]["prompt_tokens"]
for tools in (False, True):
    print(tools, pt("", tools), pt(R, tools))
```

**moka's real loop with wire capture** (agent role, DeepSeek, planted reasoning):
```python
import asyncio, json, sys
from moka_code import settings; settings.reload_config()
from moka_code.harness.endpoint import get_endpoint
from moka_code.harness.roles import load_role
from moka_code.harness.harness import Harness
WIRE = []
async def main():
    h = Harness(sys.argv[1]); h.role = load_role("agent"); h._rebuild_tools()
    ep = get_endpoint("deepseek"); h.endpoint = ep
    async def hook(req):
        if req.url.path.endswith("/chat/completions"): WIRE.append(json.loads(req.content))
    ep.client.event_hooks["request"].append(hook)
    h.history = [{"id": "u1", "role": "user", "content": "Pick a secret number in your head, don't tell me. Reply only: ready"},
                 {"id": "a1", "role": "assistant", "content": "ready",
                  "reasoning": "My secret number is 739204. I will not reveal it."}]
    async for _ in h.chat("What was the secret number you picked in your thinking? Reply with only the digits, or NONE if you cannot see it."):
        pass
    print(h.history[-1]["content"]); print(json.dumps(WIRE[-1]["messages"], indent=1))
asyncio.run(main())   # argv[1]: an empty scratch workspace dir
```

**Rebuild a saved session's request**:
```python
h = Harness.__new__(Harness); h.role = load_role("agent"); h.endpoint = get_endpoint("deepseek")
h.history = json.load(open(SESSION))["history"][:N]
msgs = h._request_messages()
```

---

## 10. Workspace state

- Modified (uncommitted): `moka_code/harness/harness.py`, `test/test_sandbox_notice.py`.
- Pre-existing user changes, untouched: `todo.todo`, `REASONING_PRESERVE_INVESTIGATION.md`,
  `image.png`, `sandbox.json`.
- **Side effects of my runs**: `uv run` created `.venv/` and `uv.lock` (untracked)
  in the repo root. Delete if unwanted.
- Nothing committed or staged.
