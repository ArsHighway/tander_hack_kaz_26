"""Живые карточки Портала поставщиков: аудит по правилам модерации, заполнение против портала,
справочник из наблюдаемых значений."""

import json
from pathlib import Path

import pytest

from app.portal import (
    PortalReport,
    ValueIndex,
    audit,
    build_positions,
    compare_card,
    fill_rows,
    load_cards,
    parse_card,
)

PAPER = ".1.1100033.793372788.793372794.793410722."


def _raw(id_: int, name: str, chars: list[tuple], path: str = PAPER, description: str = "") -> dict:
    return {
        "id": id_,
        "root": "Изделия канцелярские",
        "category": "Бумага для офисной техники",
        "category_path": path,
        "name": name,
        "description": description,
        "okpd": "17.12.14.129",
        "characteristics": [
            {"name": n, "value": v, "unit": u, "type_id": t, "level_define": None}
            for n, v, u, t in chars
        ],
    }


def _paper(id_: int, name: str, fmt: str = "А4", sheets: int = 500, color: str = "Белая") -> dict:
    return _raw(
        id_,
        name,
        [
            ("Формат", fmt, None, 5),
            ("Количество листов в пачке", sheets, "шт", 1),
            ("Цветность", color, None, 5),
            ("Марка бумаги", f"Марка {id_}", None, 6),
        ],
    )


CARDS = [
    parse_card(_paper(1, "Бумага офисная А4 500 листов")),
    parse_card(_paper(2, "Бумага SvetoCopy, белая")),
    parse_card(_paper(3, "Бумага Снегурочка А3 250 листов", fmt="А3", sheets=250)),
    parse_card(_paper(4, "Бумага цветная А4", color="Цветная")),
]


def _cards() -> list:
    return [c for c in CARDS if c is not None]


def test_values_are_text_as_on_the_portal() -> None:
    card = parse_card(
        _raw(
            9,
            "Календарь 2027",
            [
                ("Год", 2027.0, "г; лет", 2),
                ("Толщина", 0.5, "мм", 2),
                ("С логотипом", True, None, 4),
            ],
        )
    )
    assert card is not None
    got = {c.name: (c.value, c.unit, c.numeric, c.boolean) for c in card.characteristics}
    assert got == {
        "Год": ("2027", "г", True, False),
        "Толщина": ("0,5", "мм", True, False),
        "С логотипом": ("Да", None, False, True),
    }
    assert card.url == "https://zakupki.mos.ru/sku/view/9"


def test_load_cards_drops_duplicates_and_nameless(tmp_path: Path) -> None:
    f = tmp_path / "a.jsonl"
    rows = [_paper(1, "Бумага"), _paper(1, "Бумага"), _raw(2, "  ", [])]
    f.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    assert [c.id for c in load_cards([f])] == [1]


def test_list_values_leave_the_card_itself_out() -> None:
    # Перечень для карточки — из других карточек категории: иначе её собственное значение
    # попадает в перечень и заполнение выглядит лучше, чем есть.
    index = ValueIndex(_cards())
    first = _cards()[0]
    assert index.values(PAPER, "Формат", exclude=first) == ["А4", "А3"]
    assert index.values(PAPER, "Марка бумаги", exclude=first) == ["Марка 2", "Марка 3", "Марка 4"]
    assert "Марка 1" not in index.values(PAPER, "Марка бумаги", exclude=first)
    assert index.values(PAPER, "Цветность") == ["Белая", "Цветная"]


def test_fill_compares_module_values_with_the_portal() -> None:
    index = ValueIndex(_cards())
    outcomes = {o.name: o for o in compare_card(_cards()[0], index)}
    assert (outcomes["Формат"].extracted, outcomes["Формат"].correct) == ("А4", True)
    sheets = outcomes["Количество листов в пачке"]
    assert (sheets.extracted, sheets.correct, sheets.evidenced) == ("500", True, True)
    # «Белая» в наименовании нет: модуль не обязан её найти, полнота считается по видимым в тексте.
    assert (outcomes["Цветность"].evidenced, outcomes["Цветность"].extracted) == (False, None)


