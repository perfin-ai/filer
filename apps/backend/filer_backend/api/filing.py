"""Filing endpoints.

Serves the Filing screen: unfiled files awaiting organization, per-file
folder suggestions, accepting a suggestion, and the destination folder
hierarchy.

NOTE: every response here is mock data. The shapes are stable so the
frontend can build against them; the bodies will be replaced with real
business logic (Celery processing + a suggestion model) later.
"""

from datetime import datetime, timezone
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


class FolderNode(BaseModel):
    name: str
    path: str
    children: list["FolderNode"] = []


class FolderHierarchy(BaseModel):
    root_path: str
    children: list[FolderNode]


# --------------------------------------------------------------------------- #
# Mock data
# --------------------------------------------------------------------------- #
_DROP_ROOT = "/Users/ericmelz/Downloads/to-file"
_LIBRARY_ROOT = "/Users/ericmelz"


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


def _sugg(sid: str, path: str, confidence: float, rationale: str) -> Suggestion:
    return Suggestion(
        suggestion_id=sid,
        folder_name=path.rsplit("/", 1)[-1],
        folder_path=path,
        absolute_path=f"{_LIBRARY_ROOT}/{path}",
        confidence=confidence,
        rationale=rationale,
    )


_SUGGESTIONS: dict[str, list[Suggestion]] = {
    "f_invoice_q1": [
        _sugg("s_inv_invoices", "Documents/Finance/Invoices", 0.94,
              "Filename contains 'invoice'; similar PDFs filed here."),
        _sugg("s_inv_2026", "Documents/Finance/2026", 0.81,
              "Dated Q1 2026; matches the 2026 finance archive."),
        _sugg("s_inv_receipts", "Documents/Receipts", 0.62,
              "Financial document; receipts are a possible fit."),
    ],
    "f_scan_0314": [
        _sugg("s_scan_photos", "Media/Photos", 0.77, "Image file."),
        _sugg("s_scan_receipts", "Documents/Receipts", 0.58,
              "Scanned document may be a receipt."),
    ],
    "f_meeting_notes": [
        _sugg("s_mn_personal", "Documents/Personal", 0.71, "Notes document."),
        _sugg("s_mn_projects", "Projects", 0.55, "May relate to a project."),
    ],
    "f_receipt_amazon": [
        _sugg("s_rec_receipts", "Documents/Receipts", 0.9,
              "Filename contains 'receipt'."),
        _sugg("s_rec_finance", "Documents/Finance/2026", 0.64,
              "Financial document from 2026."),
    ],
}


def _folder(name: str, path: str, *children: FolderNode) -> FolderNode:
    return FolderNode(name=name, path=path, children=list(children))


_HIERARCHY: list[FolderNode] = [
    _folder(
        "Documents", "Documents",
        _folder(
            "Finance", "Documents/Finance",
            _folder("Invoices", "Documents/Finance/Invoices"),
            _folder("2026", "Documents/Finance/2026"),
        ),
        _folder("Receipts", "Documents/Receipts"),
        _folder("Personal", "Documents/Personal"),
    ),
    _folder(
        "Media", "Media",
        _folder("Photos", "Media/Photos"),
        _folder("Videos", "Media/Videos"),
    ),
    _folder("Projects", "Projects"),
    _folder("Archive", "Archive"),
]


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
        _SUGGESTIONS.get(file_id, []),
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
        (s for s in _SUGGESTIONS.get(file_id, []) if s.suggestion_id == suggestion_id),
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


@router.get("/folder-hierarchy", response_model=FolderHierarchy)
def get_folder_hierarchy() -> FolderHierarchy:
    """The destination library's folder tree."""
    return FolderHierarchy(root_path=_LIBRARY_ROOT, children=_HIERARCHY)
