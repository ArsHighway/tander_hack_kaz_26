from pathlib import Path

from fastapi.testclient import TestClient

from app.api.v1 import get_deps
from app.catalog import Catalog
from app.extract import Extracted, LLMItem, LLMItems
from app.glossary import Glossary
from app.llm import LLMError
from app.main import create_app
from app.narrowing import NOTHING_FITS
from app.pipeline import Deps, _known_from_text
from app.schemas import Span
from app.search import build_bm25
from app.session import SessionStore
from tests.fakes import FakeLLM, FakeRedis

FAKE = Path(__file__).parent / "fixtures" / "fake"
CATALOG = Catalog.load([FAKE / "ktru.json"])
WHITE = "17.12.14.129-00000101"
COLOR = "17.12.14.129-00000102"

NAME = "Бумага офисная Туринский ЦБЗ А4 500л"
DESCRIPTION = "\n".join(
    [
        "Масса бумага площадью 1 м2, г 80",
        "Толщина, мкм 104",
        "Белизна с ООВ по ISO, % 98",
        "Количество листов в пачке А4 500",
    ]
)


def _client(llm: FakeLLM) -> TestClient:
    deps = Deps(
        catalog=CATALOG,
        bm25=build_bm25(CATALOG),
        vectors=None,
        llm=llm,  # type: ignore[arg-type]
        glossary=Glossary([]),
        sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.2,
    )
    app = create_app()
    app.dependency_overrides[get_deps] = lambda: deps
    return TestClient(app)  # без with: lifespan не запускается, инфраструктура не нужна


def _empty_llm() -> FakeLLM:
    return FakeLLM([LLMItems(items=[])])


def _chars(body: dict) -> dict[str, dict]:
    return {c["name"]: c for c in body["characteristics"]}


def test_paper_needs_one_question_then_gets_final_code() -> None:
    client = _client(_empty_llm())
    r = client.post("/api/v1/autofill", json={"name": NAME, "description": DESCRIPTION})
    assert r.status_code == 200
    body = r.json()
    assert body["ktru"]["remaining"] == 2
    question = body["ktru"]["question"]
    assert question is not None
    real = [o for o in question["options"] if o["value"] != NOTHING_FITS]
    option = min(real, key=lambda o: o["remaining"])
    r2 = client.post(
        f"/api/v1/autofill/{body['session_id']}/answer",
        json={"characteristic": question["characteristic"], "value": option["value"]},
    )
    assert r2.status_code == 200
    final = r2.json()["ktru"]
    assert final["remaining"] == 1
    assert final["final_code"] in (WHITE, COLOR)


def test_values_carry_their_source_in_text() -> None:
    body = (
        _client(_empty_llm())
        .post("/api/v1/autofill", json={"name": NAME, "description": DESCRIPTION})
        .json()
    )
    mass = _chars(body)["Масса бумаги площадью 1 м2"]
    assert (mass["value"], mass["status"]) == (">=80 ... <90", "normalized")
    at = DESCRIPTION.index(", г 80") + len(", г ")
    assert mass["source"] == {"field": "description", "start": at, "end": at + 2, "text": "80"}


def test_value_that_fits_no_candidate_does_not_narrow() -> None:
    # «Белизна 98 %» не входит в диапазон белой бумаги — это конфликт, а не повод выбрать цветную.
    body = (
        _client(_empty_llm())
        .post("/api/v1/autofill", json={"name": NAME, "description": DESCRIPTION})
        .json()
    )
    assert body["ktru"]["remaining"] == 2


def test_name_only_still_fills_what_it_can() -> None:
    body = (
        _client(_empty_llm())
        .post("/api/v1/autofill", json={"name": "Бумага офисная А4 500 листов"})
        .json()
    )
    chars = _chars(body)
    assert chars["Формат"]["value"] == "A4"
    assert chars["Количество листов в пачке"]["value"] == "500"
    assert body["ktru"]["question"] is not None or body["ktru"]["final_code"] is not None


