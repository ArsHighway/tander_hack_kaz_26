import pytest

from app.catalog import Catalog
from app.catalog.model import GENERAL_NAMES
from app.portal.standards import (
    ValueStats,
    build_standard_positions,
    canon_unit,
    clean_bound,
    country_title,
    maker_name,
    parse_standard,
    path_names,
)

DIR = 793362891
PATH = ".1.72095647.793362869.793362891."


def row(pcid: int, name: str, type_id: int, **extra: object) -> dict:
    return {
        "productCharacteristicName": name,
        "productCharacteristicId": pcid,
        "productCharacteristicDescriptionTypeId": type_id,
        "productionDirectoryId": DIR,
        "isLevelDefine": False,
        "deleted": False,
        **extra,
    }


STANDARD = {
    "id": DIR,
    "path": PATH,
    "name": "Смесители водоразборные",
    "root": "Товары для строительства",
    "entity": {
        "kpgz": {"id": 1, "code": "01.11.01.04.05.01", "name": "СМЕСИТЕЛИ", "path": ".1."},
        "okpd2": {"id": 2, "code": "28.14.12.110", "name": "Краны"},
    },
    "items": [
        # Официальная: обязательна, определяет позицию, заглушка диапазона, единица «г; лет».
        row(
            10,
            "Гарантийный срок",
            1,
            isObligatory=True,
            isLevelDefine=True,
            intStartValue=-2147483648,
            intEndValue=2147483647,
            productCharacteristicUnitDesignationNational="г; лет",
        ),
        # Официальная справочная: ссылки на значения, синоним, КТРУ и СПГЗ.
        row(
            20,
            "Материал корпуса",
            5,
            isObligatory=False,
            stringValue="292902520.292902522",
            productCharacteristicBaseCharacteristicId=99,
            productCharacteristicSynonyms=[{"id": 21, "name": "Материал"}],
            ktruValues=[{"code": "28.14.12.110-00000001", "name": "Смеситель"}],
            spgzValues=[{"spgzCode": "83958728", "name": "Материал корпуса"}],
        ),
        # Добавленные поставщиками: текст двумя строками и редкая.
        row(30, "Излив", 6, stringValue="изогнутый"),
        row(30, "Излив", 6, stringValue="прямой"),
        row(40, "Цвет ручки", 6, stringValue="хром"),
        # Официальный признак и удалённая строка.
        row(50, "Термостат", 4, isObligatory=False, boolValue=False),
        row(60, "Удалённая", 6, isObligatory=True, deleted=True),
        # Официальная дробная с настоящим диапазоном.
        row(
            70,
            "Высота излива",
            2,
            isObligatory=False,
            decimalStartValue=0.1,
            decimalEndValue=999999.0,
            productCharacteristicUnitDesignationNational="см",
        ),
    ],
}


def sku(sku_id: int, chars: list[tuple[int | None, str, object]], directory: int = DIR) -> dict:
    return {
        "id": sku_id,
        "name": f"Смеситель {sku_id}",
        "category_path": PATH,
        "directory_id": directory,
        "unit": "Штука",
        "characteristics": [
            {"char_id": cid, "name": name, "value": value, "unit": None}
            for cid, name, value in chars
        ],
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        (-2147483648, None),
        (2147483647, None),
        (-1e14, None),
        (999999, None),
        (99999.0, None),
        (9999999, None),
        (175, 175),
        (99, 99),
        (0.1, 0.1),
        (0, 0),
    ],
)
def test_clean_bound_drops_placeholders(raw: float | None, expected: float | None) -> None:
    assert clean_bound(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("г; лет", "лет"),
        ("Л; ДМ3", "л"),
        ("миллиметр (мм)", "мм"),
        ("мм", "мм"),
        ("[0*]С", "°C"),  # канон `units.py` — с латинской C
        ("литров в минуту", "л/мин"),  # единица словаря
        (None, None),
        ("", None),
    ],
)
def test_canon_unit(raw: str | None, expected: str | None) -> None:
    assert canon_unit(raw) == expected


