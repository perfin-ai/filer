import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sse_starlette.sse import EventSourceResponse

from filer_backend.indexing.tasks import index_root_task
from filer_backend.storage.db import get_session
from filer_backend.storage.models import File, Folder, IndexJob

router = APIRouter(prefix="/index", tags=["indexing"])

POLL_INTERVAL_S = 0.5
TERMINAL_STATUSES = {"success", "failure", "cancelled"}


class IndexStartRequest(BaseModel):
    root_path: str = Field(..., min_length=1)


class IndexJobResponse(BaseModel):
    job_id: str
    root_path: str
    status: str
    stage: str | None = None
    files_seen: int = 0
    files_indexed: int = 0
    files_skipped: int = 0
    error: str | None = None


class IndexHistoryEntry(BaseModel):
    root_path: str
    last_indexed_at: datetime | None = None
    last_status: str
    last_job_id: str
    file_count: int = 0


def _like_prefix(root: str) -> str:
    """LIKE pattern matching every path under `root`, with wildcards escaped."""
    base = root if root.endswith("/") else root + "/"
    escaped = base.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"


def _to_response(job: IndexJob) -> IndexJobResponse:
    return IndexJobResponse(
        job_id=job.id,
        root_path=job.root_path,
        status=job.status,
        stage=job.stage,
        files_seen=job.files_seen,
        files_indexed=job.files_indexed,
        files_skipped=job.files_skipped,
        error=job.error,
    )


@router.post("/start", response_model=IndexJobResponse)
def start_index(req: IndexStartRequest) -> IndexJobResponse:
    path = Path(req.root_path).expanduser()
    if not path.exists():
        raise HTTPException(status_code=400, detail=f"path does not exist: {path}")
    if not path.is_dir():
        raise HTTPException(status_code=400, detail=f"path is not a directory: {path}")
    resolved = str(path.resolve())

    job_id = uuid4().hex
    now = datetime.now(timezone.utc)
    s = get_session()
    try:
        job = IndexJob(
            id=job_id,
            root_path=resolved,
            status="pending",
            created_at=now,
            updated_at=now,
        )
        s.add(job)
        s.commit()
        response = _to_response(job)
    finally:
        s.close()

    index_root_task.apply_async(args=[resolved, job_id], task_id=job_id)
    return response


@router.get("/history", response_model=list[IndexHistoryEntry])
def list_history() -> list[IndexHistoryEntry]:
    """One entry per indexed root, newest first, with its current file count."""
    s = get_session()
    try:
        jobs = (
            s.execute(select(IndexJob).order_by(IndexJob.created_at.desc()))
            .scalars()
            .all()
        )
        entries: list[IndexHistoryEntry] = []
        seen: set[str] = set()
        for job in jobs:
            if job.root_path in seen:
                continue
            seen.add(job.root_path)
            count = s.execute(
                select(func.count())
                .select_from(File)
                .where(File.absolute_path.like(_like_prefix(job.root_path), escape="\\"))
            ).scalar_one()
            last_indexed = job.completed_at or job.updated_at
            if last_indexed is not None and last_indexed.tzinfo is None:
                last_indexed = last_indexed.replace(tzinfo=timezone.utc)
            entries.append(
                IndexHistoryEntry(
                    root_path=job.root_path,
                    last_indexed_at=last_indexed,
                    last_status=job.status,
                    last_job_id=job.id,
                    file_count=count,
                )
            )
        return entries
    finally:
        s.close()


class DeleteHistoryResponse(BaseModel):
    root_path: str
    files_deleted: int
    jobs_deleted: int


@router.delete("/history", response_model=DeleteHistoryResponse)
def delete_history(root_path: str) -> DeleteHistoryResponse:
    """Purge all indexed files, folders, and job rows for a root."""
    prefix = _like_prefix(root_path)
    s = get_session()
    try:
        files_deleted = s.execute(
            delete(File).where(File.absolute_path.like(prefix, escape="\\"))
        ).rowcount
        s.execute(
            delete(Folder).where(
                (Folder.absolute_path == root_path)
                | (Folder.absolute_path.like(prefix, escape="\\"))
            )
        )
        jobs_deleted = s.execute(
            delete(IndexJob).where(IndexJob.root_path == root_path)
        ).rowcount
        s.commit()
        return DeleteHistoryResponse(
            root_path=root_path,
            files_deleted=files_deleted or 0,
            jobs_deleted=jobs_deleted or 0,
        )
    finally:
        s.close()


@router.get("/jobs/{job_id}", response_model=IndexJobResponse)
def get_job(job_id: str) -> IndexJobResponse:
    s = get_session()
    try:
        job = s.get(IndexJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return _to_response(job)
    finally:
        s.close()


@router.get("/jobs/{job_id}/events")
async def stream_job(job_id: str):
    async def event_gen():
        last = None
        while True:
            s = get_session()
            try:
                job = s.get(IndexJob, job_id)
                if job is None:
                    yield {
                        "event": "error",
                        "data": json.dumps({"detail": "job not found"}),
                    }
                    return
                payload = _to_response(job).model_dump_json()
                done = job.status in TERMINAL_STATUSES
            finally:
                s.close()

            if payload != last:
                yield {"event": "progress", "data": payload}
                last = payload

            if done:
                return
            await asyncio.sleep(POLL_INTERVAL_S)

    return EventSourceResponse(event_gen())
