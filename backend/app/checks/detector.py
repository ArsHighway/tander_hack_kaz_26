"""Детектор ошибок карточки: правила модерации портала плюс сверка со спецификацией позиции.

Каждое замечание говорит, что не так (`message`), почему — с перечнем или правилом (`why`, `rule`) и
чем исправить (`fix`), если исправление однозначно. Блокирующее замечание (`block`) вернёт карточку
с модерации; `warn` — исправление, которое модуль предлагает; `info` — справка.
"""

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

import regex as re

from app.catalog import CharacteristicSpec, Position
from app.catalog.model import GENERAL_NAMES
from app.checks.hygiene import extra_characteristic, hygiene_issues, not_in_position
from app.checks.moderation import MIN_CHARS, RULE_TEXT, Card, Char, Finding, auto_fix, check_card
from app.checks.profanity import profanity_issues
from app.extract.model import Extracted
from app.ingest.characteristics import split_name_unit, to_number
from app.ingest.units import normalize_unit
from app.schemas import CardIn, CharIn, Fix, Issue
from app.search.against_text import said_otherwise
from app.search.text import tokens
from app.values import (
    COUNT_UNITS,
    Fit,
    damerau_levenshtein,
    fit_value,
    fold,
    input_slip,
    nearest_allowed,
)

RULE_UNIT = "единица из справочника ОКЕИ и той же величины, что у характеристики позиции"
RULE_NUMBER = "количественная характеристика задаётся числом"
RULE_REQUIRED = "обязательные характеристики позиции заполнены"
RULE_NAME = "значение не противоречит наименованию товара"
RULE_DESCRIPTION = "значение не противоречит описанию товара"
RULE_SPEC_NAME = "название характеристики как в позиции справочника"
RULE_KTRU = "код КТРУ есть в справочнике"
RULE_CATEGORY = "код категории есть в справочнике"
RULE_KTRU_VALUE = "значение соответствует выбранной позиции КТРУ"
RULE_KTRU_REQUIRED = "обязательные характеристики выбранной позиции КТРУ заполнены"
RULE_KTRU_LINK = "позиция КТРУ привязана к категории"
RULE_PLAUSIBLE = "число в пределах, обычных для СТЕ категории"


def photo_issues(card: CardIn) -> list[Issue]:
    """Фото товара не обязательно: отсутствие снимка карточку не помечает."""
    return []


def _field(char_name: str | None) -> str:
    return f"characteristics.{char_name}" if char_name else "characteristics"


# «Общие сведения» (производитель, модель, страна) — поля карточки портала: правила модерации
# характеристик и их минимум их не считают. Позиция известна — по её группе: у части категорий
# «Страна происхождения» — своя характеристика стандарта, и она считается.
_GENERAL = {fold(n) for n in GENERAL_NAMES}


def _general_names(position: Position | None) -> set[str]:
    if position is None:
        return _GENERAL
    # Справочник без групп (demo.json): поля карточки — по имени, если у позиции нет своей
    # одноимённой характеристики.
    own = {fold(s.name) for s in position.characteristics if s.group != "general"}
    marked = {fold(s.name) for s in position.characteristics if s.group == "general"}
    return marked | (_GENERAL - own)


def _moderation_issues(card: CardIn, position: Position | None) -> list[Issue]:
    general = _general_names(position)
    mod_card = Card(
        name=card.name,
        chars=[
            Char(name=c.name, value=c.value, unit=c.unit)
            for c in card.characteristics
            if fold(c.name) not in general
        ],
    )
    findings = check_card(mod_card)
    if position is not None:
        # Минимум — не больше, чем характеристик у позиции: у категории только с полями уровня
        # (372 из 8 569) карточку «нужно не меньше 4» нечем исправить.
        own = sum(1 for s in position.characteristics if fold(s.name) not in general)
        if len(mod_card.chars) >= min(MIN_CHARS, own):
            findings = [f for f in findings if f.rule != "min-chars"]
    # Значение с единицей в наименовании: исправление — наименование после переноса
    # значений в характеристики.
    title = (
        auto_fix(mod_card)[0].name
        if any(f.rule == "value-unit-in-title" for f in findings)
        else None
    )
    return [_from_finding(f, card.name, title) for f in findings]


def _from_finding(f: Finding, name: str, fixed_title: str | None) -> Issue:
    fix = None
    if f.rule == "unit-in-char-name" and f.char:
        base, unit = split_name_unit(f.char)
        fix = Fix(name=base, unit=unit)
    elif f.rule == "value-unit-in-title" and fixed_title and fixed_title != name:
        fix = Fix(value=fixed_title)
    return Issue(
        field="name" if f.rule == "value-unit-in-title" else _field(f.char),
        kind="moderation_rule",
        severity="block" if f.level == "block" else "warn",
        message=f.text,
        why=f"Правило модерации портала: {RULE_TEXT[f.rule]}.",
        rule=RULE_TEXT[f.rule],
        fix=fix,
    )


def _spec_for(name: str, position: Position) -> tuple[CharacteristicSpec | None, str | None]:
    """Спецификация позиции для характеристики карточки и, если имя с опечаткой, верное имя."""
    base, _ = split_name_unit(name)
    for spec in position.characteristics:
        if fold(spec.name) in (fold(name), fold(base)):
            return spec, None
    near = nearest_allowed(base, [s.name for s in position.characteristics])
    if near is None:
        return None, None
    spec = next(s for s in position.characteristics if s.name == near[0])
    return spec, spec.name


# Канон значения для сверки с текстом: слова без регистра, рода, числа и окончания, прилагательное
# от существительного — к существительному («нитриловые» — «нитрил», «Оригинальное» — «Оригинал»),
# отрицание — частью слова («не стерильные» — «Нестерильные»).
_TOKEN = re.compile(r"[\p{L}\p{N}]+")
_NEGATIONS = {"не", "без"}
_ADJECTIVE = re.compile(r"(?:ый|ий|ой|ая|яя|ое|ее|ые|ие|ую|юю|ого|его|ых|их|ым|им|ыми|ими)\Z")
_DERIVED = re.compile(r"(?:ическ|ов|ев|ьн|н|ск)\Z")
# Короче — не отрезаем суффикс: «черн» не «чер», но «неонов» — «неон».
MIN_BASE = 4
MIN_FLEETING = 5
# Скобки вплотную к слову — окончание («Оригинальный(ая)»), через пробел — другое название
# («Щелочная (алкалиновая)», «Полиэтилен низкого давления (ПНД)»).
_GLUED = re.compile(r"(?<=\w)\([^()]*\)")
_INNER = re.compile(r"\(([^()]*)\)")
_NOT_INITIAL = {"и", "для", "с", "со", "из", "в", "на", "по", "от", "до"}
_YES = {"да", "есть", "имеется"}
_NO = {"нет", "отсутствует"}


# Беглая гласная: «порошок» — «порошка», «порошковый».
_FLEETING = re.compile(r"(?<=[^аеиоуыэюя])[ое]к\Z")


# Основа короче — не основа: «белья» и «белый» у snowball обе «бел», «серия» и «серый» — «сер».
# Такое слово совпадает только с самим собой или, если оба — прилагательные, по основе.
MIN_STEM = 4
# «Алюминий», «магний» — существительные на -ний, не прилагательные: суффикс не отрезаем.
_NOUN_NII = re.compile(r"ний\Z")


