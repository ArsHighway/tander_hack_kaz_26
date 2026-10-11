from pathlib import Path

from app.catalog import Catalog, CharacteristicSpec, Position
from app.checks.detector import detect, verdict
from app.config import DEMO_CATALOG
from app.schemas import CardIn, CharIn

FAKE = Path(__file__).parent / "fixtures" / "fake"
CATALOG = Catalog.load([FAKE / "ktru.json"])
WHITE = CATALOG.get("17.12.14.129-00000101")
COLOR = CATALOG.get("17.12.14.129-00000102")

# Живая карточка портала 47453345 (docs/research/zakupki-portal.md):
# белая бумага с «Интенсивность цвета: Белый».
LIVE = CardIn(
    name="Бумага офисная Туринский ЦБЗ А4 500л",
    characteristics=[
        CharIn(name="Количество листов в пачке", value="500", unit="шт"),
        CharIn(name="Формат", value="А4"),
        CharIn(name="Масса бумаги площадью 1м2", value="80", unit="г"),
        CharIn(name="Толщина, мкм", value="104"),
        CharIn(name="Интенсивность цвета", value="Белый"),
    ],
)


def _by_field(issues: list, field: str) -> list:
    return [i for i in issues if i.field == field]


def test_value_outside_position_list_blocks() -> None:
    issues = detect(LIVE, COLOR)
    found = _by_field(issues, "characteristics.Интенсивность цвета")
    assert [(i.kind, i.severity) for i in found] == [("not_in_list", "block")]
    assert "Пастель" in found[0].why


def test_unit_in_characteristic_name_is_moderation_rule_with_fix() -> None:
    found = _by_field(detect(LIVE, COLOR), "characteristics.Толщина, мкм")
    rules = [i for i in found if i.kind == "moderation_rule"]
    assert len(rules) == 1
    assert rules[0].rule == "единица измерения в отдельном поле"
    assert rules[0].fix is not None
    assert (rules[0].fix.name, rules[0].fix.unit) == ("Толщина", "мкм")


def test_live_card_needs_fix() -> None:
    assert verdict(detect(LIVE, COLOR)) == "needs_fix"


def test_missing_required_characteristic_blocks_the_card() -> None:
    # карточку без обязательной характеристики модерация не пропустит — это не «совет».
    found = _by_field(detect(LIVE, COLOR), "characteristics.Цветность")
    assert [(i.kind, i.severity) for i in found] == [("moderation_rule", "block")]
    assert "Цветность" in found[0].message


def test_without_position_only_moderation_rules() -> None:
    issues = detect(LIVE, None)
    assert issues
    assert {i.kind for i in issues} == {"moderation_rule"}


def test_value_contradicting_name_is_a_mismatch() -> None:
    position = Position(
        code="T-1",
        name="Бумага",
        okpd2="17.12.14.129",
        source="test",
        characteristics=[
            CharacteristicSpec(name="Цветность", kind="qualitative", values=["Белая", "Цветная"])
        ],
    )
    card = CardIn(
        name="Бумага цветная А4", characteristics=[CharIn(name="Цветность", value="Белая")]
    )
    found = _by_field(detect(card, position), "characteristics.Цветность")
    assert [(i.kind, i.severity) for i in found] == [("mismatch", "block")]
    assert "цветная" in found[0].why.lower()


def test_typo_in_value_is_fixable_warning() -> None:
    card = CardIn(
        name="Бумага цветная", characteristics=[CharIn(name="Интенсивность цвета", value="Медиму")]
    )
    found = _by_field(detect(card, COLOR), "characteristics.Интенсивность цвета")
    assert [(i.kind, i.severity) for i in found] == [("typo", "warn")]
    assert found[0].fix is not None and found[0].fix.value == "Медиум"


def test_typo_in_characteristic_name_points_to_position_name() -> None:
    card = CardIn(
        name="Бумага цветная", characteristics=[CharIn(name="Цветнасть", value="Цветная")]
    )
    found = _by_field(detect(card, COLOR), "characteristics.Цветнасть")
    assert [i.kind for i in found] == ["typo"]
    assert found[0].fix is not None and found[0].fix.name == "Цветность"


