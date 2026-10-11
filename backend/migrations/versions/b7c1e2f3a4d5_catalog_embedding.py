"""catalog embedding

Revision ID: b7c1e2f3a4d5
Revises: 2c657db43cfc
Create Date: 2026-10-01 22:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "b7c1e2f3a4d5"
down_revision: str | Sequence[str] | None = "2c657db43cfc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Векторы позиций справочника. Модель и версия справочника — в ключе: векторы от другой модели
    # или от старого справочника не используются (урок Перми: запрос и индекс от разных моделей).
    op.create_table(
        "catalog_embedding",
        sa.Column("code", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("catalog_version", sa.Text, nullable=False),
        sa.Column("vec", Vector(), nullable=False),
        sa.PrimaryKeyConstraint("code", "model", "catalog_version", name="catalog_embedding_pk"),
    )


def downgrade() -> None:
    op.drop_table("catalog_embedding")