@lru_cache(maxsize=65_536)
def _lex(word: str) -> str:
    stems = tokens(word)
    if not stems:
        return ""
    stem = stems[0]
    adjective = bool(_ADJECTIVE.search(word))
    if len(stem) < MIN_STEM:
        return f"{stem}~прил" if adjective and stem != word else word
    base = _DERIVED.sub("", stem)
    if base != stem and len(base) >= MIN_BASE and adjective and not _NOUN_NII.search(word):
        derived = (tokens(base) or [base])[0]
        stem = derived if len(derived) >= MIN_STEM else base
    stem = stem.rstrip("ь")
    return _FLEETING.sub("к", stem) if len(stem) >= MIN_FLEETING else stem


@lru_cache(maxsize=262_144)
def _canon(words: tuple[str, ...]) -> frozenset[str]:
    out = set()
    for i, w in enumerate(words):
        if w in _NEGATIONS or not (lex := _lex(w)):
            continue
        out.add("не" + lex if i and words[i - 1] in _NEGATIONS else lex)
    # Значение из одних служебных слов — сами слова: марка бумаги «С» не пустое значение.
    return frozenset(out) if out else frozenset(w for w in words if w not in _NEGATIONS)


@lru_cache(maxsize=65_536)
def _words(text: str) -> tuple[str, ...]:
    return tuple(_TOKEN.findall(fold(text)))


_PARTS = re.compile(r"[,;]")


@lru_cache(maxsize=65_536)
def _main_forms(value: str) -> tuple[tuple[str, ...], ...]:
    """Значение целиком и без пояснения в скобках: «Полиэтилен низкого давления (ПНД)» —
    «полиэтилен низкого давления пнд», «полиэтилен низкого давления»."""
    value = _GLUED.sub("", value)
    out: list[tuple[str, ...]] = []
    for part in (value, _INNER.sub(" ", value)):
        if (words := _words(part)) and words not in out:
            out.append(words)
    return tuple(out)


@lru_cache(maxsize=65_536)
def _forms(value: str) -> tuple[tuple[str, ...], ...]:
    """Значение и другие его названия словами: в скобках («Щелочная (алкалиновая)» —
    «щелочная», «алкалиновая») и через запятую («A286, LR03, 24A» — «lr03»)."""
    value = _GLUED.sub("", value)
    out = list(_main_forms(value))
    parts = _PARTS.split(_INNER.sub(" ", value))
    for part in (*(parts if len(parts) > 1 else ()), *_INNER.findall(value)):
        words = _words(part)
        if words and any(w.isalpha() for w in words) and words not in out:
            out.append(words)
    return tuple(out)


# Аббревиатура — из первых букв полных слов: «оп» не «одноразового пользования» внутри скобок,
# «аа» не «ааа … алкалиновая».
MIN_INITIAL_WORD = 4


def _initial_word(w: str) -> bool:
    return w.isalpha() and len(w) >= MIN_INITIAL_WORD


def _initials(words: tuple[str, ...]) -> str | None:
    """«Полиэтилен низкого давления» — «пнд», «Однопроволочная круглая» — «ок»."""
    words = tuple(w for w in words if w not in _NOT_INITIAL)
    if len(words) < 2 or not all(_initial_word(w) for w in words):
        return None
    return "".join(w[0] for w in words)


def _mentions(text: str, value: str) -> bool:
    """Значение названо в тексте: все его слова в другой форме или название в скобках.

    Аббревиатуру по первым буквам слов текста не ищем: «см» — не «Серый металлик», «пп» в
    «Пленка полиэтилен» — не «ПП». Аббревиатура против расшифровки — это `_same`: значение
    текста, которое сводится к значению поля, доводом против него не считается.
    """
    have = _canon(_words(text))
    return any((lex := _canon(form)) and lex <= have for form in _forms(value))


@lru_cache(maxsize=262_144)
def _same(a: str, b: str) -> bool:
    """Одно значение входит в другое или сокращает его: «латекс» и «Латекс натуральный», «AAA» и
    «AAA LR03», «ок» и «Однопроволочная круглая» — не противоречие."""
    for fa in _forms(a):
        for fb in _forms(b):
            la, lb = _canon(fa), _canon(fb)
            if la and lb and (la <= lb or lb <= la):
                return True
    return _abbreviation(a, b)


def _abbreviation(a: str, b: str) -> bool:
    """Одно — первые буквы другого: «ок» и «Однопроволочная круглая» (пояснение в скобках
    аббревиатурой не считается) или обрезанное слово: «муж» и «Мужские»."""
    return any(
        len(short) == 1 and (short[0] == _initials(full) or _cut(short[0], full))
        for x, y in ((a, b), (b, a))
        for short in _forms(x)
        for full in _main_forms(y)
    )


# Сокращение обрезкой слова — три-четыре буквы и хотя бы на две короче полного: «муж» —
# «Мужские», «жен» — «Женские». Длиннее — уже слово: «металл» не «Металлик» (честная карточка
# выборки получила block). «Нет» — ответ, а не начало «Нетканый».
MIN_CUT, MAX_CUT = 3, 4


def _cut(short: str, full: tuple[str, ...]) -> bool:
    return (
        len(full) == 1
        and short.isalpha()
        and MIN_CUT <= len(short) <= MAX_CUT
        and len(full[0]) >= len(short) + 2
        and full[0].startswith(short)
        and short not in _YES | _NO | _NEGATIONS
    )


@lru_cache(maxsize=262_144)
def _alias(a: str, b: str) -> bool:
    """Два названия одного значения: те же слова, название в скобках или аббревиатура
    («ПНД» и «Полиэтилен низкого давления (ПНД)»), но не «входит в»."""
    return _abbreviation(a, b) or any(
        _canon(fa) == _canon(fb) for fa in _forms(a) for fb in _forms(b)
    )


def _yes_no(spec: CharacteristicSpec, a: str, b: str) -> bool:
    """«Наличие дозатора: Да» и «С дозатором», «Нет» и «Без дозатора» — одно и то же."""
    topic = {_lex(w) for w in _words(spec.name)} - {""}
    for x, y in ((a, b), (b, a)):
        if (answer := fold(x)) not in _YES | _NO:
            continue
        words = _words(y)
        if topic & {_lex(w) for w in words}:
            return (answer in _YES) != any(w in _NEGATIONS | _NO for w in words)
    return False


@dataclass(frozen=True)
class _Said:
    """Значение характеристики, которое называет текст: как написано и значение перечня."""

    shown: str
    listed: str | None
    source: Literal["name", "description"]
    # Описание называет и характеристику («Материал: латекс»), а не только слово из перечня.
    labelled: bool = False


# Слова имени характеристики, которые не говорят, о чём она: «Тип цоколя» и «Тип лампы» — о разном.
_GENERIC = ("тип", "вид", "класс", "категория", "наличие", "требования", "изделия", "товара")


def _topic(name: str) -> frozenset[str]:
    return _canon(_words(name)) - _canon(_GENERIC)