def test_clean_card_is_ready() -> None:
    card = CardIn(
        name="Бумага для офисной техники белая",
        characteristics=[
            CharIn(name="Формат", value="A4"),
            CharIn(name="Масса бумаги площадью 1 м2", value="80", unit="г/м2"),
            CharIn(name="Количество листов в пачке", value="500", unit="шт"),
            CharIn(name="Класс", value="A"),
        ],
    )
    issues = detect(card, WHITE)
    assert issues == []
    assert verdict(issues) == "ready"


GLOVES_POS = CATALOG.get("22.19.60.119-00000301")


def test_contradiction_is_found_by_word_form() -> None:
    # «стерильная» в наименовании не совпадала со значением «Стерильные» целым словом.
    card = CardIn(
        name="Перчатки смотровые стерильная пара",
        characteristics=[CharIn(name="Стерильность", value="Нестерильные")],
    )
    found = _by_field(detect(card, GLOVES_POS), "characteristics.Стерильность")
    assert [(i.kind, i.severity) for i in found] == [("mismatch", "block")]
    assert found[0].fix is not None and found[0].fix.value == "Стерильные"


def test_same_value_in_another_word_form_is_not_a_contradiction() -> None:
    card = CardIn(
        name="Перчатки смотровые нестерильная пара",
        characteristics=[CharIn(name="Стерильность", value="Нестерильные")],
    )
    assert _by_field(detect(card, GLOVES_POS), "characteristics.Стерильность") == []


def test_multi_word_value_present_in_the_name_is_not_a_contradiction() -> None:
    # Справочник с портала: «Кресло офисное черное экокожа», значение «Черная экокожа» — то же,
    # а не противоречие с «Черный».
    spec = CharacteristicSpec(
        name="Требования к цветовому решению",
        kind="qualitative",
        values=["Черная экокожа", "Черный", "Серая ткань"],
    )
    chair = Position(code="K-1", name="Кресла офисные", okpd2="", characteristics=[spec],
                     source="test")  # fmt: skip
    card = CardIn(
        name="Кресло офисное черное экокожа",
        characteristics=[CharIn(name="Требования к цветовому решению", value="Черная экокожа")],
    )
    assert _by_field(detect(card, chair), "characteristics.Требования к цветовому решению") == []
    wrong = CardIn(
        name="Кресло офисное черное экокожа",
        characteristics=[CharIn(name="Требования к цветовому решению", value="Серая ткань")],
    )
    found = _by_field(detect(wrong, chair), "characteristics.Требования к цветовому решению")
    assert [i.kind for i in found] == ["mismatch"]


KETTLE_POS = Position(
    code="T-2",
    name="Чайник электрический",
    okpd2="27.51.24.110",
    source="test",
    characteristics=[CharacteristicSpec(name="Объем", kind="quantitative", unit="л")],
)


def test_number_in_name_contradicting_the_field_is_a_mismatch() -> None:
    # Жюри: «1,7 л» в наименовании и «1700 л» в поле не ловилось — ошибка единицы при вводе.
    card = CardIn(
        name="Чайник электрический 1,7 л",
        characteristics=[CharIn(name="Объем", value="1700", unit="л")],
    )
    found = _by_field(detect(card, KETTLE_POS), "characteristics.Объем")
    assert [(i.kind, i.severity) for i in found] == [("mismatch", "block")]
    assert "1,7 л" in found[0].why


def test_same_number_in_another_unit_is_not_a_contradiction() -> None:
    card = CardIn(
        name="Чайник электрический 1,7 л",
        characteristics=[CharIn(name="Объем", value="1700", unit="мл")],
    )
    assert _by_field(detect(card, KETTLE_POS), "characteristics.Объем") == []


