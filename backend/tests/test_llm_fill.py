"""Модель в извлечении характеристик: промпт задаёт правила, а нарушения ловит код.

Базовые отказы (цитаты нет в тексте, значение не из перечня, чужое имя, чужая единица) проверены в
`test_extract.py`; здесь — то, что нужно для правил «значение только от поставщика»: выдуманный цвет
при настоящей цитате, подмена смысла и единица, записанная поставщиком.
"""

import asyncio

from app.catalog import CharacteristicSpec
from app.extract import Extracted, LLMItem, LLMItems, extract_llm
from app.extract.llm_fill import SYSTEM, build_messages
from app.glossary import Glossary
from app.values import fit_value
from tests.fakes import FakeLLM

MATERIAL = CharacteristicSpec(
    name="Материал изделия",
    kind="qualitative",
    values=["Дерево", "Металл", "Пластик", "Нержавеющая сталь", "Комбинированный"],
)
COLOR = CharacteristicSpec(name="Цвет", kind="qualitative", values=["Белый", "Серый", "Чёрный"])
WIDTH = CharacteristicSpec(name="Габаритная ширина", kind="quantitative", unit="мм")
SPECS = [MATERIAL, COLOR, WIDTH]


def _extract(name: str, description: str, items: list[LLMItem]) -> list[Extracted]:
    llm = FakeLLM([LLMItems(items=items)])
    return asyncio.run(
        extract_llm(name, description, SPECS, llm, Glossary([]))  # type: ignore[arg-type]
    )


def test_invented_colour_is_rejected_even_with_a_real_quote() -> None:
    # Цитата настоящая («Тумбочка прикроватная»), а значения в ней нет: цвет придуман.
    got = _extract(
        "Тумбочка прикроватная",
        "Высота 55 см",
        [LLMItem(name="Цвет", value="Белый", unit=None, quote="Тумбочка прикроватная")],
    )
    assert got == []


def test_meaning_is_not_replaced_by_a_more_common_list_value() -> None:
    # «глина» → «Дерево»: значение из перечня и цитата настоящая, но в цитате нет «дерева».
    got = _extract(
        "Тумбочка",
        "Материал: глина",
        [LLMItem(name="Материал изделия", value="Дерево", unit=None, quote="Материал: глина")],
    )
    assert got == []


def test_value_outside_the_list_is_not_taken_even_when_the_quote_has_it() -> None:
    got = _extract(
        "Тумбочка",
        "Материал: глина",
        [LLMItem(name="Материал изделия", value="глина", unit=None, quote="Материал: глина")],
    )
    assert got == []


def test_list_value_is_accepted_when_the_text_says_the_same() -> None:
    got = _extract(
        "Тумбочка",
        "Материал: металл, цвет серый",
        [
            LLMItem(name="Материал изделия", value="Металл", unit=None, quote="Материал: металл"),
            LLMItem(name="Цвет", value="Серый", unit=None, quote="цвет серый"),
        ],
    )
    assert [(e.name, e.raw_value, e.method) for e in got] == [
        ("Материал изделия", "Металл", "llm"),
        ("Цвет", "Серый", "llm"),
    ]


def test_unit_is_taken_as_the_supplier_wrote_it() -> None:
    # Модель подписала число «мм», а в тексте метры: без единицы из цитаты вышло бы 20 мм.
    got = _extract(
        "Тумбочка",
        "Ширина: 20 метров",
        [LLMItem(name="Габаритная ширина", value="20", unit="мм", quote="Ширина: 20 метров")],
    )
    assert [(e.raw_value, e.raw_unit) for e in got] == [("20", "м")]
    assert fit_value(got[0].raw_value, got[0].raw_unit, WIDTH).value == "20000"


def test_missing_unit_is_filled_from_the_quote() -> None:
    got = _extract(
        "Тумбочка",
        "Ширина: 45 см",
        [LLMItem(name="Габаритная ширина", value="45", unit=None, quote="Ширина: 45 см")],
    )
    assert [(e.raw_value, e.raw_unit) for e in got] == [("45", "см")]
    assert fit_value(got[0].raw_value, got[0].raw_unit, WIDTH).value == "450"


def test_model_that_converted_the_number_is_rejected() -> None:
    # «20 метров» → «20000 мм»: числа 20000 в цитате нет, пересчёт — дело кода.
    got = _extract(
        "Тумбочка",
        "Ширина: 20 метров",
        [LLMItem(name="Габаритная ширина", value="20000", unit="мм", quote="Ширина: 20 метров")],
    )
    assert got == []


def test_prompt_tells_the_model_the_rules_of_the_plan() -> None:
    text = SYSTEM.lower()
    for fragment in (
        "точный фрагмент",  # значение только с цитатой
        "не включай",  # нет цитаты или смысл другой — не выводить
        "то же самое по смыслу",  # перечень: смысл тот же
        "как обычно",  # ничего «как обычно для такого товара»
        "не пересчитывай",  # единицы считает код
    ):
        assert fragment in text, fragment


def test_prompt_shows_the_list_and_the_unit_of_the_position() -> None:
    messages = build_messages("Тумбочка", "Ширина: 45 см", SPECS, Glossary([]))
    user = messages[1]["content"]
    assert "допустимые значения: Дерево; Металл" in user
    assert "единица позиции мм" in user
    assert "Ширина: 45 см" in user
