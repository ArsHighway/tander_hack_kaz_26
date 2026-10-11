"""Источник характеристики: КТРУ, шаблон портала, свойства вне шаблона («портал» и «новая»)."""

import asyncio

from app.catalog import Catalog, CharacteristicSpec, Position
from app.extract import LLMItems
from app.glossary import Glossary
from app.pipeline import AutofillIn, Deps, autofill, check
from app.schemas import CardIn, CharIn
from app.search import build_bm25
from app.session import SessionStore
from tests.fakes import FakeLLM, FakeRedis

WHITE, COLOR = "17.12.14.129-00000031", "17.12.14.129-00000017"

CATEGORY = Position(
    code="01.15.02.01",
    name="Бумага для офисной техники",
    okpd2="17.12.14.129",
    kpgz="01.15.02.01",
    characteristics=[
        CharacteristicSpec(name="Формат", kind="qualitative", values=["A4", "A3"],
                           ktru=[WHITE, COLOR]),
        CharacteristicSpec(name="Цветность", kind="qualitative", values=["Белая", "Цветная"]),
        CharacteristicSpec(name="Количество листов в пачке", kind="quantitative", unit="шт",
                           synonyms=["Листов в пачке"]),
    ],
    source="test",
)  # fmt: skip
# Другая категория портала: её имена — «как пишут на портале».
OTHER = Position(
    code="01.15.02.02",
    name="Блоки для записей",
    okpd2="17.23.13.199",
    characteristics=[CharacteristicSpec(name="Тип упаковки", kind="qualitative", values=["Пачка"])],
    source="test",
)


def ktru(code: str, name: str, color: str) -> Position:
    return Position(
        code=code,
        name=name,
        okpd2="17.12.14.129",
        characteristics=[
            CharacteristicSpec(name="Цветность", kind="qualitative", values=[color], required=True),
            CharacteristicSpec(name="Формат", kind="qualitative", values=["A4", "A3"]),
            # Только в КТРУ, в шаблоне портала её нет.
            CharacteristicSpec(name="Класс бумаги", kind="qualitative", values=["A", "B", "C"]),
        ],
        source="test",
    )


def deps() -> Deps:
    catalog = Catalog([CATEGORY, OTHER], version="test")
    return Deps(
        catalog=catalog,
        bm25=build_bm25(catalog),
        vectors=None,
        llm=FakeLLM([LLMItems(items=[])] * 10),  # type: ignore[arg-type]
        glossary=Glossary([]),
        sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.2,
        ktru=Catalog(
            [
                ktru(WHITE, "Бумага для офисной техники белая", "Белая"),
                ktru(COLOR, "Бумага для офисной техники цветная", "Цветная"),
            ],
            version="ktru-test",
        ),
    )


DESCRIPTION = (
    "Цветность: белая; Класс бумаги: B; Листов в пачке: 500\n"
    "Тип упаковки: пачка\nУпаковка с ручкой: да\nОписание: отличная бумага"
)


def run() -> object:
    return asyncio.run(
        autofill(AutofillIn(name="Бумага для офисной техники", description=DESCRIPTION), deps())
    )


def test_template_fields_are_marked_ktru_or_portal() -> None:
    out = run()
    assert (
        out.chain is not None and out.chain.ktru is not None and out.chain.ktru.final_code == WHITE
    )
    origin = {c.name: c.origin for c in out.characteristics}
    assert origin["Формат"] == origin["Цветность"] == "ktru"
    assert origin["Количество листов в пачке"] == "portal"


def test_ktru_characteristic_outside_template_is_added_with_its_value() -> None:
    out = run()
    added = {c.name: c for c in out.characteristics}["Класс бумаги"]
    assert (added.origin, added.value) == ("ktru", "B")


def test_properties_outside_template_go_to_extra() -> None:
    out = run()
    extra = [(c.name, c.value, c.origin) for c in out.extra]
    # «Листов в пачке» — синоним шаблона, «Описание» — служебная метка: не лишние.
    assert extra == [("Тип упаковки", "пачка", "portal"), ("Упаковка с ручкой", "да", "new")]
    assert all(c.source is not None and c.source.field == "description" for c in out.extra)


def card(origin: str | None) -> CardIn:
    return CardIn(
        name="Бумага для офисной техники белая",
        category_code=CATEGORY.code,
        ktru_code=WHITE,
        characteristics=[
            CharIn(name="Формат", value="A4"),
            CharIn(name="Цветность", value="Белая"),
            CharIn(name="Упаковка с ручкой", value="да", origin=origin),  # type: ignore[arg-type]
        ],
    )


def test_check_extra_property_is_info_not_warning() -> None:
    issues = check(card("new"), deps()).issues
    mine = [i for i in issues if i.field == "characteristics.Упаковка с ручкой"]
    assert [i.severity for i in mine] == ["info"]


def test_check_ktru_only_field_is_not_flagged_and_unmarked_is_still_a_warning() -> None:
    ktru_card = CardIn(
        name="Бумага для офисной техники белая",
        category_code=CATEGORY.code,
        ktru_code=WHITE,
        characteristics=[CharIn(name="Класс бумаги", value="B", origin="ktru")],
    )
    assert not [
        i for i in check(ktru_card, deps()).issues if i.field == "characteristics.Класс бумаги"
    ]
    legacy = [
        i
        for i in check(card(None), deps()).issues
        if i.field == "characteristics.Упаковка с ручкой"
    ]
    assert [i.severity for i in legacy] == ["warn"]


def test_pipe_separates_characteristics_like_in_portal_exports() -> None:
    inp = AutofillIn(
        name="Бумага для офисной техники",
        description="Цветность: белая | Листов в пачке: 500 | Тип упаковки: пачка",
    )
    out = asyncio.run(autofill(inp, deps()))
    values = {c.name: c.value for c in out.characteristics}
    assert values["Количество листов в пачке"] == "500"
    assert [c.name for c in out.extra] == ["Тип упаковки"]


def test_one_shared_word_with_own_qualifiers_is_another_property() -> None:
    from app.extract.rules import extract_rules

    spec = CharacteristicSpec(name="Обрезиненная ручка", kind="qualitative", values=["Да", "Нет"])
    found = extract_rules("Молоток", "Ручка противоскользящая: да", [spec])
    assert [e.name for e in found] == []
    same = extract_rules("Молоток", "Обрезиненная ручка: да", [spec])
    assert [(e.name, e.raw_value) for e in same] == [("Обрезиненная ручка", "да")]


def test_template_value_narrows_ktru_with_numeric_list() -> None:
    from app.card_fill import _template_for_ktru
    from app.extract import Extracted
    from app.schemas import Span

    spec = CharacteristicSpec(name="Класс одежды", kind="qualitative", values=["1", "2", "3"])
    found = Extracted(
        name="Класс одежды", raw_value="2 класс", raw_unit=None, method="rule",
        source=Span(field="name", start=0, end=7, text="2 класса"),
    )  # fmt: skip
    [got] = _template_for_ktru([found], [], [spec])
    assert (got.name, got.raw_value) == ("Класс одежды", "2")
    two = found.model_copy(update={"raw_value": "2 или 3"})
    assert _template_for_ktru([two], [], [spec]) == []
