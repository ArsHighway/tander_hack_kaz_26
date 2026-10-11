"""Прокси картинок портала: фото СТЕ и картинка категории, дисковый кэш, отказ портала."""

import asyncio
import time
from collections.abc import Callable
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.api.v1 import get_deps, get_images
from app.catalog import Catalog, Position
from app.main import create_app
from app.portal.images import ImageCache

JPEG = b"\xff\xd8\xff\xe0 fake jpeg"


def _cache(
    tmp_path: Path, handler: Callable[[httpx.Request], httpx.Response], timeout_s: float = 5.0
):
    calls: list[str] = []

    def counted(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(counted))
    return ImageCache(tmp_path, client, timeout_s=timeout_s), calls


def _portal(
    sku: dict | None = None, status: int = 200
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status)
        if request.url.path.endswith("/GetSku"):
            return httpx.Response(200, json=sku or {})
        return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})

    return handler


def test_sku_photo_comes_from_first_image_and_is_cached(tmp_path: Path) -> None:
    cache, calls = _cache(tmp_path, _portal({"images": [{"fileStorageId": 282710619}]}))
    assert asyncio.run(cache.sku(47453345)) == JPEG
    assert calls == [
        "/classifier/api/Sku/GetSku",
        "/newapi/api/Core/Thumbnail/282710619/300/300",
    ]
    assert asyncio.run(cache.sku(47453345)) == JPEG
    assert len(calls) == 2  # второй раз — с диска


def test_sku_without_photo_falls_back_to_category_default(tmp_path: Path) -> None:
    sku = {"images": [], "productionDirectory": {"defaultImageId": 231277051}}
    cache, calls = _cache(tmp_path, _portal(sku))
    assert asyncio.run(cache.sku(1)) == JPEG
    assert calls[-1] == "/newapi/api/Core/Thumbnail/231277051/300/300"


def test_sku_without_any_image_is_remembered(tmp_path: Path) -> None:
    cache, calls = _cache(
        tmp_path, _portal({"images": [], "productionDirectory": {"defaultImageId": None}})
    )
    assert asyncio.run(cache.sku(2)) is None
    assert asyncio.run(cache.sku(2)) is None
    assert calls == ["/classifier/api/Sku/GetSku"]  # «нет фото» запомнено


def test_portal_failure_is_not_remembered(tmp_path: Path) -> None:
    cache, calls = _cache(tmp_path, _portal(status=502))
    assert asyncio.run(cache.sku(3)) is None
    assert asyncio.run(cache.sku(3)) is None
    assert len(calls) == 2  # сбой портала — не «нет фото»: второй раз снова в сеть


def test_slow_portal_gives_up_within_timeout(tmp_path: Path) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2)
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(slow))
    cache = ImageCache(tmp_path, client, timeout_s=0.1)
    start = time.monotonic()
    assert asyncio.run(cache.sku(4)) is None
    assert time.monotonic() - start < 1.0
    assert not list(tmp_path.glob("sku-4.*"))


def test_category_image_is_cached(tmp_path: Path) -> None:
    cache, calls = _cache(tmp_path, _portal())
    assert asyncio.run(cache.category(231277051)) == JPEG
    assert asyncio.run(cache.category(231277051)) == JPEG
    assert calls == ["/newapi/api/Core/Thumbnail/231277051/300/300"]


def test_non_image_answer_is_not_an_image(tmp_path: Path) -> None:
    def html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})

    cache, _ = _cache(tmp_path, html)
    assert asyncio.run(cache.category(5)) is None


def _slow_portal(delay: float):
    """GetSku отвечает через `delay` секунд (портал 07.10.2026: 6–27 с), миниатюра — сразу."""
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/GetSku"):
            await asyncio.sleep(delay)
            return httpx.Response(200, json={"images": [{"fileStorageId": 282710619}]})
        return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


def test_soon_answers_from_disk_without_portal(tmp_path: Path) -> None:
    (tmp_path / "sku-7.jpg").write_bytes(JPEG)
    (tmp_path / "sku-8.none").write_bytes(b"")
    cache, calls = _cache(tmp_path, _portal(status=502))
    assert asyncio.run(cache.soon("sku", 7)) == JPEG
    assert asyncio.run(cache.soon("sku", 8)) is None
    assert calls == []


def test_soon_gives_up_quickly_and_finishes_in_background(tmp_path: Path) -> None:
    client, calls = _slow_portal(0.3)
    cache = ImageCache(tmp_path, client, timeout_s=5.0, wait_s=0.05)

    async def scenario() -> tuple[bytes | None, float, bytes | None]:
        start = time.monotonic()
        first = await cache.soon("sku", 9)
        waited = time.monotonic() - start
        await asyncio.sleep(0.6)  # портал ответил, фоновое скачивание положило фото на диск
        return first, waited, await cache.soon("sku", 9)

    first, waited, second = asyncio.run(scenario())
    assert first is None and waited < 0.25  # браузер не ждёт медленный портал
    assert second == JPEG and (tmp_path / "sku-9.jpg").is_file()
    assert calls == ["/classifier/api/Sku/GetSku", "/newapi/api/Core/Thumbnail/282710619/300/300"]


