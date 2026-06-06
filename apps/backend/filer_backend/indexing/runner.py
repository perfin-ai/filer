import logging
import mimetypes
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from filer_backend.embedding import get_embedder
from filer_backend.filing.fs import kind_for
from filer_backend.indexing.chunker import chunk_text
from filer_backend.indexing.extract import extract_text
from filer_backend.indexing.walker import hash_file, iter_files
from filer_backend.storage import vectors
from filer_backend.storage.db import get_session
from filer_backend.storage.models import Document, File, Folder, IndexJob

log = logging.getLogger(__name__)

PROGRESS_EVERY = 25


def _update_job(s: Session, job_id: str, **updates) -> None:
    job = s.get(IndexJob, job_id)
    if job is None:
        return
    for k, v in updates.items():
        setattr(job, k, v)
    job.updated_at = datetime.now(timezone.utc)


def _upsert_document(
    s: Session, file_id: int, parser: str | None, status: str, tokens: int
) -> None:
    doc = s.get(Document, file_id)
    now = datetime.now(timezone.utc)
    if doc is None:
        s.add(
            Document(
                file_id=file_id,
                parser_used=parser,
                extraction_status=status,
                token_count=tokens,
                extracted_at=now,
            )
        )
    else:
        doc.parser_used = parser
        doc.extraction_status = status
        doc.token_count = tokens
        doc.extracted_at = now


def _index_content(s: Session, rec: File, path: Path) -> None:
    """Extract → chunk → embed a changed file into LanceDB; record a documents row.

    Best-effort: any failure marks extraction_status and leaves the File row intact.
    """
    try:
        text, parser = extract_text(path)
    except Exception as e:  # noqa: BLE001 - isolate per-file extraction failures
        log.warning("extract failed for %s: %s", path, e)
        vectors.delete_file(rec.id)
        _upsert_document(s, rec.id, "error", "failed", 0)
        return

    chunks = chunk_text(text) if text else []
    if not chunks:
        vectors.delete_file(rec.id)
        _upsert_document(
            s, rec.id, parser, "skipped" if parser == "skipped" else "empty", 0
        )
        return

    try:
        emb = get_embedder()
        vecs = emb.embed(chunks)
        folder = str(path.parent)
        kind = kind_for(path.name)
        rows = [
            {
                "chunk_id": f"{rec.id}:{i}",
                "file_id": rec.id,
                "path": str(path),
                "folder_path": folder,
                "filename": path.name,
                "chunk_index": i,
                "chunk_text": c,
                "kind": kind,
                "vector": v,
            }
            for i, (c, v) in enumerate(zip(chunks, vecs))
        ]
        vectors.upsert_file_chunks(rec.id, rows, emb.dim)
        _upsert_document(s, rec.id, parser, "extracted", sum(len(c) for c in chunks) // 4)
    except Exception as e:  # noqa: BLE001
        log.exception("embedding failed for %s: %s", path, e)
        _upsert_document(s, rec.id, parser, "failed", 0)


def _ensure_folder(
    s: Session, path: Path, root: Path, seen: set[str]
) -> None:
    key = str(path)
    if key in seen:
        return
    seen.add(key)
    parent = str(path.parent) if path != root else None
    stmt = (
        sqlite_insert(Folder)
        .values(absolute_path=key, parent_path=parent, folder_name=path.name)
        .on_conflict_do_nothing(index_elements=["absolute_path"])
    )
    s.execute(stmt)


def run_index(root: Path, job_id: str) -> dict:
    """Walk `root`, upsert metadata, and stream progress into the index_jobs row."""
    seen = indexed = skipped = 0
    seen_folders: set[str] = set()
    s = get_session()
    try:
        _update_job(s, job_id, status="running", stage="scanning")
        s.commit()
        _ensure_folder(s, root, root, seen_folders)

        for p, st in iter_files(root):
            seen += 1
            _ensure_folder(s, p.parent, root, seen_folders)

            existing = s.execute(
                select(File).where(File.absolute_path == str(p))
            ).scalar_one_or_none()
            mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
            size = st.st_size

            if (
                existing is not None
                and existing.size_bytes == size
                and existing.modified_at == mtime
            ):
                skipped += 1
            else:
                content_hash = hash_file(p)
                if existing is not None and existing.content_hash == content_hash:
                    existing.modified_at = mtime
                    existing.size_bytes = size
                    existing.indexed_at = datetime.now(timezone.utc)
                    skipped += 1
                else:
                    rec = existing or File(absolute_path=str(p))
                    rec.filename = p.name
                    rec.extension = p.suffix.lstrip(".") or None
                    rec.mime_type = mimetypes.guess_type(p.name)[0]
                    rec.size_bytes = size
                    rec.modified_at = mtime
                    rec.created_at = datetime.fromtimestamp(
                        st.st_ctime, tz=timezone.utc
                    )
                    rec.content_hash = content_hash
                    rec.indexed_at = datetime.now(timezone.utc)
                    rec.status = "indexed"
                    if existing is None:
                        s.add(rec)
                    s.flush()  # assign rec.id for the vector rows
                    indexed += 1
                    _index_content(s, rec, p)

            if seen % PROGRESS_EVERY == 0:
                _update_job(
                    s,
                    job_id,
                    stage="embedding",
                    files_seen=seen,
                    files_indexed=indexed,
                    files_skipped=skipped,
                )
                s.commit()

        _update_job(
            s,
            job_id,
            status="success",
            stage="complete",
            files_seen=seen,
            files_indexed=indexed,
            files_skipped=skipped,
            completed_at=datetime.now(timezone.utc),
        )
        s.commit()
        vectors.ensure_fts_index(force=True)  # refresh BM25 over the new chunks
    except Exception as e:
        log.exception("indexing failed")
        s.rollback()
        fail = get_session()
        try:
            _update_job(
                fail,
                job_id,
                status="failure",
                error=str(e),
                files_seen=seen,
                files_indexed=indexed,
                files_skipped=skipped,
                completed_at=datetime.now(timezone.utc),
            )
            fail.commit()
        finally:
            fail.close()
        raise
    finally:
        s.close()

    return {
        "files_seen": seen,
        "files_indexed": indexed,
        "files_skipped": skipped,
    }
