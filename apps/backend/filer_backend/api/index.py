from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/index", tags=["indexing"])


class IndexStartRequest(BaseModel):
    root_path: str = Field(..., min_length=1)


class IndexStartResponse(BaseModel):
    job_id: str
    root_path: str
    status: str


# In-memory job registry. Replaced by Celery + SQLite in milestone 4.
_JOBS: dict[str, dict] = {}


@router.post("/start", response_model=IndexStartResponse)
def start_index(req: IndexStartRequest) -> IndexStartResponse:
    path = Path(req.root_path).expanduser()
    if not path.exists():
        raise HTTPException(status_code=400, detail=f"path does not exist: {path}")
    if not path.is_dir():
        raise HTTPException(status_code=400, detail=f"path is not a directory: {path}")

    job_id = uuid4().hex
    resolved = str(path.resolve())
    _JOBS[job_id] = {"root_path": resolved, "status": "pending"}
    return IndexStartResponse(job_id=job_id, root_path=resolved, status="pending")
