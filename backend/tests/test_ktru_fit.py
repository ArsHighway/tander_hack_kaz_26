"""Подгон значений поставщика под КТРУ: единицы, метки подряд, перечень, диапазоны."""

import asyncio

from fastapi.testclient import TestClient

from app.api.v1 import get_deps
from app.catalog import Catalog, CharacteristicSpec, NumRange, Position
from app.extract import LLMItems, extract_rules
from app.glossary import Glossary
from app.ingest.units import normalize_unit
from app.ktru_align import align_ktru
from app.main import create_app
from app.pipeline import AutofillIn, Deps, autofill
from app.search import build_bm25
from app.session import SessionStore
from app.values import fit_value, list_form
from tests.fakes import FakeLLM, FakeRedis

JURY = "Высота: 3 метра Ширина: 20 метров Глубина: 100 метров Материал: глина"
KTRU_CODE = "32.50.50.190-00000001"
CATEGORY_CODE = "01.02.11.05.04.01"

WIDTH = CharacteristicSpec(
    name="Габаритная ширина",
    kind="quantitative",
    unit="мм",
    required=True,
    values=[">=370 ... <=600"],
)
LENGTH = CharacteristicSpec(
    name="Габаритная длина",
    kind="quantitative",
    unit="мм",
    required=True,
    values=[">=400 ... <=500", ">=500 ... <=600", ">600 ... <=800", ">=900 ... <=1500"],
)
MATERIAL = CharacteristicSpec(
    name="Материал изделия",
    kind="qualitative",
    required=False,
    values=["Дерево", "Металл", "Пластик", "Нержавеющая сталь", "Комбинированный"],
)
WHEELS = CharacteristicSpec(
    name="Наличие колес",
    kind="qualitative",
    required=True,
    values=["Да", "Нет"],
)
DRAWERS = CharacteristicSpec(
    name="Наличие ящиков", kind="qualitative", required=True, values=["Да", "Нет"]
)


def _category() -> Position:
    return Position(
        code=CATEGORY_CODE,
        name="Тумбочка прикроватная",
        okpd2="32.50.50.190",
        kpgz=CATEGORY_CODE,
        characteristics=[
            CharacteristicSpec(
                name="Высота",
                kind="quantitative",
                unit="мм",
                plausible=NumRange(min=200, max=1200),
                ktru=[KTRU_CODE],
            ),
            CharacteristicSpec(name="Ширина", kind="quantitative", unit="мм", ktru=[KTRU_CODE]),
            CharacteristicSpec(
                name="Глубина",
                kind="quantitative",
                unit="мм",
                plausible=NumRange(min=200, max=1200),
                ktru=[KTRU_CODE],
            ),
            CharacteristicSpec(name="Материал", kind="qualitative", ktru=[KTRU_CODE]),
        ],
        source="test",
    )


def _ktru() -> Position:
    return Position(
        code=KTRU_CODE,
        name="Тумбочка прикроватная",
        okpd2="32.50.50.190",
        characteristics=[WIDTH, LENGTH, MATERIAL, WHEELS, DRAWERS],
        source="test",
    )


def _deps() -> Deps:
    catalog = Catalog([_category()], version="jury")
    return Deps(
        catalog=catalog,
        bm25=build_bm25(catalog),
        vectors=None,
        llm=FakeLLM([LLMItems(items=[])] * 8),  # type: ignore[arg-type]
        glossary=Glossary([]),
        sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.0,
        ktru=Catalog([_ktru()], version="jury-ktru"),
    )


def test_metre_forms_convert_to_millimetres() -> None:
    assert normalize_unit("метра") == "м"
    assert normalize_unit("метры") == "м"
    assert normalize_unit("сантиметра") == "см"
    assert normalize_unit("литра") == "л"
    assert normalize_unit("килограмма") == "кг"
    height = CharacteristicSpec(name="Высота", kind="quantitative", unit="мм")
    fit = fit_value("3", "метра", height)
    assert (fit.status, fit.value, fit.unit) == ("normalized", "3000", "мм")


def test_glued_labels_are_split_before_each_known_name() -> None:
    specs = _category().characteristics + _ktru().characteristics
    found = {
        item.name: (item.raw_value, item.raw_unit)
        for item in extract_rules("Тумбочка", JURY, specs)
    }
    assert found["Высота"] == ("3", "м")
    assert found["Ширина"][0] == "20"
    assert found["Глубина"][0] == "100"
    assert found["Материал"][0] == "глина"


