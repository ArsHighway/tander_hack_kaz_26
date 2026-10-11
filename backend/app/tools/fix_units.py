"""Единицы в собранном справочнике — по нынешнему словарю, без пересборки.

    uv run python -m app.tools.fix_units ../data/catalog/standards.json ../data/catalog/ktru.json

Собранный справочник мог записать угол как «°C» (словарь делал из любого «градуса»
температуру), а «Мегаватт; тысяча киловатт» и «° градус» оставили как есть. Здесь:
- «°C» у характеристики про угол (имя об угле, не о температуре) → «°»;
- единица не в каноне, которую `canon_unit` (с именем характеристики) приводит к канону, → канон.
Рядом остаётся копия `<файл>.bak`. Полная пересборка (`build_standard_catalog`,
`build_ktru_catalog`) даёт то же самое сама.
"""

import argparse
import json
import logging
import re
import shutil
from collections import Counter
from pathlib import Path

from app.ingest.units import normalize_unit
from app.portal.standards import _HEAT, canon_unit

log = logging.getLogger(__name__)

_ANGLE = re.compile(
    r"угол|угл[аоуы]|наклон|поворот|вращен|изгиб|обзор|зрени|панорам|килеват|конус|ротац|"
    r"раскрыт|открыван|отклонен"
)


def fixed_unit(unit: str | None, name: str) -> str | None:
    if not unit:
        return unit
    low = name.casefold()
    if unit == "°C" and _ANGLE.search(low) and not _HEAT.search(low):
        return "°"
    canon = canon_unit(unit, name)
    if canon and canon != unit and normalize_unit(canon) == canon:
        return canon
    return unit


def fix_catalog(path: Path) -> Counter[tuple[str, str]]:
    """Правит единицы в файле справочника; что на что поменялось и сколько раз."""
    data = json.loads(path.read_text(encoding="utf-8"))
    changes: Counter[tuple[str, str]] = Counter()
    for position in data.get("positions") or []:
        for spec in position.get("characteristics") or []:
            new = fixed_unit(spec.get("unit"), spec.get("name") or "")
            if new != spec.get("unit"):
                changes[(spec["unit"], new or "")] += 1
                spec["unit"] = new
    if changes:
        shutil.copyfile(path, path.with_name(path.name + ".bak"))
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return changes


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("catalogs", nargs="+", type=Path)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for path in args.catalogs:
        changes = fix_catalog(path)
        log.info("%s: исправлено %d", path, sum(changes.values()))
        for (old, new), n in changes.most_common():
            log.info("  %r -> %r: %d", old, new, n)


if __name__ == "__main__":
    main()
