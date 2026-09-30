# Process overhaul: problems, causes, solutions

Working document (started 2026-09-30), to iterate on. It records what went
wrong across the AI-assisted sessions on moka, why, and what to change in how
we work. `PLAN.md` holds the product direction; this file is about process.

Evidence is from the 2026-09-30 session and a read of the repository that day.
"Earlier sessions" means the sessions that produced the code as it was at the
start of 2026-09-30 (HEAD `2eec91d`).

---

## 1. The core problem

The user can only check what they can see: the UI, the config files, the
behaviour they try. Most of the damage is invisible from there: implicit
fallbacks, rewritten data, dead code, tests that lock in wrong behaviour,
stale docs. It surfaces later, as bugs that look unrelated, and each fix
session then pays for the previous sessions' shortcuts.

The 2026-09-30 session was mostly spent paying that debt, and it added some
of its own using the same patterns (section 2.2). So the problem is not one
bad session. It is a process that produces debt by default and has no step
that catches it.

---

## 2. Problems faced

### 2.1 Inherited from earlier sessions (found on 2026-09-30)

| # | Problem | Where | How it surfaced | Status |
|---|---|---|---|---|
| I1 | Phantom `llamacpp_default` server: the active server defaulted to a name that was never configured; whenever the selected server was missing, a llama.cpp endpoint on `localhost:8080` was built silently | `settings.py` (`active_server` default), `endpoint.py` (`default_endpoint`, `get_active_endpoint`) | User saw `llamacpp_default:?` and "unreachable localhost" after configuring OpenRouter | Fixed |
| I2 | `base_url` defaulted to llama.cpp's `localhost:8080` for every server type (Ollama, OpenAI-compatible included) | `endpoint.py` `from_dict` | Audit of I1 | Fixed (required for ollama/openai) |
| I3 | `preserve_reasoning` as a per-server / per-model `servers.toml` key, never asked for (the user's todo only asked *"make preserve_thinking config part of roles ?"*) | `settings.py`, `endpoint.py`, `harness.py` | User read the servers template | Key removed; reasoning redesign in `PLAN.md` |
| I4 | `<think>` tag parsing on every provider's answer: any answer containing `<think>` loses the rest of its text into hidden "reasoning"; an unclosed tag swallows the whole answer | `harness.py` stream loop, `thinking_parser.py` | Reasoning audit | Open (`PLAN.md` step 2) |
| I5 | Reasoning replayed to whatever model is active, including signed/encrypted OpenRouter blocks produced by another model | `harness.py` `_api_history`, `endpoint_openai.py` | Reasoning audit | Open (`PLAN.md` step 2) |
| I6 | Inline-tag reasoning stored differently live (moved out of `content`) and on import (kept in `content`), and sent back in a field the model never used | `harness.py`, `ui/commands/conversation.py`, `endpoint_openai.py` | Reasoning audit | Open (`PLAN.md` step 2) |
| I7 | `/compact` sends raw stored entries (reasoning, signed blocks, internal ids, image refs) to the summarizer | `harness.py` `compact_history` | Reasoning audit | Open (`PLAN.md` step 3) |
| I8 | Server-level `efforts` key the user had rejected; template mixing help text and settings, no marking of optional keys | `settings.py` template | User read the template | Removed from template; model-table key still in code (`PLAN.md` step 0) |
| I9 | ~20 `if type == "..."` branches spread over four files, no provider contract | `endpoint.py`, `endpoint_discovery.py`, `endpoint_openai.py`, `settings.py` | Audit | Open (`PLAN.md` step 1) |
| I10 | Suggestion menus built per caller: no shared sorting, no mouse | `ui/tui/components/menu.py`, `completion.py` | User | Partly fixed; generic version in `PLAN.md` step 0 |
| I11 | `/edit @path` opened a file literally named `@path` | `ui/commands/core.py` | User | Fixed |
| I12 | `@path` attached images but meant nothing for other files | `harness/images.py` | User question | Fixed (`@` is input syntax only) |
| I13 | Startup warnings dumped in the transcript; toasts overwrote the status bar; no single place for "what is wrong and how to fix it" | `app.py`, `main.py`, `bars.py` | User | Replaced by notice band + setup notes |
| I14 | Wrong fix hint in a startup warning (`/sandbox none` does not exist) | `harness.py` | User | Fixed |
| I15 | Model discovery skipped on `/config theme` and `/config role`, although the user had asked for rediscovery on `/model`, `/effort`, `/reload` and `/config` | `ui/commands/core.py`, `base.py` | User | Fixed (every reload rediscovers) |
| I16 | `HANDOFF.md`, the second file every agent must read, is stale: titled "Pico-Chat", branch `cleanup`, HEAD `caaa359`, test count 457/540 (actual: 861), commands for a `.pixi` env and a `pico_chat` package, and references to `SIMPLIFICATION.md` and `plans/cleanup_round2.md`, which do not exist. `principles.md` also cites the missing `SIMPLIFICATION.md` | `HANDOFF.md`, `.wiki/notes/principles.md` | Found writing this document | Open (`ISSUES.md` D1, D2) |

### 2.2 Introduced or worsened during the 2026-09-30 session

| # | Problem | What should have happened |
|---|---|---|
| S1 | Shipped a "no server" status label that masked the phantom fallback (I1) after identifying it and asking about deleting it | Stop at the root cause: delete the fallback, or wait for the answer |
| S2 | "Global efforts: no" was implemented as moving `efforts` into model tables, extending model tables to every server type, and adding template examples | Delete. Relocating a rejected feature is not removing it |
| S3 | Removing `preserve_reasoning` silently fixed replay to "always", without flagging the behaviour change | Name the resulting behaviour and ask |
| S4 | Sorting added to one list (`/config`), clicking to one kind of menu; effort ordering used as the reason not to fix it generically | Fix at the shared component; let an ordered source opt out |
| S5 | Help lines vs inline comments: all `#` turned into `##`, including trailing comments | Apply the literal rule: `##` only for whole-line help |
| S6 | Tests and headless renders passed while the user's scenario (add OpenRouter, reload) was broken | Verify the user's scenario, not only unit tests |
| S7 | Questions accumulated unanswered (15 by the end); some were settled by default in the meantime | Keep an open-questions list; never decide by default |
| S8 | Guesses stated as facts (llama.cpp `--reasoning-preserve`, DeepSeek replay rules) until challenged | Mark unverified claims; verify before building on them |
| S9 | Edits to `HANDOFF.md` (gotchas) without noticing the file was stale (I16) | Treat entry docs as something to verify, not only to append to |

---

## 3. What the user cannot see: hidden debt indicators

Measured 2026-09-30. The individual issues are tracked in **`ISSUES.md`**;
this section only summarizes what kinds of debt were found, since that is
what the process has to catch.

- **Dead code:** 97 vulture candidates (`ISSUES.md` H1). `HANDOFF.md` lists a
  vulture gate; nobody ran it in this session.
- **Hidden fallbacks** of the same class as I1: 48 fallback / legacy /
  back-compat mentions (H3); the ones checked so far all hide missing or
  wrong input: a guessed 32k context window (P4), a silently chosen editor
  (U4), unknown theme → `terminal` (U5), color typos ignored (U6), model ids
  matched by suffix (P5).
- **Tests that encoded wrong behaviour:** four tests asserted exactly what
  the user had rejected, so the suite stayed green around the bugs (H5). A
  green suite only proves the code matches itself.
- **Docs describing a past state as current:** `HANDOFF.md` (D1),
  `principles.md` (D2), the reasoning wiki page (D3).

---

## 4. Causes

1. **Existing code is treated as intent.** Each session reads the code as the
   design and builds on it. A shortcut one session took becomes "how things
   work" for the next. `AGENTS.md` says the wiki is not intent; nothing says
   the same of the code.
2. **Additive bias.** When something is questioned, the reflex is to keep it
   and move, rename, or make it configurable (S2, S3, I3), rather than delete
   it. Principle 3 ("delete before you design") is stated but not enforced.
3. **Implicit defaults "for robustness".** Missing config or state is filled
   with a guess (I1, I2, section 3 fallbacks) so nothing errors. The error
   disappears, and so does the information the user needed.
4. **Symptoms patched instead of causes.** When the root cause is known but
   the fix is bigger, a smaller patch that hides the symptom ships instead
   (S1).
5. **Local fixes instead of shared components.** A behaviour gets fixed where
   the user noticed it, not in the shared component every caller uses (I10,
   S4). The same request then comes back for the next caller.
6. **Verification against the agent's own understanding.** Unit tests are
   written from the implementation, so they confirm the implementation,
   including its mistakes (section 3). The user's actual scenario is not run
   (S6).
7. **No durable record of decisions.** The user's instructions live only in
   chat. After a session ends or its context is summarized, the "why" is gone.
   A todo line with a question mark ("preserve_thinking part of roles ?")
   became an unrequested `servers.toml` key.
8. **Entry documents rot.** `HANDOFF.md` is mandatory reading and badly
   stale (I16). Agents either follow stale instructions or learn to ignore
   the file; either way the "read this first" contract is broken.
9. **Questions asked but not tracked.** Decisions get made by default while a
   question is pending (S7), and the user cannot tell which choices were made
   for them.
10. **Unverified claims presented as facts** (S8). They become design inputs
    and code comments that later sessions trust.
11. **Batches too large for review.** The user is the only reviewer and sees
    mostly the UI. Large multi-area changes get reviewed by behaviour only;
    code-level problems pass through.
12. **Scope drift.** Features grow beyond the request during implementation
    (model tables extended to all types, S2), each growth adding surface to
    maintain and audit.

---

## 5. Solution ideas (to iterate)

### 5.1 Sources of truth
- **`DECISIONS.md`**: dated log of user decisions, in the user's words, with
  what was rejected. Example: *2026-09-30: no `efforts` in servers config;
  levels come from detection.* Agents read it before touching an area; tests
  that lock in a decision cite its entry.
- **`PLAN.md`**: direction for the next steps (exists).
- **`HANDOFF.md`**: rewrite, short, dated, verified. Only facts an agent can
  check (branch, how to run tests, gates, gotchas). Or fold it into
  `AGENTS.md` and delete it. Add a guard test that every file path it
  references exists.
- **Code is not intent.** State it in `AGENTS.md` next to the wiki rule.

### 5.2 Rules (candidates for `AGENTS.md`)
1. **Removal means delete.** When the user rejects something, delete it. Do
   not move, rename, make it configurable, or keep it as a fallback.
2. **No implicit fallbacks.** Never replace missing config or state with a
   guess. Report it (notice band / setup notes). Any fallback that stays is
   listed with its reason in one place.
3. **Fix at the shared component.** UI behaviour (sorting, clicking, keys,
   colors) goes in the toolkit; callers opt out explicitly.
4. **No masking.** If the root cause is known, fix it or stop and ask.
5. **Audit before extending.** Before building on an area, check it against
   `PLAN.md`, `DECISIONS.md` and the principles, and report existing
   shortcuts first.
6. **Unverified is labelled.** Claims about external APIs or tools are marked
   until checked against docs.
7. **Ask and wait.** A pending question blocks the decision it concerns;
   other work can continue.

### 5.3 Workflow for each task
1. **Restate** the request as literal acceptance checks
   (e.g. "`grep efforts` in the servers template returns nothing").
2. **Audit** the area touched: fallbacks, dead code, per-caller copies,
   decisions that apply.
3. **Propose** briefly: what changes, what gets deleted.
4. **Implement** in a small, reviewable step.
5. **Verify** the acceptance checks and the user's scenario end to end, not
   only unit tests.
6. **Report** results, what was deleted, and the open-questions list.

### 5.4 Verification tooling
- **Scenario checks:** headless scripts that drive the real app flows the
  user uses (configure a server → reload → status bar; `/model`; `/config`;
  a chat turn against a fake server). Run with the suite.
- **Guard tests for principles**, like the existing boundary guards:
  - no endpoint exists without a configured, selected server;
  - config templates: no key outside the spec, no rejected keys (from
    `DECISIONS.md`);
  - every `fallback` in the code is on an allowlist with a reason;
  - `HANDOFF.md` / wiki references point at existing files.
- **Dead-code gate:** run vulture in the suite (with a reviewed whitelist),
  so dead code fails a run instead of accumulating.
- **Test review:** when a test asserts behaviour, it cites the decision it
  protects; a test protecting nothing is suspect.

### 5.5 Finding the debt we cannot see
Dedicated audit passes, one area at a time, each producing a findings list
before any fix:
1. Fallback inventory (`ISSUES.md` H3: the checked ones first, then the rest).
2. Dead code (vulture candidates, confirmed one by one).
3. Providers and discovery (during `PLAN.md` step 1).
4. Reasoning (done 2026-09-30; fixes in `PLAN.md` step 2).
5. Config loading and templates (keys, defaults, validation, sync).
6. UI toolkit: per-caller copies of shared behaviour.
7. Tests: which assert decisions, which assert accidents.
8. Docs: `HANDOFF.md`, wiki pages vs code.

### 5.6 Review and session hygiene
- **Smaller steps, committed per step** (the user commits), so each diff is
  reviewable on its own.
- **Independent review:** a second agent reviews each step's diff against the
  rules in 5.2 (not for style), before the user reviews.
- **End of session:** update `DECISIONS.md` and the open-questions list;
  verify `HANDOFF.md` facts; note unfinished work.
- **Memory:** save the rules in 5.2 as feedback memories so they persist even
  when a session skips the docs.

---

## 6. Open questions

1. `DECISIONS.md` as a separate file, or a section in `PLAN.md`?
2. Keep `HANDOFF.md` (rewritten and guarded) or fold it into `AGENTS.md`?
3. Which rules from 5.2 go into `AGENTS.md` now, and in what wording?
4. Independent review of each step by a second agent: yes, and when (every
   step, or only multi-file ones)?
5. Order of the audit passes in 5.5, and whether they run before or alongside
   `PLAN.md` step 0.
6. Where the open-questions list lives between sessions (this file, `PLAN.md`,
   or its own file).
