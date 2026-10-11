"""Извлечение характеристик правилами: без модели, с точным местом в тексте.

Порядок (первое найденное для характеристики побеждает):
1. строки описания вида «метка[, ед.] значение» — метка сопоставляется с именем характеристики
   позиции;
2. числа с единицей в наименовании («500л», «60 л», «20 шт») — через правила модерации
   (`moderation`);
3. размеры «25х150х6000 мм» и одиночная длина «5м», счётные «6 розеток», «12 литров»;
4. значение из перечня позиции, встреченное в тексте словом целиком («А4», «ПНД», «нестерильные»).
"""

import os
from collections.abc import Iterator
from functools import lru_cache
from itertools import pairwise
from typing import Literal

import regex as re

from app.catalog import CharacteristicSpec
from app.checks.moderation import (
    NAME_ALIASES,
    SINGLE_NUM,
    UNIT_ALIAS,
    TitleValue,
    find_title_values,
    target_char,
)
from app.extract.model import Extracted
from app.extract.quantities import (
    bare_values,
    identifier_like,
    negative_at,
    number_in_range,
    phrase_values,
)
from app.glossary import Glossary
from app.ingest.characteristics import split_name_unit
from app.ingest.units import normalize_unit
from app.schemas import Span
from app.search.text import tokens
from app.values import COUNT_UNITS, convert, damerau_levenshtein, fit_value, fold, quantity

Field = Literal["name", "description"]
_OK = {"exact", "normalized", "bucket", "typo"}

# Метка совпала, если в ней есть не меньше половины слов имени характеристики.
MIN_LABEL_OVERLAP = 0.5
# «|» — разделитель характеристик в выгрузках портала («Вес 2 кг | Высота 100 мм»).
_SEGMENT = re.compile(r"[^\n;|]+")
# Конец предложения: точка после буквы или скобки и пробел («1.5» и «0,5» не режем); после числа —
# если дальше заглавная («Размер 60. Утеплитель: пух»).
_SENTENCE = re.compile(r"(?<=[\p{L})])\.\s+|(?<=\d)\.\s+(?=\p{Lu})")
_COMMA = re.compile(r",\s*")
_LEAD_WORD = re.compile(r"\p{L}+")
# Единица в метке стоит перед числом или «/» («, г/м2 80», «, мкм 30»); «, в рулоне» — предлог.
_AFTER_UNIT = re.compile(r"\s*(?:[/0-9]|$)")
# Значение после двоеточия — до конца фразы.
_PHRASE_END = re.compile(r"[,.;|]")
_NUMBER = re.compile(r"(?<![\p{L}\p{N}.,])[0-9]+(?:[.,][0-9]+)?(?![\p{N}])")
_LABEL_UNIT = re.compile(r",\s*([^\s,0-9][^,0-9]{0,11}?)\s+[0-9]")
_WORD = re.compile(r"[\p{L}\p{N}]+(?:[-/][\p{L}\p{N}]+)*")
_BARE_NUMBER = re.compile(r"[0-9]+(?:[.,][0-9]+)?")
# Латинские двойники с сохранением регистра: короткие значения («M», «XL») сравниваются по регистру.
_CASE_FOLD = str.maketrans("ABCEHKMOPTXYaceopxy", "АВСЕНКМОРТХУасеорху")


_COUNT_WORDS = ("количеств", "числ", "кол-в")
# Одна величина у разных написаний имени: «Вес» — это «Масса» из правил модерации.
_ALIAS_SCORE = 0.9


def _is_count(spec: CharacteristicSpec) -> bool:
    return any(w.startswith(_COUNT_WORDS) for w in fold(spec.name).split())


def _spec_unit(spec: CharacteristicSpec) -> str | None:
    """Единица позиции; нет — из имени («Толщина, мкм»); у счётной без единицы — штуки."""
    unit = normalize_unit(spec.unit) or spec.unit or split_name_unit(spec.name)[1]
    if unit is None and _is_count(spec):
        return "шт"
    return unit


def _new_number(segment: str, start: int, cm: re.Match[str]) -> bool:
    """«толщина 0,03 мм, 50 шт» — после запятой и пробела число, а во фразе до неё число уже есть:
    это новая фраза. «Плотность, 80 г/м2» — значение самой метки, «0,03» — одно число."""
    spaced = cm.end() > cm.start() + 1 and segment[cm.end() : cm.end() + 1].isdigit()
    return spaced and _NUMBER.search(segment, start, cm.start()) is not None


def _comma_parts(segment: str, offset: int) -> Iterator[tuple[int, str]]:
    """Делит по запятой, если после неё новая фраза, а не единица («, г 80» — единица)."""
    start = 0
    for cm in _COMMA.finditer(segment):
        nxt = _LEAD_WORD.match(segment, cm.end())
        unit = nxt and normalize_unit(nxt[0]) and _AFTER_UNIT.match(segment, nxt.end())
        if (nxt and not unit) or _new_number(segment, start, cm):
            if segment[start : cm.start()].strip():
                yield offset + start, segment[start : cm.start()]
            start = cm.end()
    if segment[start:].strip():
        yield offset + start, segment[start:]


def _segments(text: str) -> Iterator[tuple[int, str]]:
    """Фразы описания: строки, «;», конец предложения и запятая перед новой меткой."""
    for m in _SEGMENT.finditer(text):
        line, base, start = m[0], m.start(), 0
        for sm in _SENTENCE.finditer(line):
            yield from _comma_parts(line[start : sm.start()], base + start)
            start = sm.end()
        yield from _comma_parts(line[start:], base + start)


def _label_phrases(specs: list[CharacteristicSpec]) -> list[str]:
    """Имена характеристик и их крайние слова: по ним режем «Метка: значение» без разделителя."""
    phrases: set[str] = set()
    for spec in specs:
        for raw in (spec.name, *spec.synonyms):
            text = raw.strip()
            if len(text) >= 3:
                phrases.add(text)
            words = _WORD.findall(text)
            for word in (words[0], words[-1]) if words else ():
                if len(word) >= 4:
                    phrases.add(word)
    return sorted(phrases, key=len, reverse=True)


def _cut_known_labels(segment: str, phrases: list[str]) -> list[tuple[int, str]]:
    """Куски одной фразы перед каждой известной меткой с двоеточием. Смещения — внутри фразы."""
    if not segment.strip() or not phrases:
        return [(0, segment)] if segment else []
    pattern = re.compile(
        "(?P<label>" + "|".join(re.escape(phrase) for phrase in phrases) + r")\s*:",
        re.IGNORECASE,
    )
    found = list(pattern.finditer(segment))
    # «пачке:» внутри «Листов в пачке:» — не новая метка. Берём самую длинную, которая покрывает.
    cuts = [
        item
        for item in found
        if not any(
            other.start() <= item.start()
            and item.end() <= other.end()
            and len(other.group()) > len(item.group())
            for other in found
        )
    ]
    pieces: list[tuple[int, str]] = []
    previous = 0
    for item in cuts:
        if item.start() <= previous:
            continue
        pieces.append((previous, segment[previous : item.start()]))
        previous = item.start()
    pieces.append((previous, segment[previous:]))
    return [(start, part) for start, part in pieces if part.strip()]


_NUMBER_AFTER = re.compile(r"[\s:=–-]*\d")
_QTY_BEFORE = re.compile(r"\d(?:[.,]\d+)?\s*(?:\p{L}{1,4}\.?)?\s*\Z")


def _cut_bare_labels(segment: str, phrases: list[str]) -> list[tuple[int, str]]:
    """«ширина 120 см высота 75 см» без запятых: режем перед каждой меткой, если в куске до неё
    уже есть число. Фраза начинается с числа («120 см ширина 75 см высота») — метка стоит после
    своего числа, и режем после неё. Иначе последнее число фразы уходило к первой метке."""
    if len(_NUMBER.findall(segment)) < 2 or not phrases:
        return [(0, segment)]
    pattern = re.compile(
        r"(?<!\p{L})(?:" + "|".join(re.escape(p) for p in phrases) + r")(?!\p{L})", re.IGNORECASE
    )
    value_first = segment.lstrip()[:1].isdigit()
    cuts: list[int] = []
    previous = 0
    for label in pattern.finditer(segment):
        at = label.end() if value_first else label.start()
        # Метка вплотную к своему числу: «высота 75», а при числе впереди — «75 см высота».
        # «… 5 элементов питания типа АА» — «питания» здесь не метка, фразу не режем.
        beside = (
            _QTY_BEFORE.search(segment, 0, label.start())
            if value_first
            else _NUMBER_AFTER.match(segment, label.end())
        )
        if beside and at > previous and _NUMBER.search(segment, previous, label.start()):
            cuts.append(at)
            previous = at
    bounds = [0, *cuts, len(segment)]
    return [(a, segment[a:b]) for a, b in pairwise(bounds) if segment[a:b].strip()]


_LEADING_NUMBER = re.compile(r"\s*\d+(?:[.,]\d+)?\s*")


