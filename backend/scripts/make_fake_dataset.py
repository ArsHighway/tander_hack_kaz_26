"""Фейковый датасет для тестов приёма данных и ручной проверки LLM.

Форматы приёма: xlsx со строкой характеристик «Имя:Значение;…» и хвостом пустых
колонок, CSV без заголовка с «;», jsonl с парами. Справочник КТРУ —
в двух формах: вложенный json и «длинная» таблица. Коды, позиции и товары выдуманы, но в формате
настоящих. В `ground_truth.json` — ожидаемый код КТРУ и внесённые ошибки по каждой СТЕ.

Запуск: `uv run python scripts/make_fake_dataset.py` (детерминированно, пишет в tests/fixtures/fake/).
"""

import csv
import json
import sys
from pathlib import Path

from openpyxl import Workbook

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "fake"

# Позиции КТРУ: код, наименование, ОКПД2, единица, характеристики.
# values — исчерпывающий перечень, range — диапазон; required — обязательна по позиции.
KTRU = [
    {
        "code": "17.12.14.129-00000101",
        "name": "Бумага для офисной техники белая",
        "okpd2": "17.12.14.129",
        "unit": "пач",
        "characteristics": [
            {
                "name": "Формат",
                "kind": "qualitative",
                "values": ["A3", "A4", "A5"],
                "required": True,
            },
            {
                "name": "Масса бумаги площадью 1 м2",
                "kind": "quantitative",
                "unit": "г/м2",
                "values": [">=80 ... <90", ">=90 ... <100", ">=100"],
                "required": True,
            },
            {
                "name": "Белизна по CIE",
                "kind": "quantitative",
                "unit": "%",
                "range": {"min": 146, "max": 175},
                "required": False,
            },
            {
                "name": "Количество листов в пачке",
                "kind": "quantitative",
                "unit": "шт",
                "values": ["250", "500"],
                "required": True,
            },
            {"name": "Класс", "kind": "qualitative", "values": ["A", "B", "C"], "required": False},
        ],
    },
    {
        "code": "17.12.14.129-00000102",
        "name": "Бумага для офисной техники цветная",
        "okpd2": "17.12.14.129",
        "unit": "пач",
        "characteristics": [
            {"name": "Формат", "kind": "qualitative", "values": ["A3", "A4"], "required": True},
            {"name": "Цветность", "kind": "qualitative", "values": ["Цветная"], "required": True},
            {
                "name": "Интенсивность цвета",
                "kind": "qualitative",
                "values": ["Интенсив", "Медиум", "Неон", "Пастель"],
                "required": True,
            },
            {
                "name": "Количество листов в пачке",
                "kind": "quantitative",
                "unit": "шт",
                "values": ["50", "100", "250", "500"],
                "required": True,
            },
        ],
    },
    {
        "code": "22.22.11.000-00000201",
        "name": "Мешки для мусора",
        "okpd2": "22.22.11.000",
        "unit": "уп",
        "characteristics": [
            {
                "name": "Объем",
                "kind": "quantitative",
                "unit": "л",
                "values": ["30", "35", "60", "120", "240"],
                "required": True,
            },
            {
                "name": "Толщина",
                "kind": "quantitative",
                "unit": "мкм",
                "range": {"min": 8, "max": 120},
                "required": True,
            },
            {"name": "Материал", "kind": "qualitative", "values": ["ПВД", "ПНД"], "required": True},
            {
                "name": "Количество в упаковке",
                "kind": "quantitative",
                "unit": "шт",
                "range": {"min": 5, "max": 100},
                "required": False,
            },
        ],
    },
    {
        "code": "22.19.60.119-00000301",
        "name": "Перчатки смотровые нитриловые",
        "okpd2": "22.19.60.119",
        "unit": "пар",
        "characteristics": [
            {
                "name": "Размер",
                "kind": "qualitative",
                "values": ["XS", "S", "M", "L", "XL"],
                "required": True,
            },
            {
                "name": "Стерильность",
                "kind": "qualitative",
                "values": ["Нестерильные", "Стерильные"],
                "required": True,
            },
            {
                "name": "Опудренность",
                "kind": "qualitative",
                "values": ["Неопудренные", "Опудренные"],
                "required": False,
            },
        ],
    },
    {
        "code": "20.59.12.120-00000401",
        "name": "Картридж для МФУ лазерный",
        "okpd2": "20.59.12.120",
        "unit": "шт",
        "characteristics": [
            {
                "name": "Цвет печати",
                "kind": "qualitative",
                "values": ["Черный", "Голубой", "Пурпурный", "Желтый"],
                "required": True,
            },
            {
                "name": "Ресурс",
                "kind": "quantitative",
                "unit": "стр",
                "range": {"min": 500, "max": 40000},
                "required": True,
            },
            {
                "name": "Оригинальность",
                "kind": "qualitative",
                "values": ["Оригинальный", "Совместимый"],
                "required": False,
            },
        ],
    },
    {
        "code": "27.20.11.000-00000501",
        "name": "Батарея первичная",
        "okpd2": "27.20.11.000",
        "unit": "шт",
        "characteristics": [
            {
                "name": "Типоразмер",
                "kind": "qualitative",
                "values": ["AA", "AAA", "C", "D", "9V"],
                "required": True,
            },
            {
                "name": "Номинальное напряжение",
                "kind": "quantitative",
                "unit": "В",
                "values": ["1,5", "9"],
                "required": True,
            },
            {
                "name": "Электрохимическая система",
                "kind": "qualitative",
                "values": ["Щелочная", "Солевая", "Литиевая"],
                "required": True,
            },
        ],
    },
]

