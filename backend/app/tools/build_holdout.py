"""Отложенная выборка СТЕ для оценки на справочнике стандартов.

    uv run python -m app.tools.build_holdout --per-category 8

Берёт до `--per-category` товаров из каждой категории (не больше половины её товаров), из
обучения убирает их вместе с двойниками (то же наименование или модель) — правила в
`app.portal.holdout`. Пишет `holdout.jsonl` (вход и эталон, код позиции как у
`build_standard_catalog`) и `exclude.txt` — id СТЕ, которые сборка справочника и индекс похожих СТЕ
должны пропустить (`--exclude`).
"""

import argparse
import json
import logging
import random
from pathlib import Path

from app.portal.holdout import entry, make_case, split
from app.portal.standards import parse_standard, position_codes

log = logging.getLogger(__name__)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--standards", type=Path, default=Path("../data/standards/standards.jsonl"))
    ap.add_argument("--ste", type=Path, default=Path("../data/ste_full/ste.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("../data/holdout"))
    ap.add_argument("--per-category", type=int, default=8)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    with args.standards.open(encoding="utf-8") as fh:
        codes = position_codes([parse_standard(json.loads(line)) for line in fh if line.strip()])
    entries = []
    with args.ste.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rec = json.loads(line)
                if rec.get("directory_id") in codes:
                    entries.append(entry(rec))
    chosen, excluded = split(entries, args.per_category, args.seed)
    if not chosen:
        raise SystemExit("отложенная выборка пуста: проверьте --ste и --standards")

    args.out.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(args.seed)
    written = 0
    with (
        args.ste.open(encoding="utf-8") as src,
        (args.out / "holdout.jsonl").open("w", encoding="utf-8") as out,
    ):
        for line in src:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec["id"] in chosen:
                case = make_case(rec, codes[rec["directory_id"]], rnd)
                out.write(json.dumps(case, ensure_ascii=False) + "\n")
                written += 1
    (args.out / "exclude.txt").write_text(
        "".join(f"{i}\n" for i in sorted(excluded)), encoding="utf-8"
    )
    cats = len({e.directory for e in entries if e.id in chosen})
    log.info("СТЕ: %d, отложено %d в %d категориях, убрано из обучения %d -> %s", len(entries),
             written, cats, len(excluded), args.out.resolve())  # fmt: skip


if __name__ == "__main__":
    main()
