import pytest

from app.catalog import CharacteristicSpec, Position
from app.checks.detector import detect
from app.checks.hygiene import (
    RULE_CHAR_NAME,
    RULE_NAME_MEANING,
    RULE_PERSONAL,
    find_personal,
)
from app.schemas import CardIn, CharIn

PERSONAL = {
    "телефон": "Звоните +7 916 123-45-67",
    "телефон 8": "тел. 8 (916) 1234567",
    "почта": "пишите ivanov@mail.ru",
    "карта": "оплата на карту 4276 1300 1234 5678",
    "паспорт": "паспорт 4510 123456",
    "снилс": "СНИЛС 112-233-445 95",
    "инн": "ИНН 7707083893",
}

# Числа товара похожи на данные человека: артикулы, штрихкоды, ГОСТ, размеры, даты.
NOT_PERSONAL = [
    "Бумага А4 80 г/м2 500 листов",
    "Штрихкод 4605246001234",
    "ГОСТ Р 52901-2007",
    "Размер 210х297 мм, дата выпуска 01.10.2026",
    "Артикул 1234 5678 9012 3456",
    "Картридж HP CE285A, ресурс 1600 страниц",
    "Модель ST-1000-2000-3000",
]


@pytest.mark.parametrize("kind", list(PERSONAL))
def test_personal_data_is_found(kind: str) -> None:
    assert find_personal(PERSONAL[kind]) is not None


@pytest.mark.parametrize("text", NOT_PERSONAL)
def test_product_numbers_are_not_personal(text: str) -> None:
    assert find_personal(text) is None


POSITION = Position(
    code="17.12.14.129-00000031",
    name="Бумага для офисной техники белая",
    okpd2="17.12.14.129",
    characteristics=[
        CharacteristicSpec(name="Формат", kind="qualitative", values=["A4", "A3"]),
        CharacteristicSpec(name="Цветность", kind="qualitative", values=["Белая"]),
    ],
    source="test",
)


def _card(
    name: str = "Бумага офисная А4", description: str = "", chars: list[CharIn] | None = None
) -> CardIn:
    chars = (
        chars
        if chars is not None
        else [CharIn(name="Формат", value="A4"), CharIn(name="Цветность", value="Белая")]
    )
    return CardIn(
        name=name, description=description, characteristics=chars, ktru_code=POSITION.code
    )


def _by_rule(card: CardIn, rule: str) -> list:
    return [i for i in detect(card, POSITION) if i.rule == rule]


def test_personal_data_blocks_on_its_field_without_echoing_it() -> None:
    [issue] = _by_rule(_card(description="Звоните +7 916 123-45-67"), RULE_PERSONAL)
    assert (issue.field, issue.severity) == ("description", "block")
    assert "916" not in issue.message


@pytest.mark.parametrize("name", ["!!!", "😀😀😀", "1234567", "   "])
def test_name_without_words_is_blocked(name: str) -> None:
    [issue] = _by_rule(_card(name=name), RULE_NAME_MEANING)
    assert (issue.field, issue.severity) == ("name", "block")


@pytest.mark.parametrize(
    "name", ["Кубок KM2721", "Набор канцелярский для школы", "Бумага офисная А4 SvetoCopy"]
)
def test_name_with_words_is_not_checked_against_position_words(name: str) -> None:
    # Сверка со словами позиции на живых карточках давала 38 % шума: тип товара одним словом,
    # категория названа иначе («Кубок» в «Наградной продукции»).
    assert _by_rule(_card(name=name), RULE_NAME_MEANING) == []


def test_empty_and_repeated_characteristic_names_are_blocked() -> None:
    chars = [
        CharIn(name="Формат", value="A4"),
        CharIn(name="формат ", value="A3"),
        CharIn(name="  ", value="x"),
    ]
    issues = _by_rule(_card(chars=chars), RULE_CHAR_NAME)
    assert {(i.field, i.severity) for i in issues} == {
        ("characteristics.формат ", "warn"),
        ("characteristics", "block"),
    }


def test_same_name_with_different_text_values_is_a_list_not_a_block() -> None:
    # У портала многозначные характеристики хранятся повтором строки.
    chars = [
        CharIn(name="Конструктивные особенности", value="Система отжима"),
        CharIn(name="Конструктивные особенности", value="С ручкой"),
        CharIn(name="Конструктивные особенности", value="Со сливом"),
    ]
    issues = _by_rule(_card(chars=chars), RULE_CHAR_NAME)
    assert [(i.field, i.severity) for i in issues] == [
        ("characteristics.Конструктивные особенности", "warn")
    ]


def test_same_name_with_the_same_value_is_blocked() -> None:
    chars = [CharIn(name="Цвет", value="белый"), CharIn(name="Цвет", value="Белый")]
    issues = _by_rule(_card(chars=chars), RULE_CHAR_NAME)
    assert [(i.field, i.severity) for i in issues] == [("characteristics.Цвет", "block")]


def test_same_name_with_different_numbers_is_a_warning() -> None:
    # «Диаметр сверления» 6/8/10 мм, «Вес» 6300 г и 6 кг — не всегда ошибка, решает человек.
    chars = [
        CharIn(name="Высота", value="84", unit="мм"),
        CharIn(name="Высота", value="84", unit="см"),
    ]
    issues = _by_rule(_card(chars=chars), RULE_CHAR_NAME)
    assert [(i.field, i.severity) for i in issues] == [("characteristics.Высота", "warn")]


def test_same_number_written_differently_is_the_same_value() -> None:
    chars = [
        CharIn(name="Артикул", value="450608"),
        CharIn(name="Артикул", value="450 608"),
        CharIn(name="Вес", value="6300", unit="г"),
        CharIn(name="Вес", value="6,3", unit="кг"),
    ]
    issues = _by_rule(_card(chars=chars), RULE_CHAR_NAME)
    assert [(i.field, i.severity) for i in issues] == [
        ("characteristics.Артикул", "block"),
        ("characteristics.Вес", "block"),
    ]
