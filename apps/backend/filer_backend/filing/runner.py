"""Filing intake runner: enumerate dropped paths, persist inbox rows, and
process each one-by-one into destination-folder suggestions.

Mirrors indexing/runner.py: per-batch session, incremental commits, and a
fresh session for terminal failure writes.
"""

import logging
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from filer_backend.filing.fs import kind_for
from filer_backend.filing.suggester import suggest_folders
from filer_backend.indexing.walker import hash_file, iter_files
from filer_backend.storage.db import get_session
from filer_backend.storage.models import FilingBatch, FilingSuggestion, InboxFile

log = logging.getLogger(__name__)

# An inbox row at one of these statuses already represents the path; skip re-adding.
_ACTIVE = ("queued", "processing", "ready")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _update_batch(s: Session, batch_id: str, **updates) -> None:
    batch = s.get(FilingBatch, batch_id)
    if batch is None:
        return
    for k, v in updates.items():
        setattr(batch, k, v)
    batch.updated_at = _now()


def _enumerate(paths: list[str]):
    """Yield (Path, size_bytes) for each regular file in the dropped paths."""
    for raw in paths:
        p = Path(raw)
        try:
            st = p.lstat()
        except OSError:
            continue
        if p.is_dir():
            for fp, fst in iter_files(p):
                yield fp, fst.st_size
        elif p.is_file():
            yield p, st.st_size


def _process_one(s: Session, file_id: str) -> None:
    """Hash a file and generate + persist its suggestions; queued -> ready."""
    rec = s.get(InboxFile, file_id)
    if rec is None:
        return
    rec.status = "processing"
    rec.error = None
    s.commit()

    rec.content_hash = hash_file(Path(rec.absolute_path))
    for rank, (folder, confidence, rationale) in enumerate(suggest_folders(rec)):
        s.add(
            FilingSuggestion(
                id=uuid4().hex,
                inbox_file_id=file_id,
                folder_path=folder,
                confidence=confidence,
                rationale=rationale,
                rank=rank,
            )
        )
    rec.status = "ready"
    rec.processed_at = _now()
    s.commit()


def _mark_failed(file_id: str, err: str) -> None:
    s = get_session()
    try:
        rec = s.get(InboxFile, file_id)
        if rec is not None:
            rec.status = "failed"
            rec.error = err
            rec.processed_at = _now()
        s.commit()
    finally:
        s.close()


def run_ingest(paths: list[str], batch_id: str) -> dict:
    """Enumerate dropped paths into inbox rows, then process each into
    suggestions, streaming progress into the filing_batches row."""
    created_ids: list[str] = []
    seen: set[str] = set()
    processed = failed = 0
    s = get_session()
    try:
        _update_batch(s, batch_id, status="running", stage="scanning")
        s.commit()

        for fp, size in _enumerate(paths):
            ap = str(fp)
            if ap in seen:
                continue
            seen.add(ap)
            dup = s.execute(
                select(InboxFile.id).where(
                    InboxFile.absolute_path == ap, InboxFile.status.in_(_ACTIVE)
                )
            ).first()
            if dup is not None:
                continue
            fid = uuid4().hex
            s.add(
                InboxFile(
                    id=fid,
                    batch_id=batch_id,
                    absolute_path=ap,
                    filename=fp.name,
                    extension=fp.suffix.lstrip(".") or None,
                    mime_type=mimetypes.guess_type(fp.name)[0],
                    size_bytes=size,
                    kind=kind_for(fp.name),
                    status="queued",
                    added_at=_now(),
                )
            )
            created_ids.append(fid)
        _update_batch(s, batch_id, files_total=len(created_ids), stage="processing")
        s.commit()

        for fid in created_ids:
            try:
                _process_one(s, fid)
                processed += 1
            except Exception as e:  # noqa: BLE001 - per-file failure isolation
                log.exception("filing: processing failed for %s", fid)
                s.rollback()
                _mark_failed(fid, str(e))
                failed += 1
            _update_batch(s, batch_id, files_processed=processed, files_failed=failed)
            s.commit()

        _update_batch(
            s, batch_id, status="success", stage="complete", completed_at=_now()
        )
        s.commit()
    except Exception as e:
        log.exception("filing: ingest failed")
        s.rollback()
        fail = get_session()
        try:
            _update_batch(
                fail, batch_id, status="failure", error=str(e), completed_at=_now()
            )
            fail.commit()
        finally:
            fail.close()
        raise
    finally:
        s.close()

    return {
        "files_total": len(created_ids),
        "files_processed": processed,
        "files_failed": failed,
    }
