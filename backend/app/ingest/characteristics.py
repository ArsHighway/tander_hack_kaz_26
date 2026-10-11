"""Разбор характеристик СТЕ из любых встреченных форматов в единый вид.

Форматы: строка «Имя:Значение;…» (Пермь 2026), «Имя:Значение Ед;…» (Казань 2025), JSON-список
пар, список объектов {name, value, unit}, словарь. Кусок без «:» после характеристики —
продолжение её значения: в пермском датасете так записаны перечни внутри значения («Состав:…;
Ирис - 2; …», 1,4% кусков). Кусок без «:» в начале строки не угадывается: он попадает в
`unparsed` сырым текстом.
"""

import contextlib
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

from app.ingest.units import normalize_unit

ValueKind = Literal["number", "range", "bool", "text"]

_NUM = r"[+-]?\d+(?:[.,]\d+)?"
_NUM_RE = re.compile(rf"^{_NUM}$")
_NUM_UNIT_RE = re.compile(rf"^({_NUM})\s*([^\d\s][^\d]{{0,11}})$")
_NAME_UNIT_RE = re.compile(r"^(?P<name>.+?),\s*(?P<unit>[^,]{1,12})$")
_RANGE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"^(?:в диапазоне\s+)?от\s+({_NUM})\s+(?:и\s+)?до\s+({_NUM})$", re.I), "between"),
    (re.compile(rf"^({_NUM})\s*(?:-|–|—|\.\.\.?)\s*({_NUM})$"), "between"),
    # Корзина с двумя границами: портал пишет «>=80 ... <90», КТРУ ЕИС — и «>200 ... <=400».
    (re.compile(rf"^(>=|>)\s*({_NUM})\s*\.\.\.\s*(<=|<)\s*({_NUM})$"), "bounded"),
    (re.compile(rf"^(?:больше или равно|не менее|≥|>=)\s*({_NUM})$", re.I), "ge"),
    (re.compile(rf"^(?:меньше или равно|не более|≤|<=)\s*({_NUM})$", re.I), "le"),
    (re.compile(rf"^(?:больше|более|свыше|>)\s*({_NUM})$", re.I), "gt"),
    (re.compile(rf"^(?:меньше|менее|<)\s*({_NUM})$", re.I), "lt"),
]
_BOOL = {"да": True, "нет": False, "true": True, "false": False, "есть": True, "отсутствует": False}


def to_number(text: str) -> float | None:
    # Типографский минус и короткое тире перед числом — тоже минус (U+2212, U+2013).
    text = re.sub(r"^\s*[\u2212\u2013]", "-", text)
    compact = re.sub(r"(?<=\d)[\s  ](?=\d{3}\b)", "", text.strip())
    if not _NUM_RE.match(compact):
        return None
    return float(compact.replace(",", "."))


@dataclass(frozen=True)
class Value:
    raw: str
    kind: ValueKind
    number: float | None = None
    low: float | None = None
    high: float | None = None
    low_inclusive: bool = True
    high_inclusive: bool = True
    flag: bool | None = None


def parse_value(raw: str) -> tuple[Value, str | None]:
    """Типизирует значение; вторым элементом — единица, если она записана в самом значении."""
    text = re.sub(r"\s+", " ", raw.strip())
    number = to_number(text)
    if number is not None:
        return Value(raw=raw, kind="number", number=number), None
    if text.casefold() in _BOOL:
        return Value(raw=raw, kind="bool", flag=_BOOL[text.casefold()]), None
    for pattern, shape in _RANGE_PATTERNS:
        m = pattern.match(text)
        if not m:
            continue
        if shape == "bounded":
            low_op, low, high_op, high = m.groups()
            return Value(
                raw=raw,
                kind="range",
                low=float(low.replace(",", ".")),
                high=float(high.replace(",", ".")),
                low_inclusive=low_op == ">=",
                high_inclusive=high_op == "<=",
            ), None
        nums = [float(g.replace(",", ".")) for g in m.groups()]
        if shape == "between":
            return Value(raw=raw, kind="range", low=nums[0], high=nums[1]), None
        if shape in ("ge", "gt"):
            return Value(raw=raw, kind="range", low=nums[0], low_inclusive=shape == "ge"), None
        return Value(raw=raw, kind="range", high=nums[0], high_inclusive=shape == "le"), None
    m = _NUM_UNIT_RE.match(text)
    if m and (unit := normalize_unit(m.group(2))):
        return Value(raw=raw, kind="number", number=float(m.group(1).replace(",", "."))), unit
    return Value(raw=raw, kind="text"), None