def test_word_form_follows_ktru_in_both_directions_and_stops_when_ambiguous() -> None:
    assert list_form("глина", ["Глиняный", "Деревянный"]) == "Глиняный"
    assert list_form("металл", ["Металлический"]) == "Металлический"
    assert list_form("дерево", ["Деревянный"]) == "Деревянный"
    assert list_form("кожа", ["Кожаный"]) == "Кожаный"
    assert list_form("глина", ["Глиняный", "Глиняная"]) is None
    clay = align_ktru("глина", None, MATERIAL)
    assert clay.value == "глина"
    assert clay.ktru_fit == "not_in_list"
    assert clay.status == "conflict"


def test_boundary_falls_into_the_inclusive_side() -> None:
    spec = CharacteristicSpec(
        name="Габаритная длина",
        kind="quantitative",
        unit="мм",
        values=[">=400 ... <=500", ">500 ... <=600"],
    )
    fit = align_ktru("500", "мм", spec)
    assert (fit.value, fit.ktru_fit, fit.ktru_match) == ("500", "in_range", ">=400 ... <=500")
    above = align_ktru("501", "мм", spec)
    assert above.ktru_match == ">500 ... <=600"


def test_open_bucket_keeps_the_number_and_the_plausibility_warning() -> None:
    spec = CharacteristicSpec(
        name="Высота тумбы",
        kind="quantitative",
        unit="см",
        values=[">=100"],
        plausible=NumRange(max=120),
    )
    fitted = align_ktru("300", "см", spec)
    assert fitted.value == "300"
    assert fitted.ktru_fit == "in_range"
    assert fitted.ktru_match == ">=100"
    assert fitted.note is not None and "необычно" in fitted.note


def test_value_outside_every_ktru_bucket_offers_a_smaller_unit() -> None:
    fitted = align_ktru("20000", "мм", WIDTH)
    assert fitted.value == "20000"
    assert fitted.ktru_fit == "out_of_range"
    assert fitted.note is not None
    assert "370" in fitted.note and "600" in fitted.note
    assert fitted.fix_value == "200"


def test_jury_request_keeps_supplier_values_and_names_ktru_gaps() -> None:
    out = asyncio.run(autofill(AutofillIn(name="Тумбочка", description=JURY), _deps()))
    chars = {item.name: item for item in [*out.characteristics, *out.extra]}
    width = chars["Габаритная ширина"]
    assert width.value == "20000"
    assert width.unit == "мм"
    assert width.origin == "ktru"
    assert width.required is True
    assert width.ktru_fit == "out_of_range"
    assert "Ширина" not in chars
    material = chars["Материал изделия"]
    assert material.value == "глина"
    assert material.ktru_fit == "not_in_list"
    assert material.status == "conflict"
    assert chars["Наличие колес"].value is None
    assert chars["Наличие колес"].required is True
    assert chars["Наличие колес"].note == "нет в тексте — укажите"
    assert chars["Высота"].origin == "portal"
    assert chars["Высота"].value == "3000"
    assert chars["Глубина"].value == "100000"
    messages = " ".join(issue.message for issue in out.issues)
    assert "глина" in messages
    assert "вне всех диапазонов" in messages
    assert all(issue.field != "photo" for issue in out.issues)


def test_jury_request_through_the_api() -> None:
    deps = _deps()
    app = create_app()
    app.dependency_overrides[get_deps] = lambda: deps
    client = TestClient(app)
    body = client.post("/api/v1/autofill", json={"name": "Тумбочка", "description": JURY}).json()
    names = {item["name"] for item in body["characteristics"]}
    assert "Габаритная ширина" in names
    width = next(item for item in body["characteristics"] if item["name"] == "Габаритная ширина")
    assert width["value"] == "20000"
    assert width["ktru_fit"] == "out_of_range"
    material = next(item for item in body["characteristics"] if item["name"] == "Материал изделия")
    assert material["value"] == "глина"
    assert material["ktru_fit"] == "not_in_list"


def test_bucket_answer_does_not_become_the_value() -> None:
    answered = align_ktru("650", "мм", LENGTH)
    assert answered.value == "650"
    assert answered.ktru_match == ">600 ... <=800"


def test_nothing_is_invented_for_an_empty_list_of_one() -> None:
    only = CharacteristicSpec(name="Материал изделия", kind="qualitative", values=["Дерево"])
    fitted = align_ktru("глина", None, only)
    assert fitted.value == "глина"
    assert fitted.ktru_fit == "not_in_list"
