"""Фото товара в заявке стенда портала: файл поставщика хранится на сервере вместе с заявкой.

Стенд, не модуль: на настоящем портале фото хранит сам портал. Тип определяется по содержимому, не
по имени файла (расширение врёт): JPEG, PNG, WebP. Фото загружается сразу при выборе, до заявки,
поэтому у брошенных форм остаются сироты: при каждой загрузке удаляются фото старше суток, на
которые не ссылается ни одна заявка (`submission.card->>'photo_id'`).
"""

from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.demo.submissions import MemorySubmissionRepo

MAX_PHOTO_BYTES = 5 * 1024 * 1024
ORPHAN_AGE = timedelta(days=7)

_PNG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
_JPEG = bytes([0xFF, 0xD8, 0xFF])


def sniff_mime(data: bytes) -> str | None:
    """Тип картинки по сигнатуре файла; не JPEG, PNG или WebP — None."""
    if data.startswith(_JPEG):
        return "image/jpeg"
    if data.startswith(_PNG):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class PhotoRepo(Protocol):
    async def add(self, mime: str, data: bytes) -> int: ...
    async def get(self, id: int) -> tuple[str, bytes] | None: ...
    async def cleanup(self, max_age: timedelta) -> int: ...


class MemoryPhotoRepo:
    """Хранилище в памяти для тестов: ссылки заявок берёт из своей очереди заявок."""

    def __init__(self, submissions: MemorySubmissionRepo | None = None) -> None:
        self.submissions = submissions
        self.items: dict[int, tuple[str, bytes]] = {}
        self.created: dict[int, datetime] = {}
        self._next = 1

    async def add(self, mime: str, data: bytes) -> int:
        photo_id, self._next = self._next, self._next + 1
        self.items[photo_id] = (mime, data)
        self.created[photo_id] = datetime.now(UTC)
        return photo_id

    async def get(self, id: int) -> tuple[str, bytes] | None:
        return self.items.get(id)

    async def cleanup(self, max_age: timedelta) -> int:
        used = (
            {s.card.photo_id for s in await self.submissions.list()} if self.submissions else set()
        )
        edge = datetime.now(UTC) - max_age
        old = [i for i, at in self.created.items() if at < edge and i not in used]
        for i in old:
            del self.items[i], self.created[i]
        return len(old)


class PgPhotoRepo:
    """Таблица submission_photo (миграция e5f6a7b8c9d0)."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def add(self, mime: str, data: bytes) -> int:
        async with self.engine.begin() as conn:
            r = await conn.execute(
                text(
                    "INSERT INTO submission_photo (mime, bytes) VALUES (:mime, :data) RETURNING id"
                ),
                {"mime": mime, "data": data},
            )
            return int(r.scalar_one())

    async def get(self, id: int) -> tuple[str, bytes] | None:
        async with self.engine.connect() as conn:
            r = await conn.execute(
                text("SELECT mime, bytes FROM submission_photo WHERE id = :id"), {"id": id}
            )
            row = r.one_or_none()
            return (row.mime, bytes(row.bytes)) if row else None

    async def cleanup(self, max_age: timedelta) -> int:
        async with self.engine.begin() as conn:
            r = await conn.execute(
                # Граница считается здесь: в `now() - $1` asyncpg выводит тип параметра как дату.
                text(
                    "DELETE FROM submission_photo p WHERE p.created_at < :edge "
                    "AND NOT EXISTS (SELECT 1 FROM submission s "
                    "WHERE s.card->>'photo_id' = p.id::text)"
                ),
                {"edge": datetime.now(UTC) - max_age},
            )
            return r.rowcount or 0