def _neighbour(shown: str, value: str, spec: CharacteristicSpec, card: CardIn,
               position: Position) -> bool:  # fmt: skip
    """Значение текста уже стоит в другом поле карточки о другом («Цвет: красный» — не довод
    против «Концентрат: Нет»), и значения этого поля там не бывает. Перечни портала смешаны:
    без заполненного соседа по одному перечню соседа не определить («Тип» масла содержит
    «10w-40»)."""
    topic = _topic(spec.name)
    for c in card.characteristics:
        other, _ = _spec_for(c.name, position)
        # «Класс», «Тип» без уточнения — неизвестно о чём: такое поле ничего не объясняет.
        if other is None or other is spec or not (theirs := _topic(other.name)) or topic & theirs:
            continue
        if _same(shown, c.value) and not any(_same(value, v) for v in other.values):
            return True
    return False


def _alien(said: _Said, value: str, spec: CharacteristicSpec, card: CardIn,
           position: Position) -> bool:  # fmt: skip
    """Значение текста — не довод против поля: то же значение («латекс» и «Латекс натуральный»,
    через другое название в перечне «ПНД» — «Полиэтилен низкого давления (ПНД)» — «полиэтилен»),
    «да/нет» той же характеристики, число («205» у индекса скорости), слова названия категории
    («Масло моторное» у «Масла моторные») или значение соседнего поля."""
    shown = said.listed or said.shown
    if _same(shown, value) or _same(said.shown, value) or _yes_no(spec, value, shown):
        return True
    if any(_alias(value, v) and _same(v, shown) for v in spec.values):
        return True
    # «Цвет: ?», «Цвет: *» — в тексте нет ни одного слова: это не довод против поля.
    forms = _main_forms(shown)
    if not forms or all(_DIMENSION.fullmatch(w) for w in forms[-1]):
        return True
    if said.source == "name" and _canon(_words(shown)) <= _canon(_words(position.name)):
        return True
    return _neighbour(shown, value, spec, card, position)


# Число или размер «140х290»: в наименовании это размеры и количества, а не значение перечня.
_DIMENSION = re.compile(r"[0-9]+(?:[хx×][0-9]+)*")


def _elsewhere(shown: str, value: str, spec: CharacteristicSpec, position: Position) -> bool:
    """Значение текста есть в перечне другой характеристики позиции (о другом), а значения поля
    там нет: перечни портала смешаны («Разноцветный» в «Виде обложки»), какое поле текст
    называет — неизвестно. Не запрет, а подсказка."""
    topic = _topic(spec.name)
    for other in position.characteristics:
        if other is spec or other.kind != "qualitative" or not other.values:
            continue
        if not (theirs := _topic(other.name)) or topic & theirs:
            continue
        if any(_same(shown, v) for v in other.values) and not any(
            _same(value, v) for v in other.values
        ):
            return True
    return False


def _texts_say(
    card: CardIn,
    spec: CharacteristicSpec,
    value: str,
    position: Position,
    described: _Said | None,
) -> tuple[bool, list[_Said]]:
    """Названо ли значение поля в тексте и какие другие значения перечня текст называет."""
    in_name = _mentions(card.name, value)
    named = in_name or _mentions(card.description, value)
    found = [_Said(v, v, "name") for v in spec.values if _mentions(card.name, v)]
    if described is not None:
        found.append(described)
    found = [s for s in found if not _alien(s, value, spec, card, position)]
    if named:
        # Значение поля в тексте есть — противоречия нет, даже если там и другое значение перечня
        # («Скрепки канцелярские … овальные»). Исключение — описание прямо называет характеристику
        # («Материал: латекс», «класс вязкости SAE 10W-40»), а наименование — другое значение:
        # текст расходится сам с собой, это подсказка.
        # Значение поля стоит в самом наименовании — другие слова наименования ему не довод.
        # Значение, которое числится и за другой характеристикой позиции, — не расхождение
        # текста: перечни портала смешаны («Белый» в «Типе расцветки»).
        explicit = described is not None and described.labelled and not in_name
        found = [
            s
            for s in found
            if (s.labelled or (explicit and s.source == "name"))
            and not _elsewhere(s.listed or s.shown, value, spec, position)
        ]
    return named, found


def _groups(found: list[_Said]) -> list[list[_Said]]:
    """Значения текста, которые сводятся к одному («Черный» и «Черная экокожа»)."""
    groups: list[list[_Said]] = []
    for s in found:
        key = s.listed or s.shown
        group = next((g for g in groups if any(_same(key, t.listed or t.shown) for t in g)), None)
        if group is None:
            groups.append([s])
        else:
            group.append(s)
    return groups


_NEGATION = {"без", "не", "нет"}
_MIN_SHARED_STEM = 5


def _same_thing(said: "_Said", value: str) -> bool:
    """Текст говорит о том же другими словами, а не другое значение: слово вне перечня
    («встроенный аккумулятор») и значение перечня («От аккумуляторных батарей») с общей основой
    от пяти букв и без отрицания. Значение перечня в тексте («от сети») — всегда расхождение."""
    if said.listed:
        return False
    words = said.shown.casefold().replace("ё", "е").split()
    if _NEGATION & set(words):
        return False
    # Основы стеммер режет по-разному («аккумулятор» и «аккумуляторн»): общее начало от 5 букв.
    return any(
        min(len(a), len(b)) >= _MIN_SHARED_STEM and (a.startswith(b) or b.startswith(a))
        for a in tokens(said.shown)
        for b in tokens(value)
    )


def _text_conflict(
    card: CardIn,
    field: str,
    spec: CharacteristicSpec,
    shown: str,
    value: str,
    position: Position,
    described: _Said | None,
) -> Issue | None:
    """Значение поля против наименования и описания.

    block — значения поля в тексте нет, а текст называет одно другое значение перечня; fix —
    это значение, если с ним поле проходит ту же проверку (иначе исправление и замечание ходят
    по кругу). Сомнение — значение поля в тексте тоже есть (наименование и описание с меткой
    расходятся), текст называет несколько разных, описание без метки характеристики, значения
    из описания нет в перечне, значение числится и за другой характеристикой позиции — warn.
    """
    named, found = _texts_say(card, spec, value, position, described)
    if not found:
        return None
    if all(_same_thing(s, value) for s in found):
        return None
    groups = _groups(found)
    # Значение поля в тексте тоже есть — какое из двух верно, решает поставщик: без исправления.
    single = len(groups) == 1 and not named
    fix = next((s.listed for s in found if s.listed), None) if single else None
    if fix is not None and _texts_say(card, spec, fix, position, described)[1]:
        fix = None
    # block — только по однозначному доводу: значение перечня из наименования или описание с именем
    # характеристики, и это значение не числится за другой характеристикой позиции.
    sure = (
        fix is not None
        and not named
        and all(
            s.listed
            and (s.source == "name" or s.labelled)
            and not _elsewhere(s.listed, value, spec, position)
            for s in found
        )
    )
    in_name = [next(s for s in g if s.source == "name") for g in groups
               if any(s.source == "name" for s in g)]  # fmt: skip
    in_description = next((s for s in found if s.source == "description"), None)
    if in_name:
        said = in_name[0]
        message = f"«{spec.name}: {shown}» противоречит наименованию."
        why = f"В наименовании «{card.name}» указано " + ", ".join(
            f"«{(s.listed or s.shown).lower()}»" for s in in_name
        )
        why += (
            f", в описании — «{in_description.shown.lower()}»."
            if in_description is not None and len(groups) > len(in_name)
            else "."
        )
    else:
        said = found[0]
        message = f"«{spec.name}: {shown}» противоречит описанию: там «{said.shown.lower()}»."
        why = f"В описании товара указано «{said.shown.lower()}»" + (
            ". Проверьте, что верно, и исправьте поле или описание."
            if said.listed
            else f", а в позиции {position.code} такого значения нет: проверьте описание или "
            "выбранную позицию."
        )
    if named:
        why += f" «{shown}» в тексте тоже есть: проверьте, что верно, и исправьте текст или поле."
    elif len(groups) > 1:
        why += " Проверьте, какое значение верно."
    return Issue(
        field=field,
        kind="mismatch",
        severity="block" if sure else "warn",
        message=message,
        why=why,
        rule=RULE_NAME if said.source == "name" else RULE_DESCRIPTION,
        fix=Fix(value=fix) if fix else None,
    )


