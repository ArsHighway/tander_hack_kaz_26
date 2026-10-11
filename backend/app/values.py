"""Значение характеристики против спецификации позиции: перечень, корзины, диапазон, единицы.

Правило проекта: значение принимается, только если оно есть в перечне или диапазоне позиции,
а единица — в каноне `app/ingest/units.py`. Остальное возвращается статусом с объяснением.
"""

import math
import re
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel

from app.catalog import CharacteristicSpec
from app.ingest.characteristics import parse_value, to_number
from app.ingest.units import DEGREE_WORDS, normalize_unit

FitStatus = Literal[
    "exact", "normalized", "bucket", "typo", "not_in_list", "unit_mismatch", "unparsed"
]

# Латинские двойники кириллицы (после casefold): в перечнях «A4» латиницей, поставщики пишут «А4».
_LATIN_TO_CYRILLIC = str.maketrans("abcehkmoptxy", "авсенкмортху")

# Множители к базовой единице внутри одной размерности; между размерностями не переводим.
_SCALE: dict[str, tuple[str, float]] = {
    "мкг": ("масса", 1e-6),
    "мг": ("масса", 1e-3),
    "г": ("масса", 1.0),
    "кг": ("масса", 1e3),
    "т": ("масса", 1e6),
    "мкл": ("объём", 1e-3),
    "мл": ("объём", 1.0),
    "л": ("объём", 1e3),
    "м3": ("объём", 1e6),
    "нм": ("длина", 1e-6),
    "мкм": ("длина", 1e-3),
    "мм": ("длина", 1.0),
    "см": ("длина", 10.0),
    "м": ("длина", 1e3),
    "км": ("длина", 1e6),
    # Счётные: «500 листов» для «Количество листов в пачке, шт» — та же величина.
    "шт": ("штуки", 1.0),
    "лист": ("штуки", 1.0),
    "мВт": ("мощность", 1e-3),
    "Вт": ("мощность", 1.0),
    "кВт": ("мощность", 1e3),
    "МВт": ("мощность", 1e6),
    "л.с.": ("мощность", 735.49875),
    "Вт·ч": ("энергия", 1.0),
    "кВт·ч": ("энергия", 1e3),
    "В·А": ("полная мощность", 1.0),
    "кВ·А": ("полная мощность", 1e3),
    "мм2": ("площадь", 1.0),
    "см2": ("площадь", 100.0),
    "м2": ("площадь", 1e6),
    "мл/мин": ("расход", 1e-3),
    "л/ч": ("расход", 1 / 60),
    "л/мин": ("расход", 1.0),
    "м3/ч": ("расход", 1000 / 60),
    "м/с": ("скорость", 1.0),
    "км/ч": ("скорость", 1 / 3.6),
    "кбит/с": ("скорость передачи", 1.0),
    "Мбит/с": ("скорость передачи", 1e3),
    "Гбит/с": ("скорость передачи", 1e6),
    "МБ/с": ("поток данных", 1.0),
    "ГБ/с": ("поток данных", 1e3),
    "бар": ("давление", 1e5),
    "атм": ("давление", 101325.0),
    "мм рт. ст.": ("давление", 133.322),
    "см вод. ст.": ("давление", 98.0665),
    "мм вод. ст.": ("давление", 9.80665),
    "ат": ("давление", 98066.5),
    "кгс/см2": ("давление", 98066.5),
    "Н": ("сила", 1.0),
    "кН": ("сила", 1e3),
    "В": ("напряжение", 1.0),
    "кВ": ("напряжение", 1e3),
    "мА": ("ток", 1e-3),
    "А": ("ток", 1.0),
    "кА": ("ток", 1e3),
    "Гц": ("частота", 1.0),
    "кГц": ("частота", 1e3),
    "МГц": ("частота", 1e6),
    "ГГц": ("частота", 1e9),
    "Па": ("давление", 1.0),
    "кПа": ("давление", 1e3),
    "МПа": ("давление", 1e6),
    "мА·ч": ("заряд", 1.0),
    "А·ч": ("заряд", 1e3),
    # Портал пишет «Гбайт» десятичными: «6 ТБ» — «6000 Гбайт».
    "кБ": ("данные", 1e-3),
    "МБ": ("данные", 1.0),
    "ГБ": ("данные", 1e3),
    "ТБ": ("данные", 1e6),
    # Срок: «гарантия 1,5 года» при «мес» — 18 мес. Сутки к месяцам не
    # переводим: месяц — не 30 суток, «365 дней» и «12 мес» — разные записи одного срока.
    "мес": ("срок", 1.0),
    "лет": ("срок", 12.0),
    "нед": ("время", 168.0),
    "сут": ("время", 24.0),
    "ч": ("время", 1.0),
    "мин": ("время", 1 / 60),
    "с": ("время", 1 / 3600),
}
_COUNT = {"шт", "лист"}
# Счётные единицы: листы и штуки — одна величина.
COUNT_UNITS = frozenset(_COUNT)