def test_out_of_catalog_is_an_honest_refusal() -> None:
    llm = FakeLLM([])
    body = _client(llm).post("/api/v1/autofill", json={"name": "Услуги по уборке помещений"}).json()
    assert body["ktru"]["candidates"] == []
    assert body["ktru"]["final_code"] is None
    assert body["ktru"]["steps"][0].startswith("Категория не найдена")
    assert body["characteristics"] == []
    assert llm.calls == []


def test_llm_outage_degrades_but_rules_still_work() -> None:
    # Модель спрашивают, когда код определён: берём наименование, которое его определяет
    body = (
        _client(FakeLLM([LLMError("нет связи")]))
        .post("/api/v1/autofill", json={"name": "Бумага офисная белая А4, белизна 160 по CIE"})
        .json()
    )
    assert "llm" in body["degraded"]
    assert _chars(body)["Формат"]["value"] == "A4"


def test_llm_value_is_marked_as_llm() -> None:
    # Число без метки («160 по CIE») правила не берут — его находит модель (с меткой «белизна 160 по
    # CIE» берут правила: часть наименования начинается с полного имени характеристики).
    reply = LLMItems(
        items=[LLMItem(name="Белизна по CIE", value="160", unit="%", quote="160 по CIE")]
    )
    body = (
        _client(FakeLLM([reply]))
        .post("/api/v1/autofill", json={"name": "Бумага офисная белая А4 белизна, 160 по CIE"})
        .json()
    )
    whiteness = _chars(body)["Белизна по CIE"]
    assert (whiteness["value"], whiteness["method"]) == ("160", "llm")


def test_not_found_characteristics_are_listed_empty() -> None:
    body = _client(_empty_llm()).post("/api/v1/autofill", json={"name": "Бумага офисная А4"}).json()
    missing = [c for c in body["characteristics"] if c["status"] == "not_found"]
    assert missing
    assert all(c["value"] is None for c in missing)


def test_autofill_returns_constraints_of_the_target_position() -> None:
    # Перечень и диапазон — у выбранной позиции, а не объединение всего справочника.
    body = (
        _client(_empty_llm())
        .post("/api/v1/autofill", json={"name": NAME, "description": DESCRIPTION})
        .json()
    )
    code = body["ktru"]["final_code"] or body["ktru"]["candidates"][0]["code"]
    position = CATALOG.get(code)
    assert position is not None
    got = _chars(body)
    assert got["Формат"]["value"] == "A4"
    assert got["Формат"]["status"] in ("filled", "normalized")
    for spec in position.characteristics:
        char = got[spec.name]
        assert char["values"] == spec.values
        if spec.range is None:
            assert char["range"] is None
        else:
            assert char["range"]["min"] == spec.range.min
            assert char["range"]["max"] == spec.range.max
        kept = {"name", "value", "unit", "source", "status", "required", "values", "range"}
        assert kept <= set(char)


