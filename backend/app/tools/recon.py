"""Разведка выданных данных: `uv run python -m app.tools.recon <файл|папка|архив> [--out
report.md]`.

Печатает инвентарь файлов, таблицы, колонки с угаданной ролью и статистику характеристик.
Первое, что запускаем, когда организаторы выдали датасет.
"""

import argparse
import sys
from pathlib import Path

from app.ingest.profile import TableProfile, profile_table
from app.ingest.readers import SkippedFile, read_tables


def render(profiles: list[TableProfile], skipped: list[SkippedFile]) -> str:
    out = ["# Разведка данных", ""]
    for p in profiles:
        rows = p.row_count if p.row_count is not None else "?"
        out += [f"## {p.source}", "", f"Строк: {rows}, в выборке: {p.sampled}", ""]
        out += ["| Колонка | Заполнено | Роль (оценка) | Примеры |", "|---|---|---|---|"]
        for c in p.columns:
            ex = " · ".join(e.replace("|", "/") for e in c.examples)
            out.append(f"| {c.name} | {c.filled} | {c.role} ({c.score}) | {ex} |")
        if s := p.characteristics:
            share = s.unparsed_segments / max(1, s.parsed_items + s.unparsed_segments)
            out += [
                "",
                f"Характеристики в «{s.column}»: {s.parsed_items} пар на {s.rows} строк, "
                f"разных имён {s.distinct_names}, неразобранных кусков {s.unparsed_segments} "
                f"({share:.1%}).",
                f"- Частые имена: {', '.join(f'{n} ({k})' for n, k in s.top_names)}",
                f"- Единицы: {', '.join(f'{n} ({k})' for n, k in s.units) or 'нет'}",
                "- Аббревиатуры в значениях: "
                + ", ".join(f"{n} ({k})" for n, k in s.abbreviations),
            ]
        out.append("")
    if skipped:
        out += ["## Не таблицы и ошибки чтения", ""]
        out += [f"- {s.path} ({s.size} байт): {s.reason}" for s in skipped]
    out += ["", f"Итого таблиц: {len(profiles)}, прочих файлов: {len(skipped)}"]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--sample", type=int, default=20000, help="строк на таблицу для профиля")
    parser.add_argument("--out", type=Path, help="куда записать отчёт (по умолчанию stdout)")
    args = parser.parse_args(argv)
    if not args.path.exists():
        print(f"нет такого пути: {args.path}", file=sys.stderr)
        return 2
    tables, skipped = read_tables(args.path)
    report = render([profile_table(t, args.sample) for t in tables], skipped)
    if args.out:
        args.out.write_text(report, encoding="utf-8")
    else:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # TextIO has no reconfigure
        sys.stdout.write(report)
    if not tables:
        print("ни одной таблицы не прочитано", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