@lru_cache(maxsize=1 << 16)  # имена характеристик позиций повторяются в каждом запросе
def split_name_unit(raw_name: str) -> tuple[str, str | None]:
    """«Толщина, мкм» → («Толщина», «мкм»); запятая без единицы после неё имя не режет."""
    name = re.sub(r"\s+", " ", raw_name.strip())
    m = _NAME_UNIT_RE.match(name)
    if m and (unit := normalize_unit(m.group("unit"))):
        return m.group("name").strip(), unit
    return name, None


@dataclass(frozen=True)
class Characteristic:
    raw_name: str
    name: str
    unit: str | None
    value: Value


@dataclass
class ParsedCharacteristics:
    fmt: str
    items: list[Characteristic] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)
    merged: int = 0


def make_characteristic(raw_name: str, raw_value: Any, unit: str | None = None) -> Characteristic:
    name, name_unit = split_name_unit(str(raw_name))
    value, value_unit = parse_value("" if raw_value is None else str(raw_value))
    return Characteristic(
        raw_name=str(raw_name),
        name=name,
        unit=normalize_unit(unit) or unit or name_unit or value_unit,
        value=value,
    )


def parse_characteristics(raw: Any) -> ParsedCharacteristics:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return ParsedCharacteristics(fmt="empty")
    if isinstance(raw, str) and raw.lstrip()[:1] in "[{":
        with contextlib.suppress(ValueError):
            raw = json.loads(raw)
    if isinstance(raw, dict):
        items = [make_characteristic(k, v) for k, v in raw.items()]
        return ParsedCharacteristics(fmt="dict", items=items)
    if isinstance(raw, list):
        return _parse_list(raw)
    return _parse_kv_string(str(raw))


def _parse_list(raw: list[Any]) -> ParsedCharacteristics:
    result = ParsedCharacteristics(fmt="list")
    for entry in raw:
        if isinstance(entry, list | tuple) and len(entry) in (2, 3):
            result.items.append(make_characteristic(*entry))
        elif isinstance(entry, dict) and "name" in entry:
            result.items.append(
                make_characteristic(entry["name"], entry.get("value"), entry.get("unit"))
            )
        else:
            result.unparsed.append(json.dumps(entry, ensure_ascii=False))
    return result


# Пункт списка внутри значения — продолжение, а не новая характеристика: «- обложка: 2 шт»,
# «1) скальпель: 2 шт», «. крышка». Имя может начинаться с цифры: «1 размер», «10base-t».
_LIST_ITEM = re.compile(r"\s*(?:[-–—•.]|[0-9]+[.)]\s)")


def _parse_kv_string(raw: str) -> ParsedCharacteristics:
    result = ParsedCharacteristics(fmt="kv_string")
    for segment in raw.split(";"):
        name, sep, value = segment.partition(":")
        starts = bool(sep) and bool(name.strip()) and _LIST_ITEM.match(name) is None
        if starts:
            # «Отдушка::Морская» — у имени в источнике своё двоеточие на конце.
            result.items.append(make_characteristic(name, value.lstrip(": ")))
            continue
        if not segment.strip():
            continue
        if result.items:
            # Продолжение значения: «;» был внутри него («D(LR20;R20;HR20)»), текст сохраняем.
            prev = result.items[-1]
            joined = f"{prev.value.raw};{segment.rstrip()}"
            result.items[-1] = Characteristic(
                prev.raw_name, prev.name, prev.unit, Value(raw=joined, kind="text")
            )
            result.merged += 1
        else:
            result.unparsed.append(segment.strip())
    return result