_MAX_LISTED = 8
MIN_TYPO_LEN = 5
# Короче — перестановку букв тоже не исправляем: «ПВД» и «ПДВ» могут быть разными кодами.
MIN_SWAP_LEN = 4
_NUM_THEN_UNIT = re.compile(r"^\s*([+-]?\d+(?:[.,]\d+)?)\s*(\S.*?)\s*$")


class Bucket(BaseModel):
    lo: float | None
    lo_incl: bool
    hi: float | None
    hi_incl: bool
    text: str

    def contains(self, x: float) -> bool:
        if self.lo is not None and (x < self.lo or (x == self.lo and not self.lo_incl)):
            return False
        return not (self.hi is not None and (x > self.hi or (x == self.hi and not self.hi_incl)))

    @property
    def is_point(self) -> bool:
        return self.lo is not None and self.lo == self.hi


class Fit(BaseModel):
    status: FitStatus
    value: str | None = None
    unit: str | None = None
    note: str | None = None
    # Число поставщика в единице позиции, когда `value` — текст корзины.
    supplier: str | None = None


# Сужение сверяет одни и те же значения перечней сотни тысяч раз на карточку
# (1,26 млн вызовов fold) — короткие строки перечней и имён запоминаются.
@lru_cache(maxsize=262_144)
def fold(text: str) -> str:
    """Ключ сравнения: регистр, ё→е, латинские двойники, пробелы и точка в конце."""
    folded = re.sub(r"\s+", " ", text.strip().rstrip(".")).casefold().replace("ё", "е")
    return folded.translate(_LATIN_TO_CYRILLIC)


def levenshtein(a: str, b: str, limit: int | None = None) -> int:
    """Расстояние правки; с `limit` — не дальше: больше предела — `limit + 1` (минимум строки
    таблицы не убывает, дальше считать незачем)."""
    if limit is not None and abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if limit is not None and min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


# Опечатка — не больше стольких правок (`nearest_allowed`); дальше Левенштейн не считается.
MAX_TYPO_EDITS = 2


