"""submission_batch

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-11 03:30:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Заявка из строки пакета массовой загрузки: {job_id, row, filename}; у единичной — NULL.
    op.add_column("submission", sa.Column("batch", postgresql.JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("submission", "batch")