def test_fill_rows_count_precision_and_recall_over_evidenced_values() -> None:
    index = ValueIndex(_cards())
    outcomes = [o for c in _cards() for o in compare_card(c, index)]
    rows = {r.key: r for r in fill_rows(outcomes)}
    assert set(rows) == {"portal_evidence", "portal_fill_precision", "portal_fill_recall"}
    evidenced = [o for o in outcomes if o.evidenced]
    assert rows["portal_fill_recall"].n == len(evidenced)
    assert rows["portal_evidence"].n == len(outcomes)
    assert 0.0 < rows["portal_fill_recall"].value <= 1.0


def test_audit_counts_moderation_rules_on_live_cards() -> None:
    few = parse_card(_raw(5, "Мешки для мусора 60л", [("Объем", 60, "л", 1)], path=".1.2."))
    report = audit([*_cards(), few])  # type: ignore[list-item]
    rules = {r.rule: r for r in report.rules}
    assert report.cards == 5
    title = rules["в наименовании нет значения и единицы"]
    assert {e.id for e in title.examples} >= {1, 3, 5}
    assert rules["не меньше 4 характеристик"].cards == 1
    assert rules["не меньше 4 характеристик"].examples[0].url.endswith("/5")
    assert report.with_issues >= 3
    assert abs(report.share - report.with_issues / report.cards) < 1e-9


def _volume_card(id_: int, name: str, volume: float, char: str = "Объем") -> object:
    return parse_card(
        _raw(id_, name, [(char, volume, "л", 2), ("Цвет", "Черный", None, 5),
             ("Материал", "Сталь", None, 5), ("Количество", 1, "шт", 1)], path=".1.2.")
    )  # fmt: skip


def test_audit_finds_value_contradicting_the_name() -> None:
    # Перепутана единица: в наименовании 900 мл, в характеристике 900 л (портал, СТЕ 35545038).
    wrong = _volume_card(7, "Средство для мытья пола 900 мл", 900)
    report = audit([*_cards(), wrong])  # type: ignore[list-item]
    rules = {r.rule: r for r in report.rules}
    found = rules["значение не противоречит наименованию товара"]
    assert [e.id for e in found.examples] == [7]
    assert "«900 мл»" in found.examples[0].message


def test_typo_share_boundary_is_inclusive() -> None:
    from app.portal.audit import input_slip

    assert input_slip(1.5, 1.2)  # ровно 20 %: портал, «Varta … 1.5В» и «1,2 В»
    assert not input_slip(20, 26)


def test_another_property_with_the_same_unit_is_not_a_contradiction() -> None:
    # «Ведро 15 л» и «Объем вкладыша в урне 22 л» — разные величины, а не ошибка ввода.
    bucket = _volume_card(8, "Ведро мусорное с педалью 15 л", 22, "Объем вкладыша в урне")
    negative = _volume_card(9, "Канистра 20 л", -20)
    rules = {r.rule: r for r in audit([bucket, negative]).rules}  # type: ignore[list-item]
    assert [e.id for e in rules["значение не противоречит наименованию товара"].examples] == [9]


def test_positions_from_dense_categories() -> None:
    index = ValueIndex(_cards())
    positions = build_positions(_cards(), index, min_cards=4, min_value_cards=2)
    assert [p.code for p in positions] == ["КПГЗ 793410722"]
    specs = {s.name: s for s in positions[0].characteristics}
    assert specs["Формат"].values == ["А4"]  # «А3» — у одной карточки, в перечень не идёт
    assert specs["Количество листов в пачке"].kind == "quantitative"
    assert specs["Количество листов в пачке"].unit == "шт"
    assert specs["Марка бумаги"].values == []  # у каждой карточки своя — это не перечень
    assert all(s.required for s in positions[0].characteristics)
    assert positions[0].okpd2 == "17.12.14.129"
    assert build_positions(_cards(), index, min_cards=5) == []


def test_report_is_serializable() -> None:
    from app.portal import build_report

    report = build_report(_cards())
    again = PortalReport.model_validate_json(report.model_dump_json())
    assert again.cards == 4 and again.audit.cards == 4
    assert {r.key for r in again.fill} == {"portal_evidence", "portal_fill_precision",
                                           "portal_fill_recall"}  # fmt: skip


