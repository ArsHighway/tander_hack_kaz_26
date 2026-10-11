"""Приёмка потока D: модель выключена — запросов к ней нет ни на одном пути; «глина» остаётся
«глиной» и с моделью, и без неё."""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import get_deps
from app.batch import BatchRow, process_row
from app.catalog import Catalog
from app.extract import LLMItem, LLMItems
from app.glossary import Glossary
from app.main import create_app
from app.pipeline import AutofillIn, Deps, autofill
from app.search import build_bm25
from app.session import SessionStore
from tests.fakes import FakeLLM, FakeRedis

DEMO = Catalog.load([Path(__file__).parent.parent / "app" / "catalog" / "demo" / "demo.json"])
BAGS = Catalog.load([Path(__file__).parent / "fixtures" / "fake" / "ktru.json"])


def _deps(catalog: Catalog, llm: FakeLLM, *, enabled: bool) -> Deps:
    return Deps(
        catalog=catalog,
        bm25=build_bm25(catalog),
        vectors=None,
        llm=llm,  # type: ignore[arg-type]
        glossary=Glossary([]),
        sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.2,
        llm_enabled=enabled,
    )


def _client(deps: Deps) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_deps] = lambda: deps
    return TestClient(app)


def test_switched_off_model_gets_no_chat_request_on_any_path() -> None:
    llm = FakeLLM()  # любой chat — AssertionError, а вызовы ещё и считаются
    deps = _deps(DEMO, llm, enabled=False)
    client = _client(deps)

    # /autofill: позиция определена сразу.
    decided = client.post("/api/v1/autofill", json={"name": "Перчатки смотровые нитриловые"})
    assert decided.status_code == 200 and decided.json()["llm"] == "off"
    assert decided.json()["ktru"]["final_code"]

    # /autofill с вопросом и /answer: ответ ведёт к определённой позиции.
    asked = client.post("/api/v1/autofill", json={"name": "Перчатки"})
    body = asked.json()
    question = body["ktru"]["question"]
    assert question is not None
    answer = client.post(
        f"/api/v1/autofill/{body['session_id']}/answer",
        json={
            "characteristic": question["characteristic"],
            "value": question["options"][0]["value"],
        },
    )
    assert answer.status_code == 200 and answer.json()["llm"] == "off"

    # /check: проверка карточки модели не касается.
    card = {
        "name": "Перчатки смотровые нитриловые",
        "category_code": decided.json()["ktru"]["final_code"],
        "characteristics": [{"name": "Размер", "value": "M"}],
    }
    assert client.post("/api/v1/check", json=card).status_code == 200

    # Строка пакета идёт тем же конвейером.
    row = BatchRow(row=1, name="Перчатки смотровые нитриловые", description="размер M")
    assert asyncio.run(process_row(row, deps)).status in ("ready", "needs_answer", "needs_fix")

    assert llm.calls == []


def test_same_paths_ask_the_model_when_it_is_on() -> None:
    # Контроль теста выше: с включённой моделью её спрашивают, значит счёт вызовов живой.
    llm = FakeLLM([LLMItems(items=[]) for _ in range(4)])
    deps = _deps(DEMO, llm, enabled=True)
    out = asyncio.run(autofill(AutofillIn(name="Перчатки смотровые нитриловые"), deps))
    assert out.llm == "on"
    assert len(llm.calls) >= 1


@pytest.mark.parametrize("enabled", [False, True], ids=["без модели", "с моделью"])
def test_clay_stays_clay_and_the_model_cannot_replace_it(enabled: bool) -> None:
    # Модель «исправляет» материал на значение перечня; цитата настоящая, но «ПНД» в ней нет.
    replace = LLMItems(
        items=[LLMItem(name="Материал", value="ПНД", unit=None, quote="Материал: глина")]
    )
    llm = FakeLLM([replace, replace, replace] if enabled else [])
    deps = _deps(BAGS, llm, enabled=enabled)
    inp = AutofillIn(name="Мешки для мусора 60 л, 20 шт", description="Материал: глина")
    out = asyncio.run(autofill(inp, deps))
    material = next(c for c in out.characteristics if c.name == "Материал")
    assert material.value == "глина"
    assert material.status == "conflict"
    assert material.method == "rule"  # слово поставщика, а не ответ модели
    if not enabled:
        assert llm.calls == []
