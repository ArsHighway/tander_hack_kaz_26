"""Векторный индекс позиций справочника в pgvector (таблица catalog_embedding)."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.catalog import Position


class EmbeddingIndexEmpty(RuntimeError):
    """Индекс не построен для модели и версии справочника.

    Построить: `python -m app.tools.index_catalog`.
    """


def position_text(p: Position, limit: int = 1000) -> str:
    """Текст позиции для эмбеддинга: наименование, имена характеристик и значения."""
    parts = [p.name]
    for c in p.characteristics:
        if c.group == "general":
            continue  # перечни производителей и стран сдвинули бы поиск к чужим категориям
        parts.append(f"{c.name}: {', '.join(c.values)}" if c.values else c.name)
    return ". ".join(parts)[:limit]


def search_sql(dims: int) -> str:
    """Ближайшие по косинусу. Выражение `vec::vector(dims)` и условие `vector_dims(vec) = dims` —
    те же, что у HNSW-индекса (миграция d4e5f6a7b8c9): иначе планировщик индекс не возьмёт.
    Индекс есть для 1024 (bge-m3); у другой размерности тот же запрос идёт полным перебором."""
    dims = int(dims)  # в SQL подставляется число из len(), не текст запроса
    distance = f"vec::vector({dims}) <=> CAST(:q AS vector({dims}))"
    return (
        f"SELECT code, 1 - ({distance}) AS sim FROM catalog_embedding "
        f"WHERE model = :m AND catalog_version = :v AND vector_dims(vec) = {dims} "
        f"ORDER BY {distance} LIMIT :k"
    )


def _literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


class EmbeddingIndex:
    def __init__(self, engine: AsyncEngine, model: str, catalog_version: str) -> None:
        self.engine, self.model, self.catalog_version = engine, model, catalog_version
        self._ready = False

    async def count(self) -> int:
        async with self.engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT count(*) FROM catalog_embedding "
                    "WHERE model = :m AND catalog_version = :v"
                ),
                {"m": self.model, "v": self.catalog_version},
            )
            return int(result.scalar_one())

    async def ready(self) -> bool:
        """Есть ли векторы этой модели и версии справочника (кэшируется после первого «да»)."""
        if not self._ready:
            self._ready = await self.count() > 0
        return self._ready

    async def search(self, vector: list[float], k: int = 20) -> list[tuple[str, float]]:
        if not await self.ready():
            raise EmbeddingIndexEmpty(
                f"нет векторов для модели {self.model} и справочника {self.catalog_version}"
            )
        async with self.engine.connect() as conn:
            # Фильтр по модели и версии HNSW применяет после обхода графа: строгий итеративный
            # обход (pgvector ≥ 0.8) добирает k строк, если старые версии справочника в той же
            # таблице заняли первые места.
            await conn.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
            rows = await conn.execute(text(search_sql(len(vector))), {
                "q": _literal(vector), "m": self.model, "v": self.catalog_version, "k": k,
            })  # fmt: skip
            return [(r.code, float(r.sim)) for r in rows]

    async def replace(self, rows: list[tuple[str, list[float]]]) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM catalog_embedding WHERE model = :m AND catalog_version = :v"),
                {"m": self.model, "v": self.catalog_version},
            )
            for code, vec in rows:
                await conn.execute(
                    text(
                        "INSERT INTO catalog_embedding (code, model, catalog_version, vec) "
                        "VALUES (:c, :m, :v, CAST(:vec AS vector))"
                    ),
                    {"c": code, "m": self.model, "v": self.catalog_version, "vec": _literal(vec)},
                )