def test_supplier_gloss_in_brackets_counts_as_the_same_value() -> None:
    from app.portal import PortalChar
    from app.portal.fill import same_value

    assert same_value("Желтый", PortalChar(name="Цвет", value="желтый (yellow)"))
    assert same_value("AAA", PortalChar(name="Типоразмер", value="AAA (мизинчиковые)"))
    assert not same_value("Красный", PortalChar(name="Цвет", value="желтый (yellow)"))


def test_counts_in_the_name_are_not_contradictions() -> None:
    # «10+2 шт», «3 шт серебро и 3 шт золото» — суммы и составы, а не противоречие количеству.
    pack = parse_card(
        _raw(8, "Батарейка алкалиновая AAA 1,5В 10+2 шт",
             [("Количество в упаковке", 12, "шт", 1), ("Тип", "Алкалиновая", None, 5),
              ("Типоразмер", "AAA", None, 5), ("Напряжение", 1.5, "В", 2)], path=".1.3.")
    )  # fmt: skip
    volts = parse_card(
        _raw(9, "Элемент питания воздушно-цинковый 312 1.45В",
             [("Номинальное напряжение", 1.4, "В", 2), ("Тип", "Воздушно-цинковый", None, 5),
              ("Типоразмер", "312", None, 5), ("Количество", 6, "шт", 1)], path=".1.3.")
    )  # fmt: skip
    rules = {r.rule: r for r in audit([pack, volts]).rules}  # type: ignore[list-item]
    assert [e.id for e in rules["значение не противоречит наименованию товара"].examples] == [9]


def test_spelling_variants_are_merged_into_the_most_common_value() -> None:
    # Портал, картриджи: «Совместимый(ая)», «Оригианльный», «Картридж;» — варианты, а не значения.
    names = ["Оригинальный", "Оригинальный", "Оригинальный", "Оригианльный", "Совместимый",
             "Совместимый", "Совместимый(ая)", "Оригинал"]  # fmt: skip
    cards = [
        parse_card(_raw(i, f"Картридж {i}", [("Оригинальность", v, None, 5)], path=".1.4."))
        for i, v in enumerate(names)
    ]
    index = ValueIndex(cards)  # type: ignore[arg-type]
    assert index.values(".1.4.", "Оригинальность") == ["Оригинальный", "Совместимый", "Оригинал"]


def test_tool_writes_report_and_a_loadable_catalog(tmp_path: Path) -> None:
    from app.catalog import Catalog
    from app.tools.portal_report import main

    src = tmp_path / "cards.jsonl"
    rows = [_paper(i, f"Бумага офисная А4 500 листов {i}") for i in range(1, 6)]
    src.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    out, cat = tmp_path / "portal.json", tmp_path / "portal_catalog.json"
    assert main([str(src), "--out", str(out), "--catalog", str(cat), "--min-cards", "5"]) == 0
    assert PortalReport.model_validate_json(out.read_text(encoding="utf-8")).cards == 5
    position = Catalog.load([cat]).get("КПГЗ 793410722")
    assert position is not None and position.source == "portal"
    assert main([str(tmp_path / "missing.jsonl"), "--out", str(out)]) == 1


def test_value_is_shown_without_trailing_punctuation() -> None:
    names = ["Экокожа.", "Экокожа.", "Экокожа"]
    cards = [
        parse_card(_raw(i, f"Кресло {i}", [("Обивка", v, None, 5)], path=".1.5."))
        for i, v in enumerate(names)
    ]
    assert ValueIndex(cards).values(".1.5.", "Обивка") == ["Экокожа"]  # type: ignore[arg-type]


def test_overlong_characteristic_does_not_break_the_audit() -> None:
    long_name = "Оценка состояния систем " + "и показателей работы " * 30
    card = parse_card(
        _raw(
            9, "ИБП 1000 ВА", [(long_name, True, None, 4), ("Мощность", 600, "Вт", 1)], path=".1.2."
        )
    )
    report = audit([card])  # type: ignore[list-item]
    assert report.cards == 1
    assert any(r.rule == "характеристика укладывается в лимиты полей" for r in report.rules)


