"""Фото товара в заявке: загрузка, выдача, типы и размер, хранение с заявкой, уборка сирот."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.demo import get_photos, get_submissions
from app.demo import MemorySubmissionRepo
from app.demo.photos import MAX_PHOTO_BYTES, MemoryPhotoRepo, sniff_mime
from app.main import create_app
from tests.test_submissions import CARD
from tests.test_submissions import _client as submissions_client

PNG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + b"png body"
JPEG = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b"jpeg body"
WEBP = b"RIFF" + bytes([0x10, 0, 0, 0]) + b"WEBPVP8 webp body"
GIF = b"GIF89a gif body"


def test_sniff_by_content_not_by_name() -> None:
    assert sniff_mime(PNG) == "image/png"
    assert sniff_mime(JPEG) == "image/jpeg"
    assert sniff_mime(WEBP) == "image/webp"
    assert sniff_mime(GIF) is None
    assert sniff_mime(b"just text") is None


def _client() -> tuple[TestClient, MemoryPhotoRepo, MemorySubmissionRepo]:
    base = submissions_client()
    app = base.app
    submissions = app.dependency_overrides[get_submissions]()
    photos = app.dependency_overrides[get_photos]()
    return TestClient(app), photos, submissions


def _upload(client: TestClient, data: bytes, name: str = "photo.png"):
    return client.post("/api/demo/photos", files={"file": (name, data, "application/octet-stream")})


def test_upload_and_read_back() -> None:
    client, _, _ = _client()
    r = _upload(client, PNG)
    assert r.status_code == 200
    photo_id = r.json()["photo_id"]
    back = client.get(f"/api/demo/photos/{photo_id}")
    assert back.status_code == 200
    assert back.headers["content-type"] == "image/png"
    assert back.content == PNG


def test_wrong_type_is_415_whatever_the_name() -> None:
    client, _, _ = _client()
    assert _upload(client, GIF, "photo.gif").status_code == 415
    assert _upload(client, b"just text", "photo.jpg").status_code == 415


def test_too_large_is_413() -> None:
    client, _, _ = _client()
    big = JPEG + b"0" * MAX_PHOTO_BYTES
    assert _upload(client, big, "big.jpg").status_code == 413


def test_unknown_photo_is_404() -> None:
    client, _, _ = _client()
    assert client.get("/api/demo/photos/999").status_code == 404


def test_submission_keeps_photo_id_and_resubmit_updates_it() -> None:
    client, _, _ = _client()
    first = _upload(client, PNG).json()["photo_id"]
    sub = client.post("/api/demo/submissions", json={"card": {**CARD, "photo_id": first}}).json()
    assert sub["card"]["photo_id"] == first
    client.post(
        f"/api/demo/submissions/{sub['id']}/decision", json={"decision": "return", "note": "фото"}
    )
    second = _upload(client, JPEG, "new.jpg").json()["photo_id"]
    again = client.put(
        f"/api/demo/submissions/{sub['id']}", json={"card": {**CARD, "photo_id": second}}
    )
    assert again.json()["card"]["photo_id"] == second


def test_old_submission_without_photo_reads() -> None:
    client, _, _ = _client()
    sub = client.post("/api/demo/submissions", json={"card": CARD}).json()
    assert sub["card"]["photo_id"] is None


async def _aged(photos: MemoryPhotoRepo, data: bytes, days: float) -> int:
    photo_id = await photos.add("image/png", data)
    photos.created[photo_id] = datetime.now(UTC) - timedelta(days=days)
    return photo_id


def test_cleanup_removes_only_old_orphans() -> None:
    import asyncio

    from app.demo import SubmissionIn
    from app.schemas import CardIn

    submissions = MemorySubmissionRepo()
    photos = MemoryPhotoRepo(submissions)

    async def scenario() -> tuple[bool, bool, bool]:
        orphan = await _aged(photos, PNG, 2)
        used = await _aged(photos, PNG, 2)
        fresh = await _aged(photos, PNG, 0.1)
        await submissions.add(SubmissionIn(card=CardIn.model_validate({**CARD, "photo_id": used})))
        await photos.cleanup(timedelta(days=1))
        return (
            await photos.get(orphan) is None,
            await photos.get(used) is not None,
            await photos.get(fresh) is not None,
        )

    assert asyncio.run(scenario()) == (True, True, True)


def test_upload_route_cleans_old_orphans() -> None:
    import asyncio

    client, photos, _ = _client()
    orphan = asyncio.run(_aged(photos, PNG, 8))
    # Фото, которое поставщик загрузил и ещё не отправил (форма открыта долго), живёт неделю.
    pending = asyncio.run(_aged(photos, PNG, 2))
    assert _upload(client, PNG).status_code == 200
    assert client.get(f"/api/demo/photos/{orphan}").status_code == 404
    assert client.get(f"/api/demo/photos/{pending}").status_code == 200


def test_photo_routes_are_in_the_contract() -> None:
    paths = create_app().openapi()["paths"]
    assert {"/api/demo/photos", "/api/demo/photos/{photo_id}"} <= set(paths)


def test_submission_with_a_missing_photo_is_refused() -> None:
    client, _, _ = _client()
    card = {**CARD, "photo_id": 999999}
    r = client.post("/api/demo/submissions", json={"card": card, "accepted": []})
    assert r.status_code == 422 and "фото" in r.json()["detail"].lower()


# Таблицы как в миграциях, со своими последовательностями: счётчики стенда не трогаем.
PG_TABLES = (
    "CREATE TABLE {s}.submission (id serial PRIMARY KEY, created_at timestamptz NOT NULL "
    "DEFAULT now(), card jsonb NOT NULL, accepted jsonb NOT NULL DEFAULT '[]', status text "
    "NOT NULL DEFAULT 'pending', decision_note text)",
    "CREATE TABLE {s}.submission_photo (id serial PRIMARY KEY, mime text NOT NULL, bytes bytea "
    "NOT NULL, created_at timestamptz NOT NULL DEFAULT now())",
)


def test_pg_cleanup_keeps_photos_of_submissions() -> None:
    """Очистка в Postgres (своя схема, данные стенда не трогаются); без базы — пропуск."""
    import asyncio
    import json
    import uuid

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.config import Settings
    from app.demo.photos import PgPhotoRepo

    url = Settings().database_url
    schema = f"test_photos_{uuid.uuid4().hex[:8]}"

    async def prepare() -> bool:
        admin = create_async_engine(url)
        try:
            async with admin.begin() as conn:
                await conn.execute(text(f"CREATE SCHEMA {schema}"))
                for ddl in PG_TABLES:
                    await conn.execute(text(ddl.format(s=schema)))
            return True
        except Exception:  # noqa: BLE001 - база стенда не поднята: проверять нечего
            return False
        finally:
            await admin.dispose()

    async def scenario() -> tuple[bool, bool, bool]:
        engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
        repo = PgPhotoRepo(engine)
        try:
            orphan = await repo.add("image/png", PNG)
            used = await repo.add("image/png", PNG)
            fresh = await repo.add("image/png", PNG)
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE submission_photo SET created_at = now() - interval '10 days' "
                        "WHERE id IN (:a, :b)"
                    ),
                    {"a": orphan, "b": used},
                )
                await conn.execute(
                    text("INSERT INTO submission (card) VALUES (CAST(:card AS jsonb))"),
                    {"card": json.dumps({"name": "x", "photo_id": used})},
                )
            await repo.cleanup(timedelta(days=7))
            return (
                await repo.get(orphan) is None,
                await repo.get(used) is not None,
                await repo.get(fresh) is not None,
            )
        finally:
            await engine.dispose()
            admin = create_async_engine(url)
            async with admin.begin() as conn:
                await conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
            await admin.dispose()

    if not asyncio.run(prepare()):
        pytest.skip("Postgres стенда недоступен")
    assert asyncio.run(scenario()) == (True, True, True)
