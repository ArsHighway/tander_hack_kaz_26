"""Подгон под КТРУ, доделка: одно место значению, метка без двоеточия, ошибка единицы, новые
характеристики с той же формулировкой."""

import asyncio

from app.catalog import CharacteristicSpec, NumRange, Position
from app.checks.detector import _unit_slip
from app.extract import extract_rules
from app.extract.extra import PortalNames, extra_pairs
from app.extract.new_names import load_new_names, new_key, remember_new_names
from app.pipeline import AutofillIn, autofill
from tests.fakes import FakeRedis
from tests.test_ktru_types import _deps


def _ql(name: str, values: list[str]) -> CharacteristicSpec:
    return CharacteristicSpec(name=name, kind="qualitative", values=values)


def test_one_fragment_fills_the_field_its_neighbours_name() -> None:
    metal = ["Металл", "Дерево"]
    specs = [_ql("Вид материала спинки", metal), _ql("Материал каркаса", metal)]
    found = {e.name: e.raw_value for e in extract_rules("Стул на металлическом каркасе", "", specs)}
    assert set(found) == {"Материал каркаса"}


def test_a_label_names_a_field_only_as_a_whole() -> None:
    specs = [_ql("Материал подклада", []), _ql("Цвет обивки", [])]
    assert extract_rules("Куртка", "Материал: глина; Цвет фасада: белый", specs) == []
    whole = [_ql("Материал изделия", [])]
    assert [e.raw_value for e in extract_rules("Куртка", "Материал: кожа", whole)] == ["кожа"]


def test_a_label_without_colon_fills_its_field() -> None:
    size = _ql("Российский размер", ["50", "52", "54"])
    [e] = extract_rules("Куртка зимняя", "размер 52", [size])
    assert (e.name, e.raw_value) == ("Российский размер", "52")


def test_a_value_ends_before_the_next_known_label() -> None:
    portal = PortalNames(["Размер", "Цвет"])
    [pair] = extra_pairs("Материал: глина, размер 52", set(), portal)
    assert (pair.name, pair.value) == ("Материал", "глина")
    [listed] = extra_pairs("Материал: хлопок, полиэстер", set(), portal)
    assert listed.value == "хлопок, полиэстер"


def test_a_number_off_by_a_power_of_ten_suggests_the_usual_one() -> None:
    height = CharacteristicSpec(
        name="Высота", kind="quantitative", unit="мм", plausible=NumRange(min=300, max=1200)
    )
    assert _unit_slip(height, "5000") == 500  # 5 метров вместо 50 сантиметров
    assert _unit_slip(height, "700") is None


def test_a_new_characteristic_keeps_its_first_wording() -> None:
    redis = FakeRedis()
    have = asyncio.run(load_new_names(redis))  # type: ignore[arg-type]
    asyncio.run(remember_new_names(redis, ["Ручка противоскользящая"], have))  # type: ignore[arg-type]
    again = asyncio.run(load_new_names(redis))  # type: ignore[arg-type]
    assert again[new_key("Противоскользящая ручка")] == ("Ручка противоскользящая", 1)


def test_the_wizard_reuses_the_wording_of_a_new_characteristic() -> None:
    deps = _deps()
    first = asyncio.run(
        autofill(
            AutofillIn(name="Тумба прикроватная", description="Ручка противоскользящая: да"), deps
        )
    )
    assert [(e.name, e.origin) for e in first.extra] == [("Ручка противоскользящая", "new")]
    second = asyncio.run(
        autofill(
            AutofillIn(name="Тумба прикроватная", description="Противоскользящая ручка: да"), deps
        )
    )
    [extra] = second.extra
    assert extra.name == "Ручка противоскользящая"
    assert extra.note is not None and "уже записана" in extra.note


def test_free_text_value_does_not_choose_the_product() -> None:
    # «глина» подходит только к полю свободного текста — товар по ней не выбирается: иначе
    # «Тумба прикроватная» (перечень материалов) выпала бы, а тумба с текстовым полем осталась.
    from app.catalog import Position
    from app.extract.model import Extracted
    from app.pipeline import _known_from_text
    from app.schemas import Span

    listed = Position(
        code="A", name="Тумба прикроватная", okpd2="31", source="x",
        characteristics=[_ql("Материал", ["ЛДСП"])],
    )  # fmt: skip
    free = Position(
        code="B", name="Тумба для инструментов", okpd2="31", source="x",
        characteristics=[_ql("Материал", [])],
    )  # fmt: skip
    clay = Extracted(
        name="Материал", raw_value="глина", raw_unit=None, method="rule",
        source=Span(field="description", start=10, end=15, text="глина"),
    )  # fmt: skip
    assert _known_from_text([clay], [listed, free]) == {}


def test_a_word_names_a_ktru_field_only_with_qualifiers_around() -> None:
    from app.ktru_align import alias_of

    specs = [_ql("Вид опоры стола", []), _ql("Габаритная ширина", [])]
    assert alias_of("Вид", specs) is None
    assert alias_of("Ширина", specs) is specs[1]


def test_a_size_outside_the_list_is_kept_to_be_flagged() -> None:
    size = _ql("Российский размер", ["50", "52"])
    [e] = extract_rules("Куртка", "Размер 60", [size])
    assert e.raw_value == "60"


def test_the_unit_slip_closest_to_the_usual_middle() -> None:
    width = CharacteristicSpec(
        name="Ширина", kind="quantitative", unit="мм", plausible=NumRange(min=100, max=2400)
    )
    assert _unit_slip(width, "20000") == 200  # 20 см, а не 2 м


