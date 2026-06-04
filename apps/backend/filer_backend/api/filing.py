"""Filing endpoints.

Serves the Filing screen: unfiled files awaiting organization, per-file
folder suggestions, accepting a suggestion, and the destination folder
hierarchy.

The unfiled queue and the per-file accept/processing flow are still mock
data (to be replaced by Celery + a suggestion model). The folder tree
(`GET /folders`) reads the real filesystem under /Volumes, lazily one
level at a time, and suggestions point at real folder paths.
"""

import os
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/filing", tags=["filing"])

FileStatus = Literal["queued", "processing", "ready", "filed"]
FileKind = Literal["pdf", "image", "document", "spreadsheet", "other"]


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #
class UnfiledFile(BaseModel):
    file_id: str
    filename: str
    absolute_path: str
    size_bytes: int
    kind: FileKind
    status: FileStatus
    added_at: datetime
    suggestion_count: int = 0


class Suggestion(BaseModel):
    suggestion_id: str
    folder_name: str
    folder_path: str
    absolute_path: str
    confidence: float  # 0.0 – 1.0
    rationale: str | None = None


class SuggestionList(BaseModel):
    file_id: str
    filename: str
    suggestions: list[Suggestion]


class AcceptResult(BaseModel):
    file_id: str
    suggestion_id: str
    status: FileStatus
    moved_to: str


class FileIntoFolderRequest(BaseModel):
    folder_path: str


class FiledResult(BaseModel):
    file_id: str
    folder_path: str
    status: FileStatus
    moved_to: str


class FolderEntry(BaseModel):
    name: str
    path: str


# --------------------------------------------------------------------------- #
# Mock data + real-filesystem config
# --------------------------------------------------------------------------- #
_DROP_ROOT = "/Users/ericmelz/Downloads/to-file"
# The library is the real filesystem rooted here; the tree is read lazily.
_LIBRARY_ROOT = "/Volumes"
# The top suggestion always points at this real folder.
_RECORDS_FOLDER = (
    "/Volumes/home/_Documents/_Records/_By Year/_2026/"
    "Banking/Credit/Fidelity/Visa 6665/Documents"
)


def _ts(day: int, hour: int, minute: int) -> datetime:
    return datetime(2026, 6, day, hour, minute, tzinfo=timezone.utc)


_FILES: list[UnfiledFile] = [
    UnfiledFile(
        file_id="f_invoice_q1",
        filename="invoice_q1_2026.pdf",
        absolute_path=f"{_DROP_ROOT}/invoice_q1_2026.pdf",
        size_bytes=248_120,
        kind="pdf",
        status="ready",
        added_at=_ts(4, 9, 38),
        suggestion_count=3,
    ),
    UnfiledFile(
        file_id="f_scan_0314",
        filename="scan_2026_03_14.jpg",
        absolute_path=f"{_DROP_ROOT}/scan_2026_03_14.jpg",
        size_bytes=1_905_544,
        kind="image",
        status="ready",
        added_at=_ts(4, 9, 38),
        suggestion_count=2,
    ),
    UnfiledFile(
        file_id="f_meeting_notes",
        filename="meeting_notes.docx",
        absolute_path=f"{_DROP_ROOT}/meeting_notes.docx",
        size_bytes=34_210,
        kind="document",
        status="ready",
        added_at=_ts(4, 9, 39),
        suggestion_count=2,
    ),
    UnfiledFile(
        file_id="f_receipt_amazon",
        filename="receipt_amazon.pdf",
        absolute_path=f"{_DROP_ROOT}/receipt_amazon.pdf",
        size_bytes=88_400,
        kind="pdf",
        status="ready",
        added_at=_ts(4, 9, 39),
        suggestion_count=2,
    ),
    UnfiledFile(
        file_id="f_img_4821",
        filename="IMG_4821.png",
        absolute_path=f"{_DROP_ROOT}/IMG_4821.png",
        size_bytes=3_201_998,
        kind="image",
        status="processing",
        added_at=_ts(4, 9, 40),
        suggestion_count=0,
    ),
    UnfiledFile(
        file_id="f_budget_v2",
        filename="budget_v2.xlsx",
        absolute_path=f"{_DROP_ROOT}/budget_v2.xlsx",
        size_bytes=51_770,
        kind="spreadsheet",
        status="queued",
        added_at=_ts(4, 9, 40),
        suggestion_count=0,
    ),
    UnfiledFile(
        file_id="f_contract_draft",
        filename="contract_draft.pdf",
        absolute_path=f"{_DROP_ROOT}/contract_draft.pdf",
        size_bytes=176_300,
        kind="pdf",
        status="queued",
        added_at=_ts(4, 9, 41),
        suggestion_count=0,
    ),
]