def _name_numbers(card: CardIn, position: Position) -> dict[str, Extracted]:
    """Числа с единицей из наименования, разложенные по характеристикам позиции тем же извлечением,
    что и автозаполнение (размеры «40/150/6000 мм» — по ролям, а не первое попавшееся)."""
    from app.extract.rules import extract_rules  # rules импортирует app.checks: цикл на старте

    return {
        e.name: e
        for e in extract_rules(card.name, "", position.characteristics)
        if e.raw_unit is not None and normalize_unit(e.raw_unit) not in COUNT_UNITS
    }


_PHRASE_BREAK = re.compile(r"[,.;\n]")
# Сколько символов перед значением смотреть в поисках имени характеристики.
LABEL_WINDOW = 60


def _phrase_before(description: str, start: int) -> str:
    """Начало фразы описания до значения: «Материал: » в «…, Материал: латекс»."""
    return _PHRASE_BREAK.split(description[max(0, start - LABEL_WINDOW) : start])[-1]


def _labelled(before: str, spec: CharacteristicSpec, position: Position) -> bool:
    """Перед значением в той же фразе — имя характеристики или его синоним («Материал: латекс»,
    «класс вязкости SAE 10W-40»), а не просто слово из перечня. Слова названия категории — не
    имя: «Скрепки овальные» не метка «Вида скрепок канцелярских»."""
    category = _canon(_words(position.name))
    if ":" in before:
        # Явная метка «X:» — это поле, только если X о том же: «Материал:» у «Требований к
        # материалу изделия» да; «Тип уплотнения:» у «Типа», «Вид материала каркаса:» у
        # «Материала», «Обложка:» у «Материала обложки» — нет.
        label = before.rsplit(":", 1)[0]
        said = _topic(label) - category
        words = _canon(_words(label))
        return any(
            said == _topic(name) - category and (bool(said) or bool(words & _canon(_words(name))))
            for name in (spec.name, *spec.synonyms)
        )
    said = _canon(_words(before)) - category
    for name in (spec.name, *spec.synonyms):
        # Без метки — слова перед значением называют всё, о чём характеристика: «класс вязкости
        # SAE 10W-40». У «Тип», «Вид» — хотя бы это слово.
        topic = _topic(name) - category
        if (topic and topic <= said) or (not topic and said & _canon(_words(name))):
            return True
    return False


def _described(card: CardIn, position: Position) -> dict[str, _Said]:
    """Качественные значения, однозначно названные в описании: как написано и значение перечня
    («Материал: латекс» → «латекс» без значения перечня, если латекса в позиции нет)."""
    from app.extract.rules import extract_rules  # rules импортирует app.checks: цикл на старте

    if not card.description.strip():
        return {}
    by_name = {s.name: s for s in position.characteristics}
    out = {}
    for e in extract_rules("", card.description, position.characteristics):
        spec = by_name[e.name]
        if spec.group == "general":
            continue
        if spec.kind != "qualitative" or not spec.values:
            continue
        before = _phrase_before(card.description, e.source.start)
        labelled = _labelled(before, spec, position)
        if not labelled and ":" in before:
            # Явная метка другой характеристики («Противоскользящие ступени: Да», «Бумага:
            # офсетная»): значение про неё, а не про это поле — ни довода, ни подсказки.
            continue
        fit = fit_value(e.raw_value, e.raw_unit, spec)
        out[spec.name] = _Said(
            shown=e.raw_value.strip(),
            listed=fit.value if fit.status in _FITTED | {"typo"} else None,
            source="description",
            labelled=labelled,
        )
    return out


def _in_spec_unit(e: Extracted, spec: CharacteristicSpec) -> float | None:
    fit = fit_value(e.raw_value, e.raw_unit, spec)
    return to_number(fit.value or "") if fit.status in _FITTED else None


def _number_contradiction(
    spec: CharacteristicSpec, value: str, in_name: dict[str, Extracted]
) -> str | None:
    """Число из наименования («1,7 л») после приведения к единице позиции расходится со значением
    поля («1700 л») так, как расходится ошибка ввода: перепутана единица (отношение — степень
    десяти) или опечатка в цифре. Другое расхождение — скорее два разных размера, не замечание.
    Значение поля, которое в наименовании есть у другой характеристики («20*22 см», длина и
    ширина переставлены), — тоже не замечание. Счётные единицы не сравниваются: «10+2 шт» — состав.
    """
    e = in_name.get(spec.name)
    if e is None or spec.kind != "quantitative":
        return None
    x, y = _in_spec_unit(e, spec), to_number(value)
    if x is None or y is None or x == y or not input_slip(x, y):
        return None
    if any(_in_spec_unit(other, spec) == y for other in in_name.values()):
        return None
    return f"{e.raw_value} {e.raw_unit}"


_FITTED = {"exact", "normalized", "bucket"}


def _short(x: float) -> str:
    return (f"{x:.0f}" if abs(x) >= 100 else f"{x:.3g}").replace(".", ",")


def _checked_number(char: CharIn, fit: Fit) -> str | None:
    """Для правдоподобия берём число поставщика, а не текст корзины, в которую оно легло."""
    if to_number(char.value or "") is not None:
        return char.value
    return fit.value


def _implausible(spec: CharacteristicSpec, value: str | None) -> str | None:
    """Число вне пределов, обычных для СТЕ категории (`spec.plausible`): «не больше 256 %»."""
    rng, x = spec.plausible, to_number(value or "")
    if rng is None or x is None:
        return None
    unit = f" {spec.unit}" if spec.unit else ""
    if rng.max is not None and x > rng.max:
        return f"не больше {_short(rng.max)}{unit}"
    if rng.min is not None and x < rng.min:
        return f"не меньше {_short(rng.min)}{unit}"
    return None