def _without_leading_qty(segment: str) -> str:
    """«120 см ширина» → «ширина»: метка после своего числа сверяется с именем характеристики."""
    lead = _LEADING_NUMBER.match(segment)
    if lead is None:
        return segment
    rest = segment[lead.end() :]
    word = _LEAD_WORD.match(rest)
    if word and normalize_unit(word[0]):
        rest = rest[word.end() :]
    return rest


def _pieces(text: str, specs: list[CharacteristicSpec]) -> Iterator[tuple[int, str]]:
    phrases = _label_phrases(specs)
    for offset, segment in _segments(text):
        for start, part in _cut_known_labels(segment, phrases):
            for inner, piece in _cut_bare_labels(part, phrases):
                yield offset + start + inner, piece


def _unique_head(label: str, specs: list[CharacteristicSpec]) -> CharacteristicSpec | None:
    """Метка из одного слова совпала с краем ровно одной характеристики."""
    words = _WORD.findall(label)
    if len(words) != 1 or len(words[0]) < 4:
        return None
    head = fold(words[0])
    hits: list[CharacteristicSpec] = []
    for spec in specs:
        parts = _WORD.findall(spec.name)
        if not parts:
            continue
        edges = {fold(parts[0]), fold(parts[-1]), fold(spec.name)}
        if head in edges:
            hits.append(spec)
    unique = {fold(spec.name): spec for spec in hits}
    return next(iter(unique.values())) if len(unique) == 1 else None


# Слова имени, которые не называют часть товара: «Материал изделия» — это и есть «Материал»,
# «Габаритная ширина» — «Ширина». «Материал подклада» — уже не «Материал».
QUALIFIERS = set(
    tokens(
        "изделия изделие товара продукции габаритная габаритный габаритные общая общий российский"
    )
)
# Слова имени, которые сами не говорят, о какой части товара речь: «Вид материала спинки» и
# «Вид материала сидения» различает только «спинка» и «сидение».
_GENERIC_NAME = set(tokens("вид тип материал наличие количество значение характеристика"))


def _names_whole(label: str, spec: CharacteristicSpec) -> bool:
    """Метка называет характеристику целиком (с точностью до слов вроде «изделия»): «Материал» —
    это «Материал изделия», но не «Материал подклада»; «Цвет фасада» — не «Цвет обивки»."""
    said = set(tokens(label))
    return any(set(tokens(name)) - said <= QUALIFIERS for name in (spec.name, *spec.synonyms))


def _context(text: str, start: int, end: int) -> set[str]:
    """Слова вокруг фрагмента в пределах его фразы: метка до него и слово после
    («металлическом каркасе» — каркас)."""
    left = re.split(r"[,;\n.|]", text[:start])[-1]
    right = re.split(r"[,;\n.|]", text[end:])[0]
    return set(tokens(f"{left} {right}"))


def _one_field_per_span(found: list[Extracted], name: str, description: str) -> list[Extracted]:
    """Один фрагмент текста — одному полю. «металлическом» в «Стул на металлическом каркасе» —
    материал каркаса, а не спинки, сиденья и крестовины; «Цвет фасада: белый» — «Цвет», а не все
    поля цвета. Поле выбирают слова вокруг фрагмента; ни одно не подошло, а фрагмент стоит после
    метки («Ручка противоскользящая: да») — метка не о них, фрагмент уходит в новые свойства."""
    texts = {"name": name, "description": description}
    groups: dict[tuple[str, int, int], list[Extracted]] = {}
    for e in found:
        groups.setdefault((e.source.field, e.source.start, e.source.end), []).append(e)
    drop: set[str] = set()
    for (field, start, end), items in groups.items():
        if len(items) < 2:
            continue
        text = texts[field]
        around = _context(text, start, end)

        def score(e: Extracted, around: set[str] = around) -> float:
            own = set(tokens(e.name)) - _GENERIC_NAME
            return len(own & around) - 0.1 * len(own - around)

        best = max(score(e) for e in items)
        if best > 0:
            drop |= {e.name for e in items if score(e) < best}
        elif ":" in re.split(r"[,;\n|]", text[:start])[-1]:
            drop |= {e.name for e in items}
    return [e for e in found if e.name not in drop]


def _overlap(spec_name: str, label: str) -> float:
    need = set(tokens(spec_name))
    return len(need & set(tokens(label))) / len(need) if need else 0.0


# Синонимы меток поставщика: «Вес бумаги» — «Масса бумаги…», «Вместимость» — «Объем», «Кол-во» —
# «Количество». Метка сверяется с именем как написана и с заменой синонима; берётся лучшее.
_LABEL_SYNONYMS = [
    (re.compile(r"(?<![\p{L}])(?:вес|плотность)(?=\P{L}|$)", re.IGNORECASE), "масса"),
    (re.compile(r"(?<![\p{L}])(?:вместимость|[её]мкость)(?=\P{L}|$)", re.IGNORECASE), "объем"),
    (re.compile(r"(?<![\p{L}])(?:кол-?во|кол\.|число)(?=\P{L}|$)", re.IGNORECASE), "количество"),
]


# Совпадение по синониму чуть ниже, чем по имени: при равенстве метку забирает характеристика с
# этим именем, а не та, у которой оно синоним («Цвет» — не «Цвет корпуса» с синонимом «Цвет»).
SYNONYM_WEIGHT = 0.99


def _label_score(spec: CharacteristicSpec, label: str) -> float:
    """Лучшее совпадение метки с именем характеристики или её синонимом («Предназначение» у
    «Назначения» после склейки дублей — `app.portal.standards`)."""
    by_synonym = max((_name_score(name, label) for name in spec.synonyms), default=0.0)
    return max(_name_score(spec.name, label), by_synonym * SYNONYM_WEIGHT)


def _name_score(spec_name: str, label: str) -> float:
    score = _label_overlap(spec_name, label)
    for pattern, canon in _LABEL_SYNONYMS:
        if pattern.search(label):
            score = max(score, _label_overlap(spec_name, pattern.sub(canon, label)))
    return score


def _label_overlap(spec_name: str, label: str) -> float:
    """Совпадение метки с именем характеристики: половина слов имени и не одно случайное слово.

    Одного общего слова мало («Масса бумаги…» — не «Класс бумаги»), если только метка с него не
    начинается («Толщина, мкм 104» — «Толщина»).
    """
    need = set(tokens(spec_name))
    words = tokens(label)
    got = need & set(words)
    if not need or len(got) / len(need) < MIN_LABEL_OVERLAP:
        return 0.0
    # Одно общее слово, а уточнения у метки и у имени свои: «Ручка противоскользящая» — не
    # «Обрезиненная ручка», «Материал бойка» — не «Материал корпуса». Это другое свойство.
    if len(got) == 1 and need - got and set(words) - got:
        return 0.0
    if len(got) >= 2 or (words and words[0] in got):
        return len(got) / len(need)
    return 0.0


def _unit_hint(segment: str, spec: CharacteristicSpec) -> str | None:
    """Единица из метки («Толщина, мкм 104») — только если она той же величины, что у позиции."""
    m = _LABEL_UNIT.search(segment)
    unit = normalize_unit(m[1]) if m else None
    target = normalize_unit(spec.unit) or spec.unit
    if unit is None or target is None:
        return None
    return unit if convert(1.0, unit, target) is not None else None


def _labelled_list(field: Field, text: str, specs: list[CharacteristicSpec]) -> Iterator[Extracted]:
    """«Интенсивность цвета: Медимум» — значение после двоеточия как написано; подгонка позже."""
    for offset, segment in _pieces(text, specs):
        label, colon, rest = segment.partition(":")
        lead = len(rest) - len(rest.lstrip(" \t:-–—"))  # «Страна:: Россия», «Цвет: - белый»
        value = _PHRASE_END.split(rest[lead:], maxsplit=1)[0].strip()
        if not colon or not value:
            continue
        scored = [(s, _label_score(s, label)) for s in specs if s.kind == "qualitative"]
        scored = [(s, o) for s, o in scored if o > 0 and _names_whole(label, s)]
        if not scored:
            continue
        spec = (
            _unique_head(label, [s for s, _ in scored])
            or max(scored, key=lambda so: (so[1], len(so[0].name)))[0]
        )
        start = offset + len(label) + 1 + lead
        yield Extracted(
            name=spec.name,
            raw_value=value,
            raw_unit=None,
            source=Span(field=field, start=start, end=start + len(value), text=value),
            method="rule",
        )


_WORD_THEN_VALUE = re.compile(r"(?P<word>\p{L}{3,})[\s:–-]+(?P<value>[\p{L}\d][\p{L}\d.,/-]*)")


