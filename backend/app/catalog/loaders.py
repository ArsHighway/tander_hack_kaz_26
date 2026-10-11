"""Загрузчики справочника: файл источника → список `Position`.

Форматы: JSON (`{"positions": [...]}` или список позиций) и длинный CSV — строка на значение
характеристики («Код КТРУ;Наименование позиции;ОКПД2;Характеристика;Значение;Единица;Обязательная»).
Справочник организаторов придёт в своём формате: под него пишется ещё один загрузчик, остальной
модуль не меняется.
"""

import csv
import json
import re
from pathlib import Path
from typing import Any, Literal

from app.catalog.model import CatalogError, CharacteristicSpec, NumRange, Position
from app.ingest.characteristics import to_number
from app.ingest.readers import detect_encoding, read_head

# «146 - 175», «146–175»: диапазон в одной ячейке длинного CSV.
_RANGE = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)\s*[-–—]\s*(-?\d+(?:[.,]\d+)?)\s*$")
# Значение числовой характеристики: число или корзина «>=80 ... <90», «>=500».
_NUMERIC_VALUE = re.compile(
    r"^\s*(?:[<>]=?\s*)?\d+(?:[.,]\d+)?(?:\s*\.\.\.\s*[<>]=?\s*\d+(?:[.,]\d+)?)?\s*$"
)


def load_catalog_json(path: Path) -> list[Position]:
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("positions") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise CatalogError(f"{path}: ожидался список позиций или объект с ключом positions")
    source = data.get("source", path.name) if isinstance(data, dict) else path.name
    return [Position.model_validate({"source": source, **item}) for item in raw]


def _kind(
    unit: str | None, values: list[str], has_range: bool
) -> Literal["qualitative", "quantitative"]:
    if unit or has_range:
        return "quantitative"
    return (
        "quantitative" if values and all(_NUMERIC_VALUE.match(v) for v in values) else "qualitative"
    )


def load_catalog_csv(path: Path) -> list[Position]:
    encoding = detect_encoding(read_head(path))
    with path.open(encoding=encoding, newline="") as fh:
        rows = list(csv.reader(fh, delimiter=";"))
    if not rows:
        raise CatalogError(f"{path}: пустой файл")
    header, body = rows[0], rows[1:]
    if len(header) < 7:
        raise CatalogError(f"{path}: ожидалось 7 колонок, в заголовке {len(header)}")
    positions: dict[str, dict[str, Any]] = {}
    specs: dict[str, dict[str, dict[str, Any]]] = {}
    for line_no, row in enumerate(body, start=2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) < 7:
            raise CatalogError(f"{path}:{line_no}: ожидалось 7 колонок, получено {len(row)}")
        code, name, okpd2, char_name, value, unit, required = (cell.strip() for cell in row[:7])
        positions.setdefault(code, {"code": code, "name": name, "okpd2": okpd2})
        spec = specs.setdefault(code, {}).setdefault(
            char_name, {"name": char_name, "unit": unit or None, "values": [], "range": None}
        )
        spec["required"] = required.casefold() in {"да", "yes", "true", "1"}
        if m := _RANGE.match(value):
            spec["range"] = NumRange(min=to_number(m[1]), max=to_number(m[2]))
        elif value:
            spec["values"].append(value)
    out = []
    for code, pos in positions.items():
        chars = [
            CharacteristicSpec(kind=_kind(s["unit"], s["values"], s["range"] is not None), **s)
            for s in specs[code].values()
        ]
        out.append(Position(characteristics=chars, source=path.name, **pos))
    return out
