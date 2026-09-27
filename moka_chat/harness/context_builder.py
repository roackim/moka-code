import os
import pathspec
from pathlib import Path
from collections import deque
from typing import Dict, List
from datetime import date, datetime

def is_git_repo(root) -> bool:
    """Returns True if root (or any parent) contains a .git directory."""
    path = Path(root).resolve()
    for candidate in [path, *path.parents]:
        if (candidate / '.git').exists():
            return True
    return False

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

def build_tree_structure(root: str, spec) -> Dict:
    """Build a nested dictionary representing the file tree."""
    tree = {}
    
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune dot-folders and ignored directories
        dirnames[:] = [d for d in dirnames if not d.startswith('.') 
                       and not spec.match_file(os.path.relpath(os.path.join(dirpath, d), root))]
        
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == '.':
            current = tree
        else:
            # Navigate to the correct position in the tree
            parts = rel_dir.split(os.sep)
            current = tree
            for part in parts:
                if part not in current:
                    current[part] = {}
                current = current[part]
        
        # Add files
        for filename in sorted(filenames):
            if filename.startswith('.'):
                continue
            
            full_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(full_path, root)
            
            if spec.match_file(rel_path):
                continue
            
            current[filename] = None  # None indicates a file (not a directory)
    
    return tree

def format_tree(tree: Dict, prefix: str = "") -> List[str]:
    """Format tree structure with proper indentation."""
    lines = []
    items = sorted(tree.items(), key=lambda x: (x[1] is None, x[0]))  # Dirs first, then files
    
    for i, (name, subtree) in enumerate(items):
        is_last = (i == len(items) - 1)
        
        # Determine the connector for this item
        connector = "└── " if is_last else "├── "
        
        if subtree is None:
            # It's a file
            lines.append(f"{prefix}{connector}{name}")
        else:
            # It's a directory
            lines.append(f"{prefix}{connector}{name}/")
            
            # Determine the prefix for children based on parent's position
            extension = "    " if is_last else "│   "
            child_prefix = prefix + extension
            
            # Recurse into subdirectory
            lines.extend(format_tree(subtree, child_prefix))
    
    return lines

def build_harness_context(root, format: str = None, ignore_gitignore: bool = False):
    """Generates a project skeleton in either tree or flat format.
    
    This function walks the project structure, respecting .gitignore,
    and produces a concise textual representation of the file tree
    and key symbol signatures to give the LLM context about the project.
    
    Args:
        root: Project root directory
        format: "tree" or "flat". If None, uses settings.config.context_format
        ignore_gitignore: If True, list gitignored files too (default respects .gitignore)
    """
    # Import here to avoid circular dependency
    if format is None:
        from moka_chat import settings
        format = settings.config.context_format

    # Build the tree regardless of git status. Outside a git repo there is no
    # .gitignore, so get_ignore_spec returns an empty spec and the whole
    # directory is listed. This keeps the @ file picker working everywhere.
    spec = None if ignore_gitignore else get_ignore_spec(root)
    context_output = []
    
    # Add current time and date metadata
    cdate = datetime.now().date()
    ctime = datetime.now().strftime("%H:%M:%S")  # format to HH:MM:SS
    
    context_output.append(f"Current date: {cdate}")
    context_output.append(f"Current time: {ctime}")
    context_output.append("")  # Blank line for separation
    context_output.append(f"Project Root: {root}")
    
    if format == "tree":
        context_output.append("Files (tree format, filtered by .gitignore):")
        tree = build_tree_structure(root, spec)
        tree_lines = format_tree(tree)
        context_output.extend(tree_lines)
    else:  # flat format
        context_output.append("Files (filtered by .gitignore):")
        from moka_chat import settings
        entries = list_files_bounded(
            root,
            max_files=settings.config.context_max_files,
            max_depth=settings.config.context_max_depth,
            ignore_gitignore=ignore_gitignore,
        )
        context_output.extend(entries)

    return "\n".join(context_output)
