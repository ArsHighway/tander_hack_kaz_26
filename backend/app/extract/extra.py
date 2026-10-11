"""Свойства из текста поставщика вне шаблона категории: «как пишут на портале» или «новое».

Характеристика сначала ищется в КТРУ, затем — как это свойство
пишут на портале, и только потом заводится новая («Ручка противоскользящая: да»); ничего не
выбрасывается. Шаблон категории заполняют правила (`rules.py`); здесь — то, что в него не легло.

Пара берётся из описания: «Метка: значение» (строки, «;», «|», «•», запятая перед следующей
«Метка:») и «метка значение» без двоеточия, если метка — известное порталу имя характеристики
(формат выгрузок: «Емкость аккумулятора 1.50000 А.ч | …»). Метка шаблона или КТРУ (с синонимами
стандарта) — не лишняя. Служебные метки, общие сведения, мат и метки без слова из трёх букв
отбрасываются: заводить свойство из мусора — та же «мусорка», против которой задача.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

import regex as re

from app.checks.profanity import find_profanity
from app.extract.new_names import NewNames, new_key
from app.ingest.characteristics import parse_value, split_name_unit
from app.ingest.units import normalize_unit
from app.schemas import Span
from app.values import fold, same_word_form

MAX_EXTRA = 15
MAX_LABEL = 60
MAX_VALUE = 120
# Самое длинное имя характеристики портала, которое ищем без двоеточия, в словах.
MAX_LABEL_WORDS = 6

_FRAGMENT = re.compile(r"[^\n;|•]+")
# Запятая, после которой начинается следующая «Метка:» — граница пары внутри фрагмента.
_NEXT_LABEL = re.compile(r",\s*(?=[^,:\d][^,:]{1,58}:)")
_COLON = re.compile(r"^\s*(?P<label>[^:]{2,300}?)\s*:\s*(?P<value>.+?)\s*$", re.S)
_WORD = re.compile(r"\p{L}{3,}")
_SENTENCE_END = re.compile(r"[.!?]\s+")
# Конец значения — конец предложения: «да, без покрытия. Звоните …» — дальше уже не значение.
_NEXT_SENTENCE = re.compile(r"[.!?]\s+(?=\p{Lu})")
_TOKEN = re.compile(r"\S+")
# Метки, которые не свойства товара: заголовки и служебные поля.
_SERVICE = {
    fold(x)
    for x in (
        "Описание", "Примечание", "Примечания", "Комментарий", "Характеристики", "Наименование",
        "Товар", "Особенности", "Назначение товара", "Код", "Код товара", "Цена", "Стоимость",
        "Количество", "Срок поставки", "Доставка", "Гарантия поставщика", "Артикул поставщика",
    )
}  # fmt: skip
# «Общие сведения» карточки: у них свои поля, свойствами вне шаблона они не становятся.
_GENERAL = {
    fold(x)
    for x in ("Производитель", "Бренд", "Торговая марка", "Марка", "Модель", "Страна",
              "Страна происхождения", "Страна производства", "Страна изготовления")
}  # fmt: skip

Origin = Literal["portal", "new"]


@dataclass(frozen=True)
class ExtraPair:
    """Свойство вне шаблона: имя (портала или поставщика), значение, единица, фрагмент текста."""

    name: str
    value: str
    unit: str | None
    origin: Origin
    source: Span
    # Новая характеристика уже встречалась (`app.extract.new_names`): сколько раз записана.
    seen: int = 0


class PortalNames:
    """Имена характеристик портала по всем категориям: ключ `fold` → самое частое написание."""

    def __init__(self, names: Iterable[str]) -> None:
        counts: dict[str, Counter[str]] = {}
        for name in names:
            base, _ = split_name_unit(name)
            key = fold(base)
            if key:
                counts.setdefault(key, Counter())[base] += 1
        self.canon = {key: c.most_common(1)[0][0] for key, c in counts.items()}

    def get(self, label: str) -> str | None:
        return self.canon.get(fold(label))


def _pairs(text: str) -> Iterable[tuple[str, str, int, int]]:
    """(метка, значение, начало, конец) пар с двоеточием и фрагменты без него (метка пустая)."""
    for frag in _FRAGMENT.finditer(text):
        start = frag.start()
        pos = 0
        # Делим по запятой, только если пар во фрагменте несколько: «Длина ручки, мм: 300» — одна.
        parts = _NEXT_LABEL.split(frag.group()) if frag.group().count(":") > 1 else [frag.group()]
        for part in parts:
            offset = frag.group().find(part, pos)
            pos = offset + len(part)
            a, b = start + offset, start + offset + len(part)
            if m := _COLON.match(part):
                label = _label_tail(m["label"])
                cut = m["label"].rfind(label) if label != m["label"] else 0
                yield label, m["value"], a + m.start("label") + cut, b
            elif part.strip():
                yield "", part, a, b


def _label_tail(raw: str) -> str:
    """Метка — хвост перед двоеточием: после конца фразы («…320 мм. Ручка противоскользящая») и
    после запятой («Обивка ткань, сиденье»), если за запятой не единица («Длина ручки, мм»)."""
    label = _SENTENCE_END.split(raw)[-1]
    _, comma, tail = label.rpartition(",")
    if comma and tail.strip() and not normalize_unit(tail.strip()):
        return tail.strip()
    return label


def _split_known(fragment: str, portal: PortalNames) -> tuple[str, str] | None:
    """«Емкость аккумулятора 1.5 А.ч» → самая длинная известная порталу метка в начале и остаток."""
    words = _TOKEN.findall(fragment)
    for k in range(min(MAX_LABEL_WORDS, len(words) - 1), 0, -1):
        label = " ".join(words[:k])
        if portal.get(label):
            return label, " ".join(words[k:])
    return None


def _known_tail(raw: str, portal: PortalNames) -> int | None:
    """Где кончается значение: «глина, размер 52» — после запятой своя пара с меткой, известной
    порталу («размер 52»). «хлопок, полиэстер» — перечень одного свойства, его не режем."""
    for m in re.finditer(r",\s*", raw):
        if _split_known(raw[m.end() :].strip(), portal) is not None:
            return m.start()
    return None


def _canon_name(label: str, portal: PortalNames) -> str | None:
    """Имя как на портале: точное или единственная общая основа."""
    exact = portal.get(label)
    if exact is not None:
        return exact
    hits = [canon for canon in portal.canon.values() if same_word_form(label, canon)]
    unique = set(hits)
    return next(iter(unique)) if len(unique) == 1 else None


def _value_unit(raw: str) -> tuple[str, str | None]:
    value = " ".join(raw.split()).strip(" ,.")
    parsed, unit = parse_value(value)
    if unit and parsed.number is not None:
        return f"{parsed.number:g}".replace(".", ","), unit
    return value, None


def extra_pairs(
    description: str,
    known: set[str],
    portal: PortalNames,
    max_extra: int = MAX_EXTRA,
    new_names: NewNames | None = None,
) -> list[ExtraPair]:
    """Свойства вне шаблона из описания. `known` — `fold` имён и синонимов шаблона и КТРУ."""
    out: list[ExtraPair] = []
    seen: set[str] = set()
    for label, raw, a, b in _pairs(description):
        if not label:
            split = _split_known(raw.strip(), portal)
            if split is None:
                continue
            label, raw = split
        ends = [m.start() for m in [_NEXT_SENTENCE.search(raw)] if m is not None]
        if (tail := _known_tail(raw, portal)) is not None:
            ends.append(tail)
        if (cut := min(ends, default=None)) is not None:
            at = description[a:b].find(raw)
            raw = raw[:cut]
            if at >= 0:
                b = a + at + cut
        base, unit_in_label = split_name_unit(label)
        key = fold(base)
        value, unit = _value_unit(raw)
        if (
            not key
            or key in known
            or key in seen
            or key in _SERVICE
            or key in _GENERAL
            or not _WORD.search(base)
            or len(base) > MAX_LABEL
            or not value
            or len(value) > MAX_VALUE
            or find_profanity(f"{base} {value}")
        ):
            continue
        seen.add(key)
        canon = _canon_name(base, portal)
        recorded = (new_names or {}).get(new_key(base)) if canon is None else None
        seg = description[a:b]
        lead, trail = len(seg) - len(seg.lstrip()), len(seg) - len(seg.rstrip())
        out.append(
            ExtraPair(
                name=canon or (recorded[0] if recorded else base[:1].upper() + base[1:]),
                value=value,
                unit=unit or unit_in_label,
                origin="portal" if canon else "new",
                source=Span(field="description", start=a + lead, end=b - trail, text=seg.strip()),
                seen=recorded[1] if recorded else 0,
            )
        )
        if len(out) >= max_extra:
            break
    return out
