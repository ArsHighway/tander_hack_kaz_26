"""Отложенная выборка категорий: одежда, мебель, инструменты.

    uv run python -m app.tools.hackathon_holdout   # из backend/

Категории берутся из листа 2 примеров выгрузок организаторов (`Инструменты.xlsx`, `Мебель.xlsx`,
`Одежда.xlsx`: колонка «Код КПГЗ»), случаи — из общей отложенной выборки портала (`build_holdout`),
чьи СТЕ уже вынуты из справочника и похожих. Пишет `holdout.jsonl` (тот же формат, что у общей
выборки) и `categories.json` (код КПГЗ → файл-категория). Замер — `evaluate_holdout --holdout
<out>/holdout.jsonl`.
"""

import argparse
import json
import logging
from collections import Counter
from pathlib import Path

from python_calamine import CalamineWorkbook

log = logging.getLogger(__name__)

KPGZ_COLUMN = "Код КПГЗ"


def hackathon_categories(examples: Path) -> dict[str, str]:
    """Код КПГЗ → категория выборки (имя файла) по листу 2 каждого примера выгрузки."""
    cats: dict[str, str] = {}
    files = sorted(examples.glob("*.xlsx"))
    if not files:
        raise SystemExit(f"в {examples} нет xlsx примеров выгрузок")
    for f in files:
        rows = CalamineWorkbook.from_path(str(f)).get_sheet_by_index(1).to_python()
        header = [str(x).strip() for x in rows[0]]
        if KPGZ_COLUMN not in header:
            raise SystemExit(f"{f.name}: на листе 2 нет колонки «{KPGZ_COLUMN}»")
        col = header.index(KPGZ_COLUMN)
        for row in rows[1:]:
            if col < len(row) and (code := str(row[col]).strip()):
                cats[code] = f.stem
    return cats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--examples",
        type=Path,
        default=Path("../data/organizers/Казань 2026/Примеры выгрузок"),
        help="папка с Инструменты.xlsx, Мебель.xlsx, Одежда.xlsx",
    )
    ap.add_argument("--holdout", type=Path, default=Path("../data/holdout_full/holdout.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("../data/holdout_kazan"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    cats = hackathon_categories(args.examples)
    kept: list[str] = []
    per: Counter[str] = Counter()
    with args.holdout.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip() and (kind := cats.get(json.loads(line)["code"])) is not None:
                kept.append(line if line.endswith("\n") else line + "\n")
                per[kind] += 1
    if not kept:
        raise SystemExit("ни одного случая выборки: та ли выборка?")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "holdout.jsonl").write_text("".join(kept), encoding="utf-8")
    (args.out / "categories.json").write_text(
        json.dumps(cats, ensure_ascii=False, indent=0), encoding="utf-8"
    )
    log.info("категорий %d, случаев %d: %s → %s", len(cats), len(kept), dict(per), args.out)


if __name__ == "__main__":
    main()
