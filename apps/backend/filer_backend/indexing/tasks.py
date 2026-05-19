from pathlib import Path

from filer_backend.celery_app import celery_app
from filer_backend.indexing.runner import run_index


@celery_app.task(name="filer.index_root", bind=True)
def index_root_task(self, root_path: str, job_id: str) -> dict:
    return run_index(Path(root_path), job_id)
