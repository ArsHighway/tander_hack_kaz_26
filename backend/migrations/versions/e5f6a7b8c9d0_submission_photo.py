"""submission_photo

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-10-07 05:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Фото товара заявки стенда портала: загружается до заявки, заявка ссылается на него
    # (`card->>'photo_id'`); сироты старше суток удаляются при следующей загрузке.
    op.create_table(
        "submission_photo",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("mime", sa.Text, nullable=False),
        sa.Column("bytes", sa.LargeBinary, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("submission_photo_created_at", "submission_photo", ["created_at"])


def downgrade() -> None:
    op.drop_index("submission_photo_created_at", table_name="submission_photo")
    op.drop_table("submission_photo")
