"""Характеристики похожих СТЕ для подсказок полей (`app.search.similar`): sqlite рядом с индексом.

    uv run python -m app.tools.build_exemplar_values ../data/catalog/exemplars.jsonl

Пишет `exemplars.values.sqlite` по СТЕ из индекса похожих: те же id, тот же отбор (для оценки —
индекс без отложенной выборки, `data/catalog/eval/exemplars_new.jsonl`). ~2 мин на 3,7 млн СТЕ.
"""

import argparse
import json
import logging
from pathlib import Path

from app.search.similar import build_values, values_path

log = logging.getLogger(__name__)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("exemplars", type=Path, help="индекс похожих СТЕ (build_exemplars)")
    ap.add_argument("--ste", type=Path, default=Path("../data/ste_full/ste.jsonl"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ids = set()
    with args.exemplars.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip() and (sid := json.loads(line).get("id")) is not None:
                ids.add(int(sid))
    out = values_path(args.exemplars)
    log.info("СТЕ в индексе похожих: %d -> %s", len(ids), out)
    written = build_values(args.ste, ids, out)
    log.info("записано характеристик СТЕ: %d (нет в выгрузке: %d)", written, len(ids) - written)
    if not written:
        raise SystemExit("ни одной СТЕ из индекса нет в выгрузке: не та --ste?")


if __name__ == "__main__":
    main()