# СТЕ: (id, наименование, категория, производитель, характеристики, ожидаемый КТРУ, ошибки).
STE = [
    (
        "91000001",
        "Бумага офисная А4 80 г/м2 500 л",
        "Бумага для офисной техники",
        "ООО ПАПИРУС",
        "Формат:A4;Масса бумаги площадью 1м2:80.00000;Количество листов в пачке:500;Белизна по CIE, %:161",
        "17.12.14.129-00000101",
        [],
    ),
    (
        "91000002",
        "Бумага офисная Туринский ЦБЗ А4 500л",
        "Бумага для офисной техники",
        "ООО Туринский ЦБЗ",
        "Количество листов в пачке:500 шт;Формат:А4;Масса бумаги площадью 1м2:80 г;Толщина, мкм:104;"
        "Интенсивность цвета:Белый",
        "17.12.14.129-00000101",
        [
            {
                "type": "value_not_in_list",
                "characteristic": "Интенсивность цвета",
                "value": "Белый",
                "why": "у белой бумаги нет интенсивности цвета; это позиция цветной бумаги",
            },
            {
                "type": "cyrillic_in_latin_value",
                "characteristic": "Формат",
                "value": "А4",
                "expected": "A4",
            },
        ],
    ),
    (
        "91000003",
        "Бумага цветная неон А4 100 листов",
        "Бумага для офисной техники",
        "ООО ПАПИРУС",
        "Формат:A4;Цветность:Цветная;Интенсивность цвета:Неон;Количество листов в пачке:100",
        "17.12.14.129-00000102",
        [],
    ),
    (
        "91000004",
        "Бумага афисная A4",
        "Бумага для офисной техники",
        "ООО ПАПИРУС",
        "Формат:A4;Масса бумаги площадью 1м2:800",
        "17.12.14.129-00000101",
        [
            {"type": "typo_in_name", "value": "афисная", "expected": "офисная"},
            {
                "type": "out_of_range",
                "characteristic": "Масса бумаги площадью 1 м2",
                "value": "800",
                "why": "офисная бумага 60–300 г/м2; вероятно лишний ноль",
            },
            {"type": "missing_required", "characteristic": "Количество листов в пачке"},
        ],
    ),
    (
        "91000005",
        "Бумага А4",
        "Бумага для офисной техники",
        "ООО ПАПИРУС",
        "Формат:A4",
        None,
        [
            {
                "type": "ambiguous_ktru",
                "candidates": ["17.12.14.129-00000101", "17.12.14.129-00000102"],
                "ask": "Цветность или Белизна по CIE",
            }
        ],
    ),
    (
        "91000006",
        "Мешки для мусора 35л ПВД 30 шт",
        "Пакеты полимерные",
        "ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ СПРИНТ-ПЛАСТ",
        "Количество в упаковке:30.00000;Толщина, мкм:50.00000;Объем:35.00000;Цвет:черный;Вид материала:ПВД",
        "22.22.11.000-00000201",
        [{"type": "synonym_name", "characteristic": "Вид материала", "expected": "Материал"}],
    ),
    (
        "91000007",
        "Пакеты для мусора 120 л",
        "Пакеты полимерные",
        "ООО ПОЛИМЕР",
        "Объем:120 л;Толщина, мм:40;Материал:ПНД",
        "22.22.11.000-00000201",
        [
            {
                "type": "unit_mismatch",
                "characteristic": "Толщина",
                "value": "40 мм",
                "expected": "40 мкм",
                "why": "мешок толщиной 40 мм невозможен; в позиции КТРУ толщина в мкм",
            }
        ],
    ),
    (
        "91000008",
        "Мешки мусорные 240л черные",
        "Пакеты полимерные",
        "ООО ПОЛИМЕР",
        "",
        "22.22.11.000-00000201",
        [
            {
                "type": "empty_characteristics",
                "autofill_from_name": {"Объем": "240", "Цвет": "черный"},
            }
        ],
    ),
    (
        "91000009",
        "Перчатки нитриловые смотровые нестерильные неопудренные размер M, 50 пар",
        "Перчатки медицинские",
        "ООО МЕДТЕХ",
        "Размер:M;Стерильность:Нестерильные;Опудренность:Неопудренные",
        "22.19.60.119-00000301",
        [],
    ),
    (
        "91000010",
        "Перчатки нитриловые р-р XXL",
        "Перчатки медицинские",
        "ООО МЕДТЕХ",
        "Размер:XXL;Стерильность:нестерильн.",
        "22.19.60.119-00000301",
        [
            {
                "type": "value_not_in_list",
                "characteristic": "Размер",
                "value": "XXL",
                "why": "в позиции КТРУ размеры XS–XL",
            },
            {
                "type": "abbreviated_value",
                "characteristic": "Стерильность",
                "value": "нестерильн.",
                "expected": "Нестерильные",
            },
        ],
    ),
    (
        "91000011",
        "Картридж для МФУ HP 85A черный",
        "Картриджи",
        "HP Inc.",
        "Цвет печати:Черный;Ресурс:1600;Оригинальность:Оригинальный",
        "20.59.12.120-00000401",
        [],
    ),
    (
        "91000012",
        "Картридж совместимый CE285A",
        "Картриджи",
        "ООО ТОНЕР",
        "Цвет печати:Чорный;Ресурс, стр:1600 стр;Оригинальность:Совместимый",
        "20.59.12.120-00000401",
        [
            {
                "type": "typo_in_value",
                "characteristic": "Цвет печати",
                "value": "Чорный",
                "expected": "Черный",
            }
        ],
    ),
    (
        "91000013",
        "Батарейка АА LR6 алкалиновая",
        "Элементы питания",
        "ООО ЭНЕРДЖИ",
        "Типоразмер:АА;Номинальное напряжение, В:1,5;Электрохимическая система:Алкалиновая",
        "27.20.11.000-00000501",
        [
            {
                "type": "cyrillic_in_latin_value",
                "characteristic": "Типоразмер",
                "value": "АА",
                "expected": "AA",
            },
            {
                "type": "synonym_value",
                "characteristic": "Электрохимическая система",
                "value": "Алкалиновая",
                "expected": "Щелочная",
            },
        ],
    ),
    (
        "91000014",
        "Батарейка 9V крона",
        "Элементы питания",
        "ООО ЭНЕРДЖИ",
        "Типоразмер:9V;Номинальное напряжение:9;Электрохимическая система:Солевая",
        "27.20.11.000-00000501",
        [],
    ),
    (
        "91000015",
        "Бумага для офисной техники белая A4 500 листов 80 г/м2",
        "Бумага для офисной техники",
        "ООО ПАПИРУС",
        "Формат:A4;Количество листов в пачке:500;Масса бумаги площадью 1м2:80;Цвет:белая",
        "17.12.14.129-00000101",
        [
            {
                "type": "name_contains_value_and_unit",
                "why": "правило модерации: в наименовании не пишут значение и единицу",
            }
        ],
    ),
]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    OUT.mkdir(parents=True, exist_ok=True)
    header = [
        "Идентификатор СТЕ",
        "Наименование СТЕ",
        "Категория",
        "Производитель",
        "характеристики СТЕ",
    ]

    wb = Workbook()
    ws = wb.active
    ws.title = "СТЕ"
    ws.append([*header, *([None] * 19)])  # как в Перми: 24 колонки, из них 5 заполнены
    for row in STE:
        ws.append(list(row[:5]))
    wb.save(OUT / "ste.xlsx")

    with (OUT / "ste_headerless.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh, delimiter=";").writerows(r[:5] for r in STE)  # как в Москве: без заголовка

    with (OUT / "ste_cp1251.csv").open("w", encoding="cp1251", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(header)
        w.writerows(r[:5] for r in STE)

    with (OUT / "ste.jsonl").open("w", encoding="utf-8") as fh:
        for sid, name, cat, maker, chars, *_ in STE:
            pairs = [seg.split(":", 1) for seg in chars.split(";") if ":" in seg]
            rec = {
                "id": sid,
                "name": name,
                "category": cat,
                "manufacturer": maker,
                "attributes": pairs,
            }
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    (OUT / "ktru.json").write_text(
        json.dumps({"version": "fake-2026-09-30", "positions": KTRU}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    with (OUT / "ktru_characteristics.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(
            [
                "Код КТРУ",
                "Наименование позиции",
                "ОКПД2",
                "Характеристика",
                "Значение",
                "Единица",
                "Обязательная",
            ]
        )
        for pos in KTRU:
            for ch in pos["characteristics"]:
                values = ch.get("values") or [f"{ch['range']['min']} - {ch['range']['max']}"]
                for v in values:
                    w.writerow(
                        [
                            pos["code"],
                            pos["name"],
                            pos["okpd2"],
                            ch["name"],
                            v,
                            ch.get("unit", ""),
                            "Да" if ch["required"] else "Нет",
                        ]
                    )

    truth = [{"id": r[0], "ktru": r[5], "errors": r[6]} for r in STE]
    (OUT / "ground_truth.json").write_text(
        json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(
        f"СТЕ {len(STE)}, позиций КТРУ {len(KTRU)}, с ошибками {sum(bool(r[6]) for r in STE)} → {OUT}"
    )


if __name__ == "__main__":
    main()
