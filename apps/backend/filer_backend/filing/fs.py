"""Filesystem listing helpers for the Library pane and the filing pipeline."""

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

# The library is the real filesystem rooted here; the tree is read lazily.
LIBRARY_ROOT = "/Volumes"

FileKind = Literal["pdf", "image", "document", "spreadsheet", "other"]


class FsEntry(BaseModel):
    name: str
    path: str
    is_dir: bool
    kind: FileKind | None = None  # set for files, used to pick an icon


_KIND_BY_EXT: dict[str, FileKind] = {
    "pdf": "pdf",
    "png": "image", "jpg": "image", "jpeg": "image", "gif": "image",
    "heic": "image", "webp": "image", "tiff": "image", "bmp": "image", "svg": "image",
    "doc": "document", "docx": "document", "txt": "document", "rtf": "document",
    "md": "document", "pages": "document", "odt": "document",
    "xls": "spreadsheet", "xlsx": "spreadsheet", "csv": "spreadsheet",
    "numbers": "spreadsheet", "ods": "spreadsheet",
}


def kind_for(name: str) -> FileKind:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _KIND_BY_EXT.get(ext, "other")


def list_subdirs(path: Path) -> list[Path]:
    """Immediate subdirectories of `path`, sorted; [] on any access error.

    Skips dotfiles and symlinks (the latter avoids loops and the
    /Volumes/Macintosh HD -> / link). Errors (incl. EPERM on TCC-gated
    network volumes) degrade gracefully to an empty list.
    """
    try:
        entries = []
        with os.scandir(path) as it:
            for e in it:
                if e.name.startswith("."):
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        entries.append(Path(e.path))
                except OSError:
                    continue
        entries.sort(key=lambda p: p.name.lower())
        return entries
    except OSError:
        return []


def list_entries(path: Path) -> list[FsEntry]:
    """Subfolders (first) then files of `path`; [] on any access error.

    Skips dotfiles and symlinks. Files carry a `kind` for icon selection.
    """
    try:
        dirs: list[FsEntry] = []
        files: list[FsEntry] = []
        with os.scandir(path) as it:
            for e in it:
                if e.name.startswith("."):
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        dirs.append(FsEntry(name=e.name, path=e.path, is_dir=True))
                    elif e.is_file(follow_symlinks=False):
                        files.append(
                            FsEntry(
                                name=e.name,
                                path=e.path,
                                is_dir=False,
                                kind=kind_for(e.name),
                            )
                        )
                except OSError:
                    continue
        dirs.sort(key=lambda x: x.name.lower())
        files.sort(key=lambda x: x.name.lower())
        return dirs + files
    except OSError:
        return []