@pytest.mark.parametrize(
    ("raw", "name", "expected"),
    [
        ("градус", "Угол наклона спинки", "°"),
        ("градус", "Температура хранения", "°C"),
        ("градусов", "Условия хранения", "°C"),
        ("° градус", "Угол открывания", "°"),
        ("° градус", "Рабочая температура", "°C"),
        ("градус цельсия (°с)", "Нагрев", "°C"),
        ("градусы", "Поле зрения", "°"),
    ],
)
def test_degree_is_an_angle_unless_the_characteristic_is_about_heat(
    raw: str, name: str, expected: str
) -> None:
    # «градус» в стандартах — чаще угол (1 868 строк против 202 температурных)
    assert canon_unit(raw, name) == expected


def test_contradicting_word_and_symbol_stay_as_written() -> None:
    # «мегаватт (мвт)» у лазера: слово — мега, строчное обозначение — милли; не угадываем
    assert canon_unit("мегаватт (мвт)") == "мегаватт (мвт)"
    assert canon_unit("милливатт (мвт)") == "мВт"


def test_parse_standard_groups_rows_and_reads_flags() -> None:
    std = parse_standard(STANDARD)
    assert (std.id, std.kpgz, std.okpd2) == (DIR, "01.11.01.04.05.01", "28.14.12.110")
    assert set(std.chars) == {10, 20, 30, 40, 50, 70}  # удалённая строка отброшена
    warranty = std.chars[10]
    assert warranty.kind == "int"
    assert (warranty.required, warranty.official, warranty.level_define) == (True, True, True)
    assert warranty.unit == "лет"
    assert warranty.range is None
    material = std.chars[20]
    assert material.kind == "dict"
    assert material.values == []  # ссылки справочника — не значения
    assert material.synonyms == ["Материал"]
    assert material.link_ids == {20, 21, 99}
    assert material.ktru == ["28.14.12.110-00000001"]
    assert material.spgz == ["83958728"]
    spout = std.chars[30]
    assert (spout.official, spout.values) == (False, ["изогнутый", "прямой"])
    height = std.chars[70]
    assert height.range is not None
    assert (height.range.min, height.range.max) == (0.1, None)


def test_value_stats_links_by_id_synonym_and_name() -> None:
    std = parse_standard(STANDARD)
    stats = ValueStats([std])
    stats.add(
        sku(1, [(10, "Гарантийный срок", 5), (21, "Материал", "латунь"), (30, "Излив", "прямой")])
    )
    stats.add(
        sku(2, [(None, "излив", "Прямой."), (999, "Непонятное", "x"), (99, "Материал", "латунь")])
    )
    stats.add(sku(3, [(30, "Излив", "прямой")], directory=1))  # чужая категория — пропуск
    assert stats.cards[DIR] == 2
    assert stats.present[(DIR, 20)] == 2
    assert stats.present[(DIR, 30)] == 2
    assert stats.values(DIR, 20, min_cards=2) == ["латунь"]
    assert stats.values(DIR, 30, min_cards=2) == ["прямой"]
    assert stats.unlinked["Непонятное"] == 1
    assert stats.linked == 5


