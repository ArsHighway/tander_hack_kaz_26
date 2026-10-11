import asyncio

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import get_deps
from app.batch import STATUS_TEXT, BatchRow, BatchRowResult
from app.main import create_app
from tests.fakes import FakeRedis
from tests.test_batch import _deps, _done, _job


@pytest.mark.parametrize("status", list(STATUS_TEXT))
def test_filter_finds_each_status_beyond_first_thousand(status: str) -> None:
    deps = _deps()
    store = deps.batches
    assert store is not None
    job = _job(deps, 2000)
    result = BatchRowResult.model_validate({**_done(1500).model_dump(), "status": status})
    asyncio.run(store.save_result(job, result))
    asyncio.run(store.save_result(job, _done(1)))
    app = create_app()
    app.dependency_overrides[get_deps] = lambda: deps
    response = TestClient(app).get(f"/api/v1/batch/{job}/results?status={status}&limit=1")
    assert response.status_code == 200
    page = response.json()
    assert page["matched"] == (2 if status == "ready" else 1)
    if status == "ready":
        response = TestClient(app).get(
            f"/api/v1/batch/{job}/results?status=ready&limit=1&after_row=1"
        )
        page = response.json()
    assert [r["row"] for r in page["rows"]] == [1500]
    assert page["next_after_row"] is None


def test_pages_follow_file_order_without_duplicates_and_retries() -> None:
    async def scenario() -> None:
        store = _deps().batches
        assert store is not None
        job = await store.create("f.csv", [])
        # Sparse, out-of-order completion, including a repeated delivery.
        await store.redis.hset(f"batch:{job}:meta", "total", 20)
        for n in (20, 2, 10, 2):
            await store.save_result(job, _done(n))
        first = await store.result_page(job, limit=2)
        assert first is not None and first.matched == 3
        assert [r.row for r in first.rows] == [2, 10]
        assert first.next_after_row == 10
        last = await store.result_page(job, limit=2, after_row=10)
        assert last is not None and [r.row for r in last.rows] == [20]
        assert last.next_after_row is None
        empty = await store.result_page(job, status="failed")
        assert empty is not None and empty.matched == 0 and not empty.rows
        assert empty.next_after_row is None

    asyncio.run(scenario())


@pytest.mark.parametrize("marked_indexed", [False, True])
def test_old_batches_without_complete_index_can_be_filtered(marked_indexed: bool) -> None:
    async def scenario() -> None:
        redis = FakeRedis()
        store = _deps(redis).batches
        assert store is not None
        job = await store.create("f.csv", [BatchRow(row=i, name="x") for i in range(1, 2001)])
        if not marked_indexed:
            await redis.hdel(f"batch:{job}:meta", "result_index")
        for n in (1999, 1500):
            await redis.hset(f"batch:{job}:results", str(n), _done(n).model_dump_json())
        await redis.hset(f"batch:{job}:counts", "ready", 2)
        first = await store.result_page(job, status="ready", limit=1)
        assert first is not None and [r.row for r in first.rows] == [1500]
        assert first.next_after_row == 1500
        second = await store.result_page(job, status="ready", limit=1, after_row=1500)
        assert second is not None and [r.row for r in second.rows] == [1999]
        assert second.next_after_row is None

    asyncio.run(scenario())


def test_filter_after_cancel_includes_late_results_and_preserves_ttl() -> None:
    async def scenario() -> None:
        redis = FakeRedis()
        store = _deps(redis).batches
        assert store is not None
        job = await store.create("f.csv", [BatchRow(row=i, name="x") for i in (1, 2)])
        await store.save_result(job, _done(1))
        await store.cancel(job)
        for key in redis.ttl:
            redis.ttl[key] = 100
        late = _done(2).model_copy(update={"status": "failed"})
        await store.save_result(job, late)
        await store.save_result(job, _done(2))  # First result wins, including its index.
        page = await store.result_page(job, status="failed")
        assert page is not None and page.matched == 1
        assert [r.row for r in page.rows] == [2]
        assert set(redis.ttl.values()) == {100}
        state = await store.state(job, limit=0)
        assert state is not None and state.status == "cancelled"

    asyncio.run(scenario())


@pytest.mark.parametrize("query", ["status=unknown", "limit=0", "limit=201", "after_row=-1"])
def test_filter_api_rejects_invalid_parameters(query: str) -> None:
    app = create_app()
    app.dependency_overrides[get_deps] = lambda: _deps()
    assert TestClient(app).get(f"/api/v1/batch/missing/results?{query}").status_code == 422


def test_filter_missing_batch_is_404() -> None:
    app = create_app()
    app.dependency_overrides[get_deps] = lambda: _deps()
    assert TestClient(app).get("/api/v1/batch/missing/results").status_code == 404
