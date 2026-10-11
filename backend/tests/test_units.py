import pytest

from app.ingest.units import _UNITS, UNIT_NAMES, normalize_unit, unit_name


def test_every_canonical_unit_has_a_full_name() -> None:
    assert set(UNIT_NAMES) == set(_UNITS)


def test_unit_name_is_the_okei_full_name() -> None:
    assert unit_name("г/м2") == "Грамм на квадратный метр"
    assert unit_name("шт") == "Штука"
    assert unit_name("мм") == "Миллиметр"


def test_unit_name_unknown_or_missing() -> None:
    assert unit_name("нечто") is None
    assert unit_name(None) is None


def test_unit_names_match_eis_spelling() -> None:
    # Как единицу пишут карточки КТРУ в ЕИС и стандарты портала
    # (перепись 1181 карточки и 2070 стандартов).
    assert unit_name("т") == "Тонна; метрическая тонна (1000 кг)"
    assert unit_name("л") == "Литр; кубический дециметр"
    assert unit_name("дюйм") == "Дюйм (25,4 мм)"
    assert unit_name("мл") == "Кубический сантиметр; миллилитр"
    assert unit_name("км") == "Километр; тысяча метров"
    assert unit_name("А·ч") == "Ампер-час (3,6 кКл)"


def test_full_names_are_recognised_as_units() -> None:
    assert normalize_unit("Квадратный метр") == "м2"
    assert normalize_unit("Градус Цельсия") == "°C"
    assert normalize_unit("Кубический метр") == "м3"
    assert normalize_unit("Литр; кубический дециметр") == "л"
    assert normalize_unit("кубический дециметр") == "л"
    assert normalize_unit("Грамм на квадратный метр") == "г/м2"


def test_compound_unit_is_normalized_by_parts() -> None:
    # в стандартах портала «гр/м2» («плотность 80 г/м2» не попадала в «Плотность
    # бумаги, гр/м2» — единица позиции была незнакомой)
    assert normalize_unit("гр/м2") == "г/м2"
    assert normalize_unit("гр./кв.м") == "г/м2"
    assert normalize_unit("попугаев/м2") is None


def test_density_with_portal_unit_is_filled() -> None:
    from app.catalog import CharacteristicSpec
    from app.extract.rules import extract_rules
    from app.values import fit_value

    spec = CharacteristicSpec(name="Плотность бумаги", kind="quantitative", unit="гр/м2")
    [e] = extract_rules("Бумага А4, плотность 80 г/м2", "", [spec])
    fit = fit_value(e.raw_value, e.raw_unit, spec)
    assert (fit.status, fit.value) in {("exact", "80"), ("normalized", "80")}


def test_unit_with_abbreviation_in_brackets() -> None:
    # у батареек единица справочника «миллиампер (ма)» не распознавалась — любое
    # значение тока давало блокировку «единица не распознана»
    assert normalize_unit("миллиампер (ма)") == normalize_unit("мА")
    assert normalize_unit("мА") is not None


def test_angle_degrees_are_not_celsius() -> None:
    # в стандартах портала «градус» — в 1 868 строках угол и в 202 — температура, а
    # словарь делал из любого «градуса» °C: «Угол наклона, °C» у 159 характеристик стандартов
    # и 29 КТРУ
    assert normalize_unit("° градус") == "°"
    assert normalize_unit("Градус (плоского угла)") == "°"
    assert normalize_unit("градус цельсия (°с)") == "°C"
    assert normalize_unit("°c") == "°C"
    assert normalize_unit("градус") is None  # без имени характеристики — не угадываем
    assert normalize_unit("градусов") is None


def test_unit_written_with_its_alternative_name_after_semicolon() -> None:
    # КТРУ: «Мегаватт; тысяча киловатт» у турбин — узнаётся по любой части
    assert normalize_unit("Мегаватт; тысяча киловатт") == "МВт"


