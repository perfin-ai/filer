"""LanceDB chunk store: dense vectors + chunk text (for BM25 hybrid) + metadata.

One embedded table under `config.lancedb_dir()`. The relational source of truth
stays in SQLite; this holds chunk vectors/text used for retrieval.
"""

import logging
from functools import lru_cache
from typing import Any

import pyarrow as pa

from filer_backend.config import lancedb_dir

log = logging.getLogger(__name__)

TABLE = "chunks"
_fts_built = False


@lru_cache
def _db():
    import lancedb

    return lancedb.connect(str(lancedb_dir()))


def _schema(dim: int) -> pa.Schema:
    return pa.schema(
        [
            ("chunk_id", pa.string()),
            ("file_id", pa.int64()),
            ("path", pa.string()),
            ("folder_path", pa.string()),
            ("filename", pa.string()),
            ("chunk_index", pa.int32()),
            ("chunk_text", pa.string()),
            ("kind", pa.string()),
            ("vector", pa.list_(pa.float32(), dim)),
        ]
    )


def get_table(dim: int):
    db = _db()
    if TABLE in db.table_names():
        return db.open_table(TABLE)
    return db.create_table(TABLE, schema=_schema(dim))


def upsert_file_chunks(file_id: int, rows: list[dict[str, Any]], dim: int) -> None:
    """Replace all chunks for a file: delete existing rows, then add new ones."""
    if not rows:
        delete_file(file_id)
        return
    tbl = get_table(dim)
    tbl.delete(f"file_id = {int(file_id)}")
    tbl.add(rows)


def delete_file(file_id: int) -> None:
    db = _db()
    if TABLE in db.table_names():
        db.open_table(TABLE).delete(f"file_id = {int(file_id)}")


def count() -> int:
    db = _db()
    if TABLE not in db.table_names():
        return 0
    return db.open_table(TABLE).count_rows()


def _exclude_where(exclude_file_ids) -> str | None:
    ids = [str(int(i)) for i in exclude_file_ids]
    return f"file_id NOT IN ({','.join(ids)})" if ids else None


def ensure_fts_index(force: bool = False) -> None:
    """Build the BM25 full-text index over chunk_text (once per process)."""
    global _fts_built
    db = _db()
    if TABLE not in db.table_names():
        return
    if _fts_built and not force:
        return
    try:
        db.open_table(TABLE).create_fts_index(
            "chunk_text", use_tantivy=False, replace=True
        )
        _fts_built = True
    except Exception as e:  # noqa: BLE001
        log.warning("FTS index build failed: %s", e)


def vector_search(qvec, k: int = 20, exclude_file_ids=()) -> list[dict[str, Any]]:
    db = _db()
    if TABLE not in db.table_names():
        return []
    q = db.open_table(TABLE).search(qvec).metric("cosine").limit(k)
    where = _exclude_where(exclude_file_ids)
    if where:
        q = q.where(where, prefilter=True)
    return q.to_list()


def text_search(qtext: str, k: int = 20, exclude_file_ids=()) -> list[dict[str, Any]]:
    if not qtext.strip():
        return []
    ensure_fts_index()
    db = _db()
    if TABLE not in db.table_names():
        return []
    try:
        q = db.open_table(TABLE).search(qtext, query_type="fts").limit(k)
        where = _exclude_where(exclude_file_ids)
        if where:
            q = q.where(where, prefilter=True)
        return q.to_list()
    except Exception as e:  # noqa: BLE001
        log.warning("FTS search failed: %s", e)
        return []
