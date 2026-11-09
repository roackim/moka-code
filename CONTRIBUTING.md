# Contributing

Thanks for taking the time to improve moka. This file covers the development
setup and the checks expected before opening a pull request.

## Before you start

- Read the user guide in [`docs/`](docs/README.md).
- For design changes, read [`.wiki/notes/principles.md`](.wiki/notes/principles.md)
  and the relevant internal notes in [`.wiki/`](.wiki/).
- Open an issue before starting a large feature or a behavior change. Small bug
  fixes and documentation improvements can go straight to a pull request.

## Development setup

moka currently targets Linux and Python 3.11+.

### With uv

```bash
git clone git@github.com:roackim/moka-code.git
cd moka-code
uv sync --extra test
uv run python -m pytest -q
```

Run the app from the checkout:

```bash
uv run python moka_code/main.py
```

### With pixi

The repository also defines pixi tasks:

```bash
pixi run tests
pixi run app
```

## Tests

Run the full test suite before opening a pull request:

```bash
uv run python -m pytest -q
```

The tests use temporary configuration and cache directories, so they do not
need a model server, API key, or sandbox runtime.

If you add a test for a bug, make it fail before the fix and pass after it.

## Code layout

- `moka_code/harness/`: the agent loop, providers, tools, permissions, sessions
- `moka_code/ui/`: the terminal UI and slash commands
- `moka_code/worker.py`: tool bodies and the sandbox worker protocol
- `test/`: pytest suite
- `docs/`: user documentation
- `.wiki/`: internal design and implementation notes

Keep the harness independent of the UI. The UI consumes events; the harness
should not import `moka_code.ui`.

## Documentation

User-facing docs belong in `docs/`. Internal architecture notes belong in
`.wiki/`.

Keep guides task-oriented and avoid duplicating every config key. The file
templates created by `/config` are the reference for settings. If behavior
changes, update the README or the relevant guide in the same pull request.

## Pull request checklist

- The change is focused and described clearly.
- New behavior has tests, or the PR explains why it does not.
- `uv run python -m pytest -q` passes.
- User-visible changes update the relevant docs.
- No secrets, generated config, session files, or local scratch files are
  included.

## Commit and review style

Small commits with direct messages are easiest to review. Do not commit
unrelated local files. If you are unsure whether something belongs in the
repository, ask before adding it.