def _unit_slip(spec: CharacteristicSpec, value: str | None) -> float | None:
    """То же число в 10, 100 или 1000 раз меньше (больше) попадает в обычные пределы категории:
    скорее всего, перепутаны метры и миллиметры, граммы и килограммы («высота 5 метров» у стола).
    Из нескольких таких — ближайшее к середине пределов: 20 м ширины тумбы — скорее 20 см, чем
    2 м."""
    rng, x = spec.plausible, to_number(value or "")
    if rng is None or x is None or x <= 0:
        return None
    found = []
    for factor in (10, 100, 1000):
        for y in (x / factor, x * factor):
            inside = (rng.min is None or y >= rng.min) and (rng.max is None or y <= rng.max)
            toward = (rng.max is not None and x > rng.max and y < x) or (
                rng.min is not None and x < rng.min and y > x
            )
            if inside and toward:
                found.append(y)
    if not found:
        return None
    lo = rng.min if rng.min and rng.min > 0 else None
    hi = rng.max if rng.max and rng.max > 0 else None
    mid = math.sqrt(lo * hi) if lo and hi else (hi / 2 if hi else (lo or 1.0) * 2)
    return min(found, key=lambda y: abs(math.log(y / mid)))


def _ktru_spec(name: str, ktru: Position | None) -> CharacteristicSpec | None:
    """Одноимённая характеристика позиции КТРУ (не похожая по имени и не «Общие сведения»)."""
    if ktru is None:
        return None
    spec, right_name = _spec_for(name, ktru)
    return spec if spec is not None and right_name is None and spec.group != "general" else None


def _ktru_fit(value: str, unit: str | None, kspec: CharacteristicSpec) -> Fit:
    """Значение против перечня позиции КТРУ; то же значение другой формой слова или названием в
    скобках («Мужской» и «Мужские», «Нитриловые» и «Нитрил») — значение КТРУ, а не опечатка."""
    fit = fit_value(value, unit, kspec)
    if fit.status in _FITTED or kspec.kind != "qualitative":
        return fit
    same = next((v for v in kspec.values if _alias(value, v)), None)
    return fit if same is None else Fit(status="normalized", value=same, unit=kspec.unit)


def _portal_list_differs(
    field: str, spec: CharacteristicSpec, value: str, note: str | None, position: Position,
    ktru: Position,
) -> Issue:  # fmt: skip
    return Issue(
        field=field,
        kind="not_in_list",
        severity="info",
        message=f"«{spec.name}: {value}» — значение позиции КТРУ {ktru.code}, в перечне категории "
        "портала его нет.",
        why=f"КТРУ — эталон: значение верно. «{spec.name}» в категории {position.code}: {note}.",
        rule=RULE_KTRU_VALUE,
    )


def _ktru_typo(field: str, value: str, fixed: str, note: str | None, ktru: Position) -> Issue:
    return Issue(
        field=field,
        kind="typo",
        severity="warn",
        message=f"Похоже на опечатку: «{value}».",
        why=f"{note}: ближайшее значение перечня позиции КТРУ {ktru.code}.",
        rule=RULE_KTRU_VALUE,
        fix=Fix(value=fixed),
    )


def _ktru_conflict(
    card: CardIn, field: str, kspec: CharacteristicSpec, shown: str, value: str, ktru: Position,
    described: _Said | None, most: Literal["block", "warn"],
) -> Issue | None:  # fmt: skip
    """Значение из перечня позиции КТРУ против текста: довод — только другое значение КТРУ.

    Строже, чем `most` (что дала проверка по категории), не бывает: КТРУ снимает ложные тревоги,
    а не добавляет block (на 8 тыс. честных карточек без потолка — 6 новых block).
    """
    if kspec.kind != "qualitative" or not kspec.values:
        return None
    conflict = _text_conflict(card, field, kspec, shown, value, ktru, described)
    if conflict is not None and conflict.severity == "block" and most == "warn":
        return conflict.model_copy(update={"severity": "warn"})
    return conflict


def _portal_word(conflict: Issue, spec: CharacteristicSpec, value: str, ktru: Position) -> Issue:
    """Текст называет значение перечня категории портала, которого нет в позиции КТРУ, а значение
    поля в ней есть: КТРУ — эталон, это сведения, а не ошибка («Ножовка по дереву» и «Вид пилы:
    Классическая» — единственное значение позиции КТРУ; исправление «Ножовка» КТРУ не пройдёт)."""
    return conflict.model_copy(
        update={
            "severity": "info",
            "message": f"«{spec.name}: {value}» — значение позиции КТРУ {ktru.code}; текст "
            "называет значение перечня категории портала.",
            "why": f"{conflict.why} В позиции КТРУ {ktru.code} такого значения нет: КТРУ — эталон.",
            "rule": RULE_KTRU_VALUE,
            "fix": None,
        }
    )


