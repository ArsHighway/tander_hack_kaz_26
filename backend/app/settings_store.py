"""Настройки модуля, которые меняет модератор, — в Redis: одни для API и воркеров, живут дольше
процесса.

Конвейер читает `Deps.llm_enabled` как обычный bool; хранилище освежает его перед работой
(`refresh`) — в зависимости FastAPI и в начале порции пакета. Чтение кэшируется на 5 с: настройку
не спрашивают у Redis на каждый запрос, а изменение из другого процесса доходит не позже чем через
этот срок.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from redis.asyncio import Redis

if TYPE_CHECKING:
    from app.pipeline import Deps
    from app.resources import Resources

log = logging.getLogger(__name__)

LLM_ENABLED_KEY = "settings:llm_enabled"
CACHE_S = 5.0
# Redis, который молчит, не должен подвесить запрос: чтение и запись ждут не дольше этого.
REDIS_TIMEOUT_S = 1.0


def _parse(raw: Any) -> bool | None:
    if isinstance(raw, bytes):
        raw = raw.decode(errors="replace")
    if raw == "1":
        return True
    if raw == "0":
        return False
    return None


class SettingsStore:
    """Выключатель модели: ключа нет — значение из `LLM_ENABLED` (по умолчанию включено)."""

    def __init__(
        self,
        redis: Redis,
        default_llm_enabled: bool = True,
        *,
        cache_s: float = CACHE_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._redis = redis
        self._default = default_llm_enabled
        self._cache_s = cache_s
        self._clock = clock
        self._value = default_llm_enabled
        self._fresh_until = float("-inf")

    async def llm_enabled(self) -> bool:
        """Значение с кэшем. Redis недоступен — последнее известное, пока ничего не читали —
        значение по умолчанию; запрос из-за этого не падает."""
        now = self._clock()
        if now >= self._fresh_until:
            # Срок продлевается до чтения: параллельные запросы не ходят в Redis все разом, а
            # при сбое следующая попытка — через тот же срок, не на каждый запрос.
            self._fresh_until = now + self._cache_s
            await self._load()
        return self._value

    async def _load(self) -> None:
        try:
            raw = await asyncio.wait_for(self._redis.get(LLM_ENABLED_KEY), REDIS_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - любой сбой Redis: остаётся прежнее значение
            log.warning(
                "настройка модели не прочитана из Redis, остаётся %s: %s",
                "включена" if self._value else "выключена",
                exc,
            )
            return
        parsed = _parse(raw)
        if parsed is None and raw is not None:
            log.warning("в %s лежит %r, ожидалось 1 или 0: берём умолчание", LLM_ENABLED_KEY, raw)
        self._value = self._default if parsed is None else parsed

    async def set_llm_enabled(self, enabled: bool) -> None:
        """Записывает значение; Redis недоступен — исключение: молча «сохранить» нельзя, другие
        процессы значения не увидят."""
        await asyncio.wait_for(
            self._redis.set(LLM_ENABLED_KEY, "1" if enabled else "0"), REDIS_TIMEOUT_S
        )
        self._value = enabled
        self._fresh_until = self._clock() + self._cache_s

    async def refresh(self, deps: "Deps") -> None:
        deps.llm_enabled = await self.llm_enabled()


def make_store(resources: "Resources") -> SettingsStore:
    """Хранилище процесса: по одному на API и на воркер, Redis у них общий."""
    return SettingsStore(resources.redis, resources.settings.llm_enabled)
