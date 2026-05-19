"""initial schema: folders, files, index_jobs

Revision ID: 0001
Revises:
Create Date: 2026-05-19

"""
from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "folders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("absolute_path", sa.String(), nullable=False),
        sa.Column("parent_path", sa.String(), nullable=True),
        sa.Column("folder_name", sa.String(), nullable=False),
        sa.UniqueConstraint("absolute_path", name="uq_folders_absolute_path"),
    )
    op.create_index("ix_folders_absolute_path", "folders", ["absolute_path"])
    op.create_index("ix_folders_parent_path", "folders", ["parent_path"])

    op.create_table(
        "files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("absolute_path", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("extension", sa.String(), nullable=True),
        sa.Column("mime_type", sa.String(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("modified_at", sa.DateTime(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("indexed_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="indexed"),
        sa.UniqueConstraint("absolute_path", name="uq_files_absolute_path"),
    )
    op.create_index("ix_files_absolute_path", "files", ["absolute_path"])
    op.create_index("ix_files_content_hash", "files", ["content_hash"])

    op.create_table(
        "index_jobs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("root_path", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("stage", sa.String(), nullable=True),
        sa.Column("files_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files_indexed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("index_jobs")
    op.drop_index("ix_files_content_hash", table_name="files")
    op.drop_index("ix_files_absolute_path", table_name="files")
    op.drop_table("files")
    op.drop_index("ix_folders_parent_path", table_name="folders")
    op.drop_index("ix_folders_absolute_path", table_name="folders")
    op.drop_table("folders")
