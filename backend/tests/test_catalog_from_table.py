import json
from pathlib import Path

from app.catalog import Catalog
from app.tools.catalog_from_table import build_from_table


def _csv(tmp_path: Path) -> Path:
    lines = ["Идентификатор СТЕ;Наименование СТЕ;Категория;характеристики СТЕ"]
    for i in range(25):
        lines.append(
            f"{100 + i};Мешки для мусора {30 + i} л;Пакеты полимерные;"
            f'"Объем:{30 + i}.00000;Цвет:{"черный" if i % 2 else "синий"};Толщина, мкм:50"'
        )
    for i in range(22):
        lines.append(
            f'{200 + i};Бумага офисная А4 №{i};Бумага для офисной техники;"Формат:А4;Плотность:80"'
        )
    lines.append("300;Редкий товар;Редкая категория;Цвет:белый")
    path = tmp_path / "ste.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_table_of_ste_becomes_catalog_and_similar_names(tmp_path: Path) -> None:
    # для выгрузки СТЕ в формате организаторов (пермский: категория именем,
    # характеристики строкой «имя:значение;…») не было одной команды «таблица → справочник»
    out, exemplars = tmp_path / "org.json", tmp_path / "exemplars.jsonl"
    report = build_from_table(_csv(tmp_path), out, exemplars, min_cards=20)
    catalog = Catalog.load([out])
    names = sorted(p.name for p in catalog.all())
    assert names == ["Бумага для офисной техники", "Пакеты полимерные"]  # редкая — меньше 20 СТЕ
    bags = next(p for p in catalog.all() if p.name == "Пакеты полимерные")
    specs = {s.name: s for s in bags.characteristics}
    assert specs["Объем"].kind == "quantitative"
    assert set(specs["Цвет"].values) == {"черный", "синий"}
    rows = [json.loads(line) for line in exemplars.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 47 and {r["code"] for r in rows} == {p.code for p in catalog.all()}
    assert rows[0]["id"] == 100 and rows[0]["name"].startswith("Мешки для мусора")
    assert report == {"rows": 48, "categories": 3, "positions": 2}
