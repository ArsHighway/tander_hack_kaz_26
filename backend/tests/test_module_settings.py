"""Выключатель модели у модератора: `/api/v1/settings` и поле `llm` в ответе мастера."""

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.settings import get_store
from app.api.v1 import get_deps
from app.catalog import Catalog
from app.extract import LLMItems
from app.glossary import Glossary
from app.main import create_app
from app.pipeline import AutofillIn, Deps, autofill
from app.search import build_bm25
from app.session import SessionStore
from app.settings_store import SettingsStore
from tests.fakes import FakeLLM, FakeRedis

CATALOG = Catalog.load([Path(__file__).parent.parent / "app" / "catalog" / "demo" / "demo.json"])
DECIDED = "Перчатки смотровые нитриловые"  # код определён сразу — модель спрашивают


def _deps(llm: FakeLLM, *, enabled: bool = True) -> Deps:
    return Deps(
        catalog=CATALOG,
        bm25=build_bm25(CATALOG),
        vectors=None,
        llm=llm,  # type: ignore[arg-type]
        glossary=Glossary([]),
        sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.2,
        llm_enabled=enabled,
    )


def test_model_switched_off_is_not_asked() -> None:
    llm = FakeLLM()  # любой вызов — AssertionError
    out = asyncio.run(autofill(AutofillIn(name=DECIDED), _deps(llm, enabled=False)))
    assert out.ktru.final_code
    assert llm.calls == []
    assert out.llm == "off"
    assert "llm" not in out.degraded  # выключена модератором — не сбой


def test_model_switched_on_is_asked() -> None:
    llm = FakeLLM([LLMItems(items=[])])
    out = asyncio.run(autofill(AutofillIn(name=DECIDED), _deps(llm)))
    assert len(llm.calls) == 1
    assert out.llm == "on"


def test_model_that_failed_is_unavailable() -> None:
    llm = FakeLLM([TimeoutError()])
    deps = _deps(llm)
    deps.llm_budget_s = 0.5
    out = asyncio.run(autofill(AutofillIn(name=DECIDED), deps))
    assert out.llm == "unavailable"


def test_moderator_switches_the_model_through_the_api() -> None:
    deps = _deps(FakeLLM())
    store = SettingsStore(FakeRedis())  # type: ignore[arg-type]
    app = create_app()
    app.dependency_overrides[get_deps] = lambda: deps
    app.dependency_overrides[get_store] = lambda: store
    client = TestClient(app)
    assert client.get("/api/v1/settings").json()["llm_enabled"] is True
    got = client.put("/api/v1/settings", json={"llm_enabled": False})
    assert got.status_code == 200 and got.json()["llm_enabled"] is False
    assert deps.llm_enabled is False
    assert client.get("/api/v1/settings").json()["llm_enabled"] is False