def _word_label(
    field: Field, text: str, specs: list[CharacteristicSpec], skip: int = 0
) -> Iterator[Extracted]:
    """«размер 52» без двоеточия: слово — имя характеристики (с точностью до «российский»,
    «изделия»), за ним — значение её перечня. Только имена из одного такого слова: «Российский
    размер» — да, «Материал подклада» — нет."""
    heads: dict[str, CharacteristicSpec] = {}
    for spec in specs:
        if spec.kind != "qualitative" or not spec.values or spec.group == "general":
            continue
        own = set(tokens(spec.name)) - QUALIFIERS
        if len(own) == 1:
            heads.setdefault(next(iter(own)), spec)
    if not heads:
        return
    for m in _WORD_THEN_VALUE.finditer(text, skip):
        value = m["value"].rstrip(".,")
        hit = heads.get(_stem_of(m["word"]))
        # «Тип рукоятки: дерево», «Материал основы: …» — метка из двух слов, а не «метка значение».
        bare = ":" not in text[m.end("word") : m.start("value")] and not text[
            m.end("value") :
        ].lstrip().startswith(":")
        if bare and (
            hit is None or fit_value(value, None, hit).status not in ("exact", "normalized")
        ):
            hit = _required_word(_stem_of(m["word"]), value, specs) or hit
        if hit is None:
            continue
        if hit.required and hit is not heads.get(_stem_of(m["word"])):
            # «материал глина» у тумбы: значения нет в перечне, но поле одно — берём как написано,
            # подгон под КТРУ покажет «нет в КТРУ» с допустимыми, а не потеряет слово.
            start = m.start("value")
            yield Extracted(
                name=hit.name,
                raw_value=value,
                raw_unit=None,
                source=Span(field=field, start=start, end=start + len(value), text=value),
                method="rule",
            )
            continue
        # Число при числовом перечне берём и вне перечня («размер 60» при 38–52): пусть его покажет
        # подгон под КТРУ как «нет в перечне», а не потеряет.
        numeric = value.replace(",", ".").replace(".", "", 1).isdigit() and all(
            any(ch.isdigit() for ch in v) for v in hit.values
        )
        if not numeric and fit_value(value, None, hit).status not in ("exact", "normalized"):
            continue
        start = m.start("value")
        yield Extracted(
            name=hit.name,
            raw_value=value,
            raw_unit=None,
            source=Span(field=field, start=start, end=start + len(value), text=value),
            method="rule",
        )


# «Тип», «Вид» сами по себе поля не называют: «тип рукоятки» — не «Тип нагревания».
_VAGUE_LABEL = set(tokens("вид тип наличие количество значение характеристика"))


def _required_word(
    stem: str, value: str, specs: list[CharacteristicSpec]
) -> CharacteristicSpec | None:
    """Обязательное поле с перечнем, единственное, в имени которого есть слово метки: «материал»
    → «Вид материала корпуса». Значение — слово, а не слово чужого имени («материал корпуса»)."""
    said = tokens(value)
    if len(said) != 1 or not value[:1].isalpha() or stem in _VAGUE_LABEL:
        return None
    names = {t for s in specs for t in tokens(s.name)}
    if said[0] in names:
        return None
    found = [
        s
        for s in specs
        if s.kind == "qualitative"
        and s.values
        and s.required
        and s.group != "general"
        and stem in tokens(s.name)
    ]
    return found[0] if len(found) == 1 else None


def _stem_of(word: str) -> str:
    stems = tokens(word)
    return stems[0] if stems else ""


_RANGE_BEFORE = re.compile(r"\d(?:[.,]\d+)?\s*(?:[-–—]|до)\s*\Z")


def _range_end(segment: str, start: int) -> bool:
    """Число — правый край диапазона «170–176», «от 3 до 5»."""
    return bool(_RANGE_BEFORE.search(segment[:start]))


def _labelled(field: Field, text: str, specs: list[CharacteristicSpec]) -> Iterator[Extracted]:
    for offset, segment in _pieces(text, specs):
        label = _without_leading_qty(segment)
        scored = [(s, _label_score(s, label)) for s in specs if s.kind == "quantitative"]
        scored = [(s, o) for s, o in scored if o > 0]
        if not scored:
            continue
        head, _, _ = label.partition(":")
        spec = (
            _unique_head(head if head else segment, [s for s, _ in scored])
            or max(scored, key=lambda so: (so[1], len(so[0].name)))[0]
        )
        picked = _value_number(segment, spec) or _foreign_number(segment, spec)
        if picked is None:
            continue
        number, unit = picked
        if negative_at(segment, number.start()) or _range_end(segment, number.start()):
            # «-1510 Вт» — не 1510 (замечание `negative_notes`); «Рост: 170–176» — не 176:
            # одно число диапазона в поле исказило бы текст, фраза остаётся в extra как есть.
            continue
        yield Extracted(
            name=spec.name,
            raw_value=number[0],
            raw_unit=unit,
            source=Span(
                field=field,
                start=offset + number.start(),
                end=offset + number.end(),
                text=number[0],
            ),
            method="rule",
        )


def _labelled_name(name: str, specs: list[CharacteristicSpec]) -> Iterator[Extracted]:
    """«…, белизна 146%» в наименовании: часть после запятой, которая начинается с полного имени
    характеристики, — метка и число. Первая часть — сам товар («Бумага … 80 г/м2»): слова товара
    меткой не считаются; метка — все слова имени, а не одно общее."""
    offset = 0
    for i, part in enumerate(name.split(",")):
        start, offset = offset, offset + len(part) + 1
        words = tokens(part)
        if i == 0 or not words:
            continue
        exact = [
            s
            for s in specs
            if s.kind == "quantitative"
            and (need := set(tokens(s.name)))
            and need <= set(words)
            and words[0] in need
        ]
        if not exact:
            continue
        spec = max(exact, key=lambda s: len(s.name))
        picked = _value_number(part, spec)
        if picked is None:
            continue
        number, unit = picked
        yield Extracted(
            name=spec.name,
            raw_value=number[0],
            raw_unit=unit,
            source=Span(
                field="name",
                start=start + number.start(),
                end=start + number.end(),
                text=number[0],
            ),
            method="rule",
        )


# Единица сразу после числа: «1700 мл», «146%», «80 г/м2», «2,2 кВт», «5 шт.», «1 кв. м».
_UNIT_AFTER = re.compile(r"\s*(%|[^\s,;:()0-9][^\s,;:()]*(?:\s[^\s,;:()0-9][^\s,;:()]*)?)")
# Однобуквенные «единицы», которые чаще предлог или союз: «10 в упаковке», «5 с ручкой».
_PREPOSITION_UNITS = {"в", "с", "к", "а", "о", "у", "и"}


def _unit_after(segment: str, number: re.Match[str]) -> tuple[str | None, bool]:
    """Единица после числа (канон) и признак «может быть предлогом»; нет единицы — (None, False)."""
    m = _UNIT_AFTER.match(segment, number.end())
    if m is None:
        return None, False
    words = m[1].split(" ")
    for text in (m[1], words[0]) if len(words) > 1 else (m[1],):
        unit = normalize_unit(text)
        if unit is not None:
            return unit, text in _PREPOSITION_UNITS
    return None, False


def _compatible(unit: str, spec: CharacteristicSpec) -> str | None:
    """Единица числа в каноне, если она той же величины, что у характеристики, иначе None.

    «80 г» у «Масса бумаги площадью 1м2» (г/м2) — та же величина: метка уже говорит «на м2».
    """
    target = _spec_unit(spec)
    if target is None:
        return None if unit in COUNT_UNITS else unit  # «50 шт» — не физическая величина
    if convert(1.0, unit, target) is not None:
        return unit
    if "/" in target and target.split("/", 1)[0] == unit:
        return target
    return None


def unit_in_quote(quote: str, value: str) -> str | None:
    """Единица, которая стоит в цитате сразу после числа значения («4 шт» → «шт»); нет — None."""
    wanted = value.strip().replace(",", ".")
    for number in _NUMBER.finditer(quote):
        if number[0].replace(",", ".") == wanted:
            unit, maybe_word = _unit_after(quote, number)
            return None if maybe_word else unit
    return None


def foreign_unit_in_quote(quote: str, value: str, spec: CharacteristicSpec) -> str | None:
    """Единица, которая стоит в цитате после числа значения и не подходит характеристике:
    модель взяла «100» из «100 шт в упаковке» для «Количество пар» — это штуки, а не пары."""
    if spec.kind != "quantitative" or _spec_unit(spec) is None:
        return None
    wanted = value.strip().replace(",", ".")
    for number in _NUMBER.finditer(quote):
        if number[0].replace(",", ".") != wanted:
            continue
        unit, maybe_word = _unit_after(quote, number)
        if unit is not None and not maybe_word and _compatible(unit, spec) is None:
            return unit
    return None


def _foreign_number(segment: str, spec: CharacteristicSpec) -> tuple[re.Match[str], str] | None:
    """Единственное число фразы с меткой и с единицей другой известной величины («мощность 2200
    кг»):
    значение доходит до карточки конфликтом «единица не подходит», а не пропадает молча. Счётная
    единица («толщина 50 шт») и единицы вне таблицы величин («3 года» при «мес», «50 Гц» при «кГц»)
    — молчание: пересчитать их нечем, а «не подходит» было бы ложной блокировкой."""
    numbers = list(_NUMBER.finditer(segment))
    if len(numbers) != 1:
        return None
    unit, maybe_word = _unit_after(segment, numbers[0])
    if unit is None or maybe_word or unit in COUNT_UNITS:
        return None
    target = _spec_unit(spec)
    if target is None or target in COUNT_UNITS:
        return None
    mine, theirs = quantity(unit), quantity(target)
    if mine is None or theirs is None or mine == theirs:
        return None
    return numbers[0], unit


