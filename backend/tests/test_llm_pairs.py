"""Первый шаг: модель делит текст на пары «название: значение», раскладывает их код."""

import asyncio

from app.catalog import Catalog, CharacteristicSpec, Position
from app.extract import LLMItems
from app.extract.llm_pairs import Pair, Pairs, accept_pairs, split_pairs, structured_text
from app.glossary import Glossary
from app.pipeline import AutofillIn, Deps, autofill
from app.search import build_bm25
from app.session import SessionStore
from tests.fakes import FakeLLM, FakeRedis

NAME = "Стул офисный Престиж"
TEXT = "Каркас металлический, обивка из ткани, цвет чёрный. Нагрузка до 120 кг. Звоните!"


def _accept(*pairs: Pair) -> list[tuple[str, str]]:
    got = accept_pairs(Pairs(pairs=list(pairs)), NAME, TEXT)
    return [(p.name, p.value) for p in got]


def test_pair_with_a_verbatim_quote_and_value_is_accepted() -> None:
    assert _accept(Pair(name="Цвет", value="чёрный", quote="цвет чёрный")) == [("Цвет", "чёрный")]


def test_model_may_name_a_property_that_has_no_label() -> None:
    pair = Pair(name="Материал каркаса", value="металлический", quote="Каркас металлический")
    assert _accept(pair) == [("Материал каркаса", "металлический")]


def test_quote_that_is_not_in_the_text_is_rejected() -> None:
    assert _accept(Pair(name="Цвет", value="белый", quote="цвет белый")) == []


def test_value_that_is_not_in_the_quote_is_rejected() -> None:
    assert _accept(Pair(name="Цвет", value="антрацит", quote="цвет чёрный")) == []


def test_repeated_and_empty_pairs_are_dropped() -> None:
    got = _accept(
        Pair(name="Цвет", value="чёрный", quote="цвет чёрный"),
        Pair(name="цвет", value="чёрный", quote="цвет чёрный"),
        Pair(name="!!", value="чёрный", quote="цвет чёрный"),
        Pair(name="Нагрузка", value="", quote="Нагрузка до 120 кг"),
    )
    assert got == [("Цвет", "чёрный")]


def test_structured_text_keeps_what_the_model_did_not_split() -> None:
    pairs = accept_pairs(
        Pairs(pairs=[Pair(name="Вес", value="5 кг", quote="Вес 5 кг")]),
        "Стул",
        "Вес 5 кг | Гарантия два года | Цвет синий",
    )
    text = structured_text("Вес 5 кг | Гарантия два года | Цвет синий", pairs)
    assert text.splitlines() == ["Вес: 5 кг", "Гарантия два года", "Цвет синий"]


def test_short_text_is_not_sent_to_the_model() -> None:
    llm = FakeLLM()
    assert asyncio.run(split_pairs("Стул", "", llm)) == []  # type: ignore[arg-type]
    assert llm.calls == []


CHAIR = Position(
    code="31.01.11.150-00000001",
    name="Стул офисный",
    okpd2="31.01.11.150",
    characteristics=[
        CharacteristicSpec(
            name="Материал каркаса", kind="qualitative", values=["Металл", "Дерево"]
        ),
        CharacteristicSpec(name="Цвет", kind="qualitative"),
    ],
    source="test",
)


def test_pairs_are_distributed_by_code_ktru_field_then_new_characteristic() -> None:
    """Имя пары есть в позиции — значение идёт в её поле и подгоняется к перечню; имени нет ни в
    позиции, ни на портале — новая характеристика для модератора."""
    reply = Pairs(
        pairs=[
            Pair(name="Материал каркаса", value="металлический", quote="Каркас металлический"),
            Pair(name="Цвет", value="чёрный", quote="цвет чёрный"),
            Pair(name="Допустимая нагрузка", value="120 кг", quote="Нагрузка до 120 кг"),
        ]
    )
    cat = Catalog([CHAIR], version="test")
    deps = Deps(
        catalog=cat,
        bm25=build_bm25(cat),
        vectors=None,
        llm=FakeLLM([reply, LLMItems(items=[])]),  # type: ignore[arg-type]
        glossary=Glossary([]),
        sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.2,
        structure=True,
    )
    out = asyncio.run(autofill(AutofillIn(name="Стул офисный", description=TEXT), deps))
    assert out.description.splitlines()[:3] == [
        "Материал каркаса: металлический",
        "Цвет: чёрный",
        "Допустимая нагрузка: 120 кг",
    ]
    assert "Звоните!" in out.description  # неразобранное осталось как было
    fields = {c.name: c.value for c in out.characteristics}
    assert fields["Материал каркаса"] == "Металл" and fields["Цвет"] == "чёрный"
    new = {c.name: (c.value, c.unit, c.origin) for c in out.extra}
    assert new["Допустимая нагрузка"] == ("120", "кг", "new")


def test_number_is_taken_whole_and_with_its_unit_from_the_quote() -> None:
    text = "Вес 1010.00000 г | Высота упаковки 200 мм | Кол-во скоростей 1 шт"
    reply = Pairs(
        pairs=[
            Pair(name="Вес", value="1010", quote="Вес 1010.00000 г"),
            Pair(name="Высота упаковки", value="200", quote="Высота упаковки 200 мм"),
            Pair(name="Кол-во скоростей", value="1 шт", quote="Кол-во скоростей 1 шт"),
        ]
    )
    got = {p.name: p.value for p in accept_pairs(reply, "Отвертка", text)}
    assert got == {
        "Вес": "1010.00000 г",
        "Высота упаковки": "200 мм",
        "Кол-во скоростей": "1 шт",
    }


def test_number_cut_together_with_the_quote_is_read_from_the_text() -> None:
    text = "Вес 1010.00000 г | Цвет синий"
    reply = Pairs(pairs=[Pair(name="Вес", value="1010", quote="Вес 1010")])
    [pair] = accept_pairs(reply, "Отвертка", text)
    assert (pair.value, pair.source.text) == ("1010.00000 г", "Вес 1010.00000 г")
    assert structured_text(text, [pair]).splitlines() == ["Вес: 1010.00000 г", "Цвет синий"]