def test_number_without_unit_in_name_is_not_compared() -> None:
    # «Чайник 2000» — модель, а не объём.
    card = CardIn(
        name="Чайник электрический 2000", characteristics=[CharIn(name="Объем", value="1,7")]
    )
    assert _by_field(detect(card, KETTLE_POS), "characteristics.Объем") == []


def test_swapped_dimensions_are_not_a_contradiction() -> None:
    # Портал: «Салфетка … 20*22см», длина 20 и ширина 22 — те же числа, роли переставлены.
    position = Position(
        code="T-3",
        name="Салфетка хозяйственная",
        okpd2="13.92.29.190",
        source="test",
        characteristics=[
            CharacteristicSpec(name="Длина", kind="quantitative", unit="см"),
            CharacteristicSpec(name="Ширина", kind="quantitative", unit="см"),
        ],
    )
    card = CardIn(
        name="Салфетка хозяйственная вискоза 20*22см",
        characteristics=[CharIn(name="Длина", value="20"), CharIn(name="Ширина", value="22")],
    )
    found = detect(card, position)
    assert [i for i in found if i.message.endswith("противоречит наименованию.")] == []


DEMO_GLOVES = Catalog.load([DEMO_CATALOG]).get("22.19.60.119-00000301")


def test_field_contradicting_the_description_is_a_mismatch() -> None:
    # Стенд: «Перчатки … нитриловые» и «Материал: латекс» — модуль молча ставил «Нитрил».
    card = CardIn(
        name="Перчатки смотровые нитриловые размер M",
        description="Материал: латекс, стерильность: нестерильные",
        characteristics=[CharIn(name="Материал", value="Нитрил")],
    )
    found = _by_field(detect(card, DEMO_GLOVES), "characteristics.Материал")
    assert [(i.kind, i.severity) for i in found] == [("mismatch", "warn")]
    assert "латекс" in found[0].why and "выбранную позицию" in found[0].why  # латекса в позиции нет


def test_field_agreeing_with_the_description_is_fine() -> None:
    card = CardIn(
        name="Перчатки смотровые размер M",
        description="Материал: нитрил",
        characteristics=[CharIn(name="Материал", value="Нитрил")],
    )
    assert _by_field(detect(card, DEMO_GLOVES), "characteristics.Материал") == []


def test_description_value_from_the_list_is_offered_as_the_fix() -> None:
    card = CardIn(
        name="Перчатки смотровые нитриловые размер M",
        description="стерильность: нестерильные",
        characteristics=[CharIn(name="Стерильность", value="Стерильные")],
    )
    found = _by_field(detect(card, DEMO_GLOVES), "characteristics.Стерильность")
    assert [(i.kind, i.fix.value if i.fix else None) for i in found] == [
        ("mismatch", "Нестерильные")
    ]


def _cord() -> Position:
    return Position(
        code="К", name="Удлинители", okpd2="27.33", source="t", characteristics=[
            CharacteristicSpec(name="Длина шнура", kind="quantitative", unit="м"),
            CharacteristicSpec(name="Объем", kind="quantitative", unit="л"),
            CharacteristicSpec(
                name="Температура", kind="quantitative", unit="°C", range={"min": -40, "max": 60}
            ),
        ],
    )  # fmt: skip


def test_negative_length_is_an_error_with_any_minus_sign() -> None:
    # «Длина шнура» с типографским минусом не ловилась; минус бывает и типографский
    for value in ("-5", "\u22125", "\u20135"):
        card = CardIn(
            name="Удлинитель", characteristics=[CharIn(name="Длина шнура", value=value, unit="м")]
        )
        issues = [i for i in detect(card, _cord()) if i.field == "characteristics.Длина шнура"]
        assert issues and issues[0].severity == "block", value
    # диапазон позиции разрешает минус — не ошибка
    ok = CardIn(
        name="Удлинитель", characteristics=[CharIn(name="Температура", value="-20", unit="°C")]
    )
    assert not [i for i in detect(ok, _cord()) if i.field == "characteristics.Температура"]


