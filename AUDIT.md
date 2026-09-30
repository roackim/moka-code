# Audit of moka

Plan for auditing the whole codebase in several sessions (started
2026-09-30). Why we audit: see `PROCESS.md`. Findings go to `ISSUES.md`;
fixes are planned in `PLAN.md`, never done during an audit.

Status: **method written; sweep 0 not started.**

---

## 1. Method

### 1.1 Audit against intent, not against the code
Every bug fixed on 2026-09-30 was internally consistent: code, tests and docs
agreed with each other, and only the user could say it was wrong. An audit
that compares the code with itself finds little.

So each flow audit starts from **draft assumptions** (gathered from the wiki,
the user's notes and the plan), which the user confirms or corrects in a few
questions. The user does not re-explain the spec. The answers go to
`DECISIONS.md`, and the code is checked against them.

### 1.2 Two kinds of sweep
- **Pattern sweep** (whole codebase, mechanical): grep- and tool-driven, never
  full reads. Looks for fallbacks and guessed defaults, swallowed errors
  (`except: pass`), `type ==` branches, magic values (like `32768`),
  compatibility aliases, dead code (vulture), behaviour copied between
  callers. Produces **leads** (file:line), not verdicts. Rerunnable later as
  a check.
- **Flow audits** (the reviews): one user-facing flow per session, followed
  end to end from what the user does to what happens (e.g. "add a server →
  reload → status bar"). Following one path is what catches problems that
  span files (the phantom server spanned `settings` → `endpoint` → `harness`
  → status bar). Code is read along that path only.

### 1.3 Sessions
- Each sweep runs in a **fresh session**, from its brief in this file.
- One flow per session. A flow whose path is too large to read carefully is
  split, not stretched.
- No fixes during an audit.

---

## 2. Sweep 0: find the audit points

Builds the prioritized list of flows and a ready-to-run brief for each. No
verdicts, no fixes.

### 2.1 Inputs (cheap sources)
| Source | Gives |
|---|---|
| `.wiki/tree/*` | module map: which files do what, so flows get file lists without reading code |
| `.wiki/notes/*` | feature descriptions and claimed behaviour: the draft assumptions |
| `README.md`, command registry, config specs and templates | every entry point: commands, keys, prefixes, startup |
| `todo.todo`, `PLAN.md`, `ISSUES.md`, `DECISIONS.md` | the user's intent and the known problems |
| `git log` | churn and fix history per file: hotspots |
| tests (names only) | what behaviour is locked in, what is untested |
| pattern sweep | lead density per file |

The wiki can be stale (`ISSUES.md` D1–D3): use it as a map, and check it
cheaply (do the files and functions it names exist?). Every mismatch is a
finding and marks which parts of the map not to trust.

### 2.2 Steps
1. **Feature inventory:** the flows the user uses, each with its entry points
   (command, key, startup, keybinding).
2. **Flow → files:** from the wiki map plus a light grep for each entry
   point. No reading of function bodies.
3. **Draft assumptions per flow:** what the docs and the user's notes say it
   should do.
4. **Score each flow:**
   - security impact (tools, permissions, sandbox, file writes, shell);
   - how often the user hits it (every turn vs rarely);
   - lead density (pattern-sweep hits in its files);
   - churn and fix history;
   - known issues (`ISSUES.md`);
   - doc drift (wiki vs code);
   - `PLAN.md` overlap (audit before restructuring);
   - size and complexity (lines, branches);
   - test gaps.
5. **Size check:** split any flow too big for one session.

### 2.3 Outputs (written into this file)
- **Priority table** (section 4, replacing the tentative order): one row per
  flow: entry points, files, size, score and what drove it, known issue IDs.
- **Flow briefs** (section 8), each ready for a fresh session: scope, files,
  leads, draft assumptions, 3–5 questions for the user, the scenario to run.
- **Coverage table** (section 5): file × flow, empty at first. Files that
  belong to no flow are listed right away (dead code or a forgotten flow).
- **Pattern-sweep leads**, grouped by file (section 6).
- **Wiki mismatches**, added to `ISSUES.md`.

### 2.4 Limits
- A file is opened only to locate a flow's entry point or a lead, never read
  in full.
- If the map itself is too big for one session, split it: the pattern sweep
  first, then the flow map.

---

## 3. Flow audit brief (template for every flow session)

1. **Read:** `.wiki/notes/principles.md`, `PLAN.md`, `ISSUES.md`,
   `DECISIONS.md`, this file (the flow's brief in section 8, its leads in
   section 6).
2. **Confirm intent:** show the draft assumptions and ask the flow's
   questions; record the answers in `DECISIONS.md`.
3. **Trace the flow** from its entry points through its files only. For each
   step ask:
   - What does it read (config, state, environment)?
   - What happens when that is missing, invalid or stale? Is it visible to
     the user?
   - Does anything get guessed, rewritten or dropped?
   - Is the same behaviour implemented more than once?
   - What do the tests assert, and does it match the decisions?
   - What do the docs claim, and is it true?
4. **Run the scenario** for real (headless where possible) as evidence.
5. **Write:**
   - new `ISSUES.md` entries (confirmed / to audit);
   - the flow's report in section 7: what was covered, what was **not**
     read, leads handed to other flows;
   - the coverage table (section 5).
6. No fixes.

---

## 4. Order (tentative, sweep 0 confirms or changes it)

1. **Sweep 0:** map, scores, briefs, pattern sweep (this plan).
2. **Pilot flow audit:** config loading and reload. Small, well known after
   2026-09-30, fallback-prone. Adjust the brief (section 3) after it.
3. **Security:** tool execution and permissions; sandbox.
4. **Before `PLAN.md` restructures them:** model selection and discovery; a
   chat turn and its tool loop.
5. **The rest:** sessions and export/import, compaction, commands, input and
   completion, transcript rendering, themes and the UI toolkit.

---

## 5. Coverage (file × flow)

*Filled by sweep 0 (rows) and each flow audit (marks).*

## 6. Pattern-sweep leads

*Filled by sweep 0.*

## 7. Flow reports

*One subsection per completed flow audit.*

## 8. Flow briefs

*Filled by sweep 0: one subsection per flow (scope, files, leads, draft
assumptions, questions, scenario).*