def test_unknown_session_is_404() -> None:
    r = _client(_empty_llm()).post(
        "/api/v1/autofill/nope/answer", json={"characteristic": "Формат", "value": "A4"}
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "сессия заполнения не найдена или истекла"


def test_empty_name_is_rejected() -> None:
    assert _client(_empty_llm()).post("/api/v1/autofill", json={"name": ""}).status_code == 422


def test_check_live_card_needs_fix() -> None:
    card = {
        "name": NAME,
        "ktru_code": COLOR,
        "characteristics": [
            {"name": "Формат", "value": "А4"},
            {"name": "Интенсивность цвета", "value": "Белый"},
            {"name": "Толщина, мкм", "value": "104"},
        ],
    }
    body = _client(_empty_llm()).post("/api/v1/check", json=card).json()
    assert body["verdict"] == "needs_fix"
    assert any(i["kind"] == "not_in_list" for i in body["issues"])


def test_ktru_position_and_404() -> None:
    client = _client(_empty_llm())
    r = client.get(f"/api/v1/ktru/{WHITE}")
    assert r.status_code == 200
    assert r.json()["name"] == "Бумага для офисной техники белая"
    missing = client.get("/api/v1/ktru/00.00")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "позиция не найдена"


def test_ktru_code_is_found_in_the_ktru_reference_too() -> None:
    # модератор видит код КТРУ без названия, а `GET /ktru/{код КТРУ}` — 404:
    # позиции второго этапа лежат в `deps.ktru`, а не в справочнике категорий.
    from tests.test_ktru_reference import DESK_KTRU, _deps

    deps = _deps()
    app = create_app()
    app.dependency_overrides[get_deps] = lambda: deps
    client = TestClient(app)
    ktru = client.get(f"/api/v1/ktru/{DESK_KTRU}")
    assert (ktru.status_code, ktru.json()["name"]) == (200, "Стол письменный")
    category = client.get("/api/v1/ktru/01.16.03.01.01")  # категория — по-прежнему из справочника
    assert (category.status_code, category.json()["name"]) == (200, "Столы письменные")
    assert client.get("/api/v1/ktru/31.01.10.000-99999999").status_code == 404


def test_openapi_lists_module_contract() -> None:
    paths = _client(_empty_llm()).get("/api/openapi.json").json()["paths"]
    for p in ("/api/v1/autofill", "/api/v1/autofill/{session_id}/answer", "/api/v1/check"):
        assert p in paths


def test_openapi_snapshot_is_current() -> None:
    # Фронт берёт типы из frontend/src/api/openapi.json: схема в файле обязана совпадать с кодом.
    from app.tools.export_openapi import DEFAULT, schema_text

    assert DEFAULT.read_text(encoding="utf-8") == schema_text(), (
        "схема устарела: uv run python -m app.tools.export_openapi && npm run gen:api"
    )


def test_llm_value_does_not_narrow_the_choice() -> None:
    # значение от модели — предложение, а не факт; позицию по нему не выбираем.
    positions = [CATALOG.get(WHITE), CATALOG.get(COLOR)]

    def found(method: str) -> Extracted:
        span = Span(field="name", start=0, end=3, text="160")
        return Extracted(
            name="Белизна по CIE", raw_value="160", raw_unit="%", source=span, method=method
        )

    assert "Белизна по CIE" in _known_from_text([found("rule")], positions)  # type: ignore[arg-type]
    assert _known_from_text([found("llm")], positions) == {}  # type: ignore[arg-type]


def test_check_unknown_ktru_code_is_reported() -> None:
    # неизвестный код молча проверялся как «без позиции».
    card = {"name": NAME, "ktru_code": "00.00.00.000-99999999", "characteristics": []}
    body = _client(_empty_llm()).post("/api/v1/check", json=card).json()
    found = [i for i in body["issues"] if i["field"] == "ktru_code"]
    # по выдуманному коду карточку не опубликовать, это блок.
    assert [(i["kind"], i["severity"]) for i in found] == [("not_in_list", "block")]


def test_check_without_photo_does_not_warn() -> None:
    # Фото необязательно: ни отсутствие, ни наличие снимка не даёт замечания по полю photo.
    client = _client(_empty_llm())
    chars = [{"name": "Формат", "value": "А4"}]
    card = {"name": NAME, "ktru_code": WHITE, "characteristics": chars}
    bare = client.post("/api/v1/check", json=card).json()
    assert [i for i in bare["issues"] if i["field"] == "photo"] == []
    with_photo = client.post("/api/v1/check", json={**card, "photo_id": 1}).json()
    assert [i for i in with_photo["issues"] if i["field"] == "photo"] == []
    assert bare["verdict"] == with_photo["verdict"]
    # В /autofill фото нет совсем (вход — текст): замечание о нём — только у /check.
    filled = client.post("/api/v1/autofill", json={"name": NAME, "description": DESCRIPTION}).json()
    assert [i for i in filled["issues"] if i["field"] == "photo"] == []
