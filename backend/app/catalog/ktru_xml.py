"""Справочник КТРУ из официальной выгрузки ЕИС (XML `nsiKTRUNew`) → `Position`.

Выгрузка организаторов — zip с вложенными zip, в каждом `nsiKTRUNew_all_actual_*.xml` и
`nsiKTRUNew_all_not-actual_*.xml`; по методичке берётся только actual. Позиция —
`oos:position/oos:data`: код, версия, наименование, ОКПД2, статус (`ACTIVE` / `TERMINATED`), единицы
ОКЕИ и характеристики: `type` 1 — качественная (значения `qualityDescription`), 2 — количественная
(диапазоны `rangeSet/valueRange` с `minMathNotation`/`maxMathNotation` или точные
`valueSet/concreteValue`, единица ОКЕИ у значения), `isRequired`, `actual`. Диапазон — корзина
«>=0.1 ... <=1.5», как в карточке ЕИС (`ktru_card.bucket`): её понимает `app.values.fit_value`.
Структура — «Описание структуры xml с КТРУ.docx», стр. 133.
"""

import io
import logging
import zipfile
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Literal
from xml.etree import ElementTree as ET

from app.catalog.ktru_card import bucket
from app.catalog.model import CharacteristicSpec, Position
from app.portal.standards import canon_unit

log = logging.getLogger(__name__)

_SIGNS = {"greaterOrEqual": ">=", "greater": ">", "lessOrEqual": "<=", "less": "<"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el: ET.Element | None, name: str) -> ET.Element | None:
    if el is None:
        return None
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _children(el: ET.Element | None, name: str) -> list[ET.Element]:
    return [] if el is None else [c for c in el if _local(c.tag) == name]


def _text(el: ET.Element | None, name: str) -> str:
    """Текст дочернего элемента; двойные пробелы ЕИС («по  рецептуре») схлопываются."""
    c = _child(el, name)
    return " ".join((c.text or "").split()) if c is not None else ""


def _range(vr: ET.Element) -> str:
    """`valueRange` → «>=41 ... <=60»; граница без знака сравнения не пишется."""
    parts = []
    for bound in ("min", "max"):
        number = _text(vr, bound)
        sign = _SIGNS.get(_text(vr, f"{bound}MathNotation"))
        if number and sign:
            parts.append(f"{sign}{number}")
    return " ... ".join(parts)


def _characteristic(ch: ET.Element) -> CharacteristicSpec | None:
    name = _text(ch, "name")
    if not name or _text(ch, "actual") == "false":
        return None
    values: list[str] = []
    units: Counter[str] = Counter()
    for v in _children(_child(ch, "values"), "value"):
        found: list[str] = []
        # Качественное значение-порог («≥ 3,5») — корзиной, как в карточке ЕИС.
        if quality := _text(v, "qualityDescription"):
            found.append(bucket(quality))
        for vr in _children(_child(v, "rangeSet"), "valueRange"):
            if corridor := _range(vr):
                found.append(corridor)
        found += [
            c.text.strip()
            for c in _children(_child(v, "valueSet"), "concreteValue")
            if c.text and c.text.strip()
        ]
        if found and (okei := _text(_child(v, "OKEI"), "name")):
            units[okei] += 1
        values += [f for f in found if f not in values]
    quantitative = _text(ch, "type") == "2"
    unit = canon_unit(units.most_common(1)[0][0], name) if units else None
    kind: Literal["qualitative", "quantitative"] = (
        "quantitative" if quantitative or unit else "qualitative"
    )
    return CharacteristicSpec(
        name=name, kind=kind, unit=unit, values=values, required=_text(ch, "isRequired") == "true"
    )


@dataclass
class KtruRecord:
    position: Position
    status: str
    version: int


def parse_position(el: ET.Element) -> KtruRecord | None:
    """`oos:position` → позиция; без кода — `None`."""
    data = _child(el, "data")
    code = _text(data, "code")
    if not code:
        return None
    chars = [
        spec
        for ch in _children(_child(data, "characteristics"), "characteristic")
        if (spec := _characteristic(ch)) is not None
    ]
    okeis = _children(_child(data, "OKEIs"), "OKEI")
    position = Position(
        code=code,
        name=_text(data, "name") or code,
        okpd2=_text(_child(data, "OKPD2"), "code") or code.split("-")[0],
        unit=_text(okeis[0], "name") or None if okeis else None,
        characteristics=chars,
        source="ktru-xml",
    )
    version = _text(data, "version")
    return KtruRecord(position, _text(data, "status"), int(version) if version.isdigit() else 0)


def iter_positions(xml: IO[bytes]) -> Iterator[KtruRecord]:
    """Позиции одного XML потоком: разобранный элемент сразу освобождается."""
    for _, el in ET.iterparse(xml, events=("end",)):
        if _local(el.tag) != "position":
            continue
        if (record := parse_position(el)) is not None:
            yield record
        el.clear()


@dataclass
class Selection:
    """Что нашлось во входе: XML взяты (actual), отброшены (not-actual и прочие)."""

    taken: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def _is_actual(name: str) -> bool:
    base = name.rsplit("/", 1)[-1]
    return base.endswith(".xml") and "_actual_" in base and "not-actual" not in base


def _from_zip(zf: zipfile.ZipFile, prefix: str, sel: Selection) -> Iterator[tuple[str, bytes]]:
    for name in sorted(zf.namelist()):
        if name.endswith("/"):
            continue
        label = f"{prefix}{name}"
        if name.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(zf.read(name))) as inner:
                yield from _from_zip(inner, f"{label}:", sel)
        elif _is_actual(name):
            sel.taken.append(label)
            yield label, zf.read(name)
        else:
            sel.dropped.append(label)


def read_xml_files(path: Path, sel: Selection) -> Iterator[tuple[str, bytes]]:
    """XML actual из zip (с вложенными zip), папки или одного файла; остальное — в `sel.dropped`."""
    if path.is_dir():
        for f in sorted(path.rglob("*")):
            if f.is_file() and f.suffix in (".xml", ".zip"):
                yield from read_xml_files(f, sel)
    elif path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            yield from _from_zip(zf, f"{path.name}:", sel)
    elif _is_actual(path.name):
        sel.taken.append(path.name)
        yield path.name, path.read_bytes()
    else:
        sel.dropped.append(path.name)


def load_positions(
    path: Path, *, statuses: tuple[str, ...] = ("ACTIVE",)
) -> tuple[list[Position], dict]:
    """Позиции КТРУ из выгрузки: только нужные статусы, по коду — последняя версия.

    Возвращает позиции и отчёт: файлы взяты и отброшены, статусы, повторы кода.
    """
    sel = Selection()
    best: dict[str, KtruRecord] = {}
    statuses_seen: Counter[str] = Counter()
    repeats = 0
    for _, payload in read_xml_files(path, sel):
        for record in iter_positions(io.BytesIO(payload)):
            statuses_seen[record.status] += 1
            if record.status not in statuses:
                continue
            code = record.position.code
            if code in best:
                repeats += 1
                if best[code].version >= record.version:
                    continue
            best[code] = record
    if not sel.taken:
        raise ValueError(
            f"{path}: нет XML actual (nsiKTRUNew_all_actual_*.xml), отброшено {len(sel.dropped)}"
        )
    report = {
        "files_taken": len(sel.taken),
        "files_dropped": len(sel.dropped),
        "dropped_examples": sel.dropped[:5],
        "statuses": dict(statuses_seen),
        "repeated_codes": repeats,
        "positions": len(best),
    }
    return [r.position for r in best.values()], report
