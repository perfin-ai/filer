"""filing intake: filing_batches, inbox_files, filing_suggestions, filing_actions

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-04

"""
from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "filing_batches",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("stage", sa.String(), nullable=True),
        sa.Column("files_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "inbox_files",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("batch_id", sa.String(), nullable=False),
        sa.Column("absolute_path", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("extension", sa.String(), nullable=True),
        sa.Column("mime_type", sa.String(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("kind", sa.String(), nullable=False, server_default="other"),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("filed_to", sa.String(), nullable=True),
    )
    op.create_index("ix_inbox_files_batch_id", "inbox_files", ["batch_id"])
    op.create_index("ix_inbox_files_absolute_path", "inbox_files", ["absolute_path"])

    op.create_table(
        "filing_suggestions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("inbox_file_id", sa.String(), nullable=False),
        sa.Column("folder_path", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("rationale", sa.String(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_filing_suggestions_inbox_file_id", "filing_suggestions", ["inbox_file_id"]
    )

    op.create_table(
        "filing_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("inbox_file_id", sa.String(), nullable=True),
        sa.Column("source_path", sa.String(), nullable=False),
        sa.Column("destination_path", sa.String(), nullable=False),
        sa.Column(
            "accepted_suggestion", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("moved_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_filing_actions_inbox_file_id", "filing_actions", ["inbox_file_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_filing_actions_inbox_file_id", table_name="filing_actions")
    op.drop_table("filing_actions")
    op.drop_index(
        "ix_filing_suggestions_inbox_file_id", table_name="filing_suggestions"
    )
    op.drop_table("filing_suggestions")
    op.drop_index("ix_inbox_files_absolute_path", table_name="inbox_files")
    op.drop_index("ix_inbox_files_batch_id", table_name="inbox_files")
    op.drop_table("inbox_files")
    op.drop_table("filing_batches")
