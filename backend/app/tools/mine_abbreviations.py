"""Аббревиатуры из данных организатора в отдельную таблицу.

`uv run python -m app.tools.mine_abbreviations <файл|папка|архив> [--out candidates.csv] [--to-db]`

Берёт текстовые колонки (наименование, категория, характеристики, описание — роли угадываются по
содержимому), ищет заглавные токены и определения вида «полное название (АББР)», пишет CSV
кандидатов. С `--to-db` кладёт наш словарь (source=seed) и найденное (source=mined) в таблицу
`glossary_abbreviation`.
"""

import argparse
import asyncio
import csv
import sys
from collections.abc import Iterator
from itertools import islice
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.glossary import Candidate, Glossary, mine
from app.ingest.profile import profile_table
from app.ingest.readers import read_tables

TEXT_ROLES = {"name", "category", "characteristics", "description", "unknown"}
UPSERT = text(
    """
    INSERT INTO glossary_abbreviation
        (abbr, "full", kind, source, case_insensitive, occurrences, examples, note)
    VALUES (:abbr, :full, :kind, :source, :ci, :occurrences, :examples, :note)
    ON CONFLICT (abbr, "full") DO UPDATE SET
        occurrences = EXCLUDED.occurrences, examples = EXCLUDED.examples
    """
)


def iter_texts(path: Path, limit: int | None) -> Iterator[str]:
    tables, _ = read_tables(path)
    for table in tables:
        prof = profile_table(table, sample=2000)
        cols = [c.name for c in prof.columns if c.role in TEXT_ROLES]
        for row in islice(table.rows(), limit):
            for col in cols:
                value = row.get(col)
                if value:
                    yield str(value)


def write_csv(cands: list[Candidate], out: Path) -> None:
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["abbr", "occurrences", "known", "expansion", "examples"])
        for c in cands:
            best = c.expansions.most_common(1)[0][0] if c.expansions else ""
            w.writerow([c.abbr, c.count, int(c.known), best, " | ".join(c.examples)])


async def to_db(glossary: Glossary, cands: list[Candidate], min_count: int) -> int:
    engine = create_async_engine(get_settings().database_url)
    rows = [
        {
            "abbr": e.abbr,
            "full": e.full,
            "kind": e.kind,
            "source": "seed",
            "ci": e.case_insensitive,
            "occurrences": 0,
            "examples": "",
            "note": e.note,
        }
        for entries in glossary.by_abbr.values()
        for e in entries
    ]
    rows += [
        {
            "abbr": c.abbr,
            "full": c.expansions.most_common(1)[0][0] if c.expansions else "",
            "kind": "",
            "source": "mined",
            "ci": False,
            "occurrences": c.count,
            "examples": " | ".join(c.examples),
            "note": "",
        }
        for c in cands
        if not c.known and c.count >= min_count
    ]
    async with engine.begin() as conn:
        await conn.execute(UPSERT, rows)
    await engine.dispose()
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--out", type=Path, default=Path("abbreviation_candidates.csv"))
    parser.add_argument("--limit", type=int, default=None, help="строк на таблицу")
    parser.add_argument("--min-count", type=int, default=3, help="порог частоты для --to-db")
    parser.add_argument("--to-db", action="store_true")
    args = parser.parse_args(argv)
    glossary = Glossary.load()
    cands = mine(iter_texts(args.path, args.limit), glossary)
    write_csv(cands, args.out)
    new = [c for c in cands if not c.known]
    print(f"кандидатов {len(cands)}, новых {len(new)}, CSV: {args.out}", file=sys.stderr)
    if not cands:
        print("ни одной аббревиатуры не найдено — проверьте путь и колонки", file=sys.stderr)
        return 1
    if args.to_db:
        n = asyncio.run(to_db(glossary, cands, args.min_count))
        print(f"в glossary_abbreviation записано строк: {n}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