def _spec_issues(card: CardIn, position: Position, ktru: Position | None = None) -> list[Issue]:
    issues: list[Issue] = []
    filled: set[str] = set()
    in_name = _name_numbers(card, position)
    in_description = _described(card, position)
    by_ktru = _described(card, ktru) if ktru is not None else {}
    for c in card.characteristics:
        spec, right_name = _spec_for(c.name, position)
        if spec is None:
            # Характеристика КТРУ вне шаблона категории сверяется с позицией КТРУ
            # (`ktru_value_issues`); свойство вне шаблона (портальное или новое) — не ошибка, а
            # сведения для модератора: так велели организаторы, ничего не выбрасывать.
            if c.origin in ("portal", "new"):
                issues.append(extra_characteristic(c.name, c.origin, position))
            elif c.origin != "ktru" and c.name.strip():
                issues.append(not_in_position(c.name, position))
            continue
        filled.add(spec.name)
        field = _field(c.name)
        if right_name is not None:
            issues.append(
                Issue(
                    field=field,
                    kind="typo",
                    severity="warn",
                    message=f"Характеристика «{c.name}» похожа на «{right_name}» из позиции.",
                    why=f"В позиции {position.code} характеристика называется «{right_name}».",
                    rule=RULE_SPEC_NAME,
                    fix=Fix(name=right_name),
                )
            )
        if spec.group == "general":
            continue  # перечень производителей и стран — подсказка: новый завод не ошибка
        _, unit_in_name = split_name_unit(c.name)
        fit = fit_value(c.value, c.unit or unit_in_name, spec)
        # КТРУ — эталон (организаторы): значение из перечня итоговой позиции КТРУ верно, даже если
        # перечень категории портала его не знает; опечатку исправляем по КТРУ.
        kspec = _ktru_spec(c.name, ktru)
        kfit = _ktru_fit(c.value, c.unit or unit_in_name, kspec) if kspec is not None else None
        if (
            ktru is not None
            and kspec is not None
            and kfit is not None
            and fit.status in ("not_in_list", "typo")
        ):
            if kfit.status in _FITTED and kfit.value is not None:
                issues.append(_portal_list_differs(field, spec, c.value, fit.note, position, ktru))
                most: Literal["block", "warn"] = "block" if fit.status == "not_in_list" else "warn"
                conflict = _ktru_conflict(
                    card, field, kspec, c.value, kfit.value, ktru, by_ktru.get(kspec.name), most
                )
                if conflict is not None:
                    issues.append(conflict)
                continue
            if kfit.status == "typo" and kfit.value is not None:
                issues.append(_ktru_typo(field, c.value, kfit.value, kfit.note, ktru))
                continue
        if fit.status == "not_in_list":
            issues.append(
                Issue(
                    field=field,
                    kind="not_in_list",
                    severity="block",
                    # У количественной перечня нет: вне диапазона или отрицательное значение.
                    message=f"Значения «{c.value}» нет в перечне позиции."
                    if spec.kind == "qualitative"
                    else f"«{c.value}» не подходит к «{spec.name}»: {fit.note}.",
                    why=f"«{spec.name}» в позиции {position.code}: {fit.note}.",
                    rule=RULE_TEXT["value-not-allowed"],
                )
            )
        elif fit.status == "typo":
            issues.append(
                Issue(
                    field=field,
                    kind="typo",
                    severity="warn",
                    message=f"Похоже на опечатку: «{c.value}».",
                    why=f"{fit.note}: ближайшее значение перечня позиции {position.code}.",
                    rule=RULE_TEXT["value-not-allowed"],
                    fix=Fix(value=fit.value),
                )
            )
        elif fit.status in ("unit_mismatch", "unparsed"):
            # Значение из файла бывает уже с единицей («17 л»): «17 л л» в сообщении — не нужно.
            has_unit = c.unit and fold(c.value).endswith(" " + fold(c.unit))
            shown = f"{c.value} {c.unit}" if c.unit and not has_unit else c.value
            issues.append(
                Issue(
                    field=field,
                    kind="mismatch",
                    severity="block",
                    message=(
                        f"Значение «{spec.name}» противоречит позиции {position.code}. "
                        f"Сейчас: «{shown}». Ожидается: {fit.note}."
                    ),
                    why=f"{fit.note}.",
                    rule=RULE_UNIT if fit.status == "unit_mismatch" else RULE_NUMBER,
                )
            )
        elif (
            fit.status in _FITTED
            and fit.value is not None
            and (shown_in_name := _number_contradiction(spec, fit.value, in_name))
        ):
            shown = f"{c.value} {c.unit or unit_in_name or ''}".strip()
            issues.append(
                Issue(
                    field=field,
                    kind="mismatch",
                    severity="block",
                    message=f"«{spec.name}: {shown}» противоречит наименованию.",
                    why=f"В наименовании «{card.name}» указано «{shown_in_name}»; одно из двух — "
                    "ошибка ввода (часто единица: мл и л, мм и см).",
                    rule=RULE_NAME,
                )
            )
        elif (
            spec.kind == "quantitative"
            and fit.status in _FITTED
            and fit.value is not None
            # Число без единицы у поля с единицей («Высота: 100» при «100 см» в тексте) — та же
            # цифра, а единица не названа: это не расхождение с текстом.
            and (c.unit or unit_in_name or not spec.unit)
            and (
                said := said_otherwise(
                    spec,
                    fit.value,
                    [("name", card.name), ("description", card.description)],
                    [],
                    position.characteristics,
                )
            )
        ):
            # «10 А» в поле и «16 А» в описании, «300 листов» и «на 30 листов».
            shown = f"{c.value} {c.unit or unit_in_name or ''}".strip()
            issues.append(
                Issue(
                    field=field,
                    kind="mismatch",
                    severity="warn",
                    message=f"«{spec.name}: {shown}» расходится с текстом: там «{said}».",
                    why="Число в наименовании или описании подписано этой характеристикой или "
                    "других полей этой величины в позиции нет; одно из двух — ошибка ввода.",
                    rule=RULE_DESCRIPTION,
                )
            )
        elif spec.kind == "quantitative" and (usual := _implausible(spec, _checked_number(c, fit))):
            shown = f"{c.value} {c.unit or unit_in_name or ''}".strip()
            slip = _unit_slip(spec, _checked_number(c, fit))
            unit = c.unit or spec.unit
            guess = f" Возможно, {_short(slip)} {unit or ''}?".rstrip() if slip is not None else ""
            issues.append(
                Issue(
                    field=field,
                    kind="mismatch",
                    severity="warn",
                    message=f"«{spec.name}: {shown}» — необычно для категории: у СТЕ портала "
                    f"{usual}.{guess}",
                    why="Так далеко от значений других поставщиков категории число уходит чаще "
                    "всего из-за единицы (мм и см, г и кг) или лишней цифры.",
                    rule=RULE_PLAUSIBLE,
                    fix=Fix(value=_short(slip), unit=unit) if slip is not None else None,
                )
            )
        elif (
            fit.status in _FITTED
            and fit.value is not None
            and spec.kind == "qualitative"
            and spec.values
            and (
                conflict := _text_conflict(
                    card,
                    field,
                    spec,
                    c.value,
                    fit.value,
                    position,
                    in_description.get(spec.name),
                )
            )
        ):
            if (
                ktru is not None
                and kspec is not None
                and kfit is not None
                and kfit.status in _FITTED
                and kfit.value is not None
            ):
                # Значение есть и в перечне КТРУ: опровергает его только другое значение КТРУ.
                ktru_conflict = _ktru_conflict(
                    card, field, kspec, c.value, kfit.value, ktru, by_ktru.get(kspec.name),
                    "block" if conflict.severity == "block" else "warn",
                )  # fmt: skip
                conflict = ktru_conflict or _portal_word(conflict, spec, c.value, ktru)
            issues.append(conflict)
    for spec in position.characteristics:
        if spec.required and spec.name not in filled:
            issues.append(
                Issue(
                    field=_field(spec.name),
                    kind="moderation_rule",
                    severity="block",
                    message=(
                        f"Обязательная характеристика «{spec.name}» не заполнена. "
                        f"Позиция {position.code} требует её указать."
                    ),
                    why=f"Характеристика обязательна в позиции {position.code}.",
                    rule=RULE_REQUIRED,
                )
            )
    return issues


def unknown_code(code: str, field: str = "ktru_code") -> Issue:
    """Кода категории нет в справочнике: карточку по нему не опубликовать.

    Код может прийти и в `ktru_code` (старые клиенты кладут туда код категории): это не обязательно
    КТРУ, поэтому в сообщении — просто «код».
    """
    return Issue(
        field=field,
        kind="not_in_list",
        severity="block",
        message=f"Кода {code} нет в справочнике.",
        why="Карточка СТЕ создаётся по позиции справочника; без неё характеристики не проверить.",
        rule=RULE_CATEGORY if field == "category_code" else RULE_KTRU,
    )


def missing_code() -> Issue:
    return Issue(
        field="ktru_code",
        kind="moderation_rule",
        severity="block",
        message="Не указана категория товара.",
        why="Карточка СТЕ создаётся по позиции справочника: без неё характеристики не проверить.",
        rule=RULE_KTRU,
    )


def unknown_ktru(code: str, loaded: bool) -> Issue:
    """Код КТРУ не найден: в загруженном справочнике КТРУ его нет или справочник не загружен."""
    return Issue(
        field="ktru_code",
        kind="not_in_list",
        severity="block" if loaded else "warn",
        message=f"Кода КТРУ {code} нет в справочнике КТРУ."
        if loaded
        else f"Код КТРУ {code} не проверен: справочник КТРУ не загружен.",
        why="Код КТРУ выбирается среди позиций, привязанных к категории товара.",
        rule=RULE_KTRU,
    )


def ktru_not_linked(code: str, category: Position) -> Issue:
    return Issue(
        field="ktru_code",
        kind="mismatch",
        severity="warn",
        message=f"Позиция КТРУ {code} не привязана к категории {category.code}.",
        why=f"У категории «{category.name}» в стандарте портала другие позиции КТРУ: проверьте "
        "категорию или код КТРУ.",
        rule=RULE_KTRU_LINK,
    )


