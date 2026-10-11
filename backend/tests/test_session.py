import asyncio

from app.narrowing import Known
from app.session import Session, SessionStore
from tests.fakes import FakeRedis


def test_session_roundtrip_with_ttl() -> None:
    redis = FakeRedis()
    store = SessionStore(redis, ttl_s=900)  # type: ignore[arg-type]
    s = Session.new(name="Бумага", description="А4")
    s.known["Формат"] = Known(value="A4", source="text")
    asyncio.run(store.save(s))
    loaded = asyncio.run(store.load(s.id))
    assert loaded == s
    assert redis.ttl[f"autofill:{s.id}"] == 900


def test_unknown_session_is_none() -> None:
    store = SessionStore(FakeRedis(), ttl_s=900)  # type: ignore[arg-type]
    assert asyncio.run(store.load("nope")) is None


def test_new_sessions_get_distinct_ids() -> None:
    assert Session.new(name="a", description="").id != Session.new(name="a", description="").id


def test_memory_store_keeps_answers() -> None:
    # Хранилище в памяти для оценки без Redis: тот же интерфейс, что у SessionStore поверх Redis.
    from app.session import MemoryKV

    store = SessionStore(MemoryKV(), ttl_s=900)  # type: ignore[arg-type]
    s = Session.new(name="Бумага", description="")
    asyncio.run(store.save(s))
    asyncio.run(store.add_answer(s.id, "Цветность", "Белая"))
    loaded = asyncio.run(store.load(s.id))
    assert loaded is not None and loaded.known["Цветность"].value == "Белая"