_FILES_BY_ID: dict[str, UnfiledFile] = {f.file_id: f for f in _FILES}


def _list_subdirs(path: Path) -> list[Path]:
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


def _random_dirs(n: int) -> list[str]:
    """Pick `n` distinct real folders by random-walking down from the root."""
    out: list[str] = []
    seen: set[str] = set()
    for _ in range(n * 8):
        if len(out) >= n:
            break
        cur = Path(_LIBRARY_ROOT)
        for _ in range(random.randint(1, 4)):
            subs = _list_subdirs(cur)
            if not subs:
                break
            cur = random.choice(subs)
        s = str(cur)
        if s != _LIBRARY_ROOT and s != _RECORDS_FOLDER and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _suggestion(sid: str, path: str, confidence: float, rationale: str) -> Suggestion:
    return Suggestion(
        suggestion_id=sid,
        folder_name=Path(path).name,
        folder_path=path,
        absolute_path=path,
        confidence=confidence,
        rationale=rationale,
    )


# Generated suggestions are memoized per file so accept/drag can resolve the
# same ids the listing returned (the random folders stay stable per session).
_suggestion_cache: dict[str, list[Suggestion]] = {}


def _build_suggestions(file_id: str) -> list[Suggestion]:
    if file_id in _suggestion_cache:
        return _suggestion_cache[file_id]
    suggestions = [
        _suggestion(
            "s_records",
            _RECORDS_FOLDER,
            0.94,
            "Matches your Records filing for Fidelity Visa statements.",
        )
    ]
    for i, folder in enumerate(_random_dirs(2)):
        suggestions.append(
            _suggestion(
                f"s_rand_{i}",
                folder,
                round(random.uniform(0.45, 0.82), 2),
                "Another folder on this system.",
            )
        )
    _suggestion_cache[file_id] = suggestions
    return suggestions


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.get("/files/unfiled", response_model=list[UnfiledFile])
def list_unfiled_files() -> list[UnfiledFile]:
    """Files that have been dropped in and are awaiting (or ready for) filing."""
    return _FILES


@router.get("/files/{file_id}/suggestions", response_model=SuggestionList)
def get_suggestions(file_id: str) -> SuggestionList:
    """Top folder suggestions for a file, highest confidence first."""
    file = _FILES_BY_ID.get(file_id)
    if file is None:
        raise HTTPException(status_code=404, detail="file not found")
    suggestions = sorted(
        _build_suggestions(file_id),
        key=lambda s: s.confidence,
        reverse=True,
    )
    return SuggestionList(
        file_id=file_id, filename=file.filename, suggestions=suggestions
    )


@router.post(
    "/files/{file_id}/suggestions/{suggestion_id}/accept",
    response_model=AcceptResult,
)
def accept_suggestion(file_id: str, suggestion_id: str) -> AcceptResult:
    """Accept a suggestion: file the document into the suggested folder."""
    file = _FILES_BY_ID.get(file_id)
    if file is None:
        raise HTTPException(status_code=404, detail="file not found")
    suggestion = next(
        (s for s in _build_suggestions(file_id) if s.suggestion_id == suggestion_id),
        None,
    )
    if suggestion is None:
        raise HTTPException(status_code=404, detail="suggestion not found")
    return AcceptResult(
        file_id=file_id,
        suggestion_id=suggestion_id,
        status="filed",
        moved_to=f"{suggestion.absolute_path}/{file.filename}",
    )


@router.post("/files/{file_id}/file", response_model=FiledResult)
def file_into_folder(file_id: str, req: FileIntoFolderRequest) -> FiledResult:
    """File a document into an arbitrary folder (e.g. via drag-and-drop)."""
    file = _FILES_BY_ID.get(file_id)
    if file is None:
        raise HTTPException(status_code=404, detail="file not found")
    folder = req.folder_path.strip().rstrip("/")
    if not folder:
        raise HTTPException(status_code=400, detail="folder_path is required")
    base = folder if folder.startswith("/") else f"{_LIBRARY_ROOT}/{folder}"
    return FiledResult(
        file_id=file_id,
        folder_path=folder,
        status="filed",
        moved_to=f"{base}/{file.filename}",
    )


@router.get("/folders", response_model=list[FolderEntry])
def list_folders(path: str = _LIBRARY_ROOT) -> list[FolderEntry]:
    """Immediate subfolders of `path` (defaults to the library root).

    The Library pane calls this lazily, one level per expansion, so large or
    slow (network) volumes under /Volumes don't have to be read up front.
    """
    return [FolderEntry(name=p.name, path=str(p)) for p in _list_subdirs(Path(path))]
