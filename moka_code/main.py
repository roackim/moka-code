"""Main entry point for moka

A TUI chat app for self-hosted LLM agents.
"""

import argparse
import os
import sys
import asyncio

from moka_code.harness.harness import get_harness
from moka_code.ui.app import chatTUI


def main():
    """
    Main entry point for moka.
    Wrapps the chatTUI in an async event loop and handles keyboard interrupts gracefully.
    """
    # A shell opened by moka's /terminal is marked; moka must not nest in it
    # (two TUIs would fight over one terminal). Refuse before touching it.
    outer = os.environ.get("MOKA_TERMINAL")
    if outer:
        print(f"moka is already running (pid {outer}): this shell was opened by its "
              "/terminal.\nType 'exit' to return to it.", file=sys.stderr)
        return 1

    parser = argparse.ArgumentParser(prog="moka", description="A terminal coding assistant.")
    parser.add_argument("-r", "--resume", action="store_true",
                        help="pick a saved session of this project to resume")
    args = parser.parse_args()

    # Initialize harness first
    print("Initializing moka...")
    from moka_code.harness import roles
    from moka_code import settings

    # Before anything creates ~/.config/moka: carry the pre-rename config over.
    migration_notice = settings.migrate_legacy_config_dir()
    roles.ensure_roles_dir()
    # Insert commented lines for newly added config keys (and drop retired ones)
    # in existing files, then reload so the fresh file is what the app sees.
    settings.sync_config_files()
    # Load errors stay on the banner (``chatTUI.setup_notes``) until fixed.
    settings.reload_config()
    harness = get_harness()
    print()

    # Apply theme from config
    from moka_code.ui.tui.colors import set_theme
    set_theme(settings.config.get_active_theme())

    tui = chatTUI(harness, resume=args.resume, migration_notice=migration_notice)
    try:
        asyncio.run(tui.run())
    except KeyboardInterrupt:
        # Ctrl+C is normally handled inside the TUI (raw \x03); this is a
        # fallback for an interrupt that arrives before/around the event loop.
        pass
    except BaseExceptionGroup as group:
        # asyncio.TaskGroup wraps failures in an ExceptionGroup whose box-drawing
        # traceback format is noisy. Unwrap to the first real exception so the
        # terminal shows a clean, standard traceback.
        first = group.exceptions[0]
        if not isinstance(first, (KeyboardInterrupt, asyncio.CancelledError)):
            raise first from None
    return 0


if __name__ == "__main__":
    sys.exit(main())
