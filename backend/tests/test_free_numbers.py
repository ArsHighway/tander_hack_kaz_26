"""Число идёт в поле, только если текст сам связывает его с этим полем.

Число из обозначения модели, номер СТЕ и количество предмета внутри чужого перечня — не значения
характеристик позиции. Число, привязанное в тексте к словам, которых нет среди характеристик, —
новая характеристика: её увидит поставщик и подтвердит модератор."""

from app.catalog import CharacteristicSpec, NumRange
from app.extract import extract_rules
from app.extract.extra import PortalNames, extra_pairs
from app.values import fold

RPM = CharacteristicSpec(
    name="Максимальное число оборотов холостого хода",
    kind="quantitative",
    unit="об/мин",
    range=NumRange(min=0),
)
ITEMS = CharacteristicSpec(
    name="Количество предметов в комплекте", kind="quantitative", unit="шт", range=NumRange(min=0)
)
BATTERIES = CharacteristicSpec(
    name="Количество аккумуляторов в комплекте",
    kind="quantitative",
    unit="шт",
    range=NumRange(min=0),
)
PORTAL = PortalNames(["Вес", "Цвет"])


def _values(name: str, description: str, specs: list[CharacteristicSpec]) -> dict[str, str]:
    return {e.name: e.raw_value for e in extract_rules(name, description, specs)}


def test_number_inside_a_model_designation_is_not_a_value() -> None:
    assert _values("Отвертка аккумуляторная Bort BAS-36N-Li", "", [RPM]) == {}
    assert _values("Аккумуляторная отвертка Hitachi DB3DL2", "", [RPM]) == {}


def test_free_number_in_the_name_is_still_taken() -> None:
    assert _values("Отвертка аккумуляторная 180", "", [RPM]) == {RPM.name: "180"}


def test_long_identifier_is_not_a_count() -> None:
    assert _values("1160330 Отвертка аккумуляторная", "", [ITEMS]) == {}


def test_item_count_inside_another_list_is_not_the_field() -> None:
    text = "Комплектация Отвертка, зарядное устройство, бит-насадки (5 шт"
    assert _values("Отвертка аккумуляторная", text, [ITEMS]) == {}


def test_count_next_to_the_words_of_the_field_is_taken() -> None:
    text = "Комплектация отвертка, 2 шт аккумулятора, кейс"
    assert _values("Отвертка аккумуляторная", text, [BATTERIES]) == {BATTERIES.name: "2"}


def test_count_alone_after_a_comma_is_taken() -> None:
    assert _values("Перчатки", "Нитриловые, 100 шт", [ITEMS]) == {ITEMS.name: "100"}


def _extra(text: str, known: set[str] | None = None) -> dict[str, tuple[str, str | None, str]]:
    found = extra_pairs(text, {fold(k) for k in known or set()}, PORTAL)
    return {p.name: (p.value, p.unit, p.origin) for p in found}


def test_number_bound_to_unknown_words_becomes_a_new_characteristic() -> None:
    text = "Вес 1950 г | Макс. диаметр отверстия в дереве 38 мм | Цвет Зеленый"
    got = _extra(text)
    assert got["Макс. диаметр отверстия в дереве"] == ("38", "мм", "new")
    assert got["Вес"][2] == "portal"


def test_field_of_the_position_is_not_duplicated_as_new() -> None:
    text = "Вес 1950 г | Макс. диаметр отверстия в дереве 38 мм"
    assert "Макс. диаметр отверстия в дереве" not in _extra(
        text, {"Макс. диаметр отверстия в дереве"}
    )


def test_list_and_prose_do_not_become_new_characteristics() -> None:
    assert _extra("Комплектация Отвертка, зарядное устройство, бит-насадки (5 шт | Цвет синий") == {
        "Цвет": ("синий", None, "portal")
    }
    assert _extra("Стул офисный с подлокотниками нагрузка 120 кг") == {}