def test_unit_is_not_repeated_when_value_already_has_it() -> None:
    # в пакете значение приходит с единицей («17 л»): сообщение было «17 л л»
    card = CardIn(name="Чайник", characteristics=[CharIn(name="Объем", value="17 кг", unit="кг")])
    [issue] = [i for i in detect(card, _cord()) if i.field == "characteristics.Объем"]
    assert "кг кг" not in issue.message


def test_negative_is_fine_where_the_quantity_allows_it() -> None:
    # «Условия хранения -18 °C», «Постоянная призмы -30 мм», вакуумметр -0,095 МПа —
    # законный минус; запрещён только у массы, объёма, штук и размеров изделия
    pos = Position(
        code="Р", name="Разное", okpd2="1", source="t", characteristics=[
            CharacteristicSpec(name="Условия хранения", kind="quantitative", unit="°C"),
            CharacteristicSpec(name="Постоянная призмы", kind="quantitative", unit="мм"),
            CharacteristicSpec(name="Масса", kind="quantitative", unit="кг"),
        ],
    )  # fmt: skip

    def issues(name: str, value: str, unit: str) -> list:
        card = CardIn(name="Товар", characteristics=[CharIn(name=name, value=value, unit=unit)])
        return [i for i in detect(card, pos) if i.field == f"characteristics.{name}"]

    assert issues("Условия хранения", "-18", "°C") == []
    assert issues("Постоянная призмы", "-30", "мм") == []
    [bad] = issues("Масса", "-2", "кг")
    assert "перечн" not in bad.message  # у количественной характеристики перечня нет


def test_unit_mismatch_message_keeps_a_different_unit() -> None:
    # «5 мм» с единицей «м» — `endswith` без границы слова прятал вторую единицу
    card = CardIn(name="Удлинитель", characteristics=[CharIn(name="Объем", value="5 мм", unit="м")])
    [issue] = [i for i in detect(card, _cord()) if i.field == "characteristics.Объем"]
    assert "5 мм м" in issue.message


def test_minimum_of_four_does_not_block_a_position_with_fewer_characteristics() -> None:
    # у 372 категорий только поля уровня — «нужно не меньше 4» блокировал карточку,
    # которую нечем заполнить; правило требует не больше, чем есть у позиции
    pos = Position(
        code="К", name="Корма для ракообразных", okpd2="10.9", source="t", characteristics=[]
    )
    card = CardIn(name="Корм для креветок", characteristics=[])
    assert not [i for i in detect(card, pos) if "не меньше" in i.message]
    full = _cord()  # три характеристики у позиции: заполнены все три — минимум выполнен
    chars = [
        CharIn(name="Длина шнура", value="5", unit="м"),
        CharIn(name="Объем", value="1", unit="л"),
        CharIn(name="Температура", value="20", unit="°C"),
    ]
    assert not [
        i
        for i in detect(CardIn(name="Удлинитель", characteristics=chars), full)
        if "не меньше" in i.message
    ]
    assert [
        i
        for i in detect(CardIn(name="Удлинитель", characteristics=chars[:1]), full)
        if "не меньше" in i.message
    ]


def test_allowed_value_is_not_a_conflict() -> None:
    card = CardIn(name="Бумага цветная", characteristics=[CharIn(name="Формат", value="A4")])
    assert _by_field(detect(card, COLOR), "characteristics.Формат") == []


def test_number_outside_range_names_the_bounds() -> None:
    bags = CATALOG.get("22.22.11.000-00000201")
    card = CardIn(
        name="Мешки для мусора", characteristics=[CharIn(name="Толщина", value="200", unit="мкм")]
    )
    found = _by_field(detect(card, bags), "characteristics.Толщина")
    assert found[0].severity == "block"
    assert "200" in found[0].message
    assert "120" in found[0].message


def test_several_conflicts_stay_together() -> None:
    card = CardIn(
        name="Бумага цветная",
        characteristics=[
            CharIn(name="Формат", value="A5"),
            CharIn(name="Интенсивность цвета", value="Белый"),
        ],
    )
    fields = {issue.field for issue in detect(card, COLOR) if issue.severity == "block"}
    assert "characteristics.Формат" in fields
    assert "characteristics.Интенсивность цвета" in fields