def test_build_positions_keeps_standard_and_frequent_characteristics() -> None:
    std = parse_standard(STANDARD)
    stats = ValueStats([std])
    for i in range(10):
        chars: list[tuple[int | None, str, object]] = [
            (30, "Излив", "прямой" if i % 2 else "изогнутый")
        ]
        if i == 0:
            chars += [
                (40, "Цвет ручки", "хром"),
                (70, "Высота излива", 15.5),
                (50, "Термостат", True),
            ]
        stats.add(sku(i, chars))
    [pos] = build_standard_positions([std], stats, min_share=0.3, min_value_cards=2)
    assert pos.code == "01.11.01.04.05.01"
    assert (pos.kpgz, pos.okpd2, pos.name) == ("01.11.01.04.05.01", "28.14.12.110", std.name)
    specs = {s.name: s for s in pos.characteristics}
    # обязательная без значений, официальные заполненные и частая поставщицкая остаются;
    # редкая поставщицкая и официальная без единого значения — нет
    assert set(specs) - set(GENERAL_NAMES) == {
        "Гарантийный срок",
        "Излив",
        "Высота излива",
        "Термостат",
    }
    assert specs["Гарантийный срок"].required
    assert specs["Гарантийный срок"].level_define
    assert specs["Гарантийный срок"].kind == "quantitative"
    assert specs["Гарантийный срок"].range is None
    assert specs["Излив"].values == ["изогнутый", "прямой"]
    assert specs["Высота излива"].unit == "см"
    assert specs["Термостат"].values == ["Да", "Нет"]
    Catalog([pos], version="test")


def test_build_positions_free_text_and_duplicate_kpgz() -> None:
    std = parse_standard(STANDARD)
    twin = parse_standard({**STANDARD, "id": 5, "path": ".1.5.", "name": "Смесители другие"})
    stats = ValueStats([std, twin])
    for i in range(6):
        stats.add(sku(i, [(30, "Излив", f"вариант {i}")]))
        stats.add(sku(100 + i, [(30, "Излив", "прямой")], directory=5))
    positions = build_standard_positions(
        [std, twin], stats, min_share=0.3, min_value_cards=1, max_values=3
    )
    codes = sorted(p.code for p in positions)
    assert codes == ["01.11.01.04.05.01/5", "01.11.01.04.05.01/793362891"]
    by_name = {p.name: p for p in positions}
    spout = next(s for s in by_name[std.name].characteristics if s.name == "Излив")
    assert spout.values == []  # 6 разных значений > max_values — свободный текст
    Catalog(positions, version="test")


TREE = [
    {
        "id": 10,
        "name": "Бумага",
        "subCategories": [{"id": 11, "name": "Для офисной техники", "subCategories": []}],
    }
]


def test_path_names_follow_tree_without_root() -> None:
    assert path_names(TREE, ".1.10.11.") == ["Бумага", "Для офисной техники"]


def test_path_names_skip_unknown_ids() -> None:
    assert path_names(TREE, ".1.10.99.") == ["Бумага"]
    assert path_names(TREE, "") == []


def _one_position(**kwargs: object):
    std = parse_standard(STANDARD)
    stats = ValueStats([std])
    for i in range(4):
        stats.add(sku(i, [(30, "Излив", "прямой")]))
    [pos] = build_standard_positions([std], stats, min_share=0.3, **kwargs)  # type: ignore[arg-type]
    return pos


def test_positions_carry_portal_id_path_and_image() -> None:
    tree = [
        {
            "id": 72095647,
            "name": "Сантехника",
            "subCategories": [
                {
                    "id": 793362869,
                    "name": "Смесители",
                    "subCategories": [
                        {"id": DIR, "name": "Смесители водоразборные", "subCategories": []}
                    ],
                }
            ],
        }
    ]
    pos = _one_position(tree=tree, images={DIR: 231277051})
    assert pos.portal_id == DIR
    assert pos.path == ["Сантехника", "Смесители", "Смесители водоразборные"]
    assert pos.image_id == 231277051


def test_positions_without_tree_and_images_build_as_before() -> None:
    pos = _one_position()
    assert (pos.portal_id, pos.path, pos.image_id) == (DIR, [], None)


def test_image_sources_fresh_value_wins_but_null_does_not_erase() -> None:
    std = parse_standard({**STANDARD, "entity": {**STANDARD["entity"], "image_id": 5}})
    stats = ValueStats([std])
    for i in range(4):
        stats.add(sku(i, [(30, "Излив", "прямой")]))
    build = lambda images: build_standard_positions([std], stats, min_share=0.3, images=images)  # noqa: E731
    assert build(None)[0].image_id == 5
    assert build({DIR: 7})[0].image_id == 7
    assert build({DIR: None})[0].image_id == 5


