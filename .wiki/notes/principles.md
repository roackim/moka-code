# Design Principles

The canonical statement of what moka is and how it should evolve. **Read this
before proposing or making changes.** These principles emerged from a large
simplification effort (recorded in `SIMPLIFICATION.md`), but they are not about
simplifying — they are the project's design rules.

> This note is direction. Every other wiki page documents what *is*; never infer
> intent from state documentation.

---

## North star

Moka is a thin runtime over hand-editable configuration:

> Stream from one endpoint, run approved tools, render a transcript.
> Configuration is files. The UI is the transcript, one input line, and one
> approval prompt.

---

## Principles

1. **Config files are the settings UI.** No forms, no settings pages, no
   in-app editors for servers/models/roles/permissions. Editing spawns
   `$VISUAL`/`$EDITOR` via `/config`, `/edit`, `/config role`; `/reload` applies.

2. **Explicit over implicit.** `/reload` applies changes; nothing is watched or
   auto-applied silently. No inheritance or magic resolution.

3. **Delete before you design.** Every step ends with fewer files, layers, and
   lines. Additions must displace something.

4. **Hard core/UI boundary.** `harness/` imports no `ui/`; the UI consumes
   events. This keeps the TUI replaceable.

5. **Features must earn their place.** No new features or UI surfaces unless
   explicitly asked. Prefer headless features configured from files over new
   interactive surfaces. Interactive UI is reserved for permission approval and
   destructive confirmation.

6. **The loop and the prompt are minimal and data-driven.** Stop adding tools.
   Make the agent loop and the system prompt small; move tunable strings to
   configuration where practical.

---

## How to propose a change

- Read these principles first, then `HANDOFF.md` for current state.
- State which principle the change serves and **what it removes**.
- Additions must displace something.
- If the direction is unclear, ask before proposing.
- The wiki (`.wiki/`) documents what exists — never infer direction from it.

---

## Working preferences

- Aggressive simplification toward the essence; explicit over implicit.
- Config over UI; edit with `$EDITOR`.
- Keep the custom TUI aesthetic; do not delete the TUI toolkit.
- Terse answers; corrections welcome.
- Never commit or `git add` unless asked.

See also: [architecture.md](./architecture.md), [config.md](./config.md).
