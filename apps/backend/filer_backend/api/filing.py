"""Filing endpoints.

Serves the Filing screen end-to-end:
- POST /filing/ingest          — accept dropped folder/files, enqueue processing
- GET  /filing/jobs/{id}/events— SSE batch progress ("Processing N of M")
- GET  /filing/files/unfiled   — inbox queue (DB-backed)
- GET  /filing/files/{id}/suggestions
- POST /filing/files/{id}/suggestions/{sid}/accept — move into the suggested folder
- POST /filing/files/{id}/file — move into an arbitrary folder (drag-and-drop)
- GET  /filing/entries         — lazy real-filesystem listing for the Library tree

Files are processed one-by-one by a Celery task (see filing/runner.py). The
suggestion engine itself is still a stub (filing/suggester.py).
"""

import asyncio
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sse_starlette.sse import EventSourceResponse

from filer_backend.filing.fs import FileKind, FsEntry, LIBRARY_ROOT, list_entries
from filer_backend.filing.tasks import ingest_files_task
from filer_backend.storage.db import get_session
from filer_backend.storage.models import (
    FilingAction,
    FilingBatch,
    FilingSuggestion,
    InboxFile,
)

router = APIRouter(prefix="/filing", tags=["filing"])

POLL_INTERVAL_S = 0.5
TERMINAL_STATUSES = {"success", "failure"}

FileStatus = Literal["queued", "processing", "ready", "filed", "failed"]


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class IngestRequest(BaseModel):
    paths: list[str] = Field(..., min_length=1)


