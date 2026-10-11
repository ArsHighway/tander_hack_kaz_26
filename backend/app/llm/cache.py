"""Кэш ответов модели в Redis: тот же запрос (модель, схема, сообщения) — без второго вызова LLM.

Поставщик правит наименование и жмёт «Заполнить» снова, пакет повторяет одинаковые строки: ответ
модели на тот же текст не меняется (temperature 0), а ждать его — секунды. Кэш — только ускорение:
сбой Redis или битая запись означают обычный вызов модели.
"""

import hashlib
import json
import logging
from typing import Protocol

from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.llm.base import ChatResult, Message

log = logging.getLogger(__name__)

KEY_PREFIX = "llmcache:"


class ChatModel(Protocol):
    chat_model: str

    async def chat(
        self, messages: list[Message], *, schema: type[BaseModel], temperature: float = 0.0
    ) -> ChatResult: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class CachedChat:
    def __init__(self, inner: ChatModel, redis: Redis, ttl_s: int) -> None:
        self.inner = inner
        self.redis = redis
        self.ttl_s = ttl_s

    def _key(self, messages: list[Message], schema: type[BaseModel], temperature: float) -> str:
        raw = json.dumps(
            [self.inner.chat_model, schema.__name__, temperature, messages],
            ensure_ascii=False,
            sort_keys=True,
        )
        return KEY_PREFIX + hashlib.sha256(raw.encode()).hexdigest()

    async def _get(self, key: str, schema: type[BaseModel]) -> BaseModel | None:
        try:
            raw = await self.redis.get(key)
            return schema.model_validate_json(raw) if raw else None
        except (RedisError, ValidationError) as exc:
            log.warning("кэш ответов модели пропущен: %s", exc)
            return None

    async def chat(
        self, messages: list[Message], *, schema: type[BaseModel], temperature: float = 0.0
    ) -> ChatResult:
        if self.ttl_s <= 0:
            return await self.inner.chat(messages, schema=schema, temperature=temperature)
        key = self._key(messages, schema, temperature)
        hit = await self._get(key, schema)
        if hit is not None:
            text = hit.model_dump_json()
            return ChatResult(text=text, model=self.inner.chat_model, latency_ms=0.0, parsed=hit)
        result = await self.inner.chat(messages, schema=schema, temperature=temperature)
        if isinstance(result.parsed, schema):
            try:
                await self.redis.set(key, result.parsed.model_dump_json(), ex=self.ttl_s)
            except RedisError as exc:
                log.warning("ответ модели не записан в кэш: %s", exc)
        return result

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self.inner.embed(texts)
