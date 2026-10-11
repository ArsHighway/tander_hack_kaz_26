r"""Справочник типов товаров КТРУ для `CATALOG_PATHS` (первый этап — тип КТРУ, а не категория).

    uv run python -m app.tools.build_ktru_types --ktru ../data/catalog/ktru_hack.json \
        --categories ../data/catalog/standards.json --out ../data/catalog/ktru_types.json \
        --fallback ../data/holdout_kazan/categories.json

`--ktru` — позиции КТРУ (`build_ktru_catalog --scope hack`), `--categories` — категории портала со
ссылками на КТРУ (`build_standard_catalog`). Как строится тип — `app.catalog.ktru_types`.
`--fallback` — коды КПГЗ категорий хакатона (`hackathon_holdout`, `categories.json`): те из них,
что не стали типом КТРУ, идут в справочник как были — категорией портала со своим вторым этапом
КТРУ. Без них товары, для которых типа КТРУ в границах хакатона нет, не находились бы вовсе. После
сборки — векторы:

    CATALOG_PATHS=data/catalog/ktru_types.json uv run python -m app.tools.index_catalog
"""

import argparse
import json
import logging
from pathlib import Path

from app.catalog import Catalog
from app.catalog.ktru_types import UNLINKED_PREFIX, build_types

log = logging.getLogger(__name__)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ktru", type=Path, default=Path("../data/catalog/ktru_hack.json"))
    ap.add_argument("--categories", type=Path, default=Path("../data/catalog/standards.json"))
    ap.add_argument("--out", type=Path, default=Path("../data/catalog/ktru_types.json"))
    ap.add_argument(
        "--fallback", type=Path, help="categories.json: коды КПГЗ категорий хакатона без типа КТРУ"
    )
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    ktru = Catalog.load([args.ktru]).all()
    categories = Catalog.load([args.categories]).all()
    types = build_types(ktru, categories)
    linked = [t for t in types if not t.code.startswith(UNLINKED_PREFIX)]
    count = len(types)
    if args.fallback:
        wanted = set(json.loads(args.fallback.read_text(encoding="utf-8")))
        have = {t.code for t in types}
        # Категория, которая ссылается на позиции какого-то типа, этим типом и покрыта: рядом с ним
        # она тянула бы те же товары к себе (на выборке хакатона — минус 6 п. верного типа).
        members = {k for t in types for c in t.characteristics for k in c.ktru}
        rest = [
            c
            for c in categories
            if c.code in wanted
            and c.code not in have
            and not any(k in members for s in c.characteristics for k in s.ktru)
        ]
        log.info(
            "категорий хакатона %d: типом КТРУ стали %d, как были — %d",
            len(wanted),
            len(wanted & have),
            len(rest),
        )
        types += rest
    Catalog(types, version=args.out.name)  # коды не повторяются — иначе ошибка до записи
    log.info(
        "позиций КТРУ %d → типов %d: со связью с категорией портала %d, без связи %d",
        len(ktru),
        count,
        len(linked),
        count - len(linked),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "ktru-types",
        "positions": [t.model_dump(exclude_defaults=True) for t in types],
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    log.info("-> %s", args.out.resolve())


if __name__ == "__main__":
    main()