def test_path_and_image_do_not_change_search_text() -> None:
    from app.search import build_bm25
    from app.search.vectors import position_text

    plain = _one_position()
    rich = plain.model_copy(update={"path": ["Сантехника", "Смесители"], "image_id": 231277051})
    assert position_text(rich) == position_text(plain)
    bm25_plain, bm25_rich = build_bm25(Catalog([plain], "t")), build_bm25(Catalog([rich], "t"))
    assert bm25_rich.doc_tokens(rich.code) == bm25_plain.doc_tokens(plain.code)


def test_catalog_tool_reads_tree_and_entities_files(tmp_path, monkeypatch) -> None:
    import json
    import sys

    from app.tools import build_standard_catalog

    (tmp_path / "standards.jsonl").write_text(
        json.dumps(STANDARD, ensure_ascii=False) + chr(10), "utf-8"
    )
    skus = [sku(i, [(30, "Излив", "прямой")]) for i in range(4)]
    (tmp_path / "ste.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + chr(10) for r in skus), "utf-8"
    )
    tree = [
        {
            "id": 72095647,
            "name": "Сантехника",
            "subCategories": [
                {
                    "id": 793362869,
                    "name": "Смесители",
                    "subCategories": [{"id": DIR, "name": "Водоразборные", "subCategories": []}],
                }
            ],
        }
    ]
    (tmp_path / "tree.json").write_text(json.dumps(tree, ensure_ascii=False), "utf-8")
    (tmp_path / "entities.jsonl").write_text(
        chr(10) + json.dumps({"id": DIR, "image_id": 231277051}) + chr(10) + chr(10), "utf-8"
    )

    def run(*extra: str) -> dict:
        out = tmp_path / "out.json"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "build_standard_catalog",
                "--standards",
                str(tmp_path / "standards.jsonl"),
                "--ste",
                str(tmp_path / "ste.jsonl"),
                "--out",
                str(out),
                "--exclude",
                str(tmp_path / "none.txt"),
                "--min-share",
                "0.3",
                *extra,
            ],
        )
        build_standard_catalog.main()
        [pos] = json.loads(out.read_text("utf-8"))["positions"]
        return pos

    pos = run("--tree", str(tmp_path / "tree.json"), "--entities", str(tmp_path / "entities.jsonl"))
    assert (pos["portal_id"], pos["path"], pos["image_id"]) == (
        DIR,
        ["Сантехника", "Смесители", "Водоразборные"],
        231277051,
    )
    bare = run(
        "--tree", str(tmp_path / "no-tree.json"), "--entities", str(tmp_path / "no-entities.jsonl")
    )
    assert bare["portal_id"] == DIR and "path" not in bare and "image_id" not in bare


def test_value_stats_skips_excluded_skus() -> None:
    stats = ValueStats([parse_standard(STANDARD)], exclude={2})
    stats.add(sku(1, [(30, "Излив", "прямой")]))
    stats.add(sku(2, [(30, "Излив", "изогнутый")]))
    assert stats.cards[DIR] == 1
    assert stats.values(DIR, 30) == ["прямой"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('ООО "Туринский ЦБЗ"', "Туринский ЦБЗ"),
        ('ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "СПРИНТ-ПЛАСТ"', "СПРИНТ-ПЛАСТ"),
        ("АО «Гознак»", "Гознак"),
        ("ИП Иванов Иван Иванович", "Иванов Иван Иванович"),
        ("Kimberly-Clark", "Kimberly-Clark"),
        ("Ромашка ООО", "Ромашка"),
    ],
)
def test_maker_name_drops_legal_form_and_quotes(raw: str, expected: str) -> None:
    assert maker_name(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("РОССИЯ", "Россия"),
        ("КОРЕЯ, РЕСПУБЛИКА", "Корея, Республика"),
        ("БОСНИЯ И ГЕРЦЕГОВИНА", "Босния и Герцеговина"),
    ],
)
def test_country_title(raw: str, expected: str) -> None:
    assert country_title(raw) == expected


