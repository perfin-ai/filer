"""inbox_files.modified_at (source file mtime, for sorting)

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-05

"""
from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inbox_files", sa.Column("modified_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("inbox_files", "modified_at")