def test_corrected_value_drops_only_its_conflict() -> None:
    fixed = CardIn(
        name="Бумага цветная",
        characteristics=[
            CharIn(name="Формат", value="A4"),
            CharIn(name="Интенсивность цвета", value="Белый"),
        ],
    )
    found = detect(fixed, COLOR)
    assert _by_field(found, "characteristics.Формат") == []
    assert _by_field(found, "characteristics.Интенсивность цвета")


PAPER_POS = Position(
    code="P-1",
    name="Бумага для офисной техники",
    okpd2="17.12.14.129",
    source="test",
    characteristics=[
        CharacteristicSpec(
            name="Белизна", kind="quantitative", unit="%", plausible={"min": 30, "max": 256}
        ),
        CharacteristicSpec(
            name="Плотность", kind="quantitative", unit="г/м2", plausible={"min": 15, "max": 600}
        ),
    ],
)


def test_number_far_from_category_values_is_a_warning() -> None:
    # «Белизна 1460 %», «Плотность 8000 г/м²» проходили без замечаний.
    card = CardIn(
        name="Бумага А4",
        characteristics=[
            CharIn(name="Белизна", value="1460", unit="%"),
            CharIn(name="Плотность", value="8000", unit="г/м2"),
        ],
    )
    issues = detect(card, PAPER_POS)
    whiteness = _by_field(issues, "characteristics.Белизна")
    assert [(i.kind, i.severity) for i in whiteness] == [("mismatch", "warn")]
    assert "не больше 256 %" in whiteness[0].message
    density = _by_field(issues, "characteristics.Плотность")
    assert [(i.kind, i.severity) for i in density] == [("mismatch", "warn")]


def test_usual_number_and_position_without_range_are_fine() -> None:
    card = CardIn(
        name="Бумага А4",
        characteristics=[
            CharIn(name="Белизна", value="146", unit="%"),
            CharIn(name="Плотность", value="80", unit="г/м2"),
        ],
    )
    assert [i for i in detect(card, PAPER_POS) if i.field.startswith("characteristics.")] == []
    no_range = CardIn(name="Чайник", characteristics=[CharIn(name="Объем", value="1700")])
    assert _by_field(detect(no_range, KETTLE_POS), "characteristics.Объем") == []


BREAKER_POS = Position(
    code="E-1",
    name="Выключатели автоматические",
    okpd2="27.12.22.000",
    source="test",
    characteristics=[
        CharacteristicSpec(name="Номинальный ток", kind="quantitative", unit="А"),
        CharacteristicSpec(name="Количество листов", kind="quantitative", unit="шт"),
    ],
)


def test_number_in_description_disagreeing_with_the_field_is_a_warning() -> None:
    # сверка числа шла только с наименованием.
    card = CardIn(
        name="Автоматический выключатель",
        description="Номинальный ток 16 А, 1 полюс. Блокнот на 30 листов в комплекте",
        characteristics=[
            CharIn(name="Номинальный ток", value="10", unit="А"),
            CharIn(name="Количество листов", value="300", unit="шт"),
        ],
    )
    issues = detect(card, BREAKER_POS)
    current = _by_field(issues, "characteristics.Номинальный ток")
    assert [(i.kind, i.severity) for i in current] == [("mismatch", "warn")]
    assert "16 А" in current[0].message
    sheets = _by_field(issues, "characteristics.Количество листов")
    assert [(i.kind, i.severity) for i in sheets] == [("mismatch", "warn")]


def test_number_in_description_agreeing_with_the_field_is_fine() -> None:
    card = CardIn(
        name="Автоматический выключатель 0,016 кА",
        description="Ток 16 А",
        characteristics=[CharIn(name="Номинальный ток", value="16", unit="А")],
    )
    assert _by_field(detect(card, BREAKER_POS), "characteristics.Номинальный ток") == []
