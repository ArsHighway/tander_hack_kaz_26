r"""Справочник КТРУ для `KTRU_PATHS`: из выгрузки XML ЕИС или из скачанных карточек ЕИС.

    uv run python -m app.tools.build_ktru_catalog --out ../data/catalog/ktru_xml.json \
        --xml "../data/organizers/Казань 2026/КТРУ/КТРУ xml.zip"
    uv run python -m app.tools.build_ktru_catalog --scope hack \
        --out ../data/catalog/ktru_hack.json \
        --xml "../data/organizers/Казань 2026/КТРУ/КТРУ xml.zip"
    uv run python -m app.tools.build_ktru_catalog --out ../data/catalog/ktru.json

XML (`app.catalog.ktru_xml`): только файлы actual, позиции `ACTIVE` (`--all-status` — и
исключённые), по коду — последняя версия; отчёт о взятых и отброшенных файлах и статусах
печатается. `--scope hack` — только три категории хакатона (`app.catalog.hack_scope`: одежда,
мебель, инструменты по ОКПД2) без позиций «Услуги …» и «Работы …»: 1 493 позиции на 10.10.2026.
Карточки (`fetch_ktru`) разбираются `app.catalog.ktru_card.parse_card`; карточка без
характеристик (страница изменилась, позиция исключена) не берётся и попадает в отчёт. Справочник
проверяется загрузкой в `Catalog` до записи файла.
"""

import argparse
import gzip
import json
import logging
from pathlib import Path

from app.catalog import Catalog
from app.catalog.hack_scope import CATEGORIES, category_of, is_service
from app.catalog.ktru_card import parse_card
from app.catalog.ktru_xml import load_positions
from app.catalog.model import Position

log = logging.getLogger(__name__)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw", type=Path, default=Path("../data/ktru/raw"))
    ap.add_argument(
        "--xml", type=Path, help="выгрузка XML ЕИС: zip (с вложенными zip), папка или XML"
    )
    ap.add_argument(
        "--all-status", action="store_true", help="XML: и исключённые позиции (TERMINATED)"
    )
    ap.add_argument(
        "--scope", choices=["all", "hack"], default="all", help="hack — три категории хакатона"
    )
    ap.add_argument("--out", type=Path, default=Path("../data/catalog/ktru.json"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if args.xml:
        statuses = ("ACTIVE", "TERMINATED") if args.all_status else ("ACTIVE",)
        positions, report = load_positions(args.xml, statuses=statuses)
        for key, value in report.items():
            log.info("%s: %s", key, value)
        if args.scope == "hack":
            positions = in_hack_scope(positions)
        write(positions, args.out, "ktru-xml" if args.scope == "all" else "ktru-xml-hack")
        return
    files = sorted(args.raw.glob("*.html.gz"))
    if not files:
        raise SystemExit(f"{args.raw}: нет карточек — сначала fetch_ktru")
    positions, empty = [], []
    for f in files:
        code = f.name.removesuffix(".html.gz")
        position = parse_card(code, gzip.decompress(f.read_bytes()).decode("utf-8"))
        if position.characteristics:
            positions.append(position)
        else:
            empty.append(code)
    if not positions:
        raise SystemExit("ни одна карточка не разобралась: страница ЕИС изменилась?")
    write(positions, args.out, "ktru-eis")
    log.info("карточек %d, взято %d", len(files), len(positions))
    if empty:
        log.warning("без характеристик %d: %s", len(empty), empty[:20])


def in_hack_scope(positions: list[Position]) -> list[Position]:
    """Позиции трёх категорий хакатона, без услуг и работ; по категориям — в журнал."""
    kept = [p for p in positions if category_of(p.okpd2) is not None and not is_service(p.name)]
    if not kept:
        raise SystemExit("в выгрузке нет позиций категорий хакатона: границы ОКПД2 не те?")
    for category in CATEGORIES:
        mine = [p for p in kept if category_of(p.okpd2) == category]
        log.info(
            "%s: позиций %d, наименований %d", category, len(mine), len({p.name for p in mine})
        )
    return kept


def write(positions: list[Position], out: Path, source: str) -> None:
    Catalog(positions, version=out.name)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": source,
        "positions": [p.model_dump(exclude_defaults=True) for p in positions],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    chars = sum(len(p.characteristics) for p in positions)
    log.info("позиций КТРУ %d, характеристик %d -> %s", len(positions), chars, out.resolve())


if __name__ == "__main__":
    main()