def _value_number(
    segment: str, spec: CharacteristicSpec
) -> tuple[re.Match[str], str | None] | None:
    """Последнее число фразы, чья единица подходит характеристике или которой нет вовсе.

    Числа в метке («1 м2») стоят раньше значения; число с чужой единицей («50 шт» у толщины,
    «220 В» у мощности) — значение другой характеристики, его не берём.
    """
    for number in reversed(list(_NUMBER.finditer(segment))):
        unit, maybe_word = _unit_after(segment, number)
        if unit is None:
            return number, _unit_hint(segment, spec)
        fitted = _compatible(unit, spec)
        if fitted is not None:
            return number, fitted
        if maybe_word:
            return number, _unit_hint(segment, spec)  # «10 в упаковке» — предлог, а не вольты
    return None


# Граница или множитель перед числом: «до 80 г/м2», «2x80 г/м2», «А480 г/м2» — не значение.
_NOT_PLAIN_BEFORE = re.compile(r"(?:(?<!\p{L})(?:до|от|по|на)\s+|\p{L}|[*×xх]\s*)\Z", re.IGNORECASE)


def _per_unit(name: str, v: TitleValue) -> str | None:
    """Единица через «/» у одиночного числа («80 г/м2») — в каноне ОКЕИ, иначе None."""
    tail = name[v.at + len(v.num) : v.end].strip().rstrip(".")
    if "/" not in tail or not SINGLE_NUM.match(v.num) or _NOT_PLAIN_BEFORE.search(name[: v.at]):
        return None
    return normalize_unit(tail)


def _from_compound(name: str, v: TitleValue, specs: list[CharacteristicSpec]) -> Extracted | None:
    """«80 г/м2» — в единственную количественную характеристику с той же единицей."""
    unit = _per_unit(name, v)
    if unit is None:
        return None
    fits = [
        s
        for s in specs
        if s.kind == "quantitative" and (normalize_unit(s.unit or "") or s.unit) == unit
    ]
    if len(fits) != 1:
        return None
    text = name[v.at : v.end].rstrip()
    return Extracted(
        name=fits[0].name,
        raw_value=re.sub(r"\s", "", v.num),
        raw_unit=unit,
        source=Span(field="name", start=v.at, end=v.at + len(text), text=text),
        method="rule",
    )


def _from_name(name: str, specs: list[CharacteristicSpec]) -> Iterator[Extracted]:
    for v in find_title_values(name):
        if number_in_range(name, v.at):
            continue
        if v.compound:
            if (e := _from_compound(name, v, specs)) is not None:
                yield e
            continue
        target = target_char(v.unit, name, v.at) or _EXTRA_TARGETS.get(v.unit)
        if target is None:
            continue
        t_name, t_unit = target
        unit = normalize_unit(t_unit) or t_unit
        scored: list[tuple[float, CharacteristicSpec]] = []
        for s in specs:
            spec_unit = _spec_unit(s)
            if (
                s.kind != "quantitative"
                or spec_unit is None
                or convert(1.0, unit, spec_unit) is None
                or (t_name == "Объём памяти" and _is_cache(s))
            ):
                continue
            # «листов» считает листы, а «шт» — что угодно: «10шт» годится и для
            # «Количество в рулоне».
            # «Количество отделений» считает отделения: «10 штук в упаковке» не его; голое
            # «200шт.» — годится и для «Количество салфеток».
            specific = _is_count(s) and not _generic_count(s) and _PACKED.search(v.text) is not None
            pair = _meaningful(t_name, s.name) if specific else (t_name, s.name)
            score = 1.0 if fold(s.name) == fold(t_name) else _overlap(*pair)
            if score < MIN_LABEL_OVERLAP and _alias(t_name, s.name) and not _load(name, v.at):
                score = _ALIAS_SCORE
            if score >= MIN_LABEL_OVERLAP:
                scored.append((score, s))
        best = max((sc for sc, _ in scored), default=0.0)
        fits = [s for sc, s in scored if sc == best]
        if len(fits) > 1:
            # «250 мл» при «Объем, мл» и «Объем освежаемого помещения, м3»: при равных метках —
            # та, у которой единица как написано. Одинаковые единицы — по-прежнему неоднозначно.
            same = [s for s in fits if _spec_unit(s) == unit]
            fits = same if len(same) == 1 else fits
        if len(fits) != 1:
            continue  # ни одной или несколько равных по совпадению — решит LLM или человек
        num_end = v.at + len(v.num)
        yield Extracted(
            name=fits[0].name,
            raw_value=re.sub(r"\s", "", v.num),
            raw_unit=unit,
            source=Span(field="name", start=v.at, end=num_end, text=name[v.at : num_end]),
            method="rule",
        )


# Единицы, которых нет в правилах модерации (`moderation.UNIT_CHAR`), но цель однозначна.
_EXTRA_TARGETS = {"ТБ": ("Объём памяти", "ТБ"), "Тб": ("Объём памяти", "ТБ")}
_PREPOSITIONS = {"в", "на", "по"}
_PACKED = re.compile(r"\sв\s+(?:упак|уп\b|пачк|короб|набор|комплект)", re.IGNORECASE)


_LOAD = re.compile(r"(?:на|за)?гру[зж]\p{L}*", re.IGNORECASE)


def _load(name: str, at: int) -> bool:
    """«нагрузка на полку 100 кг», «загрузка 6 кг» — не вес товара."""
    return any(_LOAD.fullmatch(w) for w in _WORD.findall(name[:at])[-LABEL_WORDS:])


_CACHE = ("буфер", "кэш", "кеш", "cache")


def _is_cache(spec: CharacteristicSpec) -> bool:
    """«Объём буферной памяти» диска — не его ёмкость: «8TB» туда не идёт."""
    return any(w.startswith(_CACHE) for w in fold(spec.name).split())


def _generic_count(spec: CharacteristicSpec) -> bool:
    """«Количество в упаковке» считает что угодно, «Количество слоев» — только слои."""
    words = fold(spec.name).split()
    at = next((i for i, w in enumerate(words) if w.startswith(_COUNT_WORDS)), None)
    return at is not None and at + 1 < len(words) and words[at + 1] in _PREPOSITIONS


def _meaningful(target: str, spec_name: str) -> tuple[str, str]:
    """Без слова «Количество»: «Количество листов» и «Количество слоев» — разные характеристики."""
    rest = " ".join(w for w in target.split() if not fold(w).startswith(_COUNT_WORDS))
    return (rest, spec_name) if rest else (target, spec_name)


def _alias(target: str, spec_name: str) -> bool:
    stems = NAME_ALIASES.get(target, [])
    return any(w.startswith(st) for w in _WORD.findall(fold(spec_name)) for st in stems)


# Окончания прилагательных: «белая», «белые» — то же значение, что «Белый».
_ADJ_END = re.compile(r"(?:ого|его|ых|их|ый|ий|ой|ая|яя|ое|ее|ые|ие|ую|юю)\Z")
MIN_ADJ_BASE = 3


def _adj_base(word: str) -> str | None:
    f = fold(word)
    base = _ADJ_END.sub("", f)
    return base if base != f and len(base) >= MIN_ADJ_BASE else None


# Приставка числа не меняет вид — только у названных корней: «однорычажный» — рычажный,
# «двухвентильный» — вентильный, «двухстоечный» — стоечный (так различаются позиции КТРУ смесителей
# и стеллажей). У прочих меняет: «одноцветная» печать — не «Цветная», «одновременная» — не
# «Временная».
_COUNT_PREFIX = re.compile(r"^(?:одно|двух|трех|четырех)(?=(?:рычажн|вентильн|стоечн)[а-я]*$)")


# Пары «слово текста — значение перечня» повторяются: одно «Металл» стоит в перечнях семи полей,
# одни перечни — в каждом запросе категории (275 тыс. вызовов на 40 случаев).
@lru_cache(maxsize=1 << 18)
def _same_word(word: str, value: str, counted: bool = False) -> bool:
    if len(value) <= 2:  # «M», «XL», «A4»-подобные коды: только точное слово с учётом регистра
        return word.translate(_CASE_FOLD) == value.translate(_CASE_FOLD)
    if fold(word) == fold(value):
        return True
    if (bare := _COUNT_PREFIX.sub("", fold(word))) != fold(word):
        return bare == fold(value) or (_adj_base(bare) or bare) == _adj_base(value)
    if any(ch.isdigit() for ch in value):
        return False  # код с цифрами — только целиком: «CR123A» не «CR123»
    if (base := _adj_base(value)) is not None and _adj_base(word) == base:
        return True
    if counted and _adj_base(value) is not None:
        return False  # «500 листов» — число листов, а не «Листовая»; «4 рулона» — «Рулон»
    stem = tokens(value)
    return len(stem) == 1 and len(stem[0]) >= 4 and fold(word).startswith(stem[0])


def _phrase_spans(words: list[re.Match[str]], value: str) -> Iterator[tuple[int, int]]:
    """Места, где значение из нескольких слов стоит в тексте подряд («Хризантема кустовая»)."""
    parts = _WORD.findall(value)
    n = len(parts)
    for i in range(len(words) - n + 1):
        if all(_same_word(words[i + j][0], parts[j]) for j in range(n)):
            yield words[i].start(), words[i + n - 1].end()


