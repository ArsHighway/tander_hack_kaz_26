"""Проверки готовности зависимостей: каждая с таймаутом, сбой одной не роняет остальные."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import text

from app.resources import Resources

Probe = Callable[[], Awaitable[str]]


@dataclass(frozen=True)
class Check:
    name: str
    required: bool
    probe: Probe


class DependencyStatus(BaseModel):
    name: str
    ok: bool
    required: bool
    detail: str
    latency_ms: float


class Readiness(BaseModel):
    status: Literal["ok", "degraded", "fail"]
    dependencies: list[DependencyStatus]


async def run_check(check: Check, timeout: float) -> DependencyStatus:
    started = time.perf_counter()
    try:
        detail = await asyncio.wait_for(check.probe(), timeout)
        ok = True
    except TimeoutError:
        detail, ok = f"нет ответа за {timeout:g} с", False
    except Exception as exc:  # noqa: BLE001 - любой сбой зависимости означает «не готова»
        detail, ok = f"{type(exc).__name__}: {exc}", False
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    return DependencyStatus(
        name=check.name, ok=ok, required=check.required, detail=detail, latency_ms=latency_ms
    )


def summarize(statuses: list[DependencyStatus]) -> Readiness:
    status: Literal["ok", "degraded", "fail"]
    if all(s.ok for s in statuses):
        status = "ok"
    elif all(s.ok for s in statuses if s.required):
        status = "degraded"
    else:
        status = "fail"
    return Readiness(status=status, dependencies=statuses)


async def check_all(checks: list[Check], timeout: float) -> Readiness:
    statuses = await asyncio.gather(*(run_check(c, timeout) for c in checks))
    return summarize(list(statuses))


def build_checks(res: Resources) -> list[Check]:
    settings = res.settings

    async def postgres() -> str:
        async with res.engine.connect() as conn:
            version = (
                await conn.execute(
                    text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                )
            ).scalar()
        if version is None:
            raise RuntimeError("расширение pgvector не установлено: выполните alembic upgrade head")
        return f"pgvector {version}"

    async def redis() -> str:
        await res.redis.ping()
        return "PONG"

    async def rabbitmq() -> str:
        broker = await res.ensure_broker()
        if not await broker.ping(timeout=settings.health_timeout_s):
            raise RuntimeError("брокер не отвечает на ping")
        return "соединение есть"

    # Чат и эмбеддинги проверяются раздельно: это два сервера llama.cpp (`llm` и `embed`), один
    # может быть не поднят без другого.
    async def llm() -> str:
        where = f"{settings.llm_provider} {settings.llm_base_url}"
        if settings.llm_chat_model in await res.llm.missing_models():
            raise RuntimeError(f"{where}: нет модели {settings.llm_chat_model}")
        return f"{where}: {settings.llm_chat_model}"

    async def embeddings() -> str:
        if settings.embed_model in await res.llm.missing_models():
            raise RuntimeError(f"нет модели {settings.embed_model}")
        return settings.embed_model

    return [
        Check("postgres", required=True, probe=postgres),
        Check("redis", required=True, probe=redis),
        Check("rabbitmq", required=True, probe=rabbitmq),
        Check("llm", required=settings.llm_required, probe=llm),
        Check("embeddings", required=False, probe=embeddings),
    ]