class FilingBatchResponse(BaseModel):
    batch_id: str
    status: str
    stage: str | None = None
    files_total: int = 0
    files_processed: int = 0
    files_failed: int = 0
    error: str | None = None


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
    confidence: float
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


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _utc(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _batch_response(b: FilingBatch) -> FilingBatchResponse:
    return FilingBatchResponse(
        batch_id=b.id,
        status=b.status,
        stage=b.stage,
        files_total=b.files_total,
        files_processed=b.files_processed,
        files_failed=b.files_failed,
        error=b.error,
    )


def _unfiled(rec: InboxFile, suggestion_count: int) -> UnfiledFile:
    return UnfiledFile(
        file_id=rec.id,
        filename=rec.filename,
        absolute_path=rec.absolute_path,
        size_bytes=rec.size_bytes,
        kind=rec.kind,  # type: ignore[arg-type]
        status=rec.status,  # type: ignore[arg-type]
        added_at=_utc(rec.added_at),
        suggestion_count=suggestion_count,
    )


def _unique_dest(dest_dir: Path, name: str) -> Path:
    cand = dest_dir / name
    if not cand.exists():
        return cand
    stem, suffix = cand.stem, cand.suffix
    i = 1
    while True:
        alt = dest_dir / f"{stem} ({i}){suffix}"
        if not alt.exists():
            return alt
        i += 1


def _move_into(s, rec: InboxFile, folder_path: str, accepted: bool) -> str:
    """Move the inbox file into `folder_path`, record the action, mark filed."""
    folder = folder_path.strip().rstrip("/")
    if not folder:
        raise HTTPException(status_code=400, detail="folder_path is required")
    src = Path(rec.absolute_path)
    if not src.exists():
        raise HTTPException(status_code=409, detail=f"source file is gone: {src}")
    dest_dir = Path(folder)
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = _unique_dest(dest_dir, src.name)
        shutil.move(str(src), str(dest))
    except OSError as e:
        # e.g. EPERM on TCC-gated /Volumes network shares.
        raise HTTPException(status_code=409, detail=f"move failed: {e}")
    now = datetime.now(timezone.utc)
    s.add(
        FilingAction(
            inbox_file_id=rec.id,
            source_path=str(src),
            destination_path=str(dest),
            accepted_suggestion=accepted,
            moved_at=now,
        )
    )
    rec.status = "filed"
    rec.filed_to = str(dest)
    rec.absolute_path = str(dest)
    rec.processed_at = now
    s.commit()
    return str(dest)


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.post("/ingest", response_model=FilingBatchResponse)
def ingest(req: IngestRequest) -> FilingBatchResponse:
    """Accept dropped folder/files and enqueue one-by-one processing."""
    valid = [p for p in req.paths if Path(p).expanduser().exists()]
    if not valid:
        raise HTTPException(status_code=400, detail="no existing paths provided")
    valid = [str(Path(p).expanduser()) for p in valid]

    batch_id = uuid4().hex
    now = datetime.now(timezone.utc)
    s = get_session()
    try:
        batch = FilingBatch(
            id=batch_id, status="pending", created_at=now, updated_at=now
        )
        s.add(batch)
        s.commit()
        response = _batch_response(batch)
    finally:
        s.close()

    ingest_files_task.apply_async(args=[valid, batch_id], task_id=batch_id)
    return response


@router.get("/jobs/{batch_id}", response_model=FilingBatchResponse)
def get_batch(batch_id: str) -> FilingBatchResponse:
    s = get_session()
    try:
        batch = s.get(FilingBatch, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="batch not found")
        return _batch_response(batch)
    finally:
        s.close()


@router.get("/jobs/{batch_id}/events")
async def stream_batch(batch_id: str):
    async def event_gen():
        last = None
        while True:
            s = get_session()
            try:
                batch = s.get(FilingBatch, batch_id)
                if batch is None:
                    yield {
                        "event": "error",
                        "data": json.dumps({"detail": "batch not found"}),
                    }
                    return
                payload = _batch_response(batch).model_dump_json()
                done = batch.status in TERMINAL_STATUSES
            finally:
                s.close()

            if payload != last:
                yield {"event": "progress", "data": payload}
                last = payload
            if done:
                return
            await asyncio.sleep(POLL_INTERVAL_S)

    return EventSourceResponse(event_gen())


@router.get("/files/unfiled", response_model=list[UnfiledFile])
def list_unfiled_files() -> list[UnfiledFile]:
    """Inbox files not yet filed, newest first."""
    s = get_session()
    try:
        rows = (
            s.execute(
                select(InboxFile)
                .where(InboxFile.status != "filed")
                .order_by(InboxFile.added_at.desc())
            )
            .scalars()
            .all()
        )
        counts = dict(
            s.execute(
                select(FilingSuggestion.inbox_file_id, func.count()).group_by(
                    FilingSuggestion.inbox_file_id
                )
            ).all()
        )
        return [_unfiled(r, counts.get(r.id, 0)) for r in rows]
    finally:
        s.close()


@router.get("/files/{file_id}/suggestions", response_model=SuggestionList)
def get_suggestions(file_id: str) -> SuggestionList:
    """Top folder suggestions for a file, highest confidence first."""
    s = get_session()
    try:
        rec = s.get(InboxFile, file_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="file not found")
        rows = (
            s.execute(
                select(FilingSuggestion)
                .where(FilingSuggestion.inbox_file_id == file_id)
                .order_by(FilingSuggestion.confidence.desc())
            )
            .scalars()
            .all()
        )
        suggestions = [
            Suggestion(
                suggestion_id=row.id,
                folder_name=Path(row.folder_path).name,
                folder_path=row.folder_path,
                absolute_path=row.folder_path,
                confidence=row.confidence,
                rationale=row.rationale,
            )
            for row in rows
        ]
        return SuggestionList(
            file_id=file_id, filename=rec.filename, suggestions=suggestions
        )
    finally:
        s.close()


@router.post(
    "/files/{file_id}/suggestions/{suggestion_id}/accept",
    response_model=AcceptResult,
)
def accept_suggestion(file_id: str, suggestion_id: str) -> AcceptResult:
    """Accept a suggestion: move the file into the suggested folder."""
    s = get_session()
    try:
        rec = s.get(InboxFile, file_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="file not found")
        sug = s.get(FilingSuggestion, suggestion_id)
        if sug is None or sug.inbox_file_id != file_id:
            raise HTTPException(status_code=404, detail="suggestion not found")
        moved_to = _move_into(s, rec, sug.folder_path, accepted=True)
        return AcceptResult(
            file_id=file_id,
            suggestion_id=suggestion_id,
            status="filed",
            moved_to=moved_to,
        )
    finally:
        s.close()


@router.post("/files/{file_id}/file", response_model=FiledResult)
def file_into_folder(file_id: str, req: FileIntoFolderRequest) -> FiledResult:
    """File a document into an arbitrary folder (e.g. via drag-and-drop)."""
    s = get_session()
    try:
        rec = s.get(InboxFile, file_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="file not found")
        moved_to = _move_into(s, rec, req.folder_path, accepted=False)
        return FiledResult(
            file_id=file_id,
            folder_path=req.folder_path.strip().rstrip("/"),
            status="filed",
            moved_to=moved_to,
        )
    finally:
        s.close()


@router.get("/entries", response_model=list[FsEntry])
def list_entries_endpoint(path: str = LIBRARY_ROOT) -> list[FsEntry]:
    """Immediate subfolders and files of `path` (defaults to the library root).

    The Library pane calls this lazily, one level per expansion, so large or
    slow (network) volumes under /Volumes don't have to be read up front.
    """
    return list_entries(Path(path))