def test_general_specs_from_category_cards() -> None:
    std = parse_standard(STANDARD)
    stats = ValueStats([std])
    cards = [
        ('ООО "Туринский ЦБЗ"', "РОССИЯ"),
        ('АО "ТУРИНСКИЙ ЦБЗ"', "РОССИЯ"),
        ("ООО Снегурочка", "КИТАЙ"),
        ('ООО "Туринский ЦБЗ"', "КИТАЙ"),
        ("ООО Редкий", "ГЕРМАНИЯ"),
    ]
    for i, (maker, country) in enumerate(cards):
        stats.add(
            {
                **sku(i, [(30, "Излив", "прямой")]),
                "manufacturer": maker,
                "country": country,
                "model": f"M{i}",
            }
        )
    [pos] = build_standard_positions([std], stats, min_share=0.3, min_value_cards=2)
    general = {s.name: s for s in pos.characteristics if s.group == "general"}
    assert list(general) == list(GENERAL_NAMES)
    # три СТЕ одного завода в разном написании — одно значение, показ — самое частое написание
    assert general["Производитель"].values == ["Туринский ЦБЗ"]
    assert general["Модель"].values == []
    assert general["Страна происхождения"].values == ["Китай", "Россия"]
    assert all(not s.required and s.kind == "qualitative" for s in general.values())


def test_general_does_not_duplicate_a_standard_characteristic() -> None:
    country = row(80, "Страна происхождения", 6, isObligatory=False, stringValue="Россия")
    std = parse_standard({**STANDARD, "items": [*STANDARD["items"], country]})
    stats = ValueStats([std])
    for i in range(3):
        stats.add({**sku(i, [(80, "Страна происхождения", "Россия")]), "country": "РОССИЯ"})
    [pos] = build_standard_positions([std], stats, min_share=0.3)
    named = [s for s in pos.characteristics if s.name == "Страна происхождения"]
    assert len(named) == 1 and named[0].group is None


def test_general_hints_are_ordered_by_count_and_limited() -> None:
    std = parse_standard(STANDARD)
    stats = ValueStats([std])
    n = 0
    # 60 заводов по 2 СТЕ и один — по 3: он первый, всего подсказок — 50
    for i in range(60):
        for _ in range(2):
            stats.add({**sku(n, [(30, "Излив", "прямой")]), "manufacturer": f"Завод {i:02d}"})
            n += 1
    for _ in range(3):
        stats.add({**sku(n, [(30, "Излив", "прямой")]), "manufacturer": "Главный завод"})
        n += 1
    [pos] = build_standard_positions([std], stats, min_share=0.3, min_value_cards=2)
    makers = next(s.values for s in pos.characteristics if s.name == "Производитель")
    assert makers[0] == "Главный завод"
    assert len(makers) == 50


def test_catalog_report_does_not_count_general_fields_as_characteristics() -> None:
    from app.catalog import CharacteristicSpec, Position
    from app.tools.build_standard_catalog import characteristic_report

    p = Position(
        code="P",
        name="Бумага",
        okpd2="17.12.14",
        kpgz="P",
        characteristics=[
            CharacteristicSpec(name="Плотность", kind="quantitative", unit="г/м2"),
            CharacteristicSpec(
                name="Производитель", kind="qualitative", values=["Снегурочка"], group="general"
            ),
        ],
        source="t",
    )
    kept, per_position = characteristic_report([p], {("P", "Бумага", "Плотность")})
    assert (kept["standard"], kept["supplier"], kept["general"], per_position) == (1, 0, 1, 1.0)


