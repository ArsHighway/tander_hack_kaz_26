"""Диапазон правдоподобия числовых характеристик по СТЕ портала.

    uv run python -m app.tools.plausible_ranges ../data/catalog/standards.json \
        --exemplars ../data/catalog/exemplars_syn.jsonl

У большинства числовых характеристик стандарт портала диапазона не задаёт, и «Плотность 800 г/м²»,
«Белизна 1460 %», «40000 К» проходили проверку. Здесь по значениям СТЕ той же категории (индекс
похожих и его `<exemplars>.values.sqlite`) считаются 1-й и 99-й процентили, и в справочник
пишется поле `plausible` с запасом. Выход за него — замечание `warn`, а не блок: редкий товар
бывает. Рядом остаётся `<файл>.bak`.
"""

import argparse
import json
import logging
import shutil
import sqlite3
from collections import defaultdict
from pathlib import Path

from app.catalog import CharacteristicSpec, NumRange
from app.ingest.characteristics import to_number
from app.search.similar import values_path
from app.values import fit_value, fold

log = logging.getLogger(__name__)

MIN_COUNT = 20  # меньше значений — процентили случайны
MIN_DISTINCT = 3  # «все 220 В» — не диапазон, а одно значение
_FITTED = {"exact", "normalized"}


def plausible(numbers: list[float]) -> NumRange | None:
    """p1–p99 с запасом: сверху размах и не меньше чем вдвое, снизу втрое. Ошибка единицы (×10,
    ×1000) и «Белизна 1460 %» при 90–170 % выходят за него, редкий честный размер — нет: на
    отложенной выборке вне диапазона 1,4 % значений поставщиков (запас «половина размаха, ×1,5,
    /2» — 1,9 %, «×5, /5» — 1,0 %)."""
    if len(numbers) < MIN_COUNT or len(set(numbers)) < MIN_DISTINCT:
        return None
    ordered = sorted(numbers)
    p1 = ordered[int(len(ordered) * 0.01)]
    p99 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))]
    spread = p99 - p1
    hi = max(p99 + spread, p99 * 2) if p99 > 0 else p99 + spread
    # Ноль у СТЕ категории бывает («Высота: 0» — не заполнено): ниже нуля предел не уводим.
    lo = p1 / 3 if p1 > 0 else (0.0 if p1 == 0 else p1 - spread)
    return NumRange(min=round(lo, 6), max=round(hi, 6))


def _numeric_specs(position: dict) -> dict[str, CharacteristicSpec]:
    """Свёрнутое имя или синоним → числовая характеристика без перечня."""
    out: dict[str, CharacteristicSpec] = {}
    for raw in position.get("characteristics") or []:
        if raw.get("kind") != "quantitative" or raw.get("values") or raw.get("group"):
            continue
        spec = CharacteristicSpec.model_validate(raw)
        for name in (spec.name, *spec.synonyms):
            out.setdefault(fold(name), spec)
    return out


def collect(positions: list[dict], exemplars: Path) -> dict[tuple[str, str], list[float]]:
    """(код позиции, имя характеристики) → числа СТЕ в единице позиции."""
    specs = {p["code"]: s for p in positions if (s := _numeric_specs(p))}
    ids: dict[int, str] = {}
    with exemplars.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row.get("id") is not None and row.get("code") in specs:
                ids[int(row["id"])] = row["code"]
    log.info("СТЕ категорий с числовыми полями: %d", len(ids))
    db = sqlite3.connect(f"{values_path(exemplars).resolve().as_uri()}?mode=ro", uri=True)
    out: defaultdict[tuple[str, str], list[float]] = defaultdict(list)
    batch = list(ids)
    for i in range(0, len(batch), 900):
        part = batch[i : i + 900]
        marks = ",".join("?" * len(part))
        for sid, chars in db.execute(f"SELECT id, chars FROM ste WHERE id IN ({marks})", part):
            by_name = specs[ids[sid]]
            for name, (value, unit) in json.loads(chars).items():
                spec = by_name.get(fold(name))
                if spec is None:
                    continue
                fit = fit_value(str(value), unit, spec)
                if fit.status in _FITTED and (x := to_number(fit.value or "")) is not None:
                    out[(ids[sid], spec.name)].append(x)
    db.close()
    return out


def apply(path: Path, exemplars: Path) -> int:
    """Пишет `plausible` в справочник `path`; сколько характеристик получили диапазон."""
    data = json.loads(path.read_text(encoding="utf-8"))
    positions = data.get("positions") or []
    numbers = collect(positions, exemplars)
    written = 0
    for position in positions:
        for spec in position.get("characteristics") or []:
            rng = plausible(numbers.get((position["code"], spec.get("name")), []))
            if rng is None:
                spec.pop("plausible", None)
                continue
            spec["plausible"] = rng.model_dump()
            written += 1
    shutil.copyfile(path, path.with_name(path.name + ".bak"))
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("catalog", type=Path)
    ap.add_argument("--exemplars", type=Path, required=True)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log.info("%s: диапазон у %d характеристик", args.catalog, apply(args.catalog, args.exemplars))


if __name__ == "__main__":
    main()