# Сравнение и «видно в тексте» на карточках портала.
def _ch(value: str, unit: str | None = None, numeric: bool = False) -> object:
    from app.portal import PortalChar

    return PortalChar(name="X", value=value, unit=unit, numeric=numeric)


@pytest.mark.parametrize(
    ("got", "truth"),
    [("5W30", "5W-30"), ("G 13", "G13"), ("Usb3.0", "USB 3.0"), ("10 XL", "10 (XL)")],
)
def test_same_value_ignores_spaces_hyphens_and_brackets(got: str, truth: str) -> None:
    from app.portal.fill import same_value

    assert same_value(got, _ch(truth))  # type: ignore[arg-type]


def test_partial_value_is_marked_but_not_correct() -> None:
    from app.portal.fill import partial_value, same_value

    truth = _ch("мыло жидкое")
    assert not same_value("жидкое", truth)  # type: ignore[arg-type]
    assert partial_value("жидкое", truth)  # type: ignore[arg-type]
    assert not partial_value("твёрдое", truth)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "unit", "text"),
    [
        ("50", "Гц", "Автомат ВА47-29 3Р 50А"),  # число с чужой единицей
        ("30", None, "Стабилизатор SVA30"),  # число внутри кода модели
        ("120", "г", "Блокнот 148x120мм"),  # размер, а не масса
    ],
)
def test_number_by_chance_is_not_evidence(value: str, unit: str | None, text: str) -> None:
    from app.portal.fill import evidenced

    assert not evidenced(_ch(value, unit, numeric=True), text)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "unit", "text"),
    [
        ("1800", "мм", "Кабель HDMI 1.8 м"),
        ("4,5", "А·ч", "Аккумулятор 4500 mAh"),
        ("5", "м", "Удлинитель 5,0 м"),
        ("12", "В", "Аккумулятор 7Ач,12В"),
        ("6", "шт", "Сетевой фильтр 6 розеток"),
    ],
)
def test_number_with_converted_unit_is_evidence(value: str, unit: str, text: str) -> None:
    from app.portal.fill import evidenced

    assert evidenced(_ch(value, unit, numeric=True), text)  # type: ignore[arg-type]


def test_number_written_as_text_is_numeric() -> None:
    card = parse_card(_raw(7, "Напильник 3,5 мм", [("Диаметр", "3,5", "мм", 5)]))
    assert card is not None
    assert card.characteristics[0].numeric


def test_number_in_another_unit_of_the_same_quantity_is_the_same_value() -> None:
    from app.portal import PortalChar
    from app.portal.fill import same_value

    truth = PortalChar(name="Диаметр", value="8", unit="см", numeric=True)
    assert same_value("80", truth, unit="мм")
    assert not same_value("8", truth, unit="мм")


@pytest.mark.parametrize(
    ("value", "unit", "text"),
    [
        ("12", "мес", "Катетер 12 CH"),  # чужое слово после числа при единице «мес»
        ("1", None, "Кронштейн SL-P-01"),  # номер в конце артикула
        ("0", "шт", "Корпус Procase UM330-B-0"),
        ("1", "шт", "Бинт ТУ 9393-001"),  # ведущий ноль — код
    ],
)
def test_code_numbers_are_not_evidence(value: str, unit: str | None, text: str) -> None:
    from app.portal.fill import evidenced

    assert not evidenced(_ch(value, unit, numeric=True), text)  # type: ignore[arg-type]


def test_unitless_value_is_seen_next_to_a_unit() -> None:
    from app.portal.fill import evidenced

    assert evidenced(_ch("12", None, numeric=True), "Аккумулятор 12В 7Ач")  # type: ignore[arg-type]


def test_value_equal_to_the_gloss_in_brackets() -> None:
    from app.portal import PortalChar
    from app.portal.fill import same_value

    assert same_value("нитрил", PortalChar(name="Материал", value="Нитрильный латекс (нитрил)"))
