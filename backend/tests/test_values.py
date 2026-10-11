import pytest

from app.catalog import CharacteristicSpec, NumRange
from app.ingest.units import normalize_unit
from app.values import convert, fit_value, fold, nearest_allowed, parse_bucket

MASS = CharacteristicSpec(
    name="Масса бумаги площадью 1м2",
    kind="quantitative",
    unit="г/м2",
    values=[">=80 ... <90", ">=90 ... <100", ">=100"],
)
FORMAT = CharacteristicSpec(name="Формат", kind="qualitative", values=["A3", "A4", "A5"])
INTENSITY = CharacteristicSpec(
    name="Интенсивность цвета", kind="qualitative", values=["Интенсив", "Медиум", "Неон", "Пастель"]
)
SHEETS = CharacteristicSpec(
    name="Количество листов в пачке", kind="quantitative", unit="шт", values=["250", "500"]
)
WHITENESS = CharacteristicSpec(
    name="Белизна", kind="quantitative", unit="%", range=NumRange(min=146, max=175)
)
VOLUME = CharacteristicSpec(
    name="Объем", kind="quantitative", unit="мл", range=NumRange(min=250, max=5000)
)


def test_fold_maps_latin_lookalikes_to_cyrillic() -> None:
    assert fold("A4") == fold("А4")  # латинская A и кириллическая А
    assert fold(" Мeдиум ") == fold("медиум")  # латинская e
    assert fold("Ёмкость") == fold("емкость")


def test_point_number_falls_into_its_bucket() -> None:
    fit = fit_value("80", "г/м2", MASS)
    assert (fit.status, fit.value, fit.unit) == ("bucket", ">=80 ... <90", "г/м2")


def test_unit_inside_value_is_recognised() -> None:
    fit = fit_value("95 г/м2", None, MASS)
    assert (fit.status, fit.value) == ("bucket", ">=90 ... <100")


def test_open_bucket_takes_large_values() -> None:
    assert fit_value("120", "г/м2", MASS).value == ">=100"


def test_unknown_unit_is_a_mismatch() -> None:
    fit = fit_value("0,08", "кг/м2", MASS)
    assert fit.status == "unit_mismatch"
    assert fit.note is not None and "кг/м2" in fit.note


def test_cyrillic_value_normalised_to_latin_list_value() -> None:
    fit = fit_value("А4", None, FORMAT)
    assert (fit.status, fit.value) == ("normalized", "A4")


def test_exact_list_value() -> None:
    assert fit_value("A4", None, FORMAT).status == "exact"


def test_value_outside_list_names_allowed_values() -> None:
    fit = fit_value("Белый", None, INTENSITY)
    assert fit.status == "not_in_list"
    assert fit.value is None
    assert fit.note is not None and "Пастель" in fit.note


def test_latin_letter_inside_word_is_normalised() -> None:
    fit = fit_value("Мeдиум", None, INTENSITY)
    assert (fit.status, fit.value) == ("normalized", "Медиум")


def test_typo_is_fixed_to_nearest_list_value() -> None:
    fit = fit_value("Медиму", None, INTENSITY)
    assert (fit.status, fit.value) == ("typo", "Медиум")


def test_number_not_in_point_list() -> None:
    assert fit_value("300", None, SHEETS).status == "not_in_list"


def test_number_in_point_list_is_exact() -> None:
    fit = fit_value("500", "шт", SHEETS)
    assert (fit.status, fit.value, fit.unit) == ("exact", "500", "шт")


def test_number_inside_range() -> None:
    fit = fit_value("160", "%", WHITENESS)
    assert (fit.status, fit.value, fit.unit) == ("exact", "160", "%")


def test_number_outside_range() -> None:
    fit = fit_value("98", "%", WHITENESS)
    assert fit.status == "not_in_list"
    assert fit.note is not None and "146" in fit.note


def test_foreign_dimension_unit_is_a_mismatch() -> None:
    assert fit_value("160", "кг", WHITENESS).status == "unit_mismatch"


def test_convertible_unit_is_normalised() -> None:
    fit = fit_value("1,5", "л", VOLUME)
    assert (fit.status, fit.value, fit.unit) == ("normalized", "1500", "мл")