_NEGATION = {"без", "не"}


def _negated(words: list[re.Match[str]], i: int) -> bool:
    return i > 0 and fold(words[i - 1][0]) in _NEGATION


_PHRASE_EDGE = re.compile(r"[,;()\n|]")


def _phrase_at(text: str, start: int, end: int) -> str:
    """Фраза вокруг слова: от ближайшей запятой, «;» или скобки слева до такой же справа."""
    left = max((m.end() for m in _PHRASE_EDGE.finditer(text, 0, start)), default=0)
    right = _PHRASE_EDGE.search(text, end)
    return text[left : right.start() if right else len(text)]


def _near_label(
    hits: dict[str, tuple[int, int]], text: str, spec: CharacteristicSpec
) -> dict[str, tuple[int, int]]:
    """Из нескольких значений — то, в чьей фразе стоит уточнение имени: «корпус оранжевый» для
    «Цвет корпуса», а «синяя» (чернила) — нет. Нет уточнения или оно у нескольких — как было."""
    head = set(tokens(_WORD.findall(text)[0])) if _WORD.findall(text) else set()
    label = set(tokens(spec.name)[1:]) - head  # «Вид мяча»: «мяч» — сам товар, не уточнение
    if not label:
        return hits
    near = {v: span for v, span in hits.items() if set(tokens(_phrase_at(text, *span))) & label}
    return near if len(near) == 1 else hits


def _general_value(
    text: str,
    words: list[re.Match[str]],
    spec: CharacteristicSpec,
    specs: list[CharacteristicSpec],
    own: set[str] = frozenset(),  # type: ignore[assignment]
) -> Extracted | None:
    """«Общие сведения» по перечню частых заводов и стран категории — только в наименовании и
    только целым словом или фразой: перечень длинный и пёстрый, а правила основ и прилагательных
    подбирались под значения характеристик («цветов» — не завод «Цветы», «российский» — не
    «Россия»). Не метка («Формат: А4»), не имя и не значение другой характеристики («ЭВА» —
    материал). Значение — как написал поставщик: частое написание на портале бывает небрежным."""
    names = {fold(w) for s in specs for w in _WORD.findall(s.name)}
    hits: dict[str, tuple[int, int]] = {}
    for value in spec.values:
        parts = [fold(x) for x in _WORD.findall(value)]
        if not parts or (len(parts) == 1 and (parts[0] in names or parts[0] in own)):
            continue  # слово метки или названия категории («Мешки» у «Мешков полимерных»)
        for i in range(len(words) - len(parts) + 1):
            if all(fold(words[i + j][0]) == part for j, part in enumerate(parts)):
                start, end = words[i].start(), words[i + len(parts) - 1].end()
                if not text[end:].lstrip().startswith(":"):
                    hits.setdefault(value, (start, end))
                break
    # Короткое значение внутри найденной фразы — та же находка, а не второе значение.
    hits = {
        v: (a, b)
        for v, (a, b) in hits.items()
        if not any(a2 <= a and b <= b2 and (a2, b2) != (a, b) for a2, b2 in hits.values())
    }
    if len(set(hits.values())) != 1:
        return None
    start, end = next(iter(hits.values()))
    start, end = _with_marks(text, start, end)
    found = text[start:end]
    if _CYRILLIC.search(found) and found == found.lower():
        return None  # «марка», «щепа», «комфорт»: в перечнях заводов бывают нарицательные слова
    others = (
        v for s in specs if s.group != "general" and s.kind == "qualitative" for v in s.values
    )
    if any(fold(v) == fold(found) for v in others):
        return None  # то же слово — значение характеристики товара: скорее материал, чем завод
    return Extracted(
        name=spec.name,
        raw_value=found,
        raw_unit=None,
        source=Span(field="name", start=start, end=end, text=found),
        method="rule",
    )


_CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)


def _with_marks(text: str, start: int, end: int) -> tuple[int, int]:
    """Знак номера перед значением и закрывающая кавычка после него — часть названия
    («№1 School», «ФГБУ "ВГНКИ"»): совпадение идёт по словам, а знаки словами не считаются."""
    if start > 0 and text[start - 1] == "№":
        start -= 1
    found = text[start:end]
    if (found.count('"') % 2 and text[end : end + 1] == '"') or (
        found.count("«") > found.count("»") and text[end : end + 1] == "»"
    ):
        end += 1
    return start, end


_LABEL_END = re.compile(r"[ 	]*[:|]")


def _is_label(text: str, end: int) -> bool:
    """Слово стоит перед «:» или «|» таблицы — это подпись поля, а не его значение."""
    return bool(_LABEL_END.match(text, end))


# Прилагательные материала, которые есть и в перечнях цвета: без метки «цвет» это материал.
_MATERIAL_ADJECTIVES = ("металлическ", "деревянн", "пластиков", "стеклянн")


def _list_values(
    field: Field,
    text: str,
    specs: list[CharacteristicSpec],
    own: set[str] = frozenset(),  # type: ignore[assignment]
) -> Iterator[Extracted]:
    words = list(_WORD.finditer(text))
    index_at = {w.start(): i for i, w in enumerate(words)}
    for spec in specs:
        if spec.kind != "qualitative" or not spec.values:
            continue
        if spec.group == "general":
            if field == "name" and (general := _general_value(text, words, spec, specs, own)):
                yield general
            continue
        hits: dict[str, tuple[int, int]] = {}
        own = set(tokens(spec.name))
        # Слово самой метки — не значение: «Цвет чернил Синий», а не «Черный»; «Стерильность …»,
        # а не «Стерильные». Сравнение словом целиком: «стерильные» в тексте — значение.
        label_words = {fold(x) for x in _WORD.findall(spec.name)}
        colour = "цвет" in fold(spec.name)
        for value in spec.values:
            if _BARE_NUMBER.fullmatch(value.strip()):
                continue  # «20» в перечне: в тексте это может быть что угодно (размер колеса, год)
            if colour and fold(value).startswith(_MATERIAL_ADJECTIVES):
                # «Шкаф металлический» — материал, а не «Требования к цветовому решению:
                # Металлический»; цвет так берётся только с меткой («цвет металлический»).
                continue
            if len(vt := set(tokens(value))) >= MIN_NAME_REPEAT and vt <= own:
                continue  # «Назначение стола письменного: Стол письменный» — повтор имени
            if len(_WORD.findall(value)) > 1:
                for span in _phrase_spans(words, value):
                    if not _negated(words, index_at[span[0]]) and not _is_label(text, span[1]):
                        hits.setdefault(value, span)
                continue
            for i, w in enumerate(words):
                prev = words[i - 1][0] if i > 0 else ""
                if len(value) <= 2 and prev[:1].isdigit():
                    continue  # «5 м» — метры, а не размер M
                if fold(w[0]) in label_words:
                    continue
                if _is_label(text, w.end()):
                    continue  # «Длина: 210 мм» — метка другого поля, а не «Тип губок: Длинные»
                counted = bool(_BARE_NUMBER.fullmatch(prev))
                if _same_word(w[0], value, counted) and not _negated(words, i):
                    hits.setdefault(value, (w.start(), w.end()))
        # Короткое значение внутри найденной фразы («Хризантема» в «Хризантема кустовая») — та же
        # находка, а не второе значение.
        hits = {
            v: (a, b)
            for v, (a, b) in hits.items()
            if not any(a2 <= a and b <= b2 and (a2, b2) != (a, b) for a2, b2 in hits.values())
        }
        if len(hits) > 1 and len(set(hits.values())) == 1:
            # Варианты написания одного значения («Белый», «Белая») на одном слове — одна находка:
            # берём совпавший буквально, иначе первый в перечне.
            span = next(iter(hits.values()))
            exact = [v for v in hits if fold(v) == fold(text[span[0] : span[1]])]
            hits = {(exact or list(hits))[0]: span}
        if len(hits) > 1:
            hits = _near_label(hits, text, spec)
        if len(hits) != 1:
            continue  # в тексте два разных значения перечня — неоднозначно
        value, (start, end) = next(iter(hits.items()))
        yield Extracted(
            name=spec.name,
            raw_value=value,  # значение перечня; словоформа из текста — в source
            raw_unit=None,
            source=Span(field=field, start=start, end=end, text=text[start:end]),
            method="rule",
        )