@pytest.mark.parametrize(
    ("raw", "canon"),
    [
        # Самые частые единицы справочников, которых словарь не знал
        # (4 888 характеристик стандартов и 991 КТРУ с нераспознанной единицей)
        ("м[3*]/ч", "м3/ч"),
        ("Кубический метр в час", "м3/ч"),
        ("км/ч", "км/ч"),
        ("Километр в час", "км/ч"),
        ("м/с", "м/с"),
        ("Метр в секунду", "м/с"),
        ("л. с", "л.с."),
        ("Лошадиная сила", "л.с."),
        ("микролитр (мкл)", "мкл"),
        ("нанометр (нм)", "нм"),
        ("Ом", "Ом"),
        ("Джоуль", "Дж"),
        ("ккал", "ккал"),
        ("мкг", "мкг"),
        ("атм", "атм"),
        ("мм рт. ст", "мм рт. ст."),
        ("кг/м[3*]", "кг/м3"),
        ("Мбит/с", "Мбит/с"),
        ("Гигабит в секунду", "Гбит/с"),
        ("Мегабайт в секунду", "МБ/с"),
        ("Миллисекунда", "мс"),
        ("см[2*]", "см2"),
        ("Квадратный сантиметр", "см2"),
        ("кВт.ч", "кВт·ч"),
        ("Ватт-час", "Вт·ч"),
        ("кВ.А", "кВ·А"),
        ("Тонна в час", "т/ч"),
        ("лк", "лк"),
        ("Люкс", "лк"),
        ("милиметр", "мм"),
        ("миллиметры", "мм"),
        ("милилитр", "мл"),
        ("литр в минуту (л/мин)", "л/мин"),
        ("Ньютон", "Н"),
        ("млс", "мс"),
        ("Единица", "ед"),
        ("ед", "ед"),
        ("флак", "флак"),
        ("Страниц в минуту (стр/мин)", "стр/мин"),
        ("стр", "стр"),
        ("fr (френч)", "Fr"),
        ("ммоль/л", "ммоль/л"),
        ("чел", "чел"),
        ("Человек", "чел"),
        ("Тонна в сутки", "т/сут"),
        ("нед", "нед"),
        ("см вод. ст", "см вод. ст."),
        ("Сантиметр водяного столба", "см вод. ст."),
        ("Мпиксель", "Мпикс"),
        ("крат", "крат"),
        ("Н·м", "Н·м"),
        ("точек на дюйм (dpi)", "dpi"),
        ("миллилитр в минуту (мл/мин)", "мл/мин"),
        ("килоньютон", "кН"),
        ("балл", "балл"),
        ("Погонный метр", "м"),
        ("Тесла", "Тл"),
        ("д.", "сут"),
        ("киллограм", "кг"),
        ("грамм на квадраный метр", "г/м2"),
        ("литр в час (л/час)", "л/ч"),
        ("граммы", "г"),
        ("штуки", "шт"),
        ("метров в минуту (м/мин)", "м/мин"),
        ("миллиметр в секунду (мм/с)", "мм/с"),
        ("миллиампер в час", "мА·ч"),
        ("тестов", "тест"),
        ("ат", "ат"),
        ("кг/см[2*]", "кгс/см2"),
        ("мм вод. ст", "мм вод. ст."),
    ],
)
def test_frequent_catalog_units_are_known(raw: str, canon: str) -> None:
    assert normalize_unit(raw) == canon


def test_new_quantities_convert() -> None:
    from app.values import convert

    assert convert(1.0, "м3/ч", "л/мин") == pytest.approx(16.666666667)
    assert convert(36.0, "км/ч", "м/с") == pytest.approx(10.0)
    assert convert(100.0, "л.с.", "кВт") == pytest.approx(73.549875)
    assert convert(500.0, "мкл", "мл") == 0.5
    assert convert(2.0, "бар", "кПа") == 200.0
    assert convert(1.0, "кВт·ч", "Вт·ч") == 1000.0
    assert convert(1000.0, "Мбит/с", "Гбит/с") == 1.0
    assert convert(10000.0, "см2", "м2") == 1.0
