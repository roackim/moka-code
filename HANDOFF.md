# Handoff: 2026-10-01

A snapshot for resuming in a new session. It says where we stopped, not where
to go (`PLAN.md` is direction). **Delete it once you have resumed; do not keep
it updated.** `AGENTS.md` deliberately does not point here: start the new
session with "read HANDOFF.md first".

Verified against the repo on 2026-10-01: HEAD `0c6fc84`, **891 tests passing**
(`.pixi/envs/default/bin/python -m pytest test/ -q`, about 20 s).

---

## Read, in this order

1. `AGENTS.md`, then `.wiki/notes/principles.md` (design rules; delete before
   you design; no features unless asked).
2. `PLAN.md` (direction and every decision so far), `ISSUES.md` (open problems,
   one entry each), `PROCESS.md` (why we work the way we do; read §4 causes and
   §5.2 rules).
3. `.wiki/notes/providers.md`: the approved provider contract. Step 1.2 builds it.

## Where we are

| Step | State |
|---|---|
| 0.1 `efforts` key removed | done |
| 0.2 model selection (auto-pick on fresh state only; last used stays, red when unreachable; no `model` key) | done |
| 0.3 shared menus (sorted by default, `ordered` opt-out, every picker clickable, `/config` picker) | done |
| 0.4 `$` shell prefix removed | done |
| 0.5 `##` help lines in all templates + guard test | done |
| R1 `<think>` parser deleted (content verbatim) | done |
| Reasoning replay removed (nothing sent back until step 2) | done |
| **1.0 golden wire tests** (`test/test_wire_requests.py`, `test/wire_fake.py`) | done |
| **1.1 provider contract** (`.wiki/notes/providers.md`) | done, approved |
| **1.2 to 1.5 restructure, config shape, tests on the contract** | **not started** |
| Step 2 reasoning, Step 3 compaction | not started |

**Working tree.** Everything through `0c6fc84` is committed. Not committed:
`PROCESS.md` (the I17, S10, cause 13, rule 6 and "real fixtures" additions);
`plans/stream_interpolation_handoff.md` shows deleted (the user did that).
Untracked and the user's, leave alone unless asked: `plans/server_contract.md`
(partly stale: it still says earlier turns follow `preserve_reasoning`),
`.agents/`, `Dockerfile`, `check_once_per_sec.sh`.

## Next: Step 1.2. Propose first, then build

Propose a breakdown into reviewable commits and wait for an OK. A starting
suggestion, **not agreed**:

1. `ModelInfo` gets explicit `images` and `efforts`; providers fill them in
   `list_models()`; the probe caches go (contract §10.1); readers
   (`ui/commands/models.py`, `endpoint.accepts_images`) use the fields.
2. `Chunk` and `stream()` replace today's SDK-shaped chunks
   (`choices[0].delta...`) that `harness._stream_llm_response` reads; compaction
   collects from the stream and the non-streaming path goes. The provider
   assembles `reasoning_native` (today the harness calls the OpenRouter-specific
   `merge_reasoning_details`).
3. Base class, `OpenAICompatible` / `LlamaCpp` / `OpenRouter` / `Ollama`,
   registry; delete the `type ==` branches (about 23 sites in `endpoint.py`,
   `endpoint_discovery.py`, `endpoint_openai.py`, `settings.py`,
   `unserved_model`); Ollama through the shared client; llama.cpp stops
   replacing the selection.
4. The new `servers.toml` shape and the template from the registry.
5. Existing tests rewritten against the contract (27 `Endpoint(` sites in 12
   files).

**Proof of "no behaviour change":** `test/test_wire_requests.py` passes
unchanged, except the expectations marked §9.x, which change on purpose in the
same commit. Only its three helpers (`make_endpoint`, `run_chat`, `learn`)
touch the provider API.

## The contract in six lines (full version: `providers.md`)

- `Endpoint` is an instance of its provider's class; registry `{type: class}`.
  Types: `openai-compatible`, `llamacpp`, `openrouter`, `ollama`.
