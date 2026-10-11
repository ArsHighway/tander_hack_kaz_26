"""Справочник и похожие СТЕ из таблицы СТЕ организаторов — одной командой.

    uv run python -m app.tools.catalog_from_table ../data/<файл>.xlsx \
        --out ../data/catalog/org.json --exemplars ../data/catalog/org_exemplars.jsonl

Формат — как пермская выгрузка: наименование, категория именем, характеристики строкой
«имя:значение;…» (или колонками — разбор тот же, что у массовой загрузки, `app.batch.parse_upload`).
Позиция справочника — категория с не меньше чем `--min-cards` СТЕ; характеристики и перечни
значений — из её СТЕ (`app.portal.catalog.build_positions`). Похожие СТЕ — наименования с кодом
позиции для голосования категории (`EXEMPLARS_PATH`). Подключение: `CATALOG_PATHS=<out>`.
"""

import argparse
import json
import logging
from collections import Counter
from pathlib import Path

from app.batch import BatchRow, parse_upload
from app.catalog import Catalog
from app.ingest.characteristics import to_number
from app.portal.cards import PortalCard, PortalChar
from app.portal.catalog import build_positions
from app.portal.values import ValueIndex
from app.values import fold

log = logging.getLogger(__name__)


def _column(row: BatchRow, *words: str) -> str | None:
    """Колонка таблицы, в заголовке которой есть одно из слов («Категория», «Идентификатор СТЕ»)."""
    for col in row.raw:
        if any(w in fold(col) for w in words):
            return col
    return None


def _card(row: BatchRow, path: str, category: str, sku_id: int) -> PortalCard:
    chars = [
        PortalChar(name=c.name, value=c.value, unit=c.unit, numeric=to_number(c.value) is not None)
        for c in row.characteristics
    ]
    return PortalCard(
        id=sku_id,
        name=row.name,
        description=row.description,
        category=category,
        category_path=path,
        characteristics=chars,
    )


def build_from_table(
    table: Path, out: Path, exemplars: Path, *, min_cards: int = 20
) -> dict[str, int]:
    rows = [r for r in parse_upload(table.name, table.read_bytes(), max_rows=None, max_bytes=None)]
    rows = [r for r in rows if not r.empty]
    if not rows:
        raise SystemExit(f"{table}: ни одной строки с товаром")
    cat_col, id_col = _column(rows[0], "категор"), _column(rows[0], "идентификатор", "id")
    if cat_col is None:
        raise SystemExit(f"{table}: нет колонки категории (заголовок со словом «категория»)")
    sizes = Counter(r.raw[cat_col] for r in rows if r.raw.get(cat_col))
    # Путь категории — по частоте: код позиции стабилен для одного файла.
    paths = {name: f".{i}." for i, (name, _) in enumerate(sizes.most_common(), 1)}
    cards = []
    for i, r in enumerate(rows):
        category = r.raw.get(cat_col) or ""
        if not category:
            continue
        raw_id = r.raw.get(id_col, "") if id_col else ""
        sku_id = int(raw_id) if raw_id.isdigit() else i
        cards.append(_card(r, paths[category], category, sku_id))
    positions = build_positions(cards, ValueIndex(cards), min_cards=min_cards)
    # Код позиции — «КАТ-0001» по частоте категории: своих кодов у таблицы может не быть.
    codes = {
        f"КПГЗ {path.strip('.')}": f"КАТ-{int(path.strip('.')):04d}" for path in paths.values()
    }
    positions = [
        p.model_copy(update={"code": codes[p.code], "source": table.name}) for p in positions
    ]
    payload = {
        "version": table.name,
        "positions": [p.model_dump(exclude_defaults=True) for p in positions],
    }
    Catalog([*positions], table.name)  # проверка загрузкой до записи
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    kept = {f"КАТ-{int(paths[c.category].strip('.')):04d}" for c in cards}
    known = {p.code for p in positions}
    with exemplars.open("w", encoding="utf-8") as fh:
        for c in cards:
            code = f"КАТ-{int(paths[c.category].strip('.')):04d}"
            if code in known and code in kept:
                fh.write(json.dumps({"code": code, "name": c.name, "id": c.id}, ensure_ascii=False))
                fh.write("\n")
    return {"rows": len(rows), "categories": len(sizes), "positions": len(positions)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("table", type=Path)
    ap.add_argument("--out", type=Path, default=Path("../data/catalog/org.json"))
    ap.add_argument("--exemplars", type=Path, default=Path("../data/catalog/org_exemplars.jsonl"))
    ap.add_argument("--min-cards", type=int, default=20)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    report = build_from_table(args.table, args.out, args.exemplars, min_cards=args.min_cards)
    log.info("строк %(rows)d, категорий %(categories)d, позиций %(positions)d", report)
    log.info("справочник -> %s, похожие СТЕ -> %s", args.out.resolve(), args.exemplars.resolve())


if __name__ == "__main__":
    main()