_N = r"[0-9]+(?:[.,][0-9]+)?"
_LEN_UNIT = r"(?:мм|см|м)"
_X = r"\s*[xхХX×*]\s*"
_FREE_LEFT = r"(?<![\p{L}\p{N}.,])"
_FREE_RIGHT = r"\.?(?![\p{L}\p{N}])"
# Единица у числа размера — целым словом: «мм» не делится на «м» и «м».
_DIM_UNIT = rf"(?:\s*({_LEN_UNIT})(?!\p{{L}})\.?)?"
# «25х150х6000мм», «300х300х8 мм», «70х110 см», «5,0 м х 8 см»; слева от числа — не буква и
# не цифра. Единица обязательна хотя бы у последнего числа.
_DIMS = re.compile(
    rf"{_FREE_LEFT}({_N}){_DIM_UNIT}{_X}({_N}){_DIM_UNIT}(?:{_X}({_N}){_DIM_UNIT})?(?![\p{{L}}\p{{N}}])"
)
_ONE_LEN = re.compile(rf"{_FREE_LEFT}({_N})\s*({_LEN_UNIT}){_FREE_RIGHT}")
_ROLES = {
    "толщин": "t", "ширин": "w", "длин": "l", "высот": "h", "глубин": "d", "диаметр": "dia",
}  # fmt: skip
# Роли, которые раскладываются по величине без знания порядка: толщина < ширина < длина,
# диаметр < длина (саморез «4,2х16», игла, катетер). Высоту и глубину (мебель) не угадать.
_BY_SIZE: dict[int, list[tuple[str, ...]]] = {
    3: [("t", "w", "l")],
    2: [("w", "l"), ("t", "l"), ("t", "w"), ("dia", "l")],
}
_SIZE_FIELD = ("размер", "габарит")
LABEL_WORDS = 3
PRODUCT_WORDS = 2
# «Стол письменный» в «Назначение стола письменного» — повтор имени; одно слово («Сливочное» в
# «Вид сливочного масла») — бывает и значением.
MIN_NAME_REPEAT = 2
_PACK_OF = re.compile(
    rf"{_FREE_LEFT}({_N})\s*(мл|л|кг|г)\.?\s*[*xх×]\s*([0-9]+)\s*(?:шт|штук)(?!\p{{L}})"
)
_LITRES = re.compile(rf"{_FREE_LEFT}({_N})\s*литр(?:а|ов)?(?!\p{{L}})", re.IGNORECASE)
MIN_ROOT = 3
# «10 000 пользователей» — тысячи через пробел, не «000 пользователей».
_COUNTED = re.compile(r"(?<![\p{L}\p{N}.,])([0-9]+)\s*-?\s*(\p{L}{3,})")
_THOUSANDS_TAIL = re.compile(r"(?<![0-9])[0-9]{1,3}\s\Z")


def _role(spec: CharacteristicSpec) -> str | None:
    """Роль размера по первому слову-роли в имени: «Номинальная ширина пиломатериала» — ширина."""
    for w in fold(spec.name).split():
        for stem, role in _ROLES.items():
            if w.startswith(stem):
                return role
    return None


def _specs_in(specs: list[CharacteristicSpec], unit: str) -> list[CharacteristicSpec]:
    """Количественные характеристики, в единицу которых переводится `unit`."""
    out = []
    for s in specs:
        target = _spec_unit(s) if s.kind == "quantitative" else None
        if target is not None and convert(1.0, unit, target) is not None:
            out.append(s)
    return out


def _number(name: str, start: int, text: str, unit: str, spec: CharacteristicSpec) -> Extracted:
    return Extracted(
        name=spec.name,
        raw_value=text,
        raw_unit=unit,
        source=Span(field="name", start=start, end=start + len(text), text=text),
        method="rule",
    )


def _from_dims(name: str, specs: list[CharacteristicSpec]) -> Iterator[Extracted]:
    """Размеры по величине — только там, где порядок не важен (доска, плитка, мешок).

    У мебели «Ш×Г×В» и «В×Ш×Г» встречаются одинаково часто: не угадываем, решит LLM или человек.
    """
    sizes = [s for s in specs if _size_field(s)]
    if len(sizes) == 1:
        for _nums, (a, b) in _dims(name):
            text = name[a:b].rstrip(".")
            yield Extracted(
                name=sizes[0].name,
                raw_value=text,
                raw_unit=None,
                source=Span(field="name", start=a, end=a + len(text), text=text),
                method="rule",
            )
            break
    by_role: dict[str, list[CharacteristicSpec]] = {}
    for s in _specs_in(specs, "мм"):
        if _spec_unit(s) == "мкм":
            continue  # толщина плёнки в микронах — не размер изделия
        # Длина без роли (сечение, «Размер А») — размеры могут быть её: размеры не угадываем.
        by_role.setdefault(_role(s) or "?", []).append(s)
    if "?" in by_role or any(len(ss) > 1 for ss in by_role.values()):
        return
    roles = set(by_role)
    for nums, _span in _dims(name):
        order = next((o for o in _BY_SIZE.get(len(nums), []) if set(o) == roles), None)
        if order is None:
            continue
        by_size = sorted(nums, key=lambda d: d[3])
        for role, (start, text, unit, _) in zip(order, by_size, strict=True):
            yield _number(name, start, text, unit, by_role[role][0])


def _dims(name: str) -> Iterator[tuple[list[tuple[int, str, str, float]], tuple[int, int]]]:
    """Размеры: (начало, число, единица, миллиметры) и место всей записи; число без единицы берёт
    следующую справа."""
    for m in _DIMS.finditer(name):
        groups = [(g, g + 1) for g in (1, 3, 5) if m[g] is not None]
        if m[groups[-1][1]] is None:
            continue  # «1170х282х600» без единицы — не знаем, мм это или см
        out: list[tuple[int, str, str, float]] = []
        unit = m[groups[-1][1]]
        for g, u in reversed(groups):
            unit = m[u] or unit
            mm = convert(float(m[g].replace(",", ".")), unit, "мм")
            out.append((m.start(g), m[g], unit, mm if mm is not None else 0.0))
        yield out[::-1], m.span()


def _from_one_length(name: str, specs: list[CharacteristicSpec]) -> Iterator[Extracted]:
    """«5м» — в единственную характеристику длины или в ту, чья метка стоит перед числом."""
    taken = [(m.start(), m.end()) for m in _DIMS.finditer(name)]
    lengths = _specs_in(specs, "мм")
    singles = [m for m in _ONE_LEN.finditer(name) if not any(a <= m.start() < b for a, b in taken)]
    for m in singles:
        # Метка — до трёх слов прямо перед числом.
        # Числа и единицы — не метка: «шириной 10 см длиной 5 м» → «длиной» для «5 м».
        before = [
            w
            for w in _WORD.findall(name[: m.start()].rsplit(",", 1)[-1])
            if not any(ch.isdigit() for ch in w) and normalize_unit(w) is None
        ][-LABEL_WORDS:]
        roles_at = [i for i, w in enumerate(before) if fold(w).startswith(tuple(_ROLES))]
        if roles_at:
            before = before[roles_at[-1] :]  # метка начинается с последнего слова-роли
        # Слова самого товара («Полотенца бумажные … 160м») меткой не считаются, слово-роль или
        # уточнение («линия письма 0,5 мм», «рукоятка 120 см») — считаются.
        product = {fold(w) for w in _WORD.findall(name)[:PRODUCT_WORDS]}
        label = " ".join(w for w in before if fold(w) not in product)
        scored = [(_label_score(s, label), s) for s in lengths]
        best = max((sc for sc, _ in scored), default=0.0)
        fits = [s for sc, s in scored if sc == best and sc > 0]
        # Одно число длины в наименовании — в единственную длину позиции («Насадка 27 см») или,
        # если есть и диаметр, в единственную характеристику с ролью «длина» («Нить … 150 см»).
        named = [s for s in lengths if _role(s) == "l"]
        # Рядом только диаметр — длина однозначна; ширина или высота — нет («Салфетка 30 см»).
        others_dia = all(_role(s) == "dia" for s in lengths if s not in named)
        only = lengths if len(lengths) == 1 else named if len(named) == 1 and others_dia else []
        if not fits and only and len(singles) == 1 and not taken and _plain_length(only[0], before):
            fits = only
        if len(fits) == 1:
            yield _number(name, m.start(1), m[1], m[2], fits[0])


_DIAMETER_MARK = {"d", "ø", "дн", "dn", "ду", "диаметр", "диам"}


_SIZE_NAMES = {"размер", "размеры", "габариты", "габаритные размеры", "размер изделия"}


def _size_field(spec: CharacteristicSpec) -> bool:
    """«Размер», «Габаритные размеры» без единицы и перечня — всю запись «175х90 мм» целиком.
    «Размер шлица» (PH2) и «Размер» одежды из перечня — нет."""
    return fold(spec.name) in _SIZE_NAMES and _spec_unit(spec) is None and not spec.values


def _plain_length(spec: CharacteristicSpec, before: list[str]) -> bool:
    """Число без метки годится в единственную длину, если перед ним не знак диаметра («Ø 32 мм»,
    «DN 32») и характеристика — не «Габаритные размеры» (их берут целиком)."""
    if fold(spec.name).startswith("габарит"):
        return False
    marked = bool(before) and fold(before[-1]) in _DIAMETER_MARK
    return not marked or _role(spec) == "dia"


def _same_root(a: str, b: str) -> bool:
    """Основы с беглой гласной: «кнопк» и «кнопок», «полк» и «полок»."""
    common = len(os.path.commonprefix([a, b]))
    return common >= max(MIN_ROOT, min(len(a), len(b)) - 2)


