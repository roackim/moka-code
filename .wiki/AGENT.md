# moka Wiki — Agent Guide

This document tells AI agents and humans how to operate and maintain this wiki.

---

## Wiki Structure

```
.wiki/
  AGENT.md                  ← This file. Maintenance contract.
  notes/
    principles.md           ← Canonical design principles (direction; read first)
    architecture.md         ← High-level system design and data flow
    config.md               ← Configuration reference (split files under ~/.config/moka/)
    reasoning-traces.md     ← Reasoning trace handling (reasoning fields; content never parsed)
    security.md             ← Approval model and trust boundary
    sandbox.md              ← Sandboxed tool execution: worker, transport, project store
    testing.md              ← Test suite overview and how to run tests
    tools-and-permissions.md← Tool system and the no/ask/yes role model
    ui.md                   ← TUI architecture, component model, markdown rendering
    local-hostname-resolution.md ← `.local` mDNS resolution in the endpoint layer
  tree/
    README.md               ← Root package overview
    harness.md              ← moka_code/harness/ — LLM agent core
    ui.md                   ← moka_code/ui/ — Chat UI layer
    ui-tui.md               ← moka_code/ui/tui/ — Rendering engine
    ui-tui-components.md    ← moka_code/ui/tui/components/ — UI widgets
    test.md                 ← test/ — Test suite
```

### `notes/`
Conceptual documentation. Each file covers a cross-cutting concern.
**Update when**: an architectural decision changes, a new subsystem is added, or security/permission logic is modified.

### `tree/`
File-by-file reference, mirroring the source tree. Each `.md` corresponds to a directory.
Each entry contains: purpose, key classes/functions, dependencies.
**Update when**: files are added, removed, renamed, or their public API changes.

---

## State vs. direction

This wiki documents **what exists**, not what should be. The one exception is
`notes/principles.md`, which is the canonical statement of direction. Never infer
intent from state documentation.

## Staleness Warning

This wiki is maintained opportunistically — it is updated when code changes, not on a fixed schedule. **Treat all content as potentially outdated.** When in doubt, read the source file directly and verify against what's here.

Signs a page may be stale:
- A class or function name in the wiki doesn't exist in the source
- A file listed in a tree page has been renamed or removed
- A workflow described in `notes/` doesn't match actual code behaviour

If you find stale content, correct it, and warn the user rather than working around it.

---

## Maintenance Rules

1. **Keep it factual** — no aspirational "will be" statements. Document what exists now.
2. **One responsibility per note** — if a note is growing to cover two concerns, split it.
3. **Tree entries are stubs by default** — a one-liner is fine. Detail only what's non-obvious.
4. **Don't duplicate** — if notes/architecture.md covers a flow, tree entries should link to it instead of repeating it.
5. **Update atomically** — when renaming a file, update both the tree entry and any notes that reference it in the same operation.

---

## How to Update the Wiki

When source files change, update the wiki as follows:
1. Identify which source files changed
2. Locate the corresponding `.wiki/tree/` page(s) using the structure above
3. Update the page content to reflect current reality
4. Check whether any `notes/` page is affected and update it if so

Checklist:
- Does the changed file have a corresponding `.wiki/tree/` entry?
- Did any class/function names, signatures, or responsibilities change?
- Did the change affect a cross-cutting concern covered in `notes/`?
- Are all internal wiki links still valid?

---

## Skill Reference

The wiki maintenance skills are defined under `.agents/skills/`:
`auto-wiki-search` (read the wiki before grepping source) and
`auto-wiki-update` (keep it in sync after code changes). Load them for the full
procedure and general rules.
