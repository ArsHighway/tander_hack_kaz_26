"""Точность «Общих сведений» на отложенной выборке: производитель и страна из правил против портала.

Правила берут их из наименования только целым значением перечня частых заводов и стран категории
и из описания — по метке («Производитель: …»); сколько пришло из описания — `from_description`.
Верное — значение совпало с карточкой портала без учёта регистра; на портале поля нет — проверить
нечем. Бренд вместо юрлица (BRAUBERG при САМСОН-ОПТ) считается неверным: что писать в поле, решает
продукт, а мера — честная нижняя оценка.

Запуск из backend/ (~2 мин: `ste_full` читается целиком ради 10 тыс. строк выборки):
    uv run python -m app.tools.evaluate_general --out ../data/metrics/general.json
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.catalog import Catalog
from app.catalog.model import GENERAL_NAMES
from app.extract.rules import extract_rules
from app.portal.standards import country_title, maker_name
from app.values import fold

MAKER, _, COUNTRY = GENERAL_NAMES
FIELDS = (MAKER, COUNTRY)  # «Модель» в перечнях нет — её правила по перечню не берут
_ID = re.compile(r'^\{"id": (\d+)')


@dataclass
class FieldScore:
    extracted: int = 0
    right: int = 0
    wrong: int = 0
    unknown: int = 0  # на портале поля нет
    from_description: int = 0  # по метке в описании, остальное — из наименования
    examples: list[dict[str, str]] = field(default_factory=list)

    @property
    def precision(self) -> float | None:
        checked = self.right + self.wrong
        return round(self.right / checked, 4) if checked else None


def score(
    cases: Iterable[dict[str, Any]],
    truth: dict[int, dict[str, str | None]],
    catalog: Catalog,
    examples: int = 40,
) -> dict[str, FieldScore]:
    """Что правила извлекли в поля «Общих сведений» позиции случая и сколько совпало с порталом."""
    out = {name: FieldScore() for name in FIELDS}
    for case in cases:
        position = catalog.get(case["code"])
        real = truth.get(case["id"])
        if position is None or real is None:
            continue
        general = {s.name for s in position.characteristics if s.group == "general"}
        name, description = case["name"], case.get("description") or ""
        for e in extract_rules(name, description, position.characteristics, [position.name]):
            if e.name not in out or e.name not in general:
                continue
            s, portal = out[e.name], real.get(e.name)
            s.extracted += 1
            s.from_description += e.source.field == "description"
            if portal is None:
                s.unknown += 1
            elif fold(e.raw_value) == fold(portal):
                s.right += 1
            else:
                s.wrong += 1
                if len(s.examples) < examples:
                    s.examples.append(
                        {
                            "name": name,
                            "field": e.source.field,
                            "found": e.raw_value,
                            "portal": portal,
                        }
                    )
    return out


def portal_truth(ste: Path, ids: set[int]) -> dict[int, dict[str, str | None]]:
    """Производитель и страна карточек портала — в том же виде, что и перечни стандартов."""
    truth: dict[int, dict[str, str | None]] = {}
    with ste.open(encoding="utf-8") as f:
        for line in f:
            m = _ID.match(line)
            if m is None or int(m.group(1)) not in ids:
                continue
            row = json.loads(line)
            maker, country = row.get("manufacturer"), row.get("country")
            truth[row["id"]] = {
                MAKER: maker_name(maker) if maker else None,
                COUNTRY: country_title(country) if country else None,
            }
    return truth


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--catalog", type=Path, default=Path("../data/catalog/standards.json"))
    ap.add_argument("--holdout", type=Path, default=Path("../data/holdout/holdout.jsonl"))
    ap.add_argument("--ste", type=Path, default=Path("../data/ste_full/ste.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("../data/metrics/general.json"))
    args = ap.parse_args()
    cases = [json.loads(line) for line in args.holdout.open(encoding="utf-8")]
    truth = portal_truth(args.ste, {c["id"] for c in cases})
    if not truth:  # другой формат выгрузки: нули в отчёте выглядели бы как замер
        raise SystemExit(f"в {args.ste} не нашлось ни одной карточки выборки")
    scores = score(cases, truth, Catalog.load([args.catalog]))
    report = {
        "cases": len(cases),
        "with_portal_card": len(truth),
        **{name: {**asdict(s), "precision": s.precision} for name, s in scores.items()},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    brief = {name: {k: v for k, v in r.items() if k != "examples"} for name, r in report.items()
             if isinstance(r, dict)}  # fmt: skip
    print(json.dumps({"cases": len(cases), **brief}, ensure_ascii=False))


if __name__ == "__main__":
    main()
