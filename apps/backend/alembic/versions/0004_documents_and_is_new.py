"""documents table + filing_suggestions.is_new

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-05

"""
from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("file_id", sa.Integer(), primary_key=True),
        sa.Column("parser_used", sa.String(), nullable=True),
        sa.Column("extraction_status", sa.String(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("extracted_at", sa.DateTime(), nullable=False),
    )
    op.add_column(
        "filing_suggestions",
        sa.Column(
            "is_new", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("filing_suggestions", "is_new")
    op.drop_table("documents")
