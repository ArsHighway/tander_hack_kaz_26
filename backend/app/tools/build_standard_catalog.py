"""Справочник позиций из стандартов категорий портала и значений живых СТЕ.

    uv run python -m app.tools.build_standard_catalog --out ../data/catalog/standards.json \
        --standards ../data/standards/standards.jsonl --ste ../data/ste_full/ste.jsonl

Вход — выгрузки `fetch_standards` и `fetch_ste --per-category 0`. Выход — JSON для
`Catalog.load` (подключается через `CATALOG_PATHS`, после смены — `index_catalog`) и рядом
`<имя>.report.json`: сколько категорий стало позициями, какие характеристики взяты (из стандарта /
добавленные поставщиками), какая доля значений СТЕ привязалась к шаблону. Правила отбора —
`app.portal.standards`, план — `docs/plans/2026-10-06-standard-catalog.md`.
"""

import argparse
import json
import logging
from collections import Counter
from pathlib import Path

from app.catalog import Catalog, Position
from app.portal.standards import ValueStats, build_standard_positions, parse_standard

log = logging.getLogger(__name__)


def characteristic_report(
    positions: list[Position], official: set[tuple[str | None, str, str]]
) -> tuple[Counter[str], float]:
    """Счёт характеристик справочника по происхождению и виду и их среднее на позицию. Поля
    «Общих сведений» (производитель, страна) — не характеристики: отдельным числом `general`."""
    kept: Counter[str] = Counter()
    for p in positions:
        for spec in p.characteristics:
            if spec.group == "general":
                kept["general"] += 1
                continue
            origin = "standard" if (p.kpgz, p.name, spec.name) in official else "supplier"
            kind = "free_text" if spec.kind == "qualitative" and not spec.values else spec.kind
            kept[origin] += 1
            kept[kind] += 1
            kept["required"] += spec.required
            kept["level_define"] += spec.level_define
            kept["with_ktru"] += bool(spec.ktru)
    chars = sum(1 for p in positions for c in p.characteristics if c.group != "general")
    return kept, round(chars / len(positions), 1) if positions else 0.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--standards", type=Path, default=Path("../data/standards/standards.jsonl"))
    ap.add_argument("--ste", type=Path, default=Path("../data/ste_full/ste.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("../data/catalog/standards.json"))
    ap.add_argument(
        "--exclude",
        type=Path,
        default=Path("../data/holdout/exclude.txt"),
        help="id СТЕ отложенной выборки (build_holdout); нет файла — берутся все СТЕ",
    )
    ap.add_argument(
        "--tree",
        type=Path,
        default=Path("../data/standards/tree.json"),
        help="дерево категорий (fetch_standards): имена пути категории; нет файла — без пути",
    )
    ap.add_argument(
        "--entities",
        type=Path,
        default=Path("../data/standards/entities.jsonl"),
        help="привязки (fetch_standards --entity-only): картинка категории; нет файла — без неё",
    )
    ap.add_argument(
        "--synonyms",
        type=Path,
        action="append",
        default=[],
        help="принятые пары по смыслу (find_synonyms check → *.checked.json)",
    )
    ap.add_argument("--min-share", type=float, default=0.1)
    ap.add_argument("--min-value-cards", type=int, default=2)
    ap.add_argument("--max-values", type=int, default=300)
    ap.add_argument("--min-coverage", type=float, default=0.8)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    with args.standards.open(encoding="utf-8") as fh:
        standards = [parse_standard(json.loads(line)) for line in fh if line.strip()]
    if not standards:
        raise SystemExit(f"{args.standards}: ни одного стандарта")
    log.info("стандартов: %d", len(standards))

    exclude: set[int] = set()
    if args.exclude.is_file():
        exclude = {int(x) for x in args.exclude.read_text(encoding="utf-8").split()}
    log.info("СТЕ отложенной выборки, не входят в перечни: %d (%s)", len(exclude), args.exclude)
    stats = ValueStats(standards, exclude=exclude)
    skus = 0
    with args.ste.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                stats.add(json.loads(line))
                skus += 1
                if skus % 200_000 == 0:
                    log.info("СТЕ: %d", skus)
    used = sum(stats.cards.values())
    if not used:
        raise SystemExit(f"{args.ste}: ни одна СТЕ не попала в категории стандартов")

    tree = json.loads(args.tree.read_text(encoding="utf-8")) if args.tree.is_file() else None
    images: dict[int, int | None] | None = None
    if args.entities.is_file():
        with args.entities.open(encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        images = {r["id"]: r.get("image_id") for r in rows}
    log.info("дерево: %s, привязок с картинкой: %s", "есть" if tree else "нет",
             sum(1 for v in images.values() if v) if images else "нет")  # fmt: skip
    positions = build_standard_positions(
        standards,
        stats,
        min_share=args.min_share,
        min_value_cards=args.min_value_cards,
        max_values=args.max_values,
        min_coverage=args.min_coverage,
        tree=tree,
        images=images,
    )
    if args.synonyms:
        # Синонимы по смыслу (`find_synonyms check`): «Плотность» — «Масса бумаги площадью 1м2».
        from app.portal.standards import merge_pairs
        from app.tools.find_synonyms import accepted_pairs

        pairs = accepted_pairs(args.synonyms)
        positions = [
            p.model_copy(update={"characteristics": merge_pairs(p.characteristics, pairs[p.code])})
            if p.code in pairs
            else p
            for p in positions
        ]
    Catalog(positions, version=args.out.name)  # дубли кодов и схема — до записи файла
    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "portal-standards",
        "positions": [p.model_dump(exclude_defaults=True) for p in positions],
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    # Для отчёта позиция узнаёт свой стандарт по (КПГЗ, имя категории).
    official = {(s.kpgz, s.name, c.name) for s in standards for c in s.chars.values() if c.official}
    kept, per_position = characteristic_report(positions, official)
    linked_total = stats.linked + sum(stats.unlinked.values())
    report = {
        "standards": len(standards),
        "categories_with_ste": sum(1 for s in standards if stats.cards[s.id]),
        "positions": len(positions),
        "ste_read": skus,
        "ste_in_standards": used,
        "ste_excluded": len(exclude),
        "values_linked_share": round(stats.linked / linked_total, 4) if linked_total else None,
        "characteristics": dict(kept),
        "per_position": per_position,
        "top_unlinked": stats.unlinked.most_common(30),
        "params": {k: v for k, v in vars(args).items() if k.startswith(("min", "max"))},
    }
    report_path = args.out.with_suffix(".report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("позиций: %d -> %s, отчёт %s", len(positions), args.out.resolve(), report_path)


if __name__ == "__main__":
    main()
