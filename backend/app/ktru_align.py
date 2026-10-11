"""Подгон значения поставщика под характеристику КТРУ.

В поле остаётся конкретное значение поставщика. Корзина и пункт перечня — в `ktru_match`,
итог сопоставления — в `ktru_fit`. Смысл значения не подменяется: «глина» не становится «Деревом».
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.catalog import CharacteristicSpec, Position
from app.extract.rules import QUALIFIERS
from app.ingest.units import normalize_unit
from app.search.text import tokens
from app.values import (
    Bucket,
    _fmt,
    compact,
    fit_value,
    fold,
    list_form,
    parse_bucket,
    quantity,
)

KtruFit = Literal["in_list", "normalized", "in_range", "not_in_list", "out_of_range"]
_FROM_TYPE = re.compile(r"(?i)(?:^|\s)из\s+(\w+)")
_NUMBER_NOTE = re.compile(r"^([\d][\d\s,]*)\s*→")


@dataclass(frozen=True)
class Aligned:
    value: str | None
    unit: str | None
    status: Literal["filled", "normalized", "not_found", "conflict"]
    ktru_fit: KtruFit | None
    ktru_match: str | None
    note: str | None
    normalized_from: str | None = None
    fix_value: str | None = None
    fix_unit: str | None = None


def align_ktru(raw: str, raw_unit: str | None, spec: CharacteristicSpec) -> Aligned:
    """Сырое значение поставщика против одной характеристики КТРУ."""
    if spec.kind == "qualitative":
        return _qualitative(raw, spec)
    return _quantitative(raw, raw_unit, spec)


def recover_raw(value: str | None, unit: str | None, note: str | None) -> tuple[str, str | None]:
    """Число, которое `fit_value` заменил текстом корзины: оно записано в начале примечания."""
    if value and (bucket := parse_bucket(value)) is not None and not bucket.is_point:
        found = _NUMBER_NOTE.match(note or "")
        if found:
            return found.group(1).replace(" ", "").replace("\u00a0", ""), unit
    return value or "", unit


def alias_of(name: str, specs: list[CharacteristicSpec]) -> CharacteristicSpec | None:
    """«Ширина» → «Габаритная ширина», «Материал» → «Материал изделия», если такая одна.

    Слова метки целиком входят в имя характеристики КТРУ, а остальные слова имени — уточнения вроде
    «габаритная», «изделия»: «Вид» — не «Вид опоры стола». Две такие характеристики — не выбираем.
    """
    words = set(tokens(name))
    if not words:
        return None
    hits: list[CharacteristicSpec] = []
    for spec in specs:
        own = set(tokens(spec.name))
        if words < own and own - words <= QUALIFIERS:
            hits.append(spec)
    unique = {fold(spec.name): spec for spec in hits}
    return next(iter(unique.values())) if len(unique) == 1 else None


def required_on_all(positions: list[Position]) -> set[str]:
    """Имена, обязательные у каждой переданной позиции. Пустой список позиций — пустое множество."""
    if not positions:
        return set()
    sets = [
        {fold(c.name) for c in position.characteristics if c.required} for position in positions
    ]
    return set.intersection(*sets)


def implied_from_type(title: str, spec: CharacteristicSpec) -> str | None:
    """«Куртка зимняя из кожи» → материал «Кожаный», если в перечне ровно одно такое значение."""
    if spec.kind != "qualitative" or not spec.values:
        return None
    found = _FROM_TYPE.search(title)
    if found is None:
        return None
    word = found.group(1)
    fit = fit_value(word, None, spec)
    if fit.status in ("exact", "normalized") and fit.value:
        return fit.value
    return list_form(word, spec.values)


def _qualitative(raw: str, spec: CharacteristicSpec) -> Aligned:
    value = raw.strip()
    fit = fit_value(value, None, spec)
    if fit.status in ("exact", "normalized", "typo") and fit.value:
        brought = fit.status != "exact"
        note = fit.note
        if brought and fit.status == "normalized" and note and "приведено" not in note:
            note = f"приведено к формулировке КТРУ: «{value}» → «{fit.value}»"
        return Aligned(
            value=fit.value,
            unit=spec.unit,
            status="normalized" if brought else "filled",
            ktru_fit="normalized" if brought else "in_list",
            ktru_match=fit.value,
            note=note,
            normalized_from=value if brought else None,
        )
    allowed = ", ".join(spec.values)
    return Aligned(
        value=value,
        unit=spec.unit,
        status="conflict",
        ktru_fit="not_in_list",
        ktru_match=None,
        note=(
            f"Значения «{value}» нет в КТРУ для «{spec.name}». Допустимые: {allowed}. "
            "Выберите одно или проверьте тип товара."
        ),
    )


def _quantitative(raw: str, raw_unit: str | None, spec: CharacteristicSpec) -> Aligned:
    fit = fit_value(raw, raw_unit, spec)
    if fit.status in ("unit_mismatch", "unparsed") or fit.supplier is None:
        return Aligned(
            value=raw.strip(),
            unit=raw_unit or spec.unit,
            status="conflict",
            ktru_fit=None,
            ktru_match=None,
            note=fit.note,
        )
    number = fit.supplier
    unit = fit.unit or spec.unit
    brought = fold(number) != fold(raw.strip()) or (
        raw_unit is not None and normalize_unit(raw_unit) not in (None, unit)
    )
    if fit.status == "bucket" and fit.value:
        bucket = parse_bucket(fit.value)
        note = f"КТРУ: {fit.value} — подходит"
        if bucket is not None and _open(bucket) and (usual := _unusual(spec, number)):
            note = f"{note}; необычно для категории: у СТЕ портала {usual}"
        return Aligned(
            value=number,
            unit=unit,
            status="normalized" if brought else "filled",
            ktru_fit="in_range",
            ktru_match=fit.value,
            note=note,
            normalized_from=raw.strip() if brought else None,
        )
    if fit.status in ("exact", "normalized") and fit.value:
        return Aligned(
            value=number,
            unit=unit,
            status="normalized" if brought else "filled",
            ktru_fit="in_range" if _bounded(spec) else "in_list",
            ktru_match=fit.value if spec.values else None,
            note=fit.note,
            normalized_from=raw.strip() if brought else None,
        )
    if _bounded(spec):
        span = _span(spec)
        unit_text = f" {unit}" if unit else ""
        note = (
            f"{spec.name} {_grouped(number)}{unit_text} — вне всех диапазонов КТРУ "
            f"({span}{unit_text}). Проверьте единицу."
        )
        fix_value, fix_unit, hint = _slip(float(number.replace(",", ".")), spec)
        if hint:
            note = f"{note} {hint}"
        return Aligned(
            value=number,
            unit=unit,
            status="conflict",
            ktru_fit="out_of_range",
            ktru_match=None,
            note=note,
            normalized_from=raw.strip() if brought else None,
            fix_value=fix_value,
            fix_unit=fix_unit,
        )
    return Aligned(
        value=number,
        unit=unit,
        status="conflict",
        ktru_fit="not_in_list",
        ktru_match=None,
        note=fit.note
        or (
            f"Значения «{number}» нет в КТРУ для «{spec.name}». "
            "Выберите одно или проверьте тип товара."
        ),
        normalized_from=raw.strip() if brought else None,
    )


def _open(bucket: Bucket) -> bool:
    return bucket.lo is None or bucket.hi is None


def _bounded(spec: CharacteristicSpec) -> bool:
    if spec.range is not None and (spec.range.min is not None or spec.range.max is not None):
        return True
    buckets = [parse_bucket(option) for option in spec.values]
    return any(bucket is not None and not bucket.is_point for bucket in buckets)


def _intervals(spec: CharacteristicSpec) -> list[tuple[float | None, float | None]]:
    found: list[tuple[float | None, float | None]] = []
    for option in spec.values:
        bucket = parse_bucket(option)
        if bucket is not None and not bucket.is_point:
            found.append((bucket.lo, bucket.hi))
    if spec.range is not None and (spec.range.min is not None or spec.range.max is not None):
        found.append((spec.range.min, spec.range.max))
    plausible = spec.plausible
    if plausible is not None and (plausible.min is not None or plausible.max is not None):
        found.append((plausible.min, plausible.max))
    return found


def _inside(value: float, lo: float | None, hi: float | None) -> bool:
    if lo is None and hi is None:
        return False
    if lo is not None and value < lo:
        return False
    return not (hi is not None and value > hi)


def _near(value: float, lo: float | None, hi: float | None) -> bool:
    if _inside(value, lo, hi):
        return True
    if lo is not None and hi is not None:
        return lo / 2 <= value <= hi * 2
    if hi is not None:
        return hi / 2 <= value <= hi * 2
    if lo is not None:
        return lo / 2 <= value <= lo * 2
    return False


def _span(spec: CharacteristicSpec) -> str:
    lows = [lo for lo, _ in _intervals(spec) if lo is not None]
    highs = [hi for _, hi in _intervals(spec) if hi is not None]
    if lows and highs:
        return f"{_grouped(_fmt(min(lows)))}–{_grouped(_fmt(max(highs)))}"
    if highs:
        return f"не более {_grouped(_fmt(max(highs)))}"
    if lows:
        return f"более {_grouped(_fmt(min(lows)))}"
    return "—"


def _slip(number: float, spec: CharacteristicSpec) -> tuple[str | None, str | None, str | None]:
    """Делитель 10, 100 или 1000, после которого число рядом с диапазоном КТРУ или внутри него."""
    intervals = _intervals(spec)
    if not intervals or number == 0:
        return None, None, None
    best: tuple[float, float, int] | None = None
    for divisor in (10, 100, 1000):
        shrunk = number / divisor
        distance = _distance(shrunk, intervals)
        if distance is None:
            continue
        if best is None or distance < best[0]:
            best = (distance, shrunk, divisor)
    if best is None:
        return None, None, None
    _, shrunk, _ = best
    unit = spec.unit
    shown = _fmt(shrunk)
    shorter = compact(shown, unit)
    hint = (
        f"Возможно, {shorter} ({_grouped(shown)} {unit})?"
        if shorter and unit
        else (f"Возможно, {_grouped(shown)} {unit}?" if unit else f"Возможно, {_grouped(shown)}?")
    )
    return shown, unit, hint


def _distance(value: float, intervals: list[tuple[float | None, float | None]]) -> float | None:
    best: float | None = None
    for lo, hi in intervals:
        if not _near(value, lo, hi):
            continue
        if _inside(value, lo, hi):
            distance = 0.0
        elif lo is not None and value < lo:
            distance = lo - value
        elif hi is not None and value > hi:
            distance = value - hi
        else:
            continue
        best = distance if best is None else min(best, distance)
    return best


def _unusual(spec: CharacteristicSpec, number: str) -> str | None:
    plausible = spec.plausible
    if plausible is None:
        return None
    value = float(number.replace(",", "."))
    unit = f" {spec.unit}" if spec.unit else ""
    if plausible.max is not None and value > plausible.max:
        return f"не больше {_grouped(_fmt(plausible.max))}{unit}"
    if plausible.min is not None and value < plausible.min:
        return f"не меньше {_grouped(_fmt(plausible.min))}{unit}"
    return None


def _grouped(number: str) -> str:
    whole, _, frac = number.partition(",")
    if not whole.lstrip("-").isdigit():
        return number
    sign = "-" if whole.startswith("-") else ""
    digits = whole.lstrip("-")
    parts = []
    while digits:
        parts.append(digits[-3:])
        digits = digits[:-3]
    grouped = sign + " ".join(reversed(parts))
    return f"{grouped},{frac}" if frac else grouped


def _words(text: str) -> list[str]:
    return [fold(word) for word in re.findall(r"[^\W\d_]+", text, re.UNICODE)]


def name_quantity(name: str) -> str | None:
    """Величина, которую обещает имя характеристики («Вес» — масса, «Высота» — длина)."""
    folded = fold(name)
    if any(stem in folded for stem in ("вес", "масс")):
        return "масса"
    if any(stem in folded for stem in ("длин", "ширин", "высот", "глубин", "толщин", "диаметр")):
        return "длина"
    if "объем" in folded or "объём" in folded:
        return "объём"
    return None


def unit_disagrees(name: str, unit: str | None) -> bool:
    """«Вес: 5 метров» — единица другой величины, чем имя."""
    expected = name_quantity(name)
    given = quantity(normalize_unit(unit) or unit)
    return expected is not None and given is not None and expected != given
