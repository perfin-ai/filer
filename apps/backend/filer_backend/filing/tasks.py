from filer_backend.celery_app import celery_app
from filer_backend.filing.runner import run_ingest


@celery_app.task(name="filer.ingest_files", bind=True)
def ingest_files_task(self, paths: list[str], batch_id: str) -> dict:
    return run_ingest(paths, batch_id)
