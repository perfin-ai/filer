import hashlib
import os
from collections.abc import Iterator
from pathlib import Path

CHUNK = 1024 * 1024  # 1 MiB

SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".idea",
    ".vscode",
    "target",
    "dist",
    "build",
}


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def iter_files(root: Path) -> Iterator[tuple[Path, os.stat_result]]:
    """Yield (path, stat) for each regular file under root, skipping common junk."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [
            d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
        ]
        d = Path(dirpath)
        for name in filenames:
            if name.startswith(".") or name == "Thumbs.db":
                continue
            p = d / name
            try:
                st = p.lstat()
            except OSError:
                continue
            if not p.is_file():
                continue
            yield p, st
