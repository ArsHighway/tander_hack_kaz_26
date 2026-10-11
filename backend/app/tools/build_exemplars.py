"""Наименования живых СТЕ для поиска категории по похожим (`app.search.exemplars`).

    uv run python -m app.tools.build_exemplars --per-category 300

На категорию — до `--per-category` разных наименований (повторы после `fold` склеены, выбор
случайный с `--seed`), без СТЕ отложенной выборки (`--exclude`, `build_holdout`). Код позиции —
как у `build_standard_catalog`. Пишет `data/catalog/exemplars.jsonl`: `{"code", "name", "id"}`
(id СТЕ — для примеров «похожие» у варианта категории).
"""

import argparse
import json
import logging
import random
from collections import defaultdict
from pathlib import Path

from app.portal.standards import parse_standard, position_codes
from app.search.candidates import OUTSIDE_PREFIX
from app.values import fold

log = logging.getLogger(__name__)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--standards", type=Path, default=Path("../data/standards/standards.jsonl"))
    ap.add_argument("--ste", type=Path, default=Path("../data/ste_full/ste.jsonl"))
    ap.add_argument("--exclude", type=Path, default=Path("../data/holdout/exclude.txt"))
    ap.add_argument("--out", type=Path, default=Path("../data/catalog/exemplars.jsonl"))
    ap.add_argument("--per-category", type=int, default=300)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument(
        "--outside",
        action="append",
        default=[],
        metavar="ФАЙЛ:МЕТКА",
        help="СТЕ не из справочника товаров (fetch_ste --root 2/3) с меткой класса: "
        "../data/ste_root2/ste.jsonl:works; код соседа — «__works__<id категории>»",
    )
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    with args.standards.open(encoding="utf-8") as fh:
        codes = position_codes([parse_standard(json.loads(line)) for line in fh if line.strip()])
    exclude: set[int] = set()
    if args.exclude.is_file():
        exclude = {int(x) for x in args.exclude.read_text(encoding="utf-8").split()}
    # Наименование с id своей СТЕ: id нужен подсказке «похожие» у варианта категории (картинка СТЕ).
    names: defaultdict[int, dict[str, tuple[str, int]]] = defaultdict(dict)
    with args.ste.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            name = (rec.get("name") or "").strip()
            if rec["id"] in exclude or not name or rec.get("directory_id") not in codes:
                continue
            names[rec["directory_id"]].setdefault(fold(name), (name, rec["id"]))
    rnd = random.Random(args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    written = outside = 0
    with args.out.open("w", encoding="utf-8") as out:
        for directory in sorted(names):
            pool = sorted(names[directory].values())
            for name, sku in rnd.sample(pool, min(args.per_category, len(pool))):
                row = {"code": codes[directory], "name": name, "id": sku}
                out.write(json.dumps(row, ensure_ascii=False))
                out.write("\n")
                written += 1
        # Классы «не товар»: каждая категория работ и услуг — свой маленький класс, чтобы их
        # общая масса не перетягивала голос у товаров (`app.search.candidates.outside`).
        for spec in args.outside:
            path, label = spec.rsplit(":", 1)
            pools: defaultdict[str, dict[str, tuple[str, int]]] = defaultdict(dict)
            with Path(path).open(encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        rec = json.loads(line)
                        name = (rec.get("name") or "").strip()
                        # Выборочная выгрузка (`--per-category N`) пишет путь категории без id.
                        category = (rec.get("category_path") or "").strip(".").split(".")[-1]
                        if name and category:
                            pools[category].setdefault(fold(name), (name, rec["id"]))
            for category in sorted(pools):
                pool = sorted(pools[category].values())
                for name, sku in rnd.sample(pool, min(args.per_category, len(pool))):
                    row = {"code": f"{OUTSIDE_PREFIX}{label}__{category}", "name": name, "id": sku}
                    out.write(json.dumps(row, ensure_ascii=False))
                    out.write("\n")
                    outside += 1
    if not written:
        raise SystemExit("ни одного наименования: проверьте --ste и --standards")
    log.info("наименований: %d в %d категориях, «не товар»: %d (исключено СТЕ: %d) -> %s",
             written, len(names), outside, len(exclude), args.out.resolve())  # fmt: skip


if __name__ == "__main__":
    main()