def test_text_for_number_spec_is_unparsed() -> None:
    assert fit_value("много", None, SHEETS).status == "unparsed"


def test_parse_bucket_half_open() -> None:
    b = parse_bucket(">=80 ... <90")
    assert b is not None
    assert (b.lo, b.lo_incl, b.hi, b.hi_incl) == (80, True, 90, False)


def test_parse_bucket_open_and_point() -> None:
    ge = parse_bucket(">=500")
    assert ge is not None and (ge.lo, ge.hi) == (500, None)
    le = parse_bucket("<=5")
    assert le is not None and (le.lo, le.hi, le.hi_incl) == (None, 5, True)
    point = parse_bucket("80")
    assert point is not None and (point.lo, point.hi) == (80, 80)


def test_parse_bucket_rejects_text() -> None:
    assert parse_bucket("абв") is None


def test_nearest_allowed_limits() -> None:
    assert nearest_allowed("Медиму", INTENSITY.values) == (
        "Медиум",
        1,
    )  # перестановка — одна правка
    assert nearest_allowed("ЛДПС", ["ДСП", "ЛДСП", "МДФ"]) == ("ЛДСП", 1)
    assert nearest_allowed("ПДН", ["ПНД"]) is None  # трёхбуквенный код: перестановка — не опечатка
    assert nearest_allowed("ЛДСМ", ["ЛДСП"]) is None  # короткий код с другой буквой — другое
    assert nearest_allowed("Бел", ["Белый"]) is None  # 2 правки на 3 буквы — слишком много


def test_convert_within_dimension_only() -> None:
    assert convert(1.5, "л", "мл") == 1500
    assert convert(250, "г", "кг") == 0.25
    assert convert(1, "кг", "мл") is None


def test_value_written_as_list_bucket_is_exact() -> None:
    fit = fit_value(">=80 ... <90", "г/м2", MASS)
    assert (fit.status, fit.value) == ("exact", ">=80 ... <90")


def test_sheets_count_as_pieces() -> None:
    # «500 листов» для «Количество листов в пачке, шт» — та же счётная величина.
    fit = fit_value("500", "лист", SHEETS)
    assert (fit.status, fit.value) == ("exact", "500")


def test_short_codes_are_not_typos_of_each_other() -> None:
    # «ПНД» и «ПВД» — разные материалы, а не опечатка в одну букву.
    assert nearest_allowed("ПНД", ["ПВД"]) is None
    assert (
        fit_value(
            "ПНД", None, CharacteristicSpec(name="Материал", kind="qualitative", values=["ПВД"])
        ).status
        == "not_in_list"
    )


# Пересчёты единиц на карточках портала.
@pytest.mark.parametrize(
    ("value", "src", "dst", "expected"),
    [
        (1500.0, "Вт", "кВт", 1.5),
        (4500.0, "мА·ч", "А·ч", 4.5),
        (6.0, "ТБ", "ГБ", 6000.0),
        (512.0, "МБ", "ГБ", 0.512),
        (0.4, "кВ", "В", 400.0),
        # Жюри-аудит: «Объем 0,12 м3» у мешков в литрах, «толщина 0,03 мм» в микронах.
        (0.12, "м3", "л", 120.0),
        (0.03, "мм", "мкм", 30.0),
        (1700.0, "мл", "л", 1.7),
    ],
)
def test_more_unit_scales(value: float, src: str, dst: str, expected: float) -> None:
    assert convert(value, src, dst) == expected


@pytest.mark.parametrize(
    ("raw", "canon"),
    [
        ("А.ч", "А·ч"),
        ("Ач", "А·ч"),
        ("мм[2*]", "мм2"),
        ("мм2", "мм2"),
        ("[0*]С", "°C"),
        ("кВ", "кВ"),
    ],
)
def test_portal_unit_spellings(raw: str, canon: str) -> None:
    assert normalize_unit(raw) == canon


GENERAL = CharacteristicSpec(
    name="Производитель",
    kind="qualitative",
    values=["Туринский ЦБЗ", "Снегурочка"],
    group="general",
)


