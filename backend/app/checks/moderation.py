"""Движок правил модерации СТЕ — перенос `frontend/prototype/rules.ts` один в один.

Правила взяты из «Инструкции по созданию оферты и СТЕ» портала (04.06.2026): не меньше
4 характеристик; единица в отдельном поле; значение не повторяет название; в наименовании нет
значения и единицы. Пятое
правило — значение только из перечня позиции КТРУ (ПП РФ № 145).

Поведение сверено с TS-версией на 13 тыс. вызовов (`tests/fixtures/rules_golden.json.gz`),
поэтому сохранены особенности JavaScript: `\\d` только ASCII, `$` — конец строки, `Number()`
для чисел, `trim()`.
Модуль `regex`, а не `re`: в наименованиях нужен lookbehind переменной длины.
"""

import math
from typing import Literal

import regex as re
from pydantic import BaseModel


class Char(BaseModel):
    name: str
    value: str
    unit: str | None = None


class Card(BaseModel):
    name: str
    chars: list[Char]
    allowed: dict[str, list[str]] | None = None


RuleId = Literal[
    "min-chars",
    "unit-in-char-name",
    "value-repeats-name",
    "value-unit-in-title",
    "value-not-allowed",
]
Level = Literal["block", "normalize", "advice"]


class Finding(BaseModel):
    rule: RuleId
    level: Level
    text: str
    char: str | None = None
    suggestion: str | None = None


class Verdict(BaseModel):
    returns: bool
    reasons: list[Finding]


class Change(BaseModel):
    kind: Literal["title-to-char", "unit-from-name"]
    text: str


RULE_TEXT: dict[str, str] = {
    "min-chars": "не меньше 4 характеристик",
    "unit-in-char-name": "единица измерения в отдельном поле",
    "value-repeats-name": "значение не повторяет название характеристики",
    "value-unit-in-title": "в наименовании нет значения и единицы",
    "value-not-allowed": "значение только из перечня позиции КТРУ",
}

# Нарушение правила — так оно звучит в причине возврата. RULE_TEXT — само правило.
RULE_PROBLEM: dict[str, str] = {
    "min-chars": "меньше 4 характеристик",
    "unit-in-char-name": "единица в названии характеристики",
    "value-repeats-name": "значение повторяет название характеристики",
    "value-unit-in-title": "значение с единицей в наименовании",
    "value-not-allowed": "значения нет в перечне позиции КТРУ",
}

MIN_CHARS = 4

# Пробельные символы String.prototype.trim(): \s плюс BOM.
_TRIM = re.compile(r"^[\s﻿]+|[\s﻿]+$")


def _trim(s: str) -> str:
    return _TRIM.sub("", s)


def _norm(s: str) -> str:
    return _trim(s).lower().replace("ё", "е")


def _js_number(s: str) -> float:
    """`Number(s)` из JavaScript: пустая строка — 0, мусор — NaN."""
    t = _trim(s)
    if t == "":
        return 0.0
    if re.fullmatch(r"0[xX][0-9a-fA-F]+", t):
        return float(int(t, 16))
    if re.fullmatch(r"0[oO][0-7]+", t):
        return float(int(t[2:], 8))
    if re.fullmatch(r"0[bB][01]+", t):
        return float(int(t[2:], 2))
    if re.fullmatch(r"[+-]?(?:Infinity|(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)", t):
        return float(t.replace("Infinity", "inf"))
    return math.nan


def _to_number(s: str) -> float:
    return _js_number(re.sub(r"\s", "", s).replace(",", ".", 1))


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        diag = prev[0]
        prev[0] = i
        for j in range(1, len(b) + 1):
            up = prev[j]
            prev[j] = min(prev[j] + 1, prev[j - 1] + 1, diag + (0 if a[i - 1] == b[j - 1] else 1))
            diag = up
    return prev[len(b)]