def test_level_fields_nobody_fills_are_not_supplier_characteristics() -> None:
    # «Виды бумаги для канцелярских нужд» и подобные: обязательные поля уровня классификатора без
    # перечня, их не заполнила ни одна из 3,68 млн СТЕ портала — карточка из-за них не «нужно
    # исправить». Поле уровня, которое СТЕ заполняют, остаётся.
    standard = {
        **STANDARD,
        "items": [
            *STANDARD["items"],
            row(80, "Виды смесителей", 6, isObligatory=True, isLevelDefine=True),
            row(81, "Вид смесителя", 6, isObligatory=True, isLevelDefine=True),
        ],
    }
    std = parse_standard(standard)
    stats = ValueStats([std])
    for i in range(4):
        stats.add(sku(i, [(30, "Излив", "прямой"), (81, "Вид смесителя", "кухонный")]))
    [pos] = build_standard_positions([std], stats, min_share=0.3, min_value_cards=2)
    names = {s.name for s in pos.characteristics}
    assert "Виды смесителей" not in names
    assert "Вид смесителя" in names


def test_category_with_only_level_fields_stays_a_position() -> None:
    # 372 категории портала задают только поля уровня: без них категория не должна пропасть из
    # справочника — товар всё равно в ней, просто без характеристик
    standard = {
        **STANDARD,
        "items": [row(80, "Виды смесителей", 6, isObligatory=True, isLevelDefine=True)],
    }
    std = parse_standard(standard)
    stats = ValueStats([std])
    stats.add(sku(1, []))
    [pos] = build_standard_positions([std], stats, min_share=0.3, min_value_cards=2)
    assert "Виды смесителей" not in {s.name for s in pos.characteristics}


def test_characteristic_names_are_cleaned_and_merged() -> None:
    # «Тип упаковки:» с двоеточием рядом с «Тип упаковки», «Ресурc» с латинской c
    standard = {
        **STANDARD,
        "items": [
            row(91, "Тип упаковки:", 6, isObligatory=True),
            row(92, "Тип упаковки", 6, isObligatory=True),
            row(93, "Ресурc", 1, isObligatory=True),
        ],
    }
    std = parse_standard(standard)
    stats = ValueStats([std])
    stats.add(sku(1, [(None, "Тип упаковки:", "коробка")]))  # СТЕ без id — по прежнему имени
    [pos] = build_standard_positions([std], stats, min_share=0.3, min_value_cards=1)
    names = [s.name for s in pos.characteristics if s.group != "general"]
    assert sorted(names) == ["Ресурс", "Тип упаковки"]
    assert stats.linked == 1


def test_level_field_without_values_is_never_required() -> None:
    # «Виды бумаги»: поле уровня без перечня, его пишут единицы СТЕ свободным текстом —
    # поставщик не знает, что туда писать; карточка из-за него не «нужно исправить»
    from app.catalog import CharacteristicSpec

    loose = CharacteristicSpec(
        name="Виды бумаги", kind="qualitative", required=True, level_define=True
    )
    assert loose.required is False
    listed = CharacteristicSpec(
        name="Вид смесителя",
        kind="qualitative",
        required=True,
        level_define=True,
        values=["кухонный"],
    )
    assert listed.required is True