def ktru_not_chosen(category: Position) -> Issue:
    """У категории есть позиции КТРУ, а код не выбран: не ошибка (бывает «ни одна не подходит»)."""
    return Issue(
        field="ktru_code",
        kind="moderation_rule",
        severity="info",
        message="Позиция КТРУ не выбрана.",
        why=f"К категории «{category.name}» привязаны позиции КТРУ; код КТРУ останется пустым.",
        rule=RULE_KTRU_LINK,
    )


def _in_category_list(c: CharIn, unit_in_name: str | None, category: Position | None) -> bool:
    """Значение есть в перечне (или корзинах) одноимённой характеристики категории портала. Поле
    вне шаблона категории (характеристика только позиции КТРУ) — нет."""
    if category is None:
        return False
    spec, right_name = _spec_for(c.name, category)
    if spec is None or right_name is not None or not spec.values:
        return False
    return fit_value(c.value, c.unit or unit_in_name, spec).status in _FITTED


_SHOWN_KTRU_VALUES = 10


# Основа значения КТРУ внутри слова портала — уточнение, если она не короче: «рычажн» в
# «однорычажн», «вентильн» в «двухвентильн»; короче — совпадение части слова («ткан» в «оттенок»).
MIN_REFINED = 5


def _refines(value: str, ktru_value: str) -> bool:
    """Значение портала — уточнение значения КТРУ числом в приставке к тому же слову:
    «Однорычажный» при «Рычажный», «Двухвентильный» при «Вентильный». Другие приставки меняют
    смысл: «Неразборный» и «Разборный», «Бескамерная» и «Камерная», «Полуботинки» и «Ботинки»."""
    a, b = tokens(value), tokens(ktru_value)
    if len(a) != 1 or len(b) != 1 or len(b[0]) < MIN_REFINED:
        return False
    return a[0] != b[0] and a[0].endswith(b[0]) and a[0][: -len(b[0])] in _COUNTING


# Числовые приставки: «однорычажный» — разновидность «рычажного», а не другое значение.
_COUNTING = frozenset({"одно", "двух", "трех", "четырех", "много"})


def _ktru_written_otherwise(
    field: str, spec: CharacteristicSpec, value: str, ktru: Position
) -> Issue:
    shown = ", ".join(f"«{v}»" for v in spec.values[:_SHOWN_KTRU_VALUES])
    if len(spec.values) > _SHOWN_KTRU_VALUES:
        shown += f" и ещё {len(spec.values) - _SHOWN_KTRU_VALUES}"
    return Issue(
        field=field,
        kind="not_in_list",
        severity="info",
        message=f"В позиции КТРУ {ktru.code} это записано иначе: {shown}.",
        why=f"«{spec.name}: {value}» — значение перечня категории портала; в позиции КТРУ "
        f"{ktru.code} допустимы: {shown}. Проверьте, что это одно и то же.",
        rule=RULE_KTRU_VALUE,
    )


def ktru_value_issues(
    card: CardIn, ktru: Position, issues: list[Issue], category: Position | None = None
) -> list[Issue]:
    """Значения полей, одноимённых с характеристиками позиции КТРУ, — по её перечню и диапазону.

    Карточка проверяется по категории; КТРУ — эталон (организаторы): значение вне перечня позиции
    КТРУ и вне перечня категории портала — warn с допустимыми значениями, опечатка — warn с
    исправлением. Перечни ЕИС и стандартов портала пишутся по-разному: то же значение другим словом
    или его уточнение (`_same`: «Нитрил» и «Нитриловые», «Смесовая ткань» и «Ткань»; `_refines`:
    «Однорычажный» при «Рычажный») — не замечание; значение из перечня категории («есть» при
    «Да», «Груша» при «Грушевидная») — info «в позиции КТРУ записано иначе»: на честных карточках
    выборки 1011 из 1020 таких warn были на значениях перечня портала, почти все — одно и то же
    другими словами. Поля, которых в позиции КТРУ нет, не трогаются; поле с замечанием категории
    второй раз не отмечается.
    """
    flagged = {i.field for i in issues if i.kind in ("not_in_list", "mismatch", "typo")}
    out: list[Issue] = []
    for c in card.characteristics:
        field = _field(c.name)
        if field in flagged or not c.value.strip():
            continue
        spec = _ktru_spec(c.name, ktru)
        if spec is None:
            continue
        _, unit_in_name = split_name_unit(c.name)
        fit = _ktru_fit(c.value, c.unit or unit_in_name, spec)
        if fit.status not in ("not_in_list", "unit_mismatch", "typo"):
            continue
        # Опечатка — раньше «входит в»: «Металл хромированый» содержит «Металл», но это опечатка;
        # и раньше перечня категории: перечни портала собраны из карточек и хранят опечатки
        # поставщиков («ЛДПС»).
        in_category = _in_category_list(c, unit_in_name, category)
        # Значение из перечня портала опечаткой КТРУ считаем только в одну правку: «желтый» при
        # «Белый» и «Серый» — две правки, другой цвет, а не опечатка.
        one_edit = fit.value is not None and (
            damerau_levenshtein(fold(c.value), fold(fit.value), limit=2) <= 1
        )
        if fit.status == "typo" and fit.value is not None and (not in_category or one_edit):
            out.append(_ktru_typo(field, c.value, fit.value, fit.note, ktru))
            continue
        if in_category and any(_refines(c.value, v) for v in spec.values):
            continue  # «Однорычажный» при «Рычажный» — уточнение значения КТРУ, не другое значение
        if in_category:
            out.append(_ktru_written_otherwise(field, spec, c.value, ktru))
            continue
        if spec.kind == "qualitative" and any(_same(c.value, v) for v in spec.values):
            continue
        out.append(
            Issue(
                field=field,
                kind="not_in_list" if fit.status == "not_in_list" else "mismatch",
                severity="warn",
                message=f"«{spec.name}: {c.value}» не соответствует позиции КТРУ {ktru.code}.",
                why=f"«{spec.name}» в позиции КТРУ {ktru.code}: {fit.note}.",
                rule=RULE_KTRU_VALUE,
            )
        )
    # Обязательные характеристики позиции КТРУ («является обязательной для применения»): без них
    # код КТРУ не применить — организаторы: «обязательных не хватает — впиши».
    named = {fold(split_name_unit(c.name)[0]) for c in card.characteristics if c.value.strip()}
    for spec in ktru.characteristics:
        if spec.required and spec.group != "general" and fold(spec.name) not in named:
            out.append(
                Issue(
                    field=_field(spec.name),
                    kind="moderation_rule",
                    severity="warn",
                    message=f"Обязательная характеристика позиции КТРУ «{spec.name}» не заполнена.",
                    why=f"В позиции КТРУ {ktru.code} характеристика обязательна для применения.",
                    rule=RULE_KTRU_REQUIRED,
                )
            )
    return out