def test_a_value_ends_at_the_end_of_its_sentence() -> None:
    [pair] = extra_pairs(
        "Водонепроницаемое покрытие: да, без покрытия. Звоните 8 (903) 123-45-67",
        set(),
        PortalNames([]),
    )
    assert pair.value == "да, без покрытия"


def _jacket() -> Position:
    return Position(
        code="x",
        name="Куртка зимняя",
        okpd2="14",
        source="x",
        characteristics=[
            _ql("Назначение", ["Мужская", "Женская", "Универсальная"]),
            _ql("Тип капюшона", ["Отсутствует", "Съемный", "Несъемный"]),
            _ql("Наличие водонепроницаемого покрытия", ["Да", "Нет"]),
        ],
    )


def test_contradictions_inside_the_supplier_text_are_named() -> None:
    from app.checks.detector import _contradictions
    from app.schemas import CardIn

    card = CardIn(
        name="куртка зимняя мужская, женская",
        description="Капюшон: отсутствует, съёмный. "
        "Водонепроницаемое покрытие: да, без водонепроницаемого покрытия. Звоните",
    )
    fields = [i.field for i in _contradictions(card, _jacket())]
    assert fields == [
        "characteristics.Назначение",
        "characteristics.Тип капюшона",
        "characteristics.Наличие водонепроницаемого покрытия",
    ]


def test_values_listed_together_are_not_a_contradiction() -> None:
    from app.checks.detector import _contradictions
    from app.schemas import CardIn

    card = CardIn(
        name="Куртка унисекс мужская/женская",
        description="Капюшон: съёмный. Водонепроницаемое покрытие: да. Цвет: белый, черный",
    )
    assert _contradictions(card, _jacket()) == []


def _mm(name: str) -> CharacteristicSpec:
    return CharacteristicSpec(name=name, kind="quantitative", unit="мм")


def test_sizes_without_commas_go_to_their_own_labels() -> None:
    specs = [_mm("Ширина"), _mm("Высота"), _mm("Глубина")]
    for text in (
        "ширина 120 см высота 75 см",
        "120 см ширина 75 см высота",
        "высота 75 см, ширина 120 см",
    ):
        found = {e.name: e.raw_value for e in extract_rules("Стол письменный", text, specs)}
        assert found == {"Ширина": "120", "Высота": "75"}, text
    meters = "ширина 1,2 м высота 0,75 м"
    found = {e.name: e.raw_value for e in extract_rules("Стол", meters, specs)}
    assert found == {"Ширина": "1,2", "Высота": "0,75"}


def test_a_word_out_of_the_list_after_a_bare_label_goes_to_the_only_required_field() -> None:
    # Запрос жюри: «глина» — не из перечня, но поле одно; подгон под КТРУ покажет «нет в КТРУ».
    body = CharacteristicSpec(
        name="Вид материала корпуса", kind="qualitative", values=["МДФ", "ЛДСП"], required=True
    )
    specs = [body, _ql("Материал", ["ЛДСП", "Пластик"]), _ql("Вид материала фасада", ["МДФ"])]
    name = "Тумбочка прикроватная, материал глина, ширина 20000 мм"
    found = {e.name: e.raw_value for e in extract_rules(name, "", [*specs, _mm("Ширина")])}
    assert found == {"Вид материала корпуса": "глина", "Ширина": "20000"}
    # Слово чужого имени — не значение; без обязательного поля слово не угадываем.
    assert extract_rules("Тумба", "материал корпуса глина", specs) == []
    assert extract_rules("Тумба", "материал глина", specs[1:]) == []


def test_a_label_word_inside_a_value_does_not_cut_the_phrase() -> None:
    power = _ql("Источник питания", [])
    text = "Источник питания: Аккумулятор Ni-MH 6 В или 5 элементов питания типа АА"
    found = {e.name: e.raw_value for e in extract_rules("Мегаомметр", text, [power])}
    assert found == {"Источник питания": "Аккумулятор Ni-MH 6 В или 5 элементов питания типа АА"}


def test_a_sentence_ends_after_a_number_before_a_capital() -> None:
    # Куртка Богдана: «Размер 60. Тип утеплителя: гусиный пух» — метка не прилипает к «Размер 60»;
    # «гусиный пух» вне перечня покажет подгон под КТРУ.
    specs = [
        CharacteristicSpec(
            name="Российский размер", kind="qualitative", values=["58", "60"], required=True
        ),
        _ql("Тип утеплителя", ["Натуральный утеплитель", "Искусственный утеплитель"]),
    ]
    text = "Размер 60. Тип утеплителя: гусиный пух."
    found = {e.name: e.raw_value for e in extract_rules("Куртка зимняя", text, specs)}
    assert found == {"Российский размер": "60", "Тип утеплителя": "гусиный пух"}


def test_phone_digits_are_not_numbers_of_fields() -> None:
    from app.extract.quantities import numeric_notes

    specs = [
        CharacteristicSpec(name=n, kind="quantitative", unit="мм", range=NumRange(min=1, max=1000))
        for n in ("Размер логотипа", "Плотность материала подкладки")
    ]
    assert numeric_notes("Куртка", "Звоните 8 (903) 123-45-67", specs, []) == []
    assert numeric_notes("Куртка", "Звоните 8 (903) 12", specs, []) != []  # не телефон
