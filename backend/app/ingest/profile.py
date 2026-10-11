"""Профиль таблицы: заполненность колонок и их роль, угаданная по содержимому, а не по названию.

Названия колонок у организаторов меняются от города к городу («Идентификатор СТЕ», «ID»,
«sku_id»), а содержимое — нет: код КТРУ всегда `XX.XX.XX.XXX-XXXXXXXX`, характеристики — пары
«имя:значение». Название колонки даёт только небольшой бонус.
"""

import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import islice
from typing import Any

from app.ingest.characteristics import parse_characteristics, to_number
from app.ingest.readers import Table
from app.ingest.units import normalize_unit

Role = str
_KTRU = re.compile(r"^\d{2}\.\d{2}\.\d{2}\.\d{3}-\d{7,8}$")
_OKPD2 = re.compile(r"^\d{2}\.\d{2}(\.\d{1,2}(\.\d{3})?)?$")
_KPGZ = re.compile(r"^\d{2}(\.\d{2}){2,6}$")
_INN = re.compile(r"^(\d{10}|\d{12})$")
_ID = re.compile(r"^\d{4,15}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}|^\d{2}\.\d{2}\.\d{4}")
_ORG = re.compile(r"\b(ООО|ОАО|ЗАО|АО|ПАО|ИП|ОБЩЕСТВО|LTD|LLC|GMBH|INC)\b", re.I)
_ABBR = re.compile(r"\b[А-ЯЁA-Z]{2,6}\d?\b")
_ROMAN = re.compile(r"^[IVXLC]+$")

HEADER_HINTS: dict[Role, tuple[str, ...]] = {
    "id": ("идентификатор", "id", "код сте", "артикул"),
    "name": ("наименование", "название", "name", "товар"),
    "category": ("категор", "рубрик", "раздел", "category"),
    "manufacturer": ("производител", "бренд", "manufacturer", "vendor"),
    "characteristics": ("характеристик", "атрибут", "attributes", "свойств"),
    "ktru_code": ("ктру",),
    "okpd2_code": ("окпд",),
    "kpgz_code": ("кпгз", "спгз"),
    "unit": ("единиц", "ед. изм", "океи", "unit"),
    "price": ("цена", "стоимость", "price", "сумма"),
    "date": ("дата", "date"),
    "inn": ("инн",),
    "description": ("описание", "description"),
}


def _share(values: list[str], pred: Callable[[str], bool]) -> float:
    return sum(pred(v) for v in values) / len(values) if values else 0.0


def _is_characteristics(value: str) -> bool:
    if value.lstrip()[:1] in "[{":
        return bool(parse_characteristics(value).items)
    name, sep, _ = value.partition(":")
    # До двоеточия — имя со словами: так дата «2024-06-05 14:43» не сойдёт за характеристику.
    has_name = bool(sep) and any(ch.isalpha() for ch in name)
    return has_name and (value.count(";") >= 1 or len(value) < 80)


def content_scores(values: list[str]) -> dict[Role, float]:
    distinct = len(set(values)) / len(values) if values else 0.0
    avg_len = sum(map(len, values)) / len(values) if values else 0.0
    numeric = _share(values, lambda v: to_number(v) is not None)
    return {
        "ktru_code": _share(values, lambda v: bool(_KTRU.match(v))),
        "okpd2_code": _share(values, lambda v: bool(_OKPD2.match(v))) * 0.8,
        "kpgz_code": _share(values, lambda v: bool(_KPGZ.match(v))) * 0.8,
        "inn": _share(values, lambda v: bool(_INN.match(v))) * 0.7,
        "id": _share(values, lambda v: bool(_ID.match(v))) * (0.9 if distinct > 0.9 else 0.4),
        "characteristics": _share(values, _is_characteristics),
        "unit": _share(values, lambda v: normalize_unit(v) is not None),
        "date": _share(values, lambda v: bool(_DATE.match(v))),
        "price": numeric * 0.5,
        "name": (0.6 if distinct > 0.5 and 8 <= avg_len <= 250 and numeric < 0.1 else 0.0),
        "category": (0.5 if distinct < 0.5 and 4 <= avg_len <= 120 and numeric < 0.1 else 0.0),
        "manufacturer": _share(values, lambda v: bool(_ORG.search(v))) * 0.9,
        "description": (0.5 if avg_len > 250 else 0.0),
    }


def detect_role(column: str, values: list[str]) -> tuple[Role, float]:
    content = content_scores(values)
    scores = dict(content)
    header = column.casefold()
    hinted = [role for role, hints in HEADER_HINTS.items() if any(h in header for h in hints)]
    for role in hinted:
        scores[role] = scores.get(role, 0.0) + 0.5
    role, score = max(scores.items(), key=lambda kv: kv[1])
    # Заголовок поставщика главнее слабой догадки по содержимому: «Описание» из коротких фраз
    # похоже на наименование, но это описание. Уверенное содержимое (коды КТРУ, ИНН) главнее.
    for hint in hinted:
        if hint != role and scores[hint] >= 0.5 and content.get(role, 0.0) < 0.9:
            return hint, round(scores[hint], 2)
    return (role, round(score, 2)) if score >= 0.5 else ("unknown", round(score, 2))


@dataclass
class ColumnProfile:
    name: str
    filled: int
    examples: list[str]
    role: Role
    score: float


@dataclass
class CharacteristicsStats:
    column: str
    rows: int
    parsed_items: int
    unparsed_segments: int
    distinct_names: int
    top_names: list[tuple[str, int]]
    units: list[tuple[str, int]]
    abbreviations: list[tuple[str, int]]


@dataclass
class TableProfile:
    source: str
    row_count: int | None
    sampled: int
    columns: list[ColumnProfile] = field(default_factory=list)
    characteristics: CharacteristicsStats | None = None

    def column_for(self, role: Role) -> str | None:
        best = [c for c in self.columns if c.role == role]
        return max(best, key=lambda c: c.score).name if best else None


def _text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, list | dict):  # jsonl/json: характеристики списком пар или словарём
        return json.dumps(v, ensure_ascii=False)
    return str(v).strip()


def profile_table(table: Table, sample: int = 20000) -> TableProfile:
    rows = list(islice(table.rows(), sample))
    prof = TableProfile(source=table.source, row_count=table.row_count, sampled=len(rows))
    for col in table.columns:
        values = [t for r in rows if (t := _text(r.get(col)))]
        role, score = detect_role(col, values[:2000])
        examples = list(dict.fromkeys(v[:120] for v in values[:50]))[:3]
        prof.columns.append(ColumnProfile(col, len(values), examples, role, score))
    char_col = prof.column_for("characteristics")
    if char_col:
        prof.characteristics = characteristics_stats(char_col, [r.get(char_col) for r in rows])
    return prof


def characteristics_stats(column: str, raw_values: list[Any]) -> CharacteristicsStats:
    names: Counter[str] = Counter()
    units: Counter[str] = Counter()
    abbrs: Counter[str] = Counter()
    items = unparsed = 0
    for raw in raw_values:
        parsed = parse_characteristics(raw)
        items += len(parsed.items)
        unparsed += len(parsed.unparsed)
        for ch in parsed.items:
            names[ch.name] += 1
            if ch.unit:
                units[ch.unit] += 1
            abbrs.update(a for a in _ABBR.findall(ch.value.raw) if not _ROMAN.match(a))
    return CharacteristicsStats(
        column=column,
        rows=len(raw_values),
        parsed_items=items,
        unparsed_segments=unparsed,
        distinct_names=len(names),
        top_names=names.most_common(15),
        units=units.most_common(15),
        abbreviations=abbrs.most_common(20),
    )
