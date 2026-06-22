"""inbox_files.preview_text + preview_parser (cached extracted text for preview UI)

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-22

"""
from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inbox_files", sa.Column("preview_text", sa.Text(), nullable=True))
    op.add_column("inbox_files", sa.Column("preview_parser", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("inbox_files", "preview_parser")
    op.drop_column("inbox_files", "preview_text")
