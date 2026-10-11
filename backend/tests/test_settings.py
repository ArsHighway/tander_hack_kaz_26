"""Выключатель модели в Redis: общий для процессов, с кэшем 5 с, переживает пересоздание Deps."""

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.settings import get_store
from app.api.v1 import get_deps
from app.catalog import Catalog
from app.glossary import Glossary
from app.main import create_app
from app.pipeline import Deps
from app.search import build_bm25
from app.session import SessionStore
from app.settings_store import CACHE_S, LLM_ENABLED_KEY, SettingsStore
from tests.fakes import FakeLLM, FakeRedis

CATALOG = Catalog.load([Path(__file__).parent.parent / "app" / "catalog" / "demo" / "demo.json"])


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class DownRedis(FakeRedis):
    """Redis, который можно «отключить»: любое обращение — ConnectionError."""

    def __init__(self) -> None:
        super().__init__()
        self.down = False

    async def get(self, key: str) -> str | None:
        if self.down:
            raise ConnectionError("redis недоступен")
        return await super().get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        if self.down:
            raise ConnectionError("redis недоступен")
        await super().set(key, value, ex)


def _store(redis: FakeRedis, clock: Callable[[], float], *, default: bool = True) -> SettingsStore:
    return SettingsStore(redis, default, clock=clock)  # type: ignore[arg-type]


def _deps(*, enabled: bool = True) -> Deps:
    return Deps(
        catalog=CATALOG,
        bm25=build_bm25(CATALOG),
        vectors=None,
        llm=FakeLLM(),  # type: ignore[arg-type]
        glossary=Glossary([]),
        sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.2,
        llm_enabled=enabled,
    )


def test_model_is_on_by_default() -> None:
    store = _store(FakeRedis(), Clock())
    assert asyncio.run(store.llm_enabled()) is True


def test_default_comes_from_config_when_there_is_no_key() -> None:
    store = _store(FakeRedis(), Clock(), default=False)
    assert asyncio.run(store.llm_enabled()) is False


def test_value_survives_a_new_deps_and_a_new_process() -> None:
    redis, clock = FakeRedis(), Clock()
    asyncio.run(_store(redis, clock).set_llm_enabled(False))
    assert redis.data[LLM_ENABLED_KEY] == "0"

    deps = _deps()  # процесс перезапущен: стартовое значение — из конфига, «включено»
    asyncio.run(_store(redis, clock).refresh(deps))
    assert deps.llm_enabled is False


def test_other_process_sees_the_change_within_the_cache_window() -> None:
    redis, clock = FakeRedis(), Clock()
    api, worker = _store(redis, clock), _store(redis, clock)
    worker_deps = _deps()
    asyncio.run(worker.refresh(worker_deps))
    assert worker_deps.llm_enabled is True

    asyncio.run(api.set_llm_enabled(False))
    asyncio.run(worker.refresh(worker_deps))
    assert worker_deps.llm_enabled is True  # кэш ещё свежий

    clock.now += CACHE_S - 0.1
    asyncio.run(worker.refresh(worker_deps))
    assert worker_deps.llm_enabled is True

    clock.now += 0.2
    asyncio.run(worker.refresh(worker_deps))
    assert worker_deps.llm_enabled is False


def test_write_is_visible_at_once_in_the_writing_process() -> None:
    redis, clock = FakeRedis(), Clock()
    store = _store(redis, clock)
    assert asyncio.run(store.llm_enabled()) is True
    asyncio.run(store.set_llm_enabled(False))
    assert asyncio.run(store.llm_enabled()) is False
    asyncio.run(store.set_llm_enabled(True))
    assert asyncio.run(store.llm_enabled()) is True


def test_redis_down_keeps_the_last_known_value(caplog: pytest.LogCaptureFixture) -> None:
    redis, clock = DownRedis(), Clock()
    store = _store(redis, clock)
    asyncio.run(store.set_llm_enabled(False))
    redis.down = True
    clock.now += CACHE_S + 1
    with caplog.at_level(logging.WARNING, logger="app.settings_store"):
        assert asyncio.run(store.llm_enabled()) is False
    assert "не прочитана" in caplog.text


def test_redis_down_from_the_start_gives_the_default() -> None:
    redis, clock = DownRedis(), Clock()
    redis.down = True
    assert asyncio.run(_store(redis, clock).llm_enabled()) is True
    assert asyncio.run(_store(redis, clock, default=False).llm_enabled()) is False