def test_general_value_keeps_the_supplier_spelling() -> None:
    # Самое частое написание на портале бывает небрежным («grohe»): поставщик написал «Grohe».
    fit = fit_value("ТУРИНСКИЙ ЦБЗ", None, GENERAL)
    assert (fit.status, fit.value) == ("exact", "ТУРИНСКИЙ ЦБЗ")


def test_general_value_outside_the_list_is_kept_as_written() -> None:
    # Перечень производителей — подсказка: новый завод не ошибка, и опечаткой в имени собственном
    # чужое название не становится.
    assert (
        fit_value("Монди СЛПК", None, GENERAL).status,
        fit_value("Монди СЛПК", None, GENERAL).value,
    ) == (
        "exact",
        "Монди СЛПК",
    )
    near = fit_value("Снежинка", None, GENERAL)
    assert (near.status, near.value) == ("exact", "Снежинка")


# --- короткая запись значения в другой единице той же величины ---


@pytest.mark.parametrize(
    ("value", "unit", "short"),
    [
        ("1500", "мм", "1,5 м"),
        ("100", "см", "1 м"),
        ("1000", "кВт", "1 МВт"),
        ("0,15", "м", "15 см"),
        ("2500", "мА", "2,5 А"),
        ("2400", "МГц", "2,4 ГГц"),
    ],
)
def test_shorter_record_in_another_unit(value: str, unit: str, short: str) -> None:
    from app.values import compact

    assert compact(value, unit) == short


@pytest.mark.parametrize(
    ("value", "unit"),
    [
        ("250", "мл"),  # «0,25 л» не короче — своя единица
        ("12", "мм"),
        ("0,5", "т"),  # «500 кг» длиннее
        ("1,333", "м"),  # «1333 мм» той же длины
        (">=80 ... <90", "г/м2"),  # корзина — не число
        ("80", "г/м2"),  # величины нет в таблице короткой записи
        ("1", None),
        ("3", "шт"),
    ],
)
def test_no_shorter_record(value: str, unit: str | None) -> None:
    from app.values import compact

    assert compact(value, unit) is None


def test_milli_and_mega_prefixes_are_different_units() -> None:
    # «мВт» — милливатт, «МВт» — мегаватт: разница в 10⁹, регистр важен
    assert normalize_unit("МВт") == "МВт" and normalize_unit("Мвт") == "МВт"
    assert normalize_unit("мВт") == "мВт"
    assert normalize_unit("мвт") is None  # строчными — неизвестно что: не пересчитываем
    assert convert(1000.0, "кВт", "МВт") == 1.0
    assert convert(5.0, "мВт", "Вт") == 0.005
    spec = CharacteristicSpec(name="Мощность", kind="quantitative", unit="кВт")
    assert fit_value("1", "МВт", spec).value == "1000"


@pytest.mark.parametrize(("unit", "target"), [("нм", "км"), ("мкг", "т")])
def test_units_apart_beyond_rounding_are_a_mismatch_not_a_crash(unit: str, target: str) -> None:
    # Пересчёт единицы, округлённый до 9 знаков, давал 0 и log10(0) ронял /autofill
    # (отложенная выборка, «Инструменты»).
    spec = CharacteristicSpec(name="Длина", kind="quantitative", unit=target)
    got = fit_value("5", unit, spec)
    assert got.status == "unit_mismatch" and "10^12" in (got.note or "")


def test_card_field_carries_the_short_record_next_to_the_catalog_unit() -> None:
    # Значение — в единице позиции (1500 мм), рядом подсказка «1,5 м»; короче нет — пусто
    from app.card_fill import _characteristics
    from app.catalog import Position
    from app.extract import Extracted
    from app.schemas import Span

    length = CharacteristicSpec(name="Длина", kind="quantitative", unit="мм")
    width = CharacteristicSpec(name="Ширина", kind="quantitative", unit="мм")
    position = Position(
        code="P", name="Стол", okpd2="", source="t", characteristics=[length, width]
    )

    def got(name: str, value: str, unit: str) -> Extracted:
        span = Span(field="description", start=0, end=len(value), text=value)
        return Extracted(name=name, raw_value=value, raw_unit=unit, source=span, method="rule")

    out = _characteristics(position, [got("Длина", "1,5", "м"), got("Ширина", "12", "мм")], {})
    assert [(c.value, c.unit, c.compact) for c in out] == [
        ("1500", "мм", "1,5 м"),
        ("12", "мм", None),
    ]