def damerau_levenshtein(a: str, b: str, limit: int | None = None) -> int:
    """Расстояние с перестановкой соседних букв: «бумга» и «бумаг» — одна опечатка. С `limit` —
    не дальше: больше предела — `limit + 1` (две строки таблицы подряд выше предела — дальше
    только больше)."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if limit is not None and abs(la - lb) > limit:
        return limit + 1
    if la == 0 or lb == 0:
        return la + lb
    prev2: list[int] | None = None
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
            if prev2 is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if limit is not None and min(cur) > limit and min(prev) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


_DIGITS = re.compile(r"\d+")


def nearest_allowed(value: str, allowed: list[str]) -> tuple[str, int] | None:
    """Ближайшее значение перечня: совпадение или правка не больше 2 символов и трети длины.

    Перестановка соседних букв — одна правка («ЛДПС» — «ЛДСП», Дамерау — Левенштейн; у правил
    модерации портала своя копия на обычном Левенштейне).
    Расстояние считается не дальше двух правок: перечни бывают из длинных текстов (составы), и
    полный Левенштейн на каждый вариант сужения стоил до 194 с на карточку."""
    v = fold(value)
    digits = _DIGITS.findall(v)
    best: tuple[str, int] | None = None
    for option in allowed:
        d = damerau_levenshtein(v, fold(option), limit=MAX_TYPO_EDITS)
        # Другие цифры — другое значение, а не опечатка: «сталь У8» — не «сталь 45», «М16» — не
        # «М10».
        if d and _DIGITS.findall(fold(option)) != digits:
            continue
        if best is None or d < best[1]:
            best = (option, d)
    if best is None:
        return None
    if best[1] == 0:
        return best
    # Короткие коды («ПНД» и «ПВД», «XS» и «XL») — разные значения, а не опечатка; те же буквы
    # кода из четырёх и больше, переставленные соседние, — опечатка («ЛДПС» — «ЛДСП»).
    if len(v) < MIN_TYPO_LEN:
        swapped = len(v) >= MIN_SWAP_LEN and sorted(v) == sorted(fold(best[0]))
        return best if swapped and best[1] == 1 else None
    return best if best[1] <= MAX_TYPO_EDITS and best[1] <= len(v) // 3 else None


def parse_bucket(text: str) -> Bucket | None:
    value, _ = parse_value(text)
    if value.kind == "number" and value.number is not None:
        return Bucket(lo=value.number, lo_incl=True, hi=value.number, hi_incl=True, text=text)
    if value.kind == "range":
        return Bucket(
            lo=value.low,
            lo_incl=value.low_inclusive,
            hi=value.high,
            hi_incl=value.high_inclusive,
            text=text,
        )
    return None


def quantity(unit: str | None) -> str | None:
    """Величина единицы из таблицы пересчёта («кг» → «масса»); незнакомая единица — None."""
    found = _SCALE.get(unit) if unit else None
    return found[0] if found else None


def convert(value: float, from_unit: str, to_unit: str) -> float | None:
    if from_unit == to_unit:
        return value
    ratio = _ratio(from_unit, to_unit)
    return None if ratio is None else round(value * ratio, 9)


def _ratio(from_unit: str, to_unit: str) -> float | None:
    """Во сколько раз единица больше другой той же величины, без округления `convert`: «нм» к «км» —
    1e-12, а округлённый до 9 знаков пересчёт единицы дал бы 0."""
    src, dst = _SCALE.get(from_unit), _SCALE.get(to_unit)
    if src is None or dst is None or src[0] != dst[0]:
        return None
    return src[1] / dst[1]


def _fmt(x: float) -> str:
    return str(int(x)) if x == int(x) else f"{x:g}".replace(".", ",")


# Короткая запись — только в общепринятых единицах величины (не «0,001 км», не «1 м3»
# вместо литров).
_COMPACT: dict[str, tuple[str, ...]] = {
    "длина": ("мм", "см", "м", "км"),
    "масса": ("мг", "г", "кг", "т"),
    "объём": ("мл", "л"),
    "мощность": ("мВт", "Вт", "кВт", "МВт"),
    "напряжение": ("В", "кВ"),
    "ток": ("мА", "А", "кА"),
    "частота": ("Гц", "кГц", "МГц", "ГГц"),
    "давление": ("Па", "кПа", "МПа"),
}
_PLAIN_NUMBER = re.compile(r"^\d+(?:[.,]\d+)?$")


def compact(value: str | None, unit: str | None) -> str | None:
    """То же значение короче в другой единице той же величины: «1500 мм» → «1,5 м», «1000 кВт» →
    «1 МВт», «0,15 м» → «15 см». Длина — число и единица без пробела;
    при равной длине остаётся своя единица. Только точный пересчёт, не больше трёх
    знаков после запятой. Характеристика остаётся в единице позиции (ТЗ: «к единому виду») — это
    подсказка для чтения. Короче нет — None."""
    if value is None or unit is None or not _PLAIN_NUMBER.match(value.strip()):
        return None
    family = _COMPACT.get(quantity(unit) or "")
    if not family or unit not in family:
        return None
    x = float(value.strip().replace(",", "."))
    best, best_len = None, len(_fmt(x)) + len(unit)
    for other in family:
        y = convert(x, unit, other)
        if other == unit or y is None or y <= 0 or abs(y * 1000 - round(y * 1000)) > 1e-6:
            continue
        text = _fmt(y)
        if len(text) + len(other) < best_len:
            best, best_len = f"{text} {other}", len(text) + len(other)
    return best


def _listed(values: list[str]) -> str:
    head = ", ".join(f"«{v}»" for v in values[:_MAX_LISTED])
    return head + (f" и ещё {len(values) - _MAX_LISTED}" if len(values) > _MAX_LISTED else "")


def _fit_general(raw: str, spec: CharacteristicSpec) -> Fit:
    """«Общие сведения»: перечень — подсказка, а не ограничение. Значение остаётся как его написал
    поставщик: самое частое написание на портале бывает небрежным («grohe» при «Grohe»), а
    «опечатка» в имени собственном — другой завод («Снежинка» — не «Снегурочка»)."""
    return Fit(status="exact", value=raw.strip(), unit=None)


# Суффикс прилагательного: «глина» — «глиняный», «металл» — «металлический».
_FORM_TAIL = re.compile(
    r"(?:ическ|енн|янн|ян|анн|ан|ов|ев)(?:ый|ий|ой|ая|яя|ое|ее|ые|ие|ого|ому|ым|им)?$"
)


def _form_cores(word: str) -> set[str]:
    """Основа: само слово, без суффикса прилагательного и без конечной гласной."""
    folded = fold(word)
    if " " in folded or any(ch.isdigit() for ch in folded):
        return set()
    cores: set[str] = set()
    if len(folded) >= 4:
        cores.add(folded)
    stripped = _FORM_TAIL.sub("", folded)
    if stripped != folded and len(stripped) >= 3:
        cores.add(stripped)
    if len(folded) >= 4 and folded[-1] in "аяоыие":
        cores.add(folded[:-1])
    return cores


def same_word_form(left: str, right: str) -> bool:
    """Одна основа: «глина» и «Глиняный», «металл» и «Металлический». Точное совпадение — не оно."""
    if fold(left) == fold(right):
        return False
    shared = _form_cores(left) & _form_cores(right)
    if any(len(core) >= 4 for core in shared):
        return True
    short = {core for core in shared if len(core) >= 3}
    if not short:
        return False
    return _FORM_TAIL.search(fold(left)) is not None or _FORM_TAIL.search(fold(right)) is not None


def list_form(value: str, options: list[str]) -> str | None:
    """Единственный пункт перечня той же основы. Два кандидата — None, значение не угадываем."""
    found = [option for option in options if same_word_form(value, option)]
    return found[0] if len(found) == 1 else None


def _fit_qualitative(raw: str, spec: CharacteristicSpec) -> Fit:
    value = raw.strip()
    if not spec.values:
        return Fit(status="exact", value=value, unit=spec.unit)
    if value in spec.values:
        return Fit(status="exact", value=value, unit=spec.unit)
    for option in spec.values:
        if fold(option) == fold(value):
            return Fit(
                status="normalized", value=option, unit=spec.unit, note=f"«{value}» → «{option}»"
            )
    formed = list_form(value, spec.values)
    if formed is not None:
        return Fit(
            status="normalized",
            value=formed,
            unit=spec.unit,
            note=f"приведено к формулировке КТРУ: «{value}» → «{formed}»",
        )
    if sum(1 for option in spec.values if same_word_form(value, option)) > 1:
        return Fit(status="not_in_list", note=f"допустимые значения: {_listed(spec.values)}")
    near = nearest_allowed(value, spec.values)
    if near is not None:
        return Fit(
            status="typo", value=near[0], unit=spec.unit, note=f"опечатка: «{value}» → «{near[0]}»"
        )
    return Fit(status="not_in_list", note=f"допустимые значения: {_listed(spec.values)}")


def _fit_quantitative(raw: str, raw_unit: str | None, spec: CharacteristicSpec) -> Fit:
    target_unit = normalize_unit(spec.unit) or spec.unit
    for option in spec.values:  # значение уже записано как корзина перечня («>=80 ... <90»)
        same_unit = raw_unit is None or normalize_unit(raw_unit) == target_unit
        if fold(option) == fold(raw) and same_unit:
            return Fit(status="exact", value=option, unit=target_unit)
    parsed, unit_in_value = parse_value(raw)
    number = parsed.number if parsed.kind == "number" else to_number(raw)
    if number is None and (m := _NUM_THEN_UNIT.match(raw)) and normalize_unit(m[2]):
        # parse_value не берёт единицы с цифрами («г/м2», «м3»): разбираем сами.
        number, unit_in_value = to_number(m[1]), m[2]
    if number is None:
        return Fit(status="unparsed", note=f"«{raw.strip()}» не число")
    target = normalize_unit(spec.unit) or spec.unit
    given_raw = raw_unit or unit_in_value
    unit = normalize_unit(given_raw) if given_raw else target
    if unit is None and given_raw and given_raw.strip().casefold() in DEGREE_WORDS:
        unit = target if target in ("°C", "°") else None
    if unit == "г" and target == "г/м2":
        unit = target  # «бумага 80 г» — так пишут плотность (масса 1 м²)
    if given_raw and unit is None:
        return Fit(
            status="unit_mismatch", note=f"единица «{given_raw}» не распознана, нужна «{target}»"
        )
    converted = number if target is None or unit is None else convert(number, unit, target)
    if converted is None:
        return Fit(status="unit_mismatch", note=f"единица «{unit}» не подходит к «{target}»")
    factor = _ratio(unit, target) if unit and target and unit != target else None
    if factor is not None and not 1e-6 < factor < 1e6:
        # «5 мВт» при «МВт» (шаблон у лазерной указки) — описка в единице, а не 0,000000005 МВт.
        return Fit(
            status="unit_mismatch",
            note=f"«{unit}» и «{target}» отличаются в 10^{round(abs(math.log10(factor)))} раз: "
            "проверьте единицу",
        )
    was_converted = (
        unit is not None
        and target is not None
        and unit != target
        and not (unit in _COUNT and target in _COUNT)
    )
    shown_unit = target or unit
    concrete = _fmt(converted)
    if spec.range is not None:
        lo, hi = spec.range.min, spec.range.max
        if (lo is not None and converted < lo) or (hi is not None and converted > hi):
            bounds = f"{_fmt(lo) if lo is not None else '…'}–{_fmt(hi) if hi is not None else '…'}"
            return Fit(
                status="not_in_list",
                supplier=concrete,
                note=f"{concrete} вне диапазона {bounds} {target or ''}".strip(),
            )
    if converted < 0 and _negative_not_allowed(spec):
        return Fit(
            status="not_in_list", supplier=concrete, note=f"{concrete} — отрицательное значение"
        )
    for option in spec.values:
        bucket = parse_bucket(option)
        if bucket is None or not bucket.contains(converted):
            continue
        if not bucket.is_point:
            return Fit(
                status="bucket",
                value=option,
                unit=shown_unit,
                supplier=concrete,
                note=f"{concrete} → «{option}»",
            )
        status: FitStatus = "normalized" if was_converted else "exact"
        return Fit(status=status, value=option, unit=shown_unit, supplier=concrete)
    if spec.values:
        return Fit(
            status="not_in_list",
            supplier=concrete,
            note=f"допустимые значения: {_listed(spec.values)}",
        )
    status = "normalized" if was_converted else "exact"
    note = f"{_fmt(number)} {unit} → {_fmt(converted)} {target}" if was_converted else None
    return Fit(status=status, value=_fmt(converted), unit=shown_unit, note=note)


# Величины, которые не бывают меньше нуля, и размеры изделия среди длин: у «Постоянной призмы»
# и «Офсета» минус законен, у «Длины шнура» — нет.
_NEVER_NEGATIVE = {"масса", "объём", "штуки", "данные", "заряд", "мощность"}
_SIZE_WORDS = ("длин", "ширин", "высот", "толщин", "диаметр", "глубин")


def _negative_not_allowed(spec: CharacteristicSpec) -> bool:
    """Минус — ошибка у массы, объёма, штук и размеров изделия, если диапазон позиции его не
    разрешает; у температуры, напряжения, давления, углов и прочего он законен
    («Условия хранения -18 °C» блокировались)."""
    if spec.range is not None and spec.range.min is not None:
        return spec.range.min >= 0
    kind = quantity(normalize_unit(spec.unit))
    if kind in _NEVER_NEGATIVE:
        return True
    return kind == "длина" and any(w in fold(spec.name) for w in _SIZE_WORDS)


def fit_value(raw_value: str, raw_unit: str | None, spec: CharacteristicSpec) -> Fit:
    """Подгоняет сырое значение к спецификации; статус — что сделано или почему отказ."""
    if spec.group == "general":
        return _fit_general(raw_value, spec)
    if spec.kind == "qualitative":
        return _fit_qualitative(raw_value, spec)
    return _fit_quantitative(raw_value, raw_unit, spec)


# Опечатка в числе: расхождение не больше этой доли от большего («1,5 В» и «1,2 В»).
MAX_TYPO_SHARE = 0.2
# Перепутанная единица: отношение — степень десяти с этой точностью («900 мл» и «900 л»).
SLIP_TOLERANCE = 0.02


def input_slip(in_name: float, in_char: float) -> bool:
    """Значение характеристики похоже на ошибку ввода того, что написано в наименовании."""
    if in_char <= 0 < in_name:
        return True
    lo, hi = sorted((abs(in_name), abs(in_char)))
    if lo == 0:
        return False
    if (hi - lo) / hi <= MAX_TYPO_SHARE + 1e-9:
        return True
    ratio = hi / lo
    return any(abs(ratio / 10**k - 1) <= SLIP_TOLERANCE for k in (1, 2, 3))
