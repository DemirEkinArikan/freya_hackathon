"""Bound evaluator writes to the project; reject symlink traversal."""
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent


def output_path(value, workspace_root=PROJECT):
    lexical_root = Path(workspace_root).absolute()
    root = lexical_root.resolve()
    path = Path(value)
    if not path.is_absolute(): path = lexical_root / path
    absolute = path.absolute()
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("evaluator output must stay inside the project")
    # macOS /var is an OS alias for /private/var. Only reject symlinks
    # inside the caller's authorized workspace, not trusted aliases above it.
    if absolute == lexical_root or lexical_root in absolute.parents:
        relative, probe = absolute.relative_to(lexical_root), lexical_root
    elif absolute == root or root in absolute.parents:
        relative, probe = absolute.relative_to(root), root
    else:
        raise ValueError("requested output is not below the workspace root")
    if ".." in relative.parts:
        raise ValueError("evaluator output cannot traverse parent directories")
    for part in relative.parts:
        probe = probe / part
        if probe.is_symlink(): raise ValueError("evaluator output cannot traverse symlinks")
    if any(part in (".git", ".env", ".aws") for part in resolved.relative_to(root).parts):
        raise ValueError("evaluator output cannot target credential or repository metadata")
    return resolved
