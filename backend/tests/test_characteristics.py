import pytest

from app.ingest.characteristics import parse_characteristics, parse_value, split_name_unit
from app.ingest.units import normalize_unit

# Строка из настоящего датасета СТЕ TenderHack Пермь 2026.
PERM_ROW = (
    "Количество в упаковке:30.00000;Толщина, мкм:50.00000;Объем:35.00000;"
    "Цвет:черный;Вид материала:ПВД"
)


def test_perm_kv_string() -> None:
    parsed = parse_characteristics(PERM_ROW)
    assert parsed.fmt == "kv_string"
    assert parsed.unparsed == []
    by_name = {c.name: c for c in parsed.items}
    assert by_name["Количество в упаковке"].value.number == 30.0
    assert by_name["Толщина"].unit == "мкм"
    assert by_name["Толщина"].value.number == 50.0
    assert by_name["Вид материала"].value.kind == "text"


@pytest.mark.parametrize(
    "raw",
    [
        "г/м²",
        "г/м2",
        "г / м²",
        "г / м2",
        "г на м²",
        "г на м2",
        "грамм/м²",
        "грамм/м2",
        "грамм на квадратный метр",
    ],
)
def test_areal_density_spellings_share_one_unit(raw: str) -> None:
    assert normalize_unit(raw) == "г/м2"


def test_kazan2025_unit_inside_value() -> None:
    parsed = parse_characteristics("Объем:35 л;Масса:1,5 кг;Цвет:белый")
    by_name = {c.name: c for c in parsed.items}
    assert (by_name["Объем"].value.number, by_name["Объем"].unit) == (35.0, "л")
    assert (by_name["Масса"].value.number, by_name["Масса"].unit) == (1.5, "кг")
    assert by_name["Цвет"].unit is None


@pytest.mark.parametrize(
    ("raw", "low", "high", "high_inclusive"),
    [
        ("0.005 - 30", 0.005, 30.0, True),
        ("в диапазоне от 0 и до 0,5", 0.0, 0.5, True),
        ("больше или равно 200", 200.0, None, True),
        (">=80 ... <90", 80.0, 90.0, False),
        ("не более 3", None, 3.0, True),
    ],
)
def test_ranges(raw: str, low: float | None, high: float | None, high_inclusive: bool) -> None:
    value, _ = parse_value(raw)
    assert value.kind == "range"
    assert (value.low, value.high, value.high_inclusive) == (low, high, high_inclusive)


@pytest.mark.parametrize(
    ("raw", "low", "low_inclusive", "high", "high_inclusive"),
    [
        # Корзины КТРУ из XML ЕИС (`ktru_xml.py`): строгая нижняя граница, нестрогая верхняя.
        (">200 ... <=400", 200.0, False, 400.0, True),
        (">=210 ... <=220", 210.0, True, 220.0, True),
        (">40 ... <=50", 40.0, False, 50.0, True),
        (">1,5 ... <2", 1.5, False, 2.0, False),
        (">=80 ... <90", 80.0, True, 90.0, False),  # корзина портала — как была
    ],
)
def test_ktru_buckets_with_any_bounds(
    raw: str, low: float, low_inclusive: bool, high: float, high_inclusive: bool
) -> None:
    value, _ = parse_value(raw)
    assert value.kind == "range"
    assert (value.low, value.low_inclusive, value.high, value.high_inclusive) == (
        low, low_inclusive, high, high_inclusive,
    )  # fmt: skip


def test_value_falls_into_ktru_bucket_with_strict_low_bound() -> None:
    from app.catalog import CharacteristicSpec
    from app.values import fit_value

    spec = CharacteristicSpec(
        name="Длина", kind="quantitative", unit="мм", values=["<=200", ">200 ... <=400", ">400"]
    )
    assert (fit_value("267", "мм", spec).status, fit_value("267", "мм", spec).value) == (
        "bucket", ">200 ... <=400",
    )  # fmt: skip
    assert fit_value("200", "мм", spec).value == "<=200"
    assert fit_value("400", "мм", spec).value == ">200 ... <=400"


def test_bool_and_text() -> None:
    assert parse_value("Да")[0].flag is True
    assert parse_value("Портативная (носимая)")[0].kind == "text"


def test_name_comma_without_unit_is_kept() -> None:
    assert split_name_unit("Цвет, оттенок") == ("Цвет, оттенок", None)
    assert split_name_unit("Плотность бумаги, г/кв.м") == ("Плотность бумаги", "г/м2")


def test_segment_without_colon_continues_previous_value() -> None:
    parsed = parse_characteristics("Состав:сахар;Ирис Кис кис - 2;Вес:2")
    assert [c.name for c in parsed.items] == ["Состав", "Вес"]
    assert parsed.items[0].value.raw == "сахар;Ирис Кис кис - 2"
    assert (parsed.merged, parsed.unparsed) == (1, [])


def test_leading_segment_without_colon_is_not_guessed() -> None:
    parsed = parse_characteristics("мусор;Цвет:черный")
    assert parsed.unparsed == ["мусор"]
    assert [c.name for c in parsed.items] == ["Цвет"]


def test_json_pairs_objects_and_dict() -> None:
    assert parse_characteristics('[["Формат", "А4"]]').items[0].value.raw == "А4"
    objs = parse_characteristics([{"name": "Ширина", "value": "470", "unit": "мм"}])
    assert (objs.items[0].value.number, objs.items[0].unit) == (470.0, "мм")
    assert parse_characteristics({"Объем": "30"}).fmt == "dict"


def test_units_normalization() -> None:
    assert normalize_unit("кв.м") == "м2"
    assert normalize_unit("Гб") == "ГБ"
    assert normalize_unit("оттенок") is None


# Формы карточек портала.
def test_name_ending_with_colon_does_not_leave_colon_in_value() -> None:
    parsed = parse_characteristics("Область применения:: универсальная;Отдушка::Морская")
    assert [(c.name, c.value.raw) for c in parsed.items] == [
        ("Область применения", "универсальная"),
        ("Отдушка", "Морская"),
    ]


def test_list_item_with_colon_inside_value_is_not_a_new_characteristic() -> None:
    parsed = parse_characteristics(
        "Состав набора:- печатный блок: 1 шт; - обложка: 2 шт;Цвет:синий"
    )
    assert [c.name for c in parsed.items] == ["Состав набора", "Цвет"]
    assert "обложка: 2 шт" in parsed.items[0].value.raw


def test_semicolon_inside_value_keeps_the_original_text() -> None:
    parsed = parse_characteristics("Типоразмер:D(LR20;R20;HR20);Цвет:черный")
    assert [(c.name, c.value.raw) for c in parsed.items] == [
        ("Типоразмер", "D(LR20;R20;HR20)"),
        ("Цвет", "черный"),
    ]


def test_name_starting_with_a_number_is_a_characteristic() -> None:
    parsed = parse_characteristics("1 размер:5х3 дюйм;10base-t / 100base-tx:Да;Цвет:черный")
    assert [c.name for c in parsed.items] == ["1 размер", "10base-t / 100base-tx", "Цвет"]


def test_numbered_list_item_continues_the_value() -> None:
    parsed = parse_characteristics("Комплект:1) скальпель: 2 шт; 2) пинцет: 1 шт;Цвет:синий")
    assert [c.name for c in parsed.items] == ["Комплект", "Цвет"]
