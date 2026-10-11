"""Пропущенный пробел: слитное слово делится на слова справочника, и об этом сказано."""

import asyncio

from app.catalog import Catalog, CharacteristicSpec, Position
from app.checks.typos import unglue
from app.extract import LLMItems
from app.glossary import Glossary
from app.pipeline import AutofillIn, Deps, autofill
from app.search import build_bm25
from app.session import SessionStore
from tests.fakes import FakeLLM, FakeRedis

TABLE = Position(
    code="31.01.12.110-00000001",
    name="Стол письменный",
    okpd2="31.01.12.110",
    characteristics=[
        CharacteristicSpec(
            name="Материал", kind="qualitative", values=["Деревянный", "Металлический"]
        ),
        CharacteristicSpec(name="Цвет", kind="qualitative", values=["Белый", "Черный"]),
    ],
    source="test",
)
CHAIR = TABLE.model_copy(update={"code": "31.01.11.150-00000001", "name": "Стул офисный"})
CATALOG = Catalog([TABLE, CHAIR], version="test")
BM25 = build_bm25(CATALOG)


def test_glued_words_are_split_into_catalog_words() -> None:
    text, changed = unglue("столдеревянный", BM25)
    assert text == "стол деревянный" and changed == {"столдеревянный": "стол деревянный"}


def test_capital_letter_and_the_rest_of_the_text_are_kept() -> None:
    text, changed = unglue("Столписьменный белый, 2 шт", BM25)
    assert text == "Стол письменный белый, 2 шт"
    assert changed == {"Столписьменный": "Стол письменный"}


def test_three_words_without_spaces_are_split() -> None:
    text, _ = unglue("столписьменныйбелый", BM25)
    assert text == "стол письменный белый"


def test_known_and_foreign_words_are_not_touched() -> None:
    for text in (
        "стол письменный",  # слова справочника
        "столешница",  # не раскладывается на слова справочника без остатка
        "Престижлюкс",  # бренд: частей в справочнике нет
        "BAS-36N-Li стол",  # обозначение модели
        "стол-трансформер",  # через дефис пишут намеренно
    ):
        assert unglue(text, BM25) == (text, {})


def test_search_and_fields_use_the_split_text_and_the_supplier_is_told() -> None:
    deps = Deps(
        catalog=CATALOG,
        bm25=BM25,
        vectors=None,
        llm=FakeLLM([LLMItems(items=[])] * 3),  # type: ignore[arg-type]
        glossary=Glossary([]),
        sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.2,
    )
    out = asyncio.run(autofill(AutofillIn(name="столписьменный деревянный"), deps))
    assert out.name == "стол письменный деревянный"
    assert [c.code for c in out.ktru.candidates][:1] == [TABLE.code]
    fields = {c.name: c.value for c in out.characteristics}
    assert fields["Материал"] == "Деревянный"
    [issue] = [i for i in out.issues if i.kind == "typo" and i.field == "name"]
    assert "«столписьменный» — «стол письменный»" in issue.message
    assert issue.fix is not None and issue.fix.value == "стол письменный деревянный"
