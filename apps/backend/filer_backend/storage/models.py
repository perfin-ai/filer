from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from filer_backend.storage.db import Base


class Folder(Base):
    __tablename__ = "folders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    absolute_path: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    parent_path: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    folder_name: Mapped[str] = mapped_column(String, nullable=False)


class File(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    absolute_path: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    filename: Mapped[str] = mapped_column(String, nullable=False)
    extension: Mapped[str | None] = mapped_column(String, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    modified_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, index=True, nullable=False)
    indexed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="indexed")


class IndexJob(Base):
    __tablename__ = "index_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    root_path: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    stage: Mapped[str | None] = mapped_column(String, nullable=True)
    files_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_indexed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FilingBatch(Base):
    """One drop of files/folders into the Filing inbox; drives progress UI."""

    __tablename__ = "filing_batches"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    stage: Mapped[str | None] = mapped_column(String, nullable=True)
    files_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class InboxFile(Base):
    """A file awaiting filing. Lives in place until moved into the library."""

    __tablename__ = "inbox_files"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    batch_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    absolute_path: Mapped[str] = mapped_column(String, index=True, nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    extension: Mapped[str | None] = mapped_column(String, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    kind: Mapped[str] = mapped_column(String, nullable=False, default="other")
    modified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    filed_to: Mapped[str | None] = mapped_column(String, nullable=True)


class FilingSuggestion(Base):
    """A ranked destination-folder suggestion for an inbox file."""

    __tablename__ = "filing_suggestions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    inbox_file_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    folder_path: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    rationale: Mapped[str | None] = mapped_column(String, nullable=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class FilingAction(Base):
    """Audit log of a move (accept / drag-to-folder); supports undo."""

    __tablename__ = "filing_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inbox_file_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    source_path: Mapped[str] = mapped_column(String, nullable=False)
    destination_path: Mapped[str] = mapped_column(String, nullable=False)
    accepted_suggestion: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    moved_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