def test_dead_redis_is_not_asked_on_every_call() -> None:
    redis, clock = DownRedis(), Clock()
    redis.down = True
    asked = 0
    original = redis.get

    async def counting(key: str) -> str | None:
        nonlocal asked
        asked += 1
        return await original(key)

    redis.get = counting  # type: ignore[method-assign]
    store = _store(redis, clock)
    for _ in range(5):
        asyncio.run(store.llm_enabled())
    assert asked == 1
    clock.now += CACHE_S + 0.1
    asyncio.run(store.llm_enabled())
    assert asked == 2


def test_garbage_in_the_key_falls_back_to_the_default() -> None:
    redis = FakeRedis()
    redis.data[LLM_ENABLED_KEY] = "maybe"
    assert asyncio.run(_store(redis, Clock()).llm_enabled()) is True
    assert asyncio.run(_store(redis, Clock(), default=False).llm_enabled()) is False


def test_write_to_dead_redis_raises() -> None:
    redis = DownRedis()
    redis.down = True
    with pytest.raises(ConnectionError):
        asyncio.run(_store(redis, Clock()).set_llm_enabled(False))


def _client(deps: Deps, store: SettingsStore) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_deps] = lambda: deps
    app.dependency_overrides[get_store] = lambda: store
    return TestClient(app)


def test_api_put_is_stored_and_seen_by_a_second_process() -> None:
    redis, clock = FakeRedis(), Clock()
    api_deps, worker_deps = _deps(), _deps()
    client = _client(api_deps, _store(redis, clock))
    assert client.get("/api/v1/settings").json()["llm_enabled"] is True

    got = client.put("/api/v1/settings", json={"llm_enabled": False})
    assert got.status_code == 200 and got.json()["llm_enabled"] is False
    assert api_deps.llm_enabled is False

    clock.now += CACHE_S + 0.1
    asyncio.run(_store(redis, clock).refresh(worker_deps))
    assert worker_deps.llm_enabled is False


def test_api_get_reflects_a_change_made_elsewhere() -> None:
    redis, clock = FakeRedis(), Clock()
    deps = _deps()
    client = _client(deps, _store(redis, clock))
    assert client.get("/api/v1/settings").json()["llm_enabled"] is True

    asyncio.run(_store(redis, clock).set_llm_enabled(False))
    clock.now += CACHE_S + 0.1
    assert client.get("/api/v1/settings").json()["llm_enabled"] is False
    assert deps.llm_enabled is False


def test_api_get_does_not_fail_when_redis_is_down() -> None:
    redis, clock = DownRedis(), Clock()
    redis.down = True
    client = _client(_deps(), _store(redis, clock))
    got = client.get("/api/v1/settings")
    assert got.status_code == 200 and got.json()["llm_enabled"] is True


def test_api_put_with_dead_redis_says_so_and_changes_nothing() -> None:
    redis, clock = DownRedis(), Clock()
    redis.down = True
    deps = _deps()
    client = _client(deps, _store(redis, clock))
    got = client.put("/api/v1/settings", json={"llm_enabled": False})
    assert got.status_code == 503
    assert deps.llm_enabled is True


def test_api_put_takes_nothing_but_the_switch() -> None:
    client = _client(_deps(), _store(FakeRedis(), Clock()))
    assert client.put("/api/v1/settings", json={}).status_code == 422
    assert client.put("/api/v1/settings", json={"llm_enabled": "yes"}).status_code == 422


def test_worker_takes_the_switch_before_each_chunk() -> None:
    from app.batch import BatchStore
    from app.jobs import BatchJob
    from app.worker import handle_batch

    redis, clock = FakeRedis(), Clock()
    asyncio.run(_store(redis, clock).set_llm_enabled(False))  # поставил модератор в API
    deps = _deps()
    deps.batches = BatchStore(redis, ttl_s=60)  # type: ignore[arg-type]
    worker_store = _store(redis, clock)  # пакета в Redis нет: порция — «не найден или истёк»
    asyncio.run(handle_batch(BatchJob(job_id="gone"), deps, worker_store))
    assert deps.llm_enabled is False


def test_get_deps_refreshes_the_switch_for_every_endpoint() -> None:
    """Настоящий get_deps: запрос к API видит значение, которое поставили в другом процессе."""
    redis, clock = FakeRedis(), Clock()
    deps = _deps()
    app = create_app()
    app.state.deps = deps
    app.state.settings_store = _store(redis, clock)
    client = TestClient(app)
    client.get("/api/v1/ktru/none")
    assert deps.llm_enabled is True

    asyncio.run(_store(redis, clock).set_llm_enabled(False))
    clock.now += CACHE_S + 0.1
    client.get("/api/v1/ktru/none")
    assert deps.llm_enabled is False
