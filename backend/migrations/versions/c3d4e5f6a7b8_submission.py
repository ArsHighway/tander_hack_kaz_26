"""submission

Revision ID: c3d4e5f6a7b8
Revises: b7c1e2f3a4d5
Create Date: 2026-10-01 23:30:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b7c1e2f3a4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Очередь заявок стенда портала: карточка поставщика и принятые предложения модуля.
    op.create_table(
        "submission",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("card", JSONB, nullable=False),
        sa.Column("accepted", JSONB, nullable=False, server_default="[]"),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("decision_note", sa.Text, nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'returned')", name="submission_status_check"
        ),
    )


def downgrade() -> None:
    op.drop_table("submission")