def not_found(steps: list[str], kind: Literal["works", "services"] | None = None) -> Issue:
    """Модуль не нашёл позицию: замечание для клиентов API, в интерфейсе — своё сообщение.
    `kind` — наименование похоже на работы или услуги, а не на товар."""
    message = {
        "works": "Похоже на работы, а не на товар: в справочнике товаров категории нет.",
        "services": "Похоже на услугу, а не на товар: в справочнике товаров категории нет.",
    }.get(kind or "", "Категория не найдена в справочнике.")
    return Issue(
        field="ktru_code",
        kind="not_in_list",
        severity="block",
        message=message,
        why=" ".join(steps) if steps else "Ни одна позиция справочника не подходит к описанию.",
        rule=RULE_KTRU,
    )


# Противоречие внутри текста поставщика. Перечень такой длины — взаимоисключающие значения (пол,
# тип капюшона); в длинных перечнях (цвет, материал) два значения в тексте — обычно разные части
# товара, а не противоречие.
MAX_EXCLUSIVE = 4
_NO_BEFORE = re.compile(r"(?<!\p{L})(?:без|нет)\s+(\p{L}+)", re.IGNORECASE)
_YES_NO = {"да", "нет"}
# Значения, которые в тексте бывают не о поле («нет в наличии», «отсутствует запах»): считаются,
# только если рядом в тексте названо само поле.
_VAGUE_VALUES = set(tokens("отсутствует есть имеется другое иное прочее"))
_FIELD_GENERIC = set(tokens("вид тип наличие класс категория требования изделия товара"))
RULE_CONSISTENT = "характеристика описана без противоречий"


# Кусок описания «Метка: значение»: до «;», перевода строки, «|» или конца предложения.
_SEGMENT = re.compile(r"(?:[^;\n|.]|\.(?!\s+\p{Lu}))+")


def _about(spec_stems: set[str], label: str) -> bool:
    """Метка говорит об этом поле: её слова — слова имени поля (кроме «вид», «наличие»)."""
    said = set(tokens(label)) - _FIELD_GENERIC
    return bool(said) and said <= spec_stems


# Поля, у которых у товара ровно одно значение и в наименовании: пол, назначение по полу.
_ONE_PER_ITEM = set(tokens("назначение половой пол"))
_WORD_YES = re.compile(r"(?<!\p{L})да(?!\p{L})", re.IGNORECASE)
_WORD_NO = re.compile(r"(?<!\p{L})нет(?!\p{L})", re.IGNORECASE)


def _mentioned(spec: CharacteristicSpec, text: str) -> list[str]:
    """Значения перечня, названные в этом куске текста (не после «без»/«нет»), без вложенных:
    «Сталь» не считается рядом с «Нержавеющая сталь»."""
    have = set(tokens(text))
    negated = {s for m in _NO_BEFORE.finditer(text) for s in tokens(m[1])}
    said: list[str] = []
    for v in spec.values:
        stems = set(tokens(v))
        if len(v) < 3 or any(ch.isdigit() for ch in v) or fold(v) in _YES_NO:
            continue
        if not stems or not stems <= have or stems & negated:
            continue
        if any(set(tokens(s)) > stems or _same_meaning(s, v) for s in said):
            continue
        said = [s for s in said if not set(tokens(s)) < stems] + [v]
    if len(said) == 2 and _together(text, said):
        return []
    return said


_PREPOSITIONS = set(tokens("на для из с со в во по без"))


def _same_meaning(a: str, b: str) -> bool:
    """Одно значение, записанное в перечне дважды: «Сталь оцинкованная» и «оцинкованная сталь»,
    «На мальчика» и «Для мальчиков», «Демисезон» и «Демисезонная»."""
    x, y = set(tokens(a)) - _PREPOSITIONS, set(tokens(b)) - _PREPOSITIONS
    if x == y:
        return True
    if len(x) == len(y) == 1:
        s, u = next(iter(x)), next(iter(y))
        return s.startswith(u) or u.startswith(s) or s[:5] == u[:5]
    return False


# «мужской/женский», «куртка + брюки», «фуфайка и кальсоны» — оба значения сразу, а не спор.
_JOINED = r"\p{L}*\s*(?:/|\+|(?<!\p{L})и(?!\p{L}))\s*"


def _together(text: str, said: list[str]) -> bool:
    a, b = (tokens(s)[0] for s in said)
    joined = rf"{a}{_JOINED}{b}|{b}{_JOINED}{a}"
    return re.search(joined, fold(text), re.IGNORECASE) is not None


def _contradictions(card: CardIn, position: Position) -> list[Issue]:
    """Текст говорит о поле два взаимоисключающих значения: «муржская … женская» в наименовании,
    «Капюшон: отсутствует, съёмный», «Покрытие: да, без покрытия». Значения ищутся только там,
    где речь об этом поле: в наименовании и после его метки, — «ЛДСП» из «Материал: ЛДСП» не
    спорит с «деревянной» банкеткой. Модуль не выбирает за поставщика — просит уточнить."""
    labelled = [
        (label, value)
        for m in _SEGMENT.finditer(card.description or "")
        for label, colon, value in [m[0].partition(":")]
        if colon
    ]
    out: list[Issue] = []
    for spec in position.characteristics:
        if spec.group == "general" or spec.kind != "qualitative":
            continue
        if not 2 <= len(spec.values) <= MAX_EXCLUSIVE:
            continue
        own = set(tokens(spec.name)) - _FIELD_GENERIC
        parts = [value for label, value in labelled if own and _about(own, label)]
        values = {fold(v) for v in spec.values}
        if values <= _YES_NO:
            # «Водонепроницаемое покрытие: да, без водонепроницаемого покрытия»
            for v in parts:
                negated = {s for m in _NO_BEFORE.finditer(v) for s in tokens(m[1])}
                no = bool(own & negated) or _WORD_NO.search(v) is not None
                if _WORD_YES.search(v) and no:
                    out.append(_contradiction(spec, ["да", "нет"]))
                    break
            continue
        # Наименование — только о поле, где два значения сразу не бывают (пол: «муржская …
        # женская»); «белый/серый», «металл/пластик» в наименовании — разные части товара.
        sources = [card.name, *parts] if own & _ONE_PER_ITEM else parts
        for source in sources:
            said = _mentioned(spec, source)
            if len(said) >= 2:
                out.append(_contradiction(spec, said))
                break
    return out


def _contradiction(spec: CharacteristicSpec, said: list[str]) -> Issue:
    listed = " и ".join(f"«{s}»" for s in said)
    return Issue(
        field=f"characteristics.{spec.name}",
        kind="mismatch",
        severity="warn",
        message=f"В тексте сразу {listed} для «{spec.name}» — значение одно, уточните.",
        why="Противоречие в описании поставщика: модуль не выбирает значение за него.",
        rule=RULE_CONSISTENT,
    )


def detect(card: CardIn, position: Position | None, ktru: Position | None = None) -> list[Issue]:
    """Замечания карточки по категории `position`; `ktru` — выбранная позиция КТРУ, эталон."""
    issues = (
        profanity_issues(card) + hygiene_issues(card, position) + _moderation_issues(card, position)
    )
    if position is not None:
        issues.extend(_spec_issues(card, position, ktru))
        issues.extend(_contradictions(card, position))
    return issues


def verdict(issues: list[Issue]) -> Literal["ready", "needs_fix"]:
    return "needs_fix" if any(i.severity == "block" for i in issues) else "ready"
