"""«Мои заявки → Пакеты»: список загруженных пакетов, новые сверху; истёкшие пропадают из списка."""

import asyncio

from fastapi.testclient import TestClient

from app.api.v1 import get_deps
from app.batch import BATCH_INDEX_KEEP, BATCH_INDEX_KEY
from app.main import create_app
from tests.fakes import FakeRedis
from tests.test_batch import _deps, _job


def test_recent_lists_newest_first_with_date() -> None:
    deps = _deps(FakeRedis())
    store = deps.batches
    assert store is not None
    first = _job(deps, 2)
    second = _job(deps, 3)
    got = asyncio.run(store.recent())
    assert [s.job_id for s in got] == [second, first]
    assert got[0].total == 3 and got[0].filename == "cards.xlsx"
    assert all(s.created_at is not None for s in got)
    # В списке только счётчики: строк результата нет.
    assert all(s.rows == [] for s in got)


def test_expired_batch_leaves_the_list() -> None:
    redis = FakeRedis()
    deps = _deps(redis)
    store = deps.batches
    assert store is not None
    gone = _job(deps, 1)
    kept = _job(deps, 1)
    # Срок пакета вышел: Redis удалил его ключи, в индексе остался только номер.
    del redis.hashes[f"batch:{gone}:meta"]
    assert [s.job_id for s in asyncio.run(store.recent())] == [kept]
    assert gone not in redis.sorted_sets[BATCH_INDEX_KEY]


def test_index_keeps_only_the_latest() -> None:
    deps = _deps(FakeRedis())
    store = deps.batches
    assert store is not None
    for _ in range(BATCH_INDEX_KEEP + 3):
        _job(deps, 1)
    assert len(asyncio.run(store.recent(limit=BATCH_INDEX_KEEP + 10))) == BATCH_INDEX_KEEP


def test_api_lists_batches() -> None:
    deps = _deps(FakeRedis())
    job = _job(deps, 2)
    app = create_app()
    app.dependency_overrides[get_deps] = lambda: deps
    with TestClient(app) as client:
        r = client.get("/api/v1/batch")
    assert r.status_code == 200
    body = r.json()
    assert [b["job_id"] for b in body] == [job]
    assert body[0]["status"] == "queued" and body[0]["total"] == 2 and body[0]["created_at"]
