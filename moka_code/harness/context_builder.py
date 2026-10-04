import os
import pathspec
from pathlib import Path
from collections import deque
from typing import Dict, List

def get_ignore_spec(root):
    """Loads .gitignore patterns."""
    gitignore_path = os.path.join(root, '.gitignore')
    if os.path.exists(gitignore_path):
        with open(gitignore_path, 'r') as f:
            return pathspec.PathSpec.from_lines('gitwildmatch', f)
    return pathspec.PathSpec.from_lines('gitwildmatch', [])


def list_files_bounded(
    root: str,
    max_files: int = 500,
    max_depth: int = 4,
    ignore_gitignore: bool = False,
) -> List[str]:
    """List files/folders under ``root`` with bounded work.

    Walks the tree breadth-first, listing depth 0 first, then depth 1, etc.,
    and stops as soon as ``max_files`` entries are collected or ``max_depth``
    is reached. This keeps the @ file picker responsive even when opened on a
    huge tree (e.g. ``$HOME``) by never walking the whole directory.

    Dot-folders are always pruned. ``.gitignore`` is respected unless
    ``ignore_gitignore`` is True (useful when the user wants to reference a
    gitignored file such as ``.env`` or a build artifact).

    Returns a list of relative paths; directories end with ``/``.
    """
    spec = None if ignore_gitignore else get_ignore_spec(root)
    entries: List[str] = []
    # BFS queue of (abs_dir, depth). Depth 0 is the root itself.
    queue = deque([(root, 0)])
    while queue and len(entries) < max_files:
        dirpath, depth = queue.popleft()
        try:
            names = os.listdir(dirpath)
        except OSError:
            continue
        dirnames = []
        filenames = []
        for name in names:
            if name.startswith('.'):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            # Directory patterns in .gitignore (e.g. "node_modules/") only
            # match when the path carries a trailing slash.
            match_rel = rel + "/" if os.path.isdir(full) else rel
            if spec is not None and spec.match_file(match_rel):
                continue
            if os.path.isdir(full):
                dirnames.append(name)
            else:
                filenames.append(name)

        # Directories first (sorted), then files (sorted) — matches the
        # existing flat-format ordering.
        for name in sorted(dirnames):
            if len(entries) >= max_files:
                break
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            entries.append(f"{rel}/")
            if depth < max_depth:
                queue.append((os.path.join(dirpath, name), depth + 1))
        for name in sorted(filenames):
            if len(entries) >= max_files:
                break
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            entries.append(rel)
    return entries