def _counted_noun(spec: CharacteristicSpec) -> str:
    """Что считают: первое слово после «Количество»/«Число» («листов» в «… листов в рулоне»)."""
    words = [w for w in tokens(spec.name) if not w.startswith(_COUNT_WORDS)]
    return words[0] if words else ""


def _from_counted(name: str, specs: list[CharacteristicSpec]) -> Iterator[Extracted]:
    """«6 розеток», «3 кнопки», «50 штук» — в «Количество …» с тем же словом; «12 литров» —
    в объём."""
    counts = [s for s in _specs_in(specs, "шт") if _is_count(s)]
    for m in _COUNTED.finditer(name):
        if len(m[1]) == 3 and _THOUSANDS_TAIL.search(name[: m.start(1)]):
            continue  # «10 000 пользователей» — хвост тысяч, не «000 пользователей»
        noun = tokens(m[2])
        fits = [s for s in counts if noun and _same_root(noun[0], _counted_noun(s))]
        if len(fits) == 1:
            yield _number(name, m.start(1), m[1], "шт", fits[0])
    for m in _PACK_OF.finditer(name):
        # «0,5л*12штук»: объём или масса единицы и сколько их в упаковке.
        units = _specs_in(specs, m[2])
        if len(units) == 1:
            yield _number(name, m.start(1), m[1], m[2], units[0])
        if len(counts) == 1:
            yield _number(name, m.start(3), m[3], "шт", counts[0])
    volumes = _specs_in(specs, "л")
    for m in _LITRES.finditer(name):
        if len(volumes) == 1:
            yield _number(name, m.start(1), m[1], "л", volumes[0])


# «Наличие завязок», «Наличие спирта»: Да/Нет по самому предмету в тексте — «с завязками» (Да),
# «без спирта» (Нет). Служебные слова имени предмет не называют.
_YES_NO = {"да", "нет"}
_PRESENCE = ("налич", "присутств", "признак", "функц", "возможн")
_ABSENT_AFTER = {"нет", "отсутствует", "отсутствуют"}
MIN_SUBJECT = 4


def _is_adj(word: str) -> bool:
    return _ADJ_END.search(fold(word)) is not None


def _subject(spec: CharacteristicSpec) -> tuple[str, bool] | None:
    """О чём вопрос и прилагательное ли это. «Наличие завязок» — завязки; «Ручка автоматическая» —
    не ручка (это сам товар), а «автоматическая»: без слова наличия признак — последнее слово имени,
    и только прилагательное («Набор маркеров», «блок питания» — о другом)."""
    raw = [w for w in _WORD.findall(spec.name) if tokens(w)]
    if raw and tokens(raw[0])[0].startswith(_PRESENCE):
        # «Наличие функции удара» — удар, а не «функции»: служебное слово предмет не называет.
        rest = [w for w in raw[1:] if not tokens(w)[0].startswith(_PRESENCE)]
        word = rest[0] if rest else None
    elif len(raw) > 1 and _is_adj(raw[-1]):
        word = raw[-1]
    else:
        word = None
    if word is None or len(stem := tokens(word)[0]) < MIN_SUBJECT:
        return None
    return stem, _is_adj(word)


def _yes_no(field: Field, text: str, specs: list[CharacteristicSpec]) -> Iterator[Extracted]:
    words = list(_WORD.finditer(text))
    for spec in specs:
        if not spec.values or {fold(v) for v in spec.values} != _YES_NO:
            continue
        found = _subject(spec)
        if found is None:
            continue
        subject, adjective = found
        # Остальные слова имени тоже должны быть рядом: «Возможность регулировки угла наклона» —
        # не «регулировка частоты вращения», «работы с лентой» — не любая «работа».
        others = [
            t[0]
            for w in _WORD.findall(spec.name)
            if (t := tokens(w))
            and len(t[0]) >= MIN_SUBJECT
            and not t[0].startswith(_PRESENCE)
            and t[0] != subject
        ]
        seen: dict[str, re.Match[str]] = {}
        for i, w in enumerate(words):
            stem = tokens(w[0])
            if not stem or _is_adj(w[0]) != adjective:
                continue  # «Аккумуляторная дрель» — вид товара, а не «Наличие аккумуляторов»
            fused = stem[0].startswith("не") and _same_root(
                stem[0][2:], subject
            )  # «неавтоматическая»
            if not fused and not _same_root(stem[0], subject):
                continue
            near = [t[0] for x in words[max(0, i - 4) : i + 5] if (t := tokens(x[0]))]
            if not all(any(_same_root(n, o) for n in near) for o in others):
                continue
            after = [fold(x[0]) for x in words[i + 1 : i + 3]]
            absent = (
                fused
                or _negated(words, i)
                or (i > 0 and fold(words[i - 1][0]) == "нет")
                or bool(_ABSENT_AFTER & set(after))
            )
            seen.setdefault("нет" if absent else "да", w)
        if len(seen) != 1:
            continue  # предмета нет или в тексте и «с», и «без» — неоднозначно
        answer, w = next(iter(seen.items()))
        value = next(v for v in spec.values if fold(v) == answer)
        yield Extracted(
            name=spec.name,
            raw_value=value,
            raw_unit=None,
            source=Span(field=field, start=w.start(), end=w.end(), text=w[0]),
            method="rule",
        )


# Слова, которые есть почти у каждой количественной характеристики: одно такое совпадение
# ещё не значит, что фраза про эту характеристику.
_GENERIC_STEMS = ("объем", "количеств", "размер", "тип", "вид")
# Разговорные имена и аббревиатуры, чья словарная расшифровка не делит стем с именем КТРУ
# («ОЗУ» — «запоминающее устройство», а в позиции — «оперативная память»).
_LABEL_EQUIV: dict[str, tuple[str, ...]] = {
    "озу": ("оперативн", "памят"),
    "ram": ("оперативн", "памят"),
    "оперативк": ("оперативн", "памят"),
    "ssd": ("накопител", "твердотельн"),
    "hdd": ("накопител", "жестк"),
}
_NUM_UNIT = re.compile(
    r"(?<![\p{L}\p{N}])(?P<num>[0-9]+(?:[.,][0-9]+)?)(?![\p{N}])"
    r"\s*(?P<unit>[A-Za-zА-Яа-яЁё%][A-Za-zА-Яа-яЁё0-9%./]{0,12})?"
)
_CLAUSE_SPLIT = re.compile(r"[;()]")
_BOUND_LABEL = re.compile(r"(?:^|\s)(?:до|от|по)\s*$", re.IGNORECASE)


@lru_cache(maxsize=1)
def _label_glossary() -> Glossary:
    return Glossary.load()


def _canon_unit(raw: str | None) -> str | None:
    if not raw:
        return None
    unit = normalize_unit(raw)
    if unit:
        return unit
    alias = UNIT_ALIAS.get(raw) or UNIT_ALIAS.get(raw.upper())
    if not alias:
        return None
    return normalize_unit(alias) or alias


