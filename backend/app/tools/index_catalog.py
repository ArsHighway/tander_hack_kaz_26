"""Строит векторный индекс справочника: `python -m app.tools.index_catalog`.

Без доступной модели эмбеддингов завершается с кодом 1 и текстом причины: пустой индекс молча не
создаётся (поиск тогда честно работает по BM25 и помечает ответ `degraded: ["embeddings"]`).
"""

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import create_async_engine

from app.catalog import Catalog, Position
from app.config import get_settings
from app.llm import LLMClient, LLMInputError
from app.search.vectors import EmbeddingIndex, position_text

BATCH = 32
log = logging.getLogger(__name__)


Embed = Callable[[list[str]], Awaitable[list[list[float]]]]


async def embed_positions(
    positions: list[Position], embed: Embed, batch: int = BATCH
) -> tuple[list[tuple[str, list[float]]], list[str]]:
    """Векторы позиций пачками. Текст, на котором модель отдаёт NaN (`LLMInputError`), не роняет
    индекс: пачка кодируется по одной позиции, а такая позиция — короче (только наименование).
    Возвращает строки индекса и коды позиций, которые не закодировались совсем."""
    rows: list[tuple[str, list[float]]] = []
    skipped: list[str] = []
    for i in range(0, len(positions), batch):
        chunk = positions[i : i + batch]
        try:
            vectors = await embed([position_text(p) for p in chunk])
            rows.extend(zip((p.code for p in chunk), vectors, strict=True))
            continue
        except LLMInputError:
            pass
        for p in chunk:
            for text in (position_text(p), p.name):
                try:
                    rows.append((p.code, (await embed([text]))[0]))
                    break
                except LLMInputError:
                    continue
            else:
                skipped.append(p.code)
    return rows, skipped


async def run() -> int:
    settings = get_settings()
    catalog = Catalog.load(settings.catalog_files())
    llm = LLMClient.from_settings(settings)
    engine = create_async_engine(settings.database_url)
    try:
        positions = catalog.all()
        try:
            rows, skipped = await embed_positions(positions, llm.embed)
        except Exception as exc:  # noqa: BLE001 — любой отказ модели: индекс не строим
            print(f"эмбеддинги недоступны ({settings.embed_model}): {exc}", file=sys.stderr)
            return 1
        if not rows:
            print("ни одна позиция не закодирована: индекс не строим", file=sys.stderr)
            return 1
        if skipped:
            print(f"без вектора (только BM25): {len(skipped)} позиций: {skipped[:20]}")
        index = EmbeddingIndex(engine, llm.embed_model, catalog.version)
        await index.replace(rows)
        print(
            f"проиндексировано позиций: {len(rows)} (модель {llm.embed_model}, {catalog.version})"
        )
        return 0
    finally:
        await llm.aclose()
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(asyncio.run(run()))
