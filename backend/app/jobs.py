"""Сообщения очереди и состояние фоновых задач (хранится в Redis с TTL)."""

from datetime import UTC, datetime
from typing import Literal

from faststream.rabbit import RabbitQueue
from pydantic import BaseModel, Field

# Очередь durable, её объявляют и API, и воркер: сообщение не теряется, пока воркер лежит.
PING_QUEUE = RabbitQueue("jobs.ping", durable=True)
BATCH_QUEUE = RabbitQueue("jobs.batch", durable=True)
QUEUES = (PING_QUEUE, BATCH_QUEUE)


def job_key(job_id: str) -> str:
    return f"job:{job_id}"


def utcnow() -> datetime:
    return datetime.now(UTC)


class PingJob(BaseModel):
    """Проверочная задача: проходит API → RabbitMQ → воркер → Redis."""

    job_id: str
    created_at: datetime = Field(default_factory=utcnow)


class JobState(BaseModel):
    job_id: str
    status: Literal["queued", "done", "failed"]
    detail: str = ""
    updated_at: datetime = Field(default_factory=utcnow)


class JobAccepted(BaseModel):
    job_id: str
    status_url: str


class BatchJob(BaseModel):
    """Порция пакета СТЕ: строки с позиции `start` по `stop` (не включая) списка строк файла.

    Строки и результаты лежат в Redis (`app.batch.BatchStore`), в сообщении — только id и
    границы: файл не гоняется через брокер. Порции одного файла разбирают разные воркеры.
    `stop=None` — до конца файла (так выглядят сообщения до разбиения на порции).
    """

    job_id: str
    start: int = Field(default=0, ge=0)
    stop: int | None = Field(default=None, ge=0)
    created_at: datetime = Field(default_factory=utcnow)


def batch_chunks(job_id: str, total: int, size: int) -> list[BatchJob]:
    """Порции по `size` строк; пустой файл — без сообщений (пакет сразу «готов»)."""
    return [
        BatchJob(job_id=job_id, start=s, stop=min(s + size, total)) for s in range(0, total, size)
    ]