def test_duplicate_names_merge_into_the_ktru_characteristic() -> None:
    # «Формат», «Формат листа», «Формат листов» у бумаги — одно поле; имя — как в КТРУ («Формат»
    # со ссылкой на позиции КТРУ), остальные — синонимы. «Цвет корпуса» — другое поле, не сливается.
    ktru = [{"code": "17.12.14.110-00000001", "name": "Бумага"}]
    standard = {
        **STANDARD,
        "name": "Бумага для офисной техники",
        "items": [
            row(1, "Формат", 6, isObligatory=True, ktruValues=ktru),
            row(2, "Формат листа", 6),
            row(3, "Формат листов", 6),
            row(4, "Цвет бумаги", 6),
            row(5, "Цвет", 6),
            row(6, "Цвет корпуса", 6),
            row(7, "Плотность бумаги", 1),
            row(8, "Плотность", 1),
        ],
    }
    std = parse_standard(standard)
    stats = ValueStats([std])
    for i in range(6):
        stats.add(sku(i, [
            (1, "Формат", "А4"),
            (2, "Формат листа", "А3" if i % 2 else "А4"),
            (3, "Формат листов", "А5"),
            (4, "Цвет бумаги", "белый"), (5, "Цвет", "белый"), (6, "Цвет корпуса", "серый"),
            (7, "Плотность бумаги", 80), (8, "Плотность", 80),
        ]))  # fmt: skip
    [pos] = build_standard_positions([std], stats, min_share=0.3, min_value_cards=1)
    specs = {s.name: s for s in pos.characteristics if s.group != "general"}
    assert set(specs) == {"Формат", "Цвет", "Цвет корпуса", "Плотность"}
    fmt = specs["Формат"]
    assert {"Формат листа", "Формат листов"} <= set(fmt.synonyms)
    assert set(fmt.values) == {"А4", "А3", "А5"} and fmt.ktru == ["17.12.14.110-00000001"]
    assert "Цвет бумаги" in specs["Цвет"].synonyms


def test_merge_keeps_different_characteristics_apart() -> None:
    from app.catalog import CharacteristicSpec
    from app.portal.standards import _merge_duplicates

    def q(name: str) -> CharacteristicSpec:
        return CharacteristicSpec(name=name, kind="qualitative")

    # «Тип кожи» у тоников — не «Тип» тоника: к общим «Тип»/«Вид» слово категории не присоединяется
    tonic = _merge_duplicates(
        [q("Тип"), q("Тип кожи"), q("Тип изделия")], "Тоники для ухода за кожей лица"
    )
    assert {s.name for s in tonic} == {"Тип", "Тип кожи"}
    # «грипп А» и «грипп В» — разные: короткие слова тоже считаются
    flu = _merge_duplicates(
        [q("Специфичность теста грипп А"), q("Специфичность теста грипп В")], "Тесты"
    )
    assert len(flu) == 2
    # не главное слово категории («изоляцией») не присоединяется
    wire = _merge_duplicates([q("Материал"), q("Материал изоляции")], "Провода с изоляцией")
    assert len(wire) == 2


def test_same_words_in_other_order_merge_into_one() -> None:
    # «Полотно прыжковое» и «Прыжковое полотно»: равный вес — каждое считало главным другое
    from app.catalog import CharacteristicSpec
    from app.portal.standards import _merge_duplicates

    a = CharacteristicSpec(name="Полотно прыжковое", kind="qualitative", values=["сетка"])
    b = CharacteristicSpec(name="Прыжковое полотно", kind="qualitative", values=["ткань"])
    [one] = _merge_duplicates([a, b], "Комплектующие для батутов")
    assert set(one.values) == {"сетка", "ткань"}


def test_found_synonym_pairs_merge_into_the_ktru_name() -> None:
    # пары по смыслу (модель или векторы, проверенные по СТЕ): «Плотность» и «Масса бумаги
    # площадью 1м2» — одно поле с именем КТРУ, второе имя — синоним, перечни и ссылки слиты
    from app.catalog import CharacteristicSpec
    from app.portal.standards import merge_pairs

    mass = CharacteristicSpec(
        name="Масса бумаги площадью 1м2", kind="quantitative", unit="г", ktru=["K"]
    )
    dens = CharacteristicSpec(
        name="Плотность", kind="quantitative", unit="г/м2", synonyms=["Плотность бумаги"]
    )
    other = CharacteristicSpec(name="Белизна", kind="quantitative", unit="%")
    out = merge_pairs(
        [dens, mass, other], [["Плотность", "Масса бумаги площадью 1м2"], ["Нет", "Белизна"]]
    )
    assert [s.name for s in out] == ["Масса бумаги площадью 1м2", "Белизна"]
    assert {"Плотность", "Плотность бумаги"} <= set(out[0].synonyms) and out[0].ktru == ["K"]