- Class attributes: `fixed_url`, `default_url`, `extra_keys`, `template`.
- Methods: `list_models()`, `stream(messages, tools, model, effort)` yielding
  neutral `Chunk`s, `effort_payload()`, `replay()` (returns `{}` in step 1),
  `ping()`.
- The harness never sees a vendor field name and never inspects `type`.
- Effort levels come only from what a server states; nothing is guessed.
- New `servers.toml`: `type` always written; OpenRouter `models` list plus
  `providers` and `providers_by_model`; old shapes reported, never aliased.

## Decisions made this session (all recorded in `PLAN.md` / `providers.md`)

- No reasoning is sent back until step 2; no user setting for replay depth.
- `content` is never parsed. A server that does not split reasoning shows raw
  tags (llama.cpp splits by default, verified).
- A last used model is never replaced; it stays, red.
- Effort: exact levels only (OpenRouter `supported_efforts`, Ollama
  `thinking.values`); a server that states none gets no `/effort` menu.
- `llamacpp` can serve several models (router mode): always send the selected id.
- `openai` is renamed `openai-compatible`; explicit `type` everywhere.

## How we work (the vibe that worked)

- **One small step at a time.** Audit, then a short proposal (what changes,
  what gets deleted, acceptance checks, the open choices), wait for the OK,
  implement, verify, report, stop. The user reviews and commits; never commit
  or `git add` (a `git rm` stages: use `rm`). Suggest a commit message at the
  end of each step.
- **Verify the scenario, not just the suite.** Run it against a copy of the
  user's real config (`MOKA_CONFIG_DIR`), or on the wire with the fake server.
  Mutation-check new tests by breaking the code they cover.
- **Decisions live in tests and docs.** A test cites the decision it protects
  (date in the docstring). Each step ends by updating `PLAN.md` (mark done),
  `ISSUES.md` (delete fixed entries, add new leads) and the wiki pages it touched.
- **Delete, don't relocate.** A rejected thing is removed, not made
  configurable. No implicit fallbacks, no masking, no invented values.
- **Never assume an external API.** Check the docs or a live response first,
  copy fixtures from the real thing, write the source and date next to it, and
  label anything unchecked. This session got it wrong twice (PROCESS I17, S10)
  and the user caught it both times.
- **When the user is sharp ("why do we guess?"), it is a signal.** Stop
  defending, look at the data, say plainly what was wrong, then propose.
- Terse answers; short tables over paragraphs; state what you did not verify.

## Do not

- Query the user's local servers (their `openai-compatible` one is on
  `localhost:8010`) without asking; it may be busy. Public endpoints are fine:
  OpenRouter's `/models` needs no key.
- Hand-edit the user's `~/.config/moka/*` files. Their `servers.toml` still has
  the old shape and a stray `api_key_env = "NONE"` (a permanent false
  `$NONE is not set` error while that server is active); fix it only on request.
- Put direction in the wiki (it documents state). `providers.md` carries an
  "approved, not implemented" banner until step 1 lands.

## Open items

- **Risk R8.** With no replay, some models may degrade inside a tool loop
  (DeepSeek thinking mode; signed blocks via OpenRouter). Unverified. If tool
  calls start failing on DeepSeek, suspect this first.
- **Leads, unverified** (`ISSUES.md`): P8 (Ollama `/api/show` sent `name`, docs
  say `model`), P10 (Ollama tool-loop messages differ from its API reference),
  and whether llama.cpp `/props` answers per model in router mode.
- **Audits not started:** P4 (invented 32768 context window), P5 (bare model id
  matched by suffix), U4 to U7, H1 to H5 (dead code, fallbacks, tests written
  from the implementation).
- **Question on the plan:** in compaction, do `edit` / `write` one-liners keep
  the edited line ranges or only the replacement count (`PLAN.md`, Open questions)?
- **Unfinished reading:** the OpenAI docs gave no concrete final-usage chunk
  (the fixture follows their description); `thinking.values: [false]` in
  Ollama's docs came through a summary garbled and was not relied on.
