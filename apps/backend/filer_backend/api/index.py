import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from filer_backend.indexing.tasks import index_root_task
from filer_backend.storage.db import get_session
from filer_backend.storage.models import IndexJob

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