def test_soon_fetches_one_picture_once_at_a_time(tmp_path: Path) -> None:
    client, calls = _slow_portal(0.2)
    cache = ImageCache(tmp_path, client, timeout_s=5.0, wait_s=0.01)

    async def scenario() -> list[bytes | None]:
        answers = await asyncio.gather(*(cache.soon("sku", 10) for _ in range(5)))
        await asyncio.sleep(0.5)
        return answers

    assert asyncio.run(scenario()) == [None] * 5
    assert calls.count("/classifier/api/Sku/GetSku") == 1


def test_soon_category_in_background(tmp_path: Path) -> None:
    cache, calls = _cache(tmp_path, _portal(), timeout_s=5.0)

    async def scenario() -> bytes | None:
        await cache.soon("category", 231277051)
        await asyncio.sleep(0.05)
        return await cache.soon("category", 231277051)

    assert asyncio.run(scenario()) == JPEG
    assert calls == ["/newapi/api/Core/Thumbnail/231277051/300/300"]


class _FakeImages:
    async def soon(self, kind: str, key: int) -> bytes | None:
        known = {("sku", 1), ("category", 231277051)}
        return JPEG if (kind, key) in known else None


def _client() -> TestClient:
    from types import SimpleNamespace

    positions = [
        Position(
            code="A",
            name="Смесители",
            okpd2="",
            characteristics=[],
            source="t",
            portal_id=793362891,
            image_id=231277051,
        ),
        Position(
            code="B", name="Бланки", okpd2="", characteristics=[], source="t", portal_id=793371198
        ),
    ]
    app = create_app()
    app.dependency_overrides[get_deps] = lambda: SimpleNamespace(catalog=Catalog(positions, "t"))
    app.dependency_overrides[get_images] = lambda: _FakeImages()
    return TestClient(app)


def test_image_routes() -> None:
    client = _client()
    ok = client.get("/api/v1/images/sku/1")
    assert (
        ok.status_code == 200 and ok.headers["content-type"] == "image/jpeg" and ok.content == JPEG
    )
    assert "max-age" in ok.headers["cache-control"]
    missing = client.get("/api/v1/images/sku/2")
    assert missing.status_code == 404
    # фото может скачаться в фоне к следующему показу: 404 не кэшировать
    assert missing.headers["cache-control"] == "no-store"
    assert client.get("/api/v1/images/category/793362891").content == JPEG
    assert client.get("/api/v1/images/category/793371198").status_code == 404  # нет картинки
    assert client.get("/api/v1/images/category/1").status_code == 404  # нет категории


def test_portal_client_waits_as_long_as_the_cache_allows() -> None:
    # Умолчание httpx — 5 с: без своего таймаута GetSku (6–27 с) не скачивался бы никогда.
    from app.portal.images import portal_client

    client = portal_client(30.0)
    assert (client.timeout.connect, client.timeout.read) == (30.0, 30.0)
    asyncio.run(client.aclose())


def test_background_queue_is_bounded(tmp_path: Path) -> None:
    client, calls = _slow_portal(0.3)
    cache = ImageCache(tmp_path, client, timeout_s=5.0, wait_s=0.01, queue=2)

    async def scenario() -> tuple[list[bytes | None], int]:
        answers = [await cache.soon("sku", i) for i in (21, 22, 23)]
        queued = len(cache._inflight)
        await asyncio.sleep(0.8)
        return answers, queued

    answers, queued = asyncio.run(scenario())
    assert answers == [None, None, None]
    assert queued == 2  # третья картинка не встала в очередь: 404 сразу, в следующий показ — снова
    assert calls.count("/classifier/api/Sku/GetSku") == 2


def test_portal_is_asked_at_most_background_at_once(tmp_path: Path) -> None:
    busy = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal busy, peak
        busy += 1
        peak = max(peak, busy)
        await asyncio.sleep(0.1)
        busy -= 1
        if request.url.path.endswith("/GetSku"):
            return httpx.Response(200, json={"images": []})
        return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache = ImageCache(tmp_path, client, timeout_s=5.0, wait_s=0.01, background=2)

    async def scenario() -> None:
        for i in range(6):
            await cache.soon("sku", 30 + i)
        await asyncio.sleep(0.6)

    asyncio.run(scenario())
    assert peak == 2


def test_close_cancels_background_downloads(tmp_path: Path) -> None:
    client, _ = _slow_portal(5.0)
    cache = ImageCache(tmp_path, client, timeout_s=10.0, wait_s=0.01)

    async def scenario() -> tuple[int, bool]:
        await cache.soon("sku", 40)
        task = next(iter(cache._inflight.values()))
        await cache.close()
        return len(cache._inflight), task.cancelled()

    assert asyncio.run(scenario()) == (0, True)


def test_huge_sku_id_is_a_bad_request_not_a_crash() -> None:
    client = _client()
    assert client.get("/api/v1/images/sku/" + "9" * 300).status_code == 422
    assert client.get("/api/v1/images/category/" + "9" * 300).status_code == 422
