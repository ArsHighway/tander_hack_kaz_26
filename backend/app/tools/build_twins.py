"""Индекс двойников (`app.search.twins`): «такая СТЕ уже есть на портале».

    uv run python -m app.tools.build_twins --ste ../data/ste_full/ste.jsonl

Пишет `data/catalog/twins.sqlite`: наименование и модель → СТЕ с категорией и характеристиками.
Отложенную выборку из индекса не убираем: при оценке её id вычитаются при поиске
(`evaluate_holdout --twins`), а двойники в выборке остаются — как на портале.
"""

import argparse
import logging
from pathlib import Path

from app.search.twins import build_twins

log = logging.getLogger(__name__)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ste", type=Path, default=Path("../data/ste_full/ste.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("../data/catalog/twins.sqlite"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    written = build_twins(args.ste, args.out)
    log.info("СТЕ в индексе двойников: %d -> %s", written, args.out)
    if not written:
        raise SystemExit("в выгрузке нет ни одной СТЕ с наименованием: не та --ste?")


if __name__ == "__main__":
    main()
