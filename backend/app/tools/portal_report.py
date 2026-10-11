"""Отчёт по живым карточкам Портала поставщиков и справочник из них.

`uv run python -m app.tools.portal_report [файлы.jsonl ...] [--catalog data/catalog/portal.json]`

Читает карточки, собранные `data/portal/fetch_portal.py` и `fetch_categories.py` (по умолчанию
`data/portal/skus.jsonl` и `data/portal/categories.jsonl`), пишет отчёт в `PORTAL_REPORT_PATH`
(его отдаёт `GET /api/v1/metrics/portal`). С `--catalog` ещё и справочник из плотных категорий:
его можно подключить через `CATALOG_PATHS`, чтобы прогнать модуль на реальных характеристиках.
"""

import argparse
import json
import sys
from pathlib import Path

from app.config import get_settings
from app.portal import ValueIndex, build_positions, build_report, load_cards

DEFAULT_INPUTS = [Path("data/portal/skus.jsonl"), Path("data/portal/categories.jsonl")]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("inputs", nargs="*", type=Path)
    ap.add_argument("--out", type=Path, default=None, help="куда писать отчёт (JSON)")
    ap.add_argument("--catalog", type=Path, default=None, help="куда писать справочник (JSON)")
    ap.add_argument("--min-cards", type=int, default=20, help="карточек в категории для позиции")
    args = ap.parse_args(argv)
    inputs = [p for p in (args.inputs or DEFAULT_INPUTS) if p.is_file()]
    if not inputs:
        print("нет файлов с карточками портала (data/portal/*.jsonl)", file=sys.stderr)
        return 1
    cards = load_cards(inputs)
    if not cards:
        print("в файлах нет карточек", file=sys.stderr)
        return 1
    source = ", ".join(p.name for p in inputs)
    report = build_report(cards, source=source)
    out = args.out or Path(get_settings().portal_report_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.model_dump_json(indent=1), encoding="utf-8")
    a = report.audit
    print(f"карточек {report.cards}, категорий {report.categories}; с замечаниями {a.with_issues} "
          f"({a.share:.0%})")  # fmt: skip
    for rule in a.rules:
        print(f"  {rule.cards:>6} {rule.share:6.1%}  {rule.rule}")
    for row in report.fill:
        print(f"  {row.key:24} {row.value:.3f}  n={row.n}")
    print(f"записано: {out}")
    if args.catalog:
        positions = build_positions(cards, ValueIndex(cards), min_cards=args.min_cards)
        args.catalog.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "version": f"portal-{report.created_at:%Y-%m-%d}",
            "source": "portal",
            "positions": [p.model_dump(exclude={"source"}) for p in positions],
        }
        args.catalog.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
        chars = sum(len(p.characteristics) for p in positions)
        print(f"справочник: позиций {len(positions)}, характеристик {chars} → {args.catalog}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
