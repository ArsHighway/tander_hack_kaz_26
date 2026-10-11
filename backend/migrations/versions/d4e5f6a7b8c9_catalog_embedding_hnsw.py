"""catalog embedding hnsw

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-04 16:00:00

"""

from collections.abc import Sequence

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Размерность bge-m3 (EMBED_MODEL по умолчанию). Колонка `vec` без размерности (векторы разных
# моделей живут в одной таблице), а HNSW строится только по типу с размерностью — поэтому индекс
# по выражению `vec::vector(1024)` и только для строк этой размерности. Запрос в
# app/search/vectors.py пишет то же выражение и то же условие, иначе планировщик индекс не возьмёт.
DIMS = 1024
INDEX = f"catalog_embedding_vec_hnsw_{DIMS}"


def upgrade() -> None:
    # Поиск — по косинусу (оператор <=> в app/search/vectors.py), значит vector_cosine_ops.
    # m и ef_construction — значения pgvector по умолчанию; замер полноты и задержки — docs/scaling.md.
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {INDEX} ON catalog_embedding "
        f"USING hnsw ((vec::vector({DIMS})) vector_cosine_ops) "
        f"WITH (m = 16, ef_construction = 64) "
        f"WHERE vector_dims(vec) = {DIMS}"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX}")