def nearest_allowed(value: str, allowed: list[str]) -> tuple[str, int] | None:
    """Ближайшее допустимое значение: правка не больше 2 символов и трети длины, иначе None."""
    v = _norm(value)
    best: tuple[str, int] | None = None
    for option in allowed:
        o = _norm(option)
        # Длины разнятся больше чем на 2 — правок больше 2, ответ не меняется; а перечни бывают из
        # длинных текстов, и полный Левенштейн по ним стоил секунды на карточку.
        if abs(len(v) - len(o)) > 2:
            continue
        d = levenshtein(v, o)
        if best is None or d < best[1]:
            best = (option, d)
    if best is None:
        return None
    return best if best[1] == 0 or (best[1] <= 2 and best[1] <= len(v) // 3) else None


# Единицы в наименовании: длинные раньше коротких, иначе «мкм» распадётся на «м», «листов» на «л».
# Латинских W и V нет намеренно: индексы шин («94W»), вязкость масла («5W-40»), тип сложения («V»).
TITLE_UNITS = [
    "л.с.", "л. с.", "л.с", "мкм", "мм", "см", "км", "м²", "м2", "м3", "мл", "мг", "мАч", "mAh",
    "кВт",
    "квт", "kW", "Вт", "вт", "ГБ", "Гб", "GB", "ТБ", "Тб", "TB", "кг", "Ач", "грамм", "гр", "штуки",
    "штук", "шт", "листов", "листа", "лист", "упак", "м", "л", "г", "В", "%",
]  # fmt: skip
# Сокращения, полные слова, регистр и латиница приводятся к одной единице.
UNIT_ALIAS = {
    "грамм": "г", "гр": "г", "штуки": "шт", "штук": "шт", "листов": "лист", "листа": "лист",
    "л. с.": "л.с.", "л.с": "л.с.", "mAh": "мАч", "kW": "кВт", "квт": "кВт", "вт": "Вт", "GB": "ГБ",
    "TB": "ТБ",
}  # fmt: skip

LETTER = "а-яёА-ЯЁa-zA-Z"
_NBSP = " "
# Число; разряды — через неразрывный пробел или круглые тысячи через обычный («1 000 листов»).
# У числа, приклеенного к букве, разрядов нет («А4 500л»). С нуля не начинается, кроме дроби «0,5».
NUM = (
    rf"(?:(?<![{LETTER}])[0-9]{{1,3}}(?:(?:{_NBSP}[0-9]{{3}})+|(?: 000)+)|(?!0[0-9])[0-9]+)"
    r"(?:[.,][0-9]+)?"
)
# Диапазон или размер («1-4мм», «48x25 мм», «50/194см») — одно значение.
VALUE = rf"(?:(?<![{LETTER}]){NUM}(?:\s*[-xх×*/]\s*{NUM})+|{NUM})"
# «до/от» — граница, «по» — на единицу, «на» — вместимость: не переносим. «из» уходит со значением.
BOUND = rf"(?:(?<![{LETTER}])(до|от|по|на|из)\s+)?"
# Единица через «/» — составная величина: «г/м2», «шт/уп», «л/имп».
PER = rf"(\.?\/[{LETTER}0-9.²³]+)?"
# Точка сокращения после единицы уходит вместе со значением.
DOT_OR_END = rf"(?:\.(?=\s|\Z|[,;:)А-ЯЁA-Z])|(?![{LETTER}0-9]))"
PACK = (
    r"(?:\s+в\s+(?:упаковке|упак\.?|уп\.?|пачке|коробке|наборе|комплекте)"
    rf"(?![{LETTER}]))?"
)
TITLE_RE = re.compile(
    rf"(?<![0-9])(?<![0-9][.,])(?<!(?:^|[\s/(])[{LETTER}]{{1,2}}\/){BOUND}({VALUE})\s*"
    rf"({'|'.join(re.escape(u) for u in TITLE_UNITS)}){PER}{DOT_OR_END}{PACK}"
)
# Слово, которое вводит значение, уходит вместе с ним: «объем 1050л», «КОМПЛЕКТ 5 шт.».
LABEL_RE = re.compile(
    rf"(?:^|(?<=[\s,(]))((?:объ[её]м|вместимость|масса|вес|[её]мкость)(?:\s+[{LETTER}]+)?|расф\.?"
    r"|расфасовка|фасовка|набор|комплект|упаковка|(?:в\s+)?упак\.?|(?:в\s+)?уп\.?)\s*[-:–]?\s*\Z",
    re.IGNORECASE,
)
SINGLE_NUM = re.compile(rf"^{NUM}\Z")
# Метка-существительное с согласованным прилагательным — часть названия товара: «Подарочный набор».
PRODUCT_LABEL_AGREEMENT = {
    "набор": re.compile(r"(ый|ий|ой)\Z", re.IGNORECASE),
    "комплект": re.compile(r"(ый|ий|ой)\Z", re.IGNORECASE),
    "упаковка": re.compile(r"(ая|яя)\Z", re.IGNORECASE),
}
ADJECTIVE = re.compile(r"(ый|ий|ой|ая|яя|ое|ее|ые|ие|ую|юю)\Z", re.IGNORECASE)
_PREV_WORD = re.compile(rf"([{LETTER}-]+)\s+\Z")


def _label_start(title: str, value_start: int) -> int | None:
    """Начало метки перед значением или None, если метки нет или она часть названия товара."""
    label = LABEL_RE.search(title[:value_start])
    if not label:
        return None
    before = title[: label.start()]
    if _trim(before) == "":
        return None  # «Набор 12 шт» без слова «Набор» потерял бы товар
    words = re.split(r"\s+", _trim(label[1]))
    first, second = words[0], (words[1] if len(words) > 1 else None)
    if second and re.match(r"^масса\Z", first, re.IGNORECASE) and ADJECTIVE.search(second):
        return None
    prev = _PREV_WORD.search(before)
    prev_word = prev[1] if prev else None
    agreement = PRODUCT_LABEL_AGREEMENT.get(first.lower())
    if agreement and prev_word and agreement.search(prev_word):
        return None
    return label.start()


class _TitleValue(BaseModel):
    text: str
    start: int
    end: int
    at: int  # начало самого числа
    num: str
    unit: str
    compound: bool


_YEAR = re.compile(r"^(19|20)[0-9][0-9]\Z")


def _find_title_values(title: str) -> list[_TitleValue]:
    out: list[_TitleValue] = []
    for m in TITLE_RE.finditer(title):
        text, bound, num, raw_unit, per = m[0], m[1], m[2], m[3], m[4]
        # «2027 г.» — год, а не граммы.
        if raw_unit == "г" and all(_YEAR.match(n) for n in re.split(r"[^0-9.,]+", num)):
            continue
        end = m.start() + len(text)
        num_start = m.start() + text.find(num)
        before = title[:num_start]
        after = title[end:]
        compound = (
            (bool(bound) and bound != "из")
            or (bound == "из" and bool(re.match(r"^\s*[а-яёa-z]", after, re.IGNORECASE)))
            or bool(per)
            or not SINGLE_NUM.match(num)
            or bool(re.search(rf"[{LETTER}]\Z", before))
            or bool(re.search(r"(?:[*×]|[0-9]\s*[xхXХ])\s*\Z", before))
            or bool(re.match(r"^\s*(?:[*×]|[xхXХ]\s*[0-9])", after))
        )
        label = _label_start(title, m.start())
        start = label if label is not None else m.start()
        out.append(
            _TitleValue(
                text=title[start:end],
                start=start,
                end=end,
                at=num_start,
                num=num,
                unit=UNIT_ALIAS.get(raw_unit, raw_unit),
                compound=compound,
            )
        )
    return out


def title_value_units(title: str) -> list[str]:
    return [v.text for v in _find_title_values(title)]


CHAR_UNITS = {
    "мкм", "мм", "см", "м", "км", "г", "кг", "т", "мл", "л", "шт", "%", "г/м2", "г/м²", "м2", "м²",
    "м3", "в", "вт", "квт", "мач", "гб", "тб", "мин", "ч", "дб", "лм",
}  # fmt: skip
_NAME_UNIT = re.compile(r",\s*([^,]+?)\s*\Z")


def unit_in_char_name(name: str) -> str | None:
    """Единица, записанная в названии характеристики через запятую («Толщина, мкм»), или None."""
    m = _NAME_UNIT.search(name)
    if not m:
        return None
    unit = re.sub(r"\.\Z", "", m[1])  # «г.» — точка сокращения, не часть единицы
    return unit if _norm(unit) in CHAR_UNITS else None


def plural(n: int, one: str, few: str, many: str) -> str:
    n10 = n % 10
    n100 = n % 100
    if n10 == 1 and n100 != 11:
        return one
    if 2 <= n10 <= 4 and (n100 < 12 or n100 > 14):
        return few
    return many


def check_card(card: Card) -> list[Finding]:
    found: list[Finding] = []
    n = len(card.chars)
    if n < MIN_CHARS:
        word = plural(n, "характеристика", "характеристики", "характеристик")
        found.append(
            Finding(
                rule="min-chars",
                level="block",
                text=f"В карточке {n} {word}, нужно не меньше {MIN_CHARS}.",
            )
        )
    for c in card.chars:
        unit = unit_in_char_name(c.name)
        if unit:
            base = _trim(c.name[: c.name.rfind(",")])
            found.append(
                Finding(
                    rule="unit-in-char-name",
                    level="normalize",
                    char=c.name,
                    text=f"Единица «{unit}» записана в названии «{c.name}».",
                    suggestion=f"«{base}» = {c.value} {unit}",
                )
            )
        if _norm(c.value) != "" and _norm(c.value) == _norm(c.name):
            found.append(
                Finding(
                    rule="value-repeats-name",
                    level="normalize",
                    char=c.name,
                    text=f"Значение «{c.value}» повторяет название характеристики.",
                )
            )
        allowed = card.allowed.get(c.name) if card.allowed else None
        if allowed is not None and not any(_norm(a) == _norm(c.value) for a in allowed):
            near = nearest_allowed(c.value, allowed)
            found.append(
                Finding(
                    rule="value-not-allowed",
                    level="block",
                    char=c.name,
                    text=f"Значения «{c.value}» нет в перечне: {', '.join(allowed)}.",
                    suggestion=near[0] if near and near[1] > 0 else None,
                )
            )
    in_title = title_value_units(card.name)
    if in_title:
        listed = ", ".join(f"«{t}»" for t in in_title)
        found.append(
            Finding(rule="value-unit-in-title", level="normalize", text=f"В наименовании {listed}.")
        )
    return found


def moderation_verdict(findings: list[Finding]) -> Verdict:
    """Вернёт ли модератор карточку и по каким причинам: всё, кроме рекомендаций."""
    reasons = [f for f in findings if f.level != "advice"]
    return Verdict(returns=len(reasons) > 0, reasons=reasons)


# Какую характеристику образует единица из наименования.
# Неоднозначные (мм, см, %, упак) не переносим.
UNIT_CHAR = {
    "л": "Объём", "мл": "Объём", "мкм": "Толщина", "шт": "Количество в упаковке", "кг": "Масса",
    "г": "Масса", "В": "Напряжение", "Вт": "Мощность", "кВт": "Мощность", "мАч": "Ёмкость",
    "Ач": "Ёмкость", "ГБ": "Объём памяти", "Гб": "Объём памяти", "лист": "Количество листов",
}  # fmt: skip
# Мощность и напряжение у принадлежности («Мешок-пылесборник … 1400 Вт») — характеристики
# устройства.
DEVICE_ONLY = {"Мощность", "Напряжение"}
# Принадлежность — по слову целиком: «Гайковерт» и «Винтоверт» — инструменты, а не гайка и винт.
ACCESSORY_WORDS = {
    "мешок", "мешки", "мешков", "пылесборник", "фильтр", "болт", "винт", "гайка", "запчасть",
    "ремень",
    "щетка", "насадка", "чехол", "переходник", "держатель", "шланг", "нож", "диск",
}  # fmt: skip
# Товар со своей мощностью: «Блок питания для ноутбука 65 Вт», «Насос для воды 750 Вт».
OWN_POWER_HEAD = re.compile(
    r"^(блок|аккумулятор|батаре|зарядн|адаптер|лампа|светильник|пылесос|насос|обогреватель|фен|чайник"
    r"|станок|дрель|перфоратор|шуруповерт|гайковерт|винтоверт|пила|миксер|блендер|утюг|холодильник"
    r"|плита|печь|кофеварк|машина|компрессор|генератор|двигатель|электродвигатель)",
    re.IGNORECASE,
)


def _first_word(text: str) -> str:
    return re.split(r"\s+", text)[0]


def _device_attribute(target_name: str, title: str, at: int) -> bool:
    """Мощность или напряжение устройства, к которому товар относится, а не самого товара."""
    if target_name not in DEVICE_ONLY:
        return False
    head = _first_word(_norm(title))
    if OWN_POWER_HEAD.search(head):
        return False
    return any(w in ACCESSORY_WORDS for w in head.split("-")) or bool(
        re.search(r"(^|\s)для\s", title[:at], re.IGNORECASE)
    )


# Основы слов, по которым характеристика карточки считается той же.
NAME_ALIASES = {
    "Масса": ["масса", "вес", "лп", "плотность"],
    "Объём": ["объем", "вместимость", "емкость", "лп"],
    "Ёмкость": ["емкость"],
    "Объём памяти": ["объем", "память", "емкость"],
    "Количество листов": ["лист", "салфет"],
    "Вместимость": ["вместимость"],
}


def _name_matches(char_name: str, stems: list[str]) -> bool:
    return any(
        w != "" and any(w.startswith(s) for s in stems)
        for w in re.split(r"[^a-zа-я0-9]+", _norm(char_name))
    )


GENERIC_COUNT = re.compile(r"^(шт|штук|предмет|издели|единиц|позиц)")
PACK_PLACE = re.compile(
    r"^(?:во?\s+(?:потребительской\s+)?(?:упаковке|наборе|комплекте|пачке|блистере|коробке))?\Z"
)
_COUNT_NAME = re.compile(r"^количество(?:\s+(?!во?\s)([a-zа-я.]+))?\s*(.*)\Z")


def _pack_count_matches(char_name: str, title: str) -> bool:
    """«Количество в упаковке», «Количество штук …» или «Количество <товара> …» из наименования."""
    name = re.sub(r"^кол-во(?=\s|\Z)", "количество", _norm(char_name), count=1)
    name = re.sub(r"[\s:.;,]+\Z", "", name, count=1)
    m = _COUNT_NAME.match(name)
    if not m or not PACK_PLACE.match(_trim(m[2])):
        return False
    noun = m[1]
    if not noun or GENERIC_COUNT.match(noun):
        return True
    # Первые четыре буквы: «файлов» и «Файл», «тетрадей» и «Тетради».
    head = next((w for w in re.split(r"[^a-zа-я]+", _norm(title)) if w), "")
    return head[:4] == noun[:4]


# «Комплектация: 10 штук в упаковке», «Состав: …» — тоже про количество, если в тексте штуки.
COMPOSITION_NAME = re.compile(r"^(комплектация|комплект|состав)")

# Единицы портала: «Л; ДМ3», «см[3*]; мл», «Гбайт». Берём последнюю часть.
PORTAL_UNIT = {
    "дм3": "л", "см[3*]": "мл", "см3": "мл", "мм[3*]": "мм3", "литр": "л", "литров": "л",
    "миллилитр": "мл", "миллилитров": "мл", "гбайт": "гб", "миллиампер в час": "мач",
    "миллиампер-час": "мач", "миллиампер": "мач", "ампер-час": "ач", "ампер в час": "ач",
    "а.ч": "ач",
    "грамм": "г", "гр": "г", "килограмм": "кг", "штука": "шт", "штук": "шт", "листов": "лист",
    "листа": "лист", "ватт": "вт", "киловатт": "квт",
}  # fmt: skip


def _canon_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    parts = [p for p in (_norm(u) for u in unit.split(";")) if p]
    if not parts:
        return None
    last = parts[-1]
    if last == "л.":
        return "лист"  # портал пишет «л.» в смысле листов: «Вместимость папки = 300 [л.]»
    bare = re.sub(r"\.\Z", "", last)
    return PORTAL_UNIT.get(bare, bare)


# Величина и множитель к её базовой единице. Длину не берём: ширина, длина и толщина — разные.
SCALE: dict[str, tuple[str, float]] = {
    "мг": ("масса", 1e-3),
    "г": ("масса", 1),
    "кг": ("масса", 1e3),
    "т": ("масса", 1e6),
    "мм3": ("объём", 1e-3),
    "мл": ("объём", 1),
    "л": ("объём", 1e3),
    "вт": ("мощность", 1),
    "квт": ("мощность", 1e3),
    # «4400mAh» и «Емкость = 4400 [А.ч]» — разные значения: ошибка в 1000 раз, решит человек.
    "мач": ("заряд", 1),
    "ач": ("заряд", 1e3),
}


def _scale_of(unit: str | None) -> tuple[str, float] | None:
    u = _canon_unit(unit)
    return SCALE.get(u) if u else None


def _same_kind(a: str | None, b: str | None) -> bool:
    if _canon_unit(a) == _canon_unit(b):
        return True
    sa, sb = _scale_of(a), _scale_of(b)
    return sa is not None and sb is not None and sa[0] == sb[0]


COUNT_UNITS = {"шт", "лист"}
KNOWN_UNITS = {
    *SCALE, *COUNT_UNITS, "г/м2", "г/м²", "%", "мм", "см", "м", "км", "мкм", "в", "гб", "тб", "мин",
    "ч", "дб", "лм",
}  # fmt: skip


def _compatible(char_unit: str | None, target_unit: str) -> bool:
    """Может ли характеристика с такой единицей быть той же величиной. Без единицы — может."""
    a = _canon_unit(char_unit)
    b = _canon_unit(target_unit)
    if not a or a == b or a not in KNOWN_UNITS:
        return True
    sa = SCALE.get(a)
    sb = SCALE.get(b) if b else None
    if sa and sb:
        return sa[0] == sb[0]
    return a in COUNT_UNITS and b is not None and b in COUNT_UNITS


def _is_tolerance(c: Char) -> bool:
    """«+/- 0,1» — допуск, а не значение."""
    return bool(re.match(r"^\s*(±|\+\/-|\+-)", c.value))


_VALUE_WITH_UNIT = re.compile(
    r"^([0-9][0-9\s]*(?:[.,][0-9]+)?)\s*([a-zа-яё%][a-zа-яё%./²³]*)(?:\s.*)?\Z", re.IGNORECASE
)


def _parse_value(c: Char) -> tuple[str, str | None] | None:
    """Число и единица значения; без числа — None."""
    v = _trim(c.value)
    if v == "":
        return None
    if not math.isnan(_to_number(v)):
        return v, c.unit
    m = _VALUE_WITH_UNIT.match(v)
    return (m[1], m[2]) if m else None


def _same_quantity(a: str, a_unit: str | None, b: str, b_unit: str) -> bool:
    x, y = _to_number(a), _to_number(b)
    if math.isnan(x) or math.isnan(y):
        return False
    sa, sb = _scale_of(a_unit), _scale_of(b_unit)
    if not sa or not sb:
        return x == y
    if sa[0] != sb[0]:
        return False
    p, q = x * sa[1], y * sb[1]
    return abs(p - q) <= 1e-9 * max(1, abs(q))


# «л» у бумажных изделий — листы, у папок — вместимость в листах, у остального — литры.
PAPER_RE = re.compile(
    r"(бумаг|тетрад|салфет|блокнот|альбом|книг|журнал|ежедневник|полотенц|стикер|бланк|открытк|календар"
    r"|папк|блок|этикет|скоросшив|конверт)",
    re.IGNORECASE,
)
LITRE_WORD = re.compile(
    r"^(корзин|контейнер|ведр|бак|канистр|урн|мешк|мешок|пакет|бутыл|флакон|термос|чайник|кастрюл|емкост"
    r"|ёмкост|бочк|таз|лейк|холодильник|морозильник|бидон|кувшин|графин|пылесос|мусорн)",
    re.IGNORECASE,
)
CAPACITY_HEAD = re.compile(r"^(папк|скоросшив|регистратор)", re.IGNORECASE)


def _is_capacity(title: str, head: str) -> bool:
    return bool(CAPACITY_HEAD.match(head)) or (
        bool(re.match(r"^лоток", head, re.IGNORECASE))
        and bool(re.search(r"для\s+(бумаг|документ)", title, re.IGNORECASE))
    )


def _litre_or_sheets(title: str, at: int) -> tuple[str, str] | None:
    """Литры, листы или вместимость в листах; None — неоднозначно, не переносим."""
    head = _first_word(_trim(title))
    words = [w for w in re.split(r"[\s,(]+", title[:at]) if w]
    prev = words[-1] if words else ""
    if LITRE_WORD.match(head) or LITRE_WORD.match(prev):
        return "Объём", "л"
    if _is_capacity(title, head):
        return "Вместимость", "лист"
    if re.match(r"^короб", head, re.IGNORECASE) and re.search(r"архив", title, re.IGNORECASE):
        return None
    if PAPER_RE.search(re.sub(r"(^|\s)для\s+[а-яё]+", " ", title, flags=re.IGNORECASE)):
        return "Количество листов", "лист"
    return "Объём", "л"


def _target_char(unit: str, title: str, at: int) -> tuple[str, str] | None:
    if unit == "л":
        return _litre_or_sheets(title, at)
    # «Папка-регистратор …, 500 листов» — та же вместимость, что «500л».
    if unit == "лист" and _is_capacity(title, _first_word(_trim(title))):
        return "Вместимость", "лист"
    name = UNIT_CHAR.get(unit)
    return (name, unit) if name else None


# Метка места вырезания: чистка трогает только соседство меток.
CUT = "\x01"
CUT_RULES: list[tuple[re.Pattern[str], str, int]] = [
    (re.compile(rf"{CUT}[\s,;:{CUT}]*{CUT}"), CUT, 0),  # подряд вырезанные значения — одно место
    # «уп./30 шт», «пояском/10 штук» — разделитель уходит
    (re.compile(rf"(?:\s*(?<![{LETTER}])(?:уп|упак)\.?)?\s*\/\s*{CUT}"), CUT, 0),
    (re.compile(rf"{CUT}\s*\/(?=\s)"), CUT, 0),  # «100 шт/ белые» — разделитель после реза
    # висящий предлог
    (re.compile(rf"\s+(?:по|из|в|на|с|со|для|до|от|и)\s*{CUT}(?=\s*(?:[,;:()]|\Z))"), CUT, 0),
    (re.compile(rf"\s+[-–—]\s*{CUT}(?=\s*(?:[,;:()]|\Z))"), CUT, 0),  # висящее тире
    (re.compile(rf"\s*\(\s*{CUT}[\s.,;:]*\)"), "", 0),  # пустые скобки
    (re.compile(rf"\(\s*{CUT}[\s.,;:]*"), "(", 0),  # «(» сразу после реза
    (re.compile(rf"[\s,;:]*{CUT}\s*\)"), ")", 0),  # «, )» перед закрывающей
    (re.compile(rf"([.,;:])\s*{CUT}\s*[.,;:]\s*"), r"\1 ", 0),  # два знака через рез — первый
    (re.compile(rf"\s*{CUT}\s*([.,;:])"), r"\1", 0),  # пробел перед знаком после реза
    (re.compile(rf"[\s,;:]*{CUT}[\s,;:]*\Z"), "", 1),  # рез в конце с хвостовой запятой
    (re.compile(rf"\s*{CUT}\s*"), " ", 0),  # рез между словами
]


def _cut_values(title: str, remove: list[_TitleValue]) -> str:
    if not remove:
        return title
    t = title
    for v in sorted(remove, key=lambda v: v.start, reverse=True):
        t = t[: v.start] + CUT + t[v.end :]
    for pattern, repl, count in CUT_RULES:
        t = pattern.sub(repl, t, count=count)
    return _trim(t)


def extract_from_title(card: Card) -> tuple[str, list[Char]]:
    """Переносит значения с единицей из наименования в характеристики.

    Есть такая характеристика с тем же значением — только чистит наименование; с другим — не трогает
    ничего (конфликт). Не переносятся диапазоны, размеры, границы («до 100 листов»), значения «на
    единицу» («по 50 мл»), составные единицы («г/м2») и две величины одного рода в одном
    наименовании.
    """
    added: list[Char] = []
    remove: list[_TitleValue] = []
    values = []
    for v in _find_title_values(card.name):
        if v.compound:
            continue
        target = _target_char(v.unit, card.name, v.at)
        if target is not None:
            values.append((v, target))
    per_target: dict[str, int] = {}
    for _, (t_name, _) in values:
        per_target[t_name] = per_target.get(t_name, 0) + 1
    for v, (t_name, t_unit) in values:
        if per_target[t_name] > 1:
            continue  # «705 Вт 220 Вт» — какое из значений, решит человек
        stems = NAME_ALIASES.get(t_name, [_norm(t_name)])
        value = re.sub(r"\s", "", v.num)
        is_pack = t_name == "Количество в упаковке"

        def in_pieces(c: Char) -> bool:
            parsed = _parse_value(c)
            unit = parsed[1] if parsed is not None and parsed[1] is not None else c.unit
            return _canon_unit(unit) == "шт"

        def matches_target(
            c: Char, t_unit: str = t_unit, stems: list[str] = stems, is_pack: bool = is_pack
        ) -> bool:
            if is_pack:
                return (_pack_count_matches(c.name, card.name) and _compatible(c.unit, t_unit)) or (
                    bool(COMPOSITION_NAME.match(_norm(c.name))) and in_pieces(c)
                )
            return bool(c.unit and _same_kind(c.unit, t_unit)) or (
                _name_matches(c.name, stems) and _compatible(c.unit, t_unit)
            )

        matches = [c for c in card.chars if matches_target(c)]
        confirmers = (
            matches + [c for c in card.chars if all(c is not m for m in matches) and in_pieces(c)]
            if is_pack
            else matches
        )
        parsed = [p for p in (_parse_value(c) for c in confirmers) if p is not None]
        blocks = any(
            _parse_value(c) is not None
            or (c.unit is not None and _same_kind(c.unit, t_unit) and not _is_tolerance(c))
            for c in matches
        )
        same_name = any(_norm(c.name) == _norm(t_name) for c in card.chars)
        if any(_same_quantity(p[0], p[1], value, t_unit) for p in parsed):
            remove.append(v)
        elif not blocks and not same_name and not _device_attribute(t_name, card.name, v.at):
            added.append(Char(name=t_name, value=value, unit=t_unit))
            remove.append(v)
    return _cut_values(card.name, remove), added


def auto_fix(card: Card) -> tuple[Card, list[Change]]:
    """Автоисправление без решения человека: единица из названия характеристики в поле, значения с
    единицей из наименования в характеристики. Единицы выносятся первыми. Вход не изменяется."""
    unit_changes: list[Change] = []
    chars: list[Char] = []
    for c in card.chars:
        unit = unit_in_char_name(c.name)
        if not unit:
            chars.append(c)
            continue
        base = _trim(c.name[: c.name.rfind(",")])
        unit_changes.append(
            Change(kind="unit-from-name", text=f"«{c.name}» → «{base}», единица «{unit}»")
        )
        chars.append(
            c.model_copy(update={"name": base, "unit": c.unit if c.unit is not None else unit})
        )
    title, added = extract_from_title(card.model_copy(update={"chars": chars}))
    title_changes = [
        Change(kind="title-to-char", text=f"«{a.value} {a.unit}» из наименования → «{a.name}»")
        for a in added
    ]
    if title != card.name and not added:
        title_changes.append(
            Change(
                kind="title-to-char",
                text="из наименования убрано значение, которое уже есть в характеристиках",
            )
        )
    changes = title_changes + unit_changes
    if not changes:
        return card, changes
    return card.model_copy(update={"name": title, "chars": chars + added}), changes


# Публичные имена для извлечения характеристик из наименования (app/extract):
# то же поведение, что выше.
TitleValue = _TitleValue
find_title_values = _find_title_values
target_char = _target_char