def test_typo_search_skips_values_that_cannot_be_a_typo() -> None:
    # Отложенная выборка: «Молочная смесь» отвечала 194 с — Левенштейн по перечню из длинных
    # текстов (составы) на каждый вариант сужения. Опечатка — до двух правок: дальше считать незачем
    import time

    from app.values import nearest_allowed

    long_values = [f"состав {i}: " + "мука, сахар, молоко сухое, " * 80 for i in range(40)]
    started = time.perf_counter()
    assert nearest_allowed("Сухая смесь " + "x" * 2000, long_values) is None
    assert nearest_allowed("Молоко", [*long_values, "Молоко"]) == ("Молоко", 0)
    assert nearest_allowed("Нитрилл", [*long_values, "Нитрил"]) == ("Нитрил", 1)
    assert time.perf_counter() - started < 1.0


def test_degrees_from_the_supplier_fit_both_celsius_and_angle() -> None:
    # «градусов» без уточнения словарь не угадывает — но у характеристики с °C или ° это её единица
    heat = CharacteristicSpec(name="Температура нагрева", kind="quantitative", unit="°C")
    angle = CharacteristicSpec(name="Угол наклона", kind="quantitative", unit="°")
    assert (
        fit_value("100", "градусов", heat).status,
        fit_value("100", "градусов", heat).value,
    ) == (
        "exact",
        "100",
    )
    assert fit_value("45", "градусов", angle).status == "exact"


def test_milliwatt_word_with_lowercase_symbol_is_milli_but_megawatt_word_is_a_contradiction() -> (
    None
):
    # Стандарты портала: «милливатт (мвт)» — лазеры и наушники; «мегаватт (мвт)» — у лазера и
    # «диапазона напряжения»: слово и обозначение спорят — не угадываем
    assert normalize_unit("милливатт (мвт)") == "мВт"
    assert normalize_unit("мегаватт (мвт)") is None


def test_conversion_across_a_million_times_is_a_unit_slip_not_a_value() -> None:
    # «МВт; 10³ кВт» у красной лазерной указки — ошибка шаблона; «5 мВт» не превращаем в
    # 0,000000005 МВт: это вопрос к единице, а не значение
    power = CharacteristicSpec(name="Мощность лазера", kind="quantitative", unit="МВт")
    slip = fit_value("5", "мВт", power)
    assert slip.status == "unit_mismatch" and "10" in (slip.note or "")
    assert fit_value("1000", "кВт", power).value == "1"


def test_typo_fix_never_changes_digits() -> None:
    # «сталь У8» и «сталь 45» — две правки, но это разные марки стали, а не опечатка.
    assert nearest_allowed("сталь У8", ["сталь 45", "сталь У7"]) is None
    assert nearest_allowed("стал 45", ["сталь 45"]) == ("сталь 45", 1)


def test_years_and_months_convert_but_days_do_not() -> None:
    # «гарантия 1,5 года» при «мес» была «единица не подходит».
    warranty = CharacteristicSpec(name="Гарантийный срок", kind="quantitative", unit="мес")
    fit = fit_value("1,5", "года", warranty)
    assert (fit.status, fit.value, fit.unit) == ("normalized", "18", "мес")
    assert fit_value("365", "дней", warranty).status == "unit_mismatch"
    assert convert(90, "мин", "ч") == 1.5


def test_areal_mass_in_grams_is_grams_per_square_metre() -> None:
    # «Масса бумаги площадью 1 м²» в КТРУ записана в граммах, а «80 г/м²» — то же самое.
    spec = CharacteristicSpec(name="Масса бумаги площадью 1 м²", kind="quantitative", unit="г")
    assert spec.unit == "г/м2"
    assert fit_value("80", "г/м2", spec).status == "exact"
    plain = CharacteristicSpec(name="Масса", kind="quantitative", unit="г")
    assert plain.unit == "г"
