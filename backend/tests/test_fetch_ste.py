import json

import httpx

from app.tools.fetch_ste import chars_line, csv_row, fetch_page, full_record, leaves, pages

TREE = [
    {
        "name": "Бумажная продукция",
        "treePathId": ".1.10.",
        "isLastLevel": False,
        "subCategories": [
            {"name": "Бланки", "treePathId": ".1.10.11.", "isLastLevel": True, "subCategories": []},
            {
                "name": "Ежедневники",
                "treePathId": ".1.10.12.",
                "isLastLevel": False,
                "subCategories": [
                    {"name": "Недатированные", "treePathId": ".1.10.12.13.", "isLastLevel": True},
                ],
            },
        ],
    }
]


def test_leaves_are_last_level_categories_with_their_root() -> None:
    assert list(leaves(TREE)) == [
        {"path": ".1.10.11.", "name": "Бланки", "root": "Бумажная продукция"},
        {"path": ".1.10.12.13.", "name": "Недатированные", "root": "Бумажная продукция"},
    ]


def test_chars_line_is_perm_export_format() -> None:
    chars = [
        {"name": "Формат", "value": "A4", "unit": None},
        {"name": "Количество листов в пачке", "value": 500.0, "unit": "шт"},
        {"name": "Белизна", "value": 98.5, "unit": "%"},
        {"name": "Пустая", "value": None, "unit": None},
        {"name": "Состав", "value": "хлопок; лён", "unit": None},
    ]
    assert chars_line(chars) == (
        "Формат:A4;Количество листов в пачке:500 шт;Белизна:98.5 %;Состав:хлопок, лён"
    )


def test_csv_row_keeps_columns_in_order() -> None:
    rec = {
        "id": 7,
        "name": "Бланк",
        "description": "",
        "category": "Бланки",
        "category_path": ".1.10.11.",
        "manufacturer": "",
        "okpd": "17.23",
        "unit": "Штука",
        "characteristics": [{"name": "Формат", "value": "A5", "unit": None}],
    }
    assert csv_row(rec) == [
        *["7", "Бланк", "", "Бланки", ".1.10.11.", "", "17.23", "Штука"],
        "Формат:A5",
    ]


def test_pages_cover_whole_category_in_chunks() -> None:
    assert pages(2500, None, 1000) == [(0, 1000), (1000, 1000), (2000, 500)]
    assert pages(2500, 200, 1000) == [(0, 200)]
    assert pages(150, 200, 1000) == [(0, 150)]
    assert pages(0, None, 1000) == []


def test_full_record_keeps_codes_price_and_characteristic_ids() -> None:
    item = {
        "id": 47537001,
        "name": "Смеситель для умывальника РМС SUS124-001F-1",
        "productionDirectoryName": "Смесители водоразборные",
        "productionDirectoryPath": ".1.72095647.793362869.793362891.",
        "productionDirectoryId": 793362891,
        "productionCode": "01.11.01.04.05.01",
        "okpdId": 1128111,
        "okeiName": "Штука",
        "modelName": "SUS124-001F-1",
        "manufacturerName": "РМС",
        "referencePrice": 1951.36,
        "oksmName": "РОССИЯ",
        "skuCharacteristics": [
            {
                "productCharacteristicValueId": 1700385460,
                "characteristicValueDecimalValue": 1.25,
                "productCharacteristicValue": {
                    "productCharacteristicName": "Вес",
                    "productCharacteristicId": 1558620195,
                    "productCharacteristicDescriptionTypeId": 2,
                    "productCharacteristicUnitDesignationNational": "кг",
                },
            }
        ],
    }
    rec = full_record(item, "Товары для строительства")
    assert rec["okpd"] == "01.11.01.04.05.01"  # старое поле: на деле код КПГЗ, читатели его ждут
    assert rec["kpgz"] == "01.11.01.04.05.01"
    assert rec["directory_id"] == 793362891
    assert rec["okpd_id"] == 1128111
    assert rec["model"] == "SUS124-001F-1"
    assert rec["price"] == 1951.36
    assert rec["country"] == "РОССИЯ"
    assert rec["characteristics"] == [
        {
            "name": "Вес",
            "value": 1.25,
            "unit": "кг",
            "char_id": 1558620195,
            "value_id": 1700385460,
            "type": 2,
        }
    ]


def test_fetch_page_asks_for_uncollapsed_results() -> None:
    """Без doNotCollapse поиск склеивает варианты: «Папки пластиковые» — 5753 из 9211 СТЕ."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"items": [{"id": 1}], "count": 1})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        items, count = fetch_page(client, ".1.10.11.", 2000, 1000)
    assert (items, count) == ([{"id": 1}], 1)
    params = seen[0].url.params
    assert params["doNotCollapse"] == "true"
    query = json.loads(params["queryFilter"])
    assert (query["skip"], query["take"]) == (2000, 1000)


def test_load_tree_expands_branches_cut_by_depth() -> None:
    """CategoryList отдаёт дерево не до конца: у 443 нелистовых узлов «Товаров» пустой
    subCategories — их ветки (бумага для офисной техники, медицинские перчатки) догружаются."""
    from app.tools.fetch_ste import load_tree

    answers = {
        "1": [
            {"id": 10, "name": "Бумага", "treePathId": ".1.10.", "isLastLevel": False,
             "subCategories": []},
            {"id": 20, "name": "Ручки", "treePathId": ".1.20.", "isLastLevel": True},
        ],
        "10": [
            {"id": 11, "name": "Для офисной техники", "treePathId": ".1.10.11.",
             "isLastLevel": True},
            {"id": 12, "name": "Для плоттеров", "treePathId": ".1.10.12.", "isLastLevel": False,
             "subCategories": []},
        ],
        "12": [{"id": 13, "name": "Рулоны", "treePathId": ".1.10.12.13.", "isLastLevel": True}],
    }  # fmt: skip

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=answers[request.url.params["parentId"]])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        tree = load_tree(client, 1, pause=0)
    assert [leaf["path"] for leaf in leaves(tree)] == [".1.10.11.", ".1.10.12.13.", ".1.20."]
    assert [leaf["root"] for leaf in leaves(tree)] == ["Бумага", "Бумага", "Ручки"]


def test_branch_cut_by_the_portal_is_fetched_by_its_path() -> None:
    # CategoryList отдаёт пустой subCategories и на дозапрос («Бумага для канцелярских нужд»):
    # поиск СТЕ по пути ветки всё равно отдаёт её товары, каждый со своей конечной категорией
    tree = [
        {
            "name": "Канцелярия",
            "treePathId": ".1.30.",
            "isLastLevel": False,
            "subCategories": [
                {"name": "Бумага для нужд", "treePathId": ".1.30.31.", "isLastLevel": False},
            ],
        }
    ]
    assert list(leaves(tree)) == [
        {"path": ".1.30.31.", "name": "Бумага для нужд", "root": "Канцелярия", "branch": True}
    ]


def test_other_process_done_paths_are_read_fresh(tmp_path) -> None:
    # два процесса идут навстречу: перед каждой категорией читается свежий done.txt соседа
    from app.tools.fetch_ste import done_paths

    done = tmp_path / "done.txt"
    assert done_paths([tmp_path / "нет.txt"]) == set()
    done.write_text(".1.10.11.\n", "utf-8")
    assert done_paths([done]) == {".1.10.11."}
    done.write_text(".1.10.11.\n.1.20.\n", "utf-8")
    assert done_paths([done]) == {".1.10.11.", ".1.20."}