def _token_close(left: str, right: str) -> bool:
    if left == right:
        return True
    if len(left) < 5 or len(right) < 5:
        return False
    distance = damerau_levenshtein(left, right)
    return distance <= 1 or (distance <= 2 and distance <= min(len(left), len(right)) // 4)


def _distinctive(stem: str) -> bool:
    return len(stem) >= 5 and not any(stem.startswith(prefix) for prefix in _GENERIC_STEMS)


def _enriched_tokens(label: str) -> list[str]:
    """Стемы метки плюс расшифровки аббревиатур и разговорные эквиваленты."""
    glossary = _label_glossary()
    found = list(tokens(glossary.expand(label)))
    for word in re.findall(r"[\p{L}\p{N}]+", fold(label)):
        for hit in glossary.lookup(word):
            found.extend(tokens(hit.full))
        found.extend(_LABEL_EQUIV.get(word, ()))
    return found


def _fuzzy_overlap(spec_name: str, got: list[str]) -> float:
    need = tokens(spec_name)
    if not need or not got:
        return 0.0
    matched = [stem for stem in need if any(_token_close(stem, word) for word in got)]
    if not matched:
        return 0.0
    ratio = len(matched) / len(need)
    if ratio >= MIN_LABEL_OVERLAP and (
        len(matched) >= 2 or any(_token_close(got[0], stem) for stem in matched)
    ):
        return ratio
    if any(_distinctive(stem) for stem in matched):
        return MIN_LABEL_OVERLAP
    return 0.0


def _label_match(spec: CharacteristicSpec, label: str) -> float:
    if not label.strip():
        return 0.0
    return max(_label_score(spec, label), _fuzzy_overlap(spec.name, _enriched_tokens(label)))


def _name_clauses(text: str) -> Iterator[tuple[int, str]]:
    """Фразы наименования: скобки и «;», затем запятая перед новой меткой."""
    pieces: list[tuple[int, str]] = []
    start = 0
    for match in _CLAUSE_SPLIT.finditer(text):
        chunk = text[start : match.start()]
        if chunk.strip():
            pieces.append((start, chunk))
        start = match.end()
    tail = text[start:]
    if tail.strip():
        pieces.append((start, tail))
    for offset, chunk in pieces:
        yield from _comma_parts(chunk, offset)


def _quantity_in_clause(clause: str) -> tuple[re.Match[str], str | None] | None:
    """Число с единицей в фразе. Без единицы — только если другого числа нет."""
    matches = list(_NUM_UNIT.finditer(clause))
    if not matches:
        return None
    with_unit = [m for m in matches if _canon_unit(m.group("unit"))]
    if with_unit:
        match = with_unit[-1]
        return match, _canon_unit(match.group("unit"))
    return matches[-1], None


def _unit_compatible(unit: str | None, spec: CharacteristicSpec) -> bool:
    if unit is None:
        return True
    target = normalize_unit(spec.unit) or spec.unit
    if target is None:
        return False
    return convert(1.0, unit, target) is not None


def _span_taken(found: dict[str, Extracted], field: Field, start: int, end: int) -> bool:
    return any(
        item.source.field == field and not (end <= item.source.start or start >= item.source.end)
        for item in found.values()
    )


def _embedded_quantities(
    field: Field, text: str, specs: list[CharacteristicSpec], found: dict[str, Extracted]
) -> Iterator[Extracted]:
    """Характеристика, вписанная в наименование: метка из позиции КТРУ и значение с единицей.

    Совпадение — по стемам, опечаткам и аббревиатурам. Несколько равных кандидатов или слабая
    метка ничего не забирают: спорный кусок остаётся в тексте.
    """
    for offset, clause in _name_clauses(text):
        found_value = _quantity_in_clause(clause)
        if found_value is None:
            continue
        match, unit = found_value
        if unit is None or number_in_range(text, offset + match.start()):
            continue
        label = (clause[: match.start()] + " " + clause[match.end() :]).strip(" \t,;:—–-()/")
        if _BOUND_LABEL.search(label):
            continue
        # В наименовании метка стоит вплотную перед числом («ОЗУ 16 ГБ»): слова товара («Полотенца
        # … в рулонах Veiro 160м»), бренд и знак диаметра («Шланг Ø 32 мм») меткой не считаются.
        before = _WORD.findall(clause[: match.start()])
        adjacent = before[-1] if field == "name" and before else ""
        start, end = offset + match.start(), offset + match.end()
        if _span_taken(found, field, start, end):
            continue
        number = match.group("num")
        scored: list[tuple[float, CharacteristicSpec]] = []
        for spec in specs:
            if spec.kind != "quantitative" or spec.name in found:
                continue
            if not _unit_compatible(unit, spec):
                continue
            if fit_value(number, unit, spec).status not in _OK:
                continue
            score = _label_match(spec, label)
            if field == "name" and _label_match(spec, adjacent) <= 0:
                continue
            if score <= 0:
                continue
            scored.append((score, spec))
        if not scored:
            continue
        best = max(score for score, _ in scored)
        fits = [spec for score, spec in scored if score == best]
        if len(fits) != 1:
            continue
        fragment = text[start:end]
        yield Extracted(
            name=fits[0].name,
            raw_value=number.replace(" ", ""),
            raw_unit=unit,
            source=Span(field=field, start=start, end=end, text=fragment),
            method="rule",
        )


def extract_rules(
    name: str,
    description: str,
    specs: list[CharacteristicSpec],
    titles: list[str] | None = None,
) -> list[Extracted]:
    """Значения характеристик из текста. `titles` — названия позиций-кандидатов: их слова не бывают
    значениями «Общих сведений» («Мешки» у «Мешков полимерных» — товар, а не завод)."""
    found: dict[str, Extracted] = {}
    list_found: dict[str, Extracted] = {}
    own = {fold(w) for t in titles or [] for w in _WORD.findall(t)}
    for e in [
        *_list_values("name", name, specs, own),
        *_list_values("description", description, specs, own),
    ]:
        list_found.setdefault(e.name, e)
    by_name = {s.name: s for s in specs}
    passes = [
        _labelled("description", description, specs),
        _labelled_list("description", description, specs),
        _from_name(name, specs),
        _from_dims(name, specs),
        _from_one_length(name, specs),
        _from_counted(name, specs),
        _labelled_name(name, specs),
        _yes_no("name", name, specs),
        _yes_no("description", description, specs),
    ]
    for items in passes:
        for e in items:
            found.setdefault(e.name, e)
    for spec_name, e in list(found.items()):
        # Значение после метки не из перечня, а в тексте есть значение перечня — верим перечню.
        alt = list_found.get(spec_name)
        if (
            alt is not None
            and not set(tokens(alt.source.text)) & set(tokens(spec_name))  # слово самой метки
            and fit_value(e.raw_value, e.raw_unit, by_name[spec_name]).status not in _OK
        ):
            found[spec_name] = alt
    for spec_name, e in list_found.items():
        found.setdefault(spec_name, e)
    # «размер 52» без двоеточия — после значений перечня: те точнее, это добор.
    for e in _word_label("description", description, specs):
        found.setdefault(e.name, e)
    # В наименовании — после первой запятой: «Тумбочка прикроватная, материал глина» (до неё товар).
    if "," in name:
        for e in _word_label("name", name, specs, skip=name.index(",") + 1):
            found.setdefault(e.name, e)
    fields: tuple[tuple[Field, str], ...] = (("name", name), ("description", description))
    for field, text in fields:
        for e in phrase_values(field, text, specs, found):
            found.setdefault(e.name, e)
        # Голое число — только в наименовании («А4 80 500»): в описании чисел без единицы много
        # (сроки, артикулы, «(50)» рядом со словами, которые читает модель).
        for e in bare_values(field, text, specs, found) if field == "name" else []:
            found.setdefault(e.name, e)
        for e in _embedded_quantities(field, text, specs, found):
            found.setdefault(e.name, e)
    texts = {"name": name, "description": description}
    kept = [e for e in found.values() if not _distorted(e, texts.get(e.source.field, ""))]
    kept = [e for e in kept if not _list_detail(e, description, by_name.get(e.name))]
    kept = [e for e in kept if not _identifier(e, texts.get(e.source.field, ""))]
    return _one_field_per_span(kept, name, description)


def _identifier(e: Extracted, text: str) -> bool:
    """Число, взятое само по себе (без подписи и единицы в цитате), — на деле номер, порядковое
    или часть обозначения (`identifier_like`). Проверка общая для всех правил: какое из них взяло
    число, неважно."""
    quote = e.source.text.strip()
    if not e.raw_value[:1].isdigit() or not _BARE_NUMBER.fullmatch(quote):
        return False
    start = e.source.start + e.source.text.index(quote)
    if text[start : start + len(quote)] != quote:
        return False
    return identifier_like(text, start, start + len(quote))


_LIST_COMMA = re.compile(r",\s")


def _names_field(spec: CharacteristicSpec, words: str) -> bool:
    """Слова рядом с числом называют эту характеристику: общее слово с её именем или синонимом."""
    need = {t for raw in (spec.name, *spec.synonyms) for t in tokens(raw)}
    variants = [words, *(p.sub(canon, words) for p, canon in _LABEL_SYNONYMS if p.search(words))]
    return any(need & set(tokens(v)) for v in variants)


def _list_detail(e: Extracted, description: str, spec: CharacteristicSpec | None) -> bool:
    """Число — подробность перечня под чужой подписью, а не значение этого поля.

    «Комплектация Отвертка, зарядное устройство, бит-насадки (5 шт»: пять — это насадок, а не
    «Количество предметов в комплекте». Число идёт в поле, только если текст сам связывает его с
    полем: оно стоит в первой части фразы (после её подписи), рядом с ним слова имени
    характеристики («2 аккумулятора» — «Количество аккумуляторов») или рядом нет других слов
    («…, 100 шт»). Иначе фраза остаётся целой и уходит в отдельную характеристику (`extra`)."""
    if spec is None or e.source.field != "description" or not e.raw_value[:1].isdigit():
        return False
    line = next(
        (m for m in _SEGMENT.finditer(description) if m.start() <= e.source.start < m.end()), None
    )
    if line is None:
        return False
    # Фраза — предложение внутри строки: перечень не тянется через точку.
    starts = [m.end() for m in _SENTENCE.finditer(description, line.start(), e.source.start)]
    stop = _SENTENCE.search(description, e.source.end, line.end())
    first, last = (starts[-1] if starts else line.start()), (stop.start() if stop else line.end())
    before = list(_LIST_COMMA.finditer(description, first, e.source.start))
    if not before:
        return False
    after = _LIST_COMMA.search(description, e.source.end, last)
    part_end = after.start() if after else last
    rest = (
        description[before[-1].end() : e.source.start] + " " + description[e.source.end : part_end]
    )
    words = [
        w
        for w in _WORD.findall(rest)
        if any(ch.isalpha() for ch in w) and normalize_unit(w) is None
    ]
    return bool(words) and not _names_field(spec, " ".join(words))


def _distorted(e: Extracted, text: str) -> bool:
    """Число без своего минуса («-1510 Вт» → 1510) или правый край диапазона («170–176» → 176):
    в поле оно исказило бы сказанное. Фраза остаётся в тексте и в extra, о минусе — замечание."""
    start = e.source.start
    if not text or not e.raw_value[:1].isdigit() or text[start : start + 1] != e.raw_value[:1]:
        return False
    return negative_at(text, start) or _range_end(text, start)
