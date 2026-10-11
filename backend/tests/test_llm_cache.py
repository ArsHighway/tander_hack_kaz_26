"""Кэш ответов модели в Redis: тот же текст поставщика не ждёт LLM второй раз."""

import asyncio

from redis.exceptions import ConnectionError as RedisConnectionError

from app.extract import LLMItem, LLMItems
from app.llm.cache import CachedChat
from tests.fakes import FakeLLM, FakeRedis

REPLY = LLMItems(items=[LLMItem(name="Класс", value="A", unit=None, quote="класс A")])
ASK = [{"role": "user", "content": "Бумага, класс A"}]
OTHER = [{"role": "user", "content": "Бумага, класс B"}]


def _chat(cached: CachedChat, messages: list) -> LLMItems:
    result = asyncio.run(cached.chat(messages, schema=LLMItems))
    assert isinstance(result.parsed, LLMItems)
    return result.parsed


def test_same_request_is_answered_from_cache() -> None:
    llm = FakeLLM([REPLY])
    cached = CachedChat(llm, FakeRedis(), ttl_s=60)  # type: ignore[arg-type]
    assert _chat(cached, ASK) == REPLY
    assert _chat(cached, ASK) == REPLY
    assert len(llm.calls) == 1


def test_different_request_goes_to_the_model() -> None:
    llm = FakeLLM([REPLY, LLMItems(items=[])])
    cached = CachedChat(llm, FakeRedis(), ttl_s=60)  # type: ignore[arg-type]
    _chat(cached, ASK)
    assert _chat(cached, OTHER) == LLMItems(items=[])
    assert len(llm.calls) == 2


class DownRedis(FakeRedis):
    async def get(self, key: str) -> str | None:
        raise RedisConnectionError("нет связи")

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        raise RedisConnectionError("нет связи")


def test_redis_outage_bypasses_the_cache() -> None:
    llm = FakeLLM([REPLY, REPLY])
    cached = CachedChat(llm, DownRedis(), ttl_s=60)  # type: ignore[arg-type]
    assert _chat(cached, ASK) == REPLY
    assert _chat(cached, ASK) == REPLY
    assert len(llm.calls) == 2


def test_broken_cache_entry_is_a_miss() -> None:
    redis = FakeRedis()
    llm = FakeLLM([REPLY, REPLY])
    cached = CachedChat(llm, redis, ttl_s=60)  # type: ignore[arg-type]
    _chat(cached, ASK)
    for key in list(redis.data):
        redis.data[key] = "{не json"
    assert _chat(cached, ASK) == REPLY
    assert len(llm.calls) == 2


def test_zero_ttl_disables_the_cache() -> None:
    llm = FakeLLM([REPLY, REPLY])
    cached = CachedChat(llm, FakeRedis(), ttl_s=0)  # type: ignore[arg-type]
    _chat(cached, ASK)
    _chat(cached, ASK)
    assert len(llm.calls) == 2
