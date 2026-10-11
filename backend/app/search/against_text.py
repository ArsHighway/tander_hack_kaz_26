"""Подсказка против текста поставщика и подсказка, которую текст называет.

Подсказка похожих СТЕ или двойника — для поля, в котором правила по тексту ничего не нашли. Но
текст может говорить о поле иное в форме, которую правила уверенно не берут: число той же
величины, не легшее ни в одно поле («16 А» при подсказке «10 А»), число рядом со словом из имени
поля («3 жилы» при «Число жил = 5»), размер изделия («1900х800 мм» при «Длине спального места
1860»), обозначение того же вида («E28» при «E27») или отрицание («без Wi-Fi» при «Да»). Такую
подсказку не ставим: значение поставщика важнее чужих СТЕ (`said_otherwise`, `keep_unsaid`). Тот же
разбор в `/check` сверяет число поля карточки с текстом («10 А» в поле и «16 А» в описании).

Число относится к полю, только если подпись называет его (все слова имени, одно из них рядом с
числом; без двоеточия хватает главного слова рядом: «3 жилы», «вес 600 г»; «7 TPI» — «Количество
зубьев на дюйм») или других полей этой величины в позиции нет. Размер называет измерения изделия
по правилам `_size_against`. Края диапазонов, годы, артикулы и разряды «1 412» не сравниваются.
Слово перечня в свободном тексте против подсказки не довод: «Аппарат» из наименования, «скольжения»
из метки соседнего поля отбрасывали верные подсказки чаще неверных.

Обратная сторона — `said_same`: текст называет значение подсказки (те же подписи чисел и размеры,
а для слов — все слова значения подряд: «латунь», «однорычажный»). Тогда это значение из текста с
фрагментом, а не предложение «Принять». Цифры замеров — в README («Качество на отложенной выборке»).
"""

import math
from collections.abc import Sequence
from typing import Literal

import regex as re

from app.catalog import CharacteristicSpec
from app.extract.model import Extracted
from app.ingest.characteristics import to_number
from app.ingest.units import normalize_unit
from app.schemas import Span
from app.search.similar import Similar
from app.search.text import negated_stems, tokens
from app.values import COUNT_UNITS, convert, fold, parse_bucket, quantity

Texts = Sequence[tuple[str, str]]  # (поле, текст): наименование и описание

_QUANTITY = re.compile(
    r"(?<![\p{L}\p{N}.,])(?P<num>[0-9]+(?:[.,][0-9]+)?)(?![.,]?[0-9])"
    r"\s*(?P<word>[\p{L}%°][\p{L}²³0-9/]*)?"
)
_TOKEN = re.compile(r"[\p{L}\p{N}]+")
# Между меткой и числом: «Число жил: 3», «нагрузка до 120 кг», «ток не более 16 А».
_GAP = re.compile(r"[\s:=\-–—]+$")
_LEAD = re.compile(r"(?:^|\s)(?:до|от|не|более|менее|около|свыше|макс|мин)\.?$", re.IGNORECASE)
# Подпись числа — до трёх слов перед ним в той же фразе: «Глубина сиденья: 600 мм».
_CLAUSE = re.compile(r"[,;.()\n]|(?P<num>\d)")
_WORD = re.compile(r"\p{L}[\p{L}\p{N}-]*")
_LABEL_WORDS = 3
# Слово после числа подписывает следующее число («р. 54 рост 182»), а не это.
_NEXT_NUMBER = re.compile(r"[\s:]*[-\u2212]?\d")
# Слова имени поля, которые о величине не говорят: «Наличие замка» — про замок.
_GENERIC = frozenset(tokens("наличие число количество тип вид общий номинальный значение"))
# «Минимальная дальность: 0,5 м» — не о «Максимальной дальности», хотя главное слово то же.
_BOUNDS = frozenset(tokens("максимальный минимальный"))
_YES_NO = {"да", "нет"}
# Число — часть артикула или модели: «BE-9882S», «WITTE 653867».
_ARTICLE_DIGITS = 5


class _Named:
    """Имя поля для подписи числа: значимые слова по порядку (первое — главное: «Высота ящика»
    — о высоте) и границы «максимальный/минимальный»."""

    def __init__(self, spec: CharacteristicSpec) -> None:
        words = tokens(spec.name)
        self.name = spec.name
        self.keys = [t for t in words if t not in _GENERIC | _BOUNDS and not t.isdigit()]
        self.bounds = set(words) & _BOUNDS
        self.words = set(words)
        self.kind = _kind(spec.unit) if spec.kind == "quantitative" and spec.unit else None

    def bounded(self, window: set[str]) -> bool:
        said = window & _BOUNDS
        return not said or said == self.bounds


def _keys(spec: CharacteristicSpec) -> list[str]:
    return _Named(spec).keys


def _covered(
    extracted: list[Extracted], field: str, start: int, end: int, names: set[str] | None = None
) -> bool:
    """Фрагмент взят правилами в поле; `names` — только в поле из этого списка (поля позиции):
    число, взятое в поле КТРУ вне позиции («5 м» в «Высоту до»), ничьё."""
    return any(
        e.source.field == field
        and not (end <= e.source.start or start >= e.source.end)
        and (names is None or fold(e.name) in names)
        for e in extracted
    )


def _label_before(text: str, start: int) -> tuple[list[str], bool]:
    """До трёх слов перед числом в той же фразе, ближнее последним, и стоит ли между ними
    двоеточие («Диаметр: 10 мм» — подпись явная)."""
    head = text[:start]
    colon = ":" in text[max(0, start - 4) : start]
    while True:
        cut = _GAP.sub("", head)
        cut = _LEAD.sub("", cut)
        if cut == head:
            break
        head = cut
    stops = list(_CLAUSE.finditer(head))
    last = stops[-1] if stops else None
    words = _WORD.findall(head[last.end() :] if last else head)
    if last is not None and last["num"]:
        words = words[1:]  # «6 ящиков 50х80» — «ящиков» подписывает 6, а не 50
    return words[-_LABEL_WORDS:], colon


_CLAUSE_STOP = re.compile(r"[,;.()\n]")


def _value_first(text: str, start: int) -> bool:
    """Фраза начинается с числа («120 см ширина …»): поставщик пишет число, потом подпись."""
    stops = list(_CLAUSE_STOP.finditer(text, 0, start))
    clause = text[stops[-1].end() if stops else 0 : start + 1].lstrip()
    return clause[:1].isdigit()


def _negative(text: str, start: int) -> bool:
    """Минус перед числом («от -30», «-50 дБм»), а не тире диапазона («85-265 В») и не дефис
    («шаг-1.2»)."""
    if start == 0 or text[start - 1] not in "-\u2212":
        return False
    if start >= 2 and text[start - 2].isalnum():
        return False
    before = text[: start - 1].rstrip()
    return not before or not before[-1].isdigit()


def _article(text: str, m: re.Match[str], unit: str | None) -> bool:
    """Число из артикула: приклеено к буквам («BE-9882S», «А22120-04») или длинное без единицы."""
    glued_after = bool(m["word"]) and m.end("num") == m.start("word") and unit is None
    glued_before = m.start() >= 2 and text[m.start() - 1] == "-" and text[m.start() - 2].isalpha()
    long_bare = unit is None and len(m["num"]) >= _ARTICLE_DIGITS and m["num"].isdigit()
    return glued_after or glued_before or long_bare


_RANGE_AFTER = re.compile(r"\s*[-–—]\s*\d")
_RANGE_BEFORE = re.compile(r"\d\s*[-–—]\s*$")
_BY_BEFORE = re.compile(r"\d\s*[xх×*]\s*$", re.IGNORECASE)
_BY_AFTER = re.compile(r"\s*[xх×*]\s*\d", re.IGNORECASE)
_GROUP_BEFORE = re.compile(r"(?<![\d.,])\d{1,3}\s$")
_GROUP_AFTER = re.compile(r"\s\d{3}(?![\d.,])")
_ABBREV_TAIL = re.compile(r"\.\s?\p{L}")
_YEAR = re.compile(r"(19|20)\d\d")
_COMPOUND = re.compile(r"\s+на\s", re.IGNORECASE)
# Единица, которая сама называет поле: «7 TPI» — 7 зубьев на дюйм.
_UNIT_NAMES = {"tpi": frozenset(tokens("зубьев на дюйм"))}
# Число в качественном поле («Количество зубьев на дюйм: 11», «Размер: 52») сверяется как число.
_PLAIN_NUMBER = re.compile(r"[0-9]+(?:[.,][0-9]+)?")
# Размер изделия «1900х800 мм», «1200х600х750 мм»: два-три числа и единица длины.
_SIZE = re.compile(
    r"(?<![\p{L}\p{N}.,])(?P<nums>[0-9]+(?:[.,][0-9]+)?(?:\s*[xх×*]\s*[0-9]+(?:[.,][0-9]+)?){1,2})"
    r"(?![.,]?[0-9])\s*(?P<word>\p{L}+)?",
    re.IGNORECASE,
)
_SIZE_SEP = re.compile(r"\s*[xх×*]\s*", re.IGNORECASE)
_DIMS = frozenset(tokens("длина ширина высота глубина"))
_HEIGHT = tokens("высота")[0]
_SIZE_WORDS = frozenset(tokens("размер размеры габарит габариты габаритные"))


def _not_a_value(text: str, m: re.Match[str], unit: str | None) -> bool:
    """Число — край диапазона («размер 56-60», «85-265 В»), год («2019 г.») или часть составной
    единицы («780 килограмм на метр кубический»): с одним значением поля его не сравнить."""
    if _RANGE_AFTER.match(text, m.end("num")) or _RANGE_BEFORE.search(text[: m.start()]):
        return True
    if _BY_BEFORE.search(text[: m.start()]) or _BY_AFTER.match(text, m.end("num")):
        return True  # «200*10 мм» — размер изделия, а не одно значение
    if unit == "г" and _YEAR.fullmatch(m["num"]):
        return True
    if _GROUP_BEFORE.search(text[: m.start()]) or _GROUP_AFTER.match(text, m.end("num")):
        return True  # «1 412 гр», «А4 80 500»: разряды или соседние числа — не понять, где число
    if m["word"] and _ABBREV_TAIL.match(text, m.end()):
        return True  # «280 л.с.» — не литры: единица дальше точки
    return bool(m["word"]) and bool(_COMPOUND.match(text, m.end()))


def _kind(unit: str | None) -> str | None:
    """Величина единицы («мм» и «м» — длина); незнакомая единица — сама себе величина."""
    unit = normalize_unit(unit) or unit
    return quantity(unit) or unit


def _agrees(value: str, x: float, strict: bool = False) -> bool | None:
    """Число текста `x` (в единице позиции) — то же, что подсказка; None — подсказка не число.
    `strict` — равно с точностью записи подсказки («747» не «750»): так текст подтверждает
    подсказку; без него близкое число лишь не спорит с ней."""
    bucket = parse_bucket(value)
    if bucket is not None and not bucket.is_point:
        return bucket.contains(x)
    y = to_number(value)
    if y is None:
        return None
    if strict:
        return _equal(x, value.strip(), y)
    # 17,4 кВт = 23,7 л.с. при «24 л.с.»: пересчёт и округление — не расхождение.
    return math.isclose(x, y, rel_tol=0.02, abs_tol=1e-9)


def _equal(x: float, written: str, y: float) -> bool:
    """`x` округляется до знаков записи `written` («0,383» — до тысячных) и равно `y`, и не
    дальше 0,5 %: «1500 г» не «2 кг», хотя 1,5 при целой записи округлилось бы до 2."""
    decimals = len(re.split(r"[.,]", written)[1]) if re.search(r"[.,]", written) else 0
    near = abs(x - y) <= 0.5 * 10**-decimals + 1e-9
    return near and math.isclose(x, y, rel_tol=0.005, abs_tol=1e-9)


_LENGTH = _kind("мм")


def _size_numbers(value: str) -> list[float] | None:
    """Числа значения-размера «1900х800» (в единице поля); не размер — None."""
    m = _SIZE.fullmatch(value.strip())
    if m is None:
        return None
    xs = [x for n in _SIZE_SEP.split(m["nums"]) if (x := to_number(n)) is not None]
    return xs if len(xs) >= 2 else None


def _same_size(a: list[float], b: list[float], strict: bool = False) -> bool:
    """Те же измерения в любом порядке («900x2000» и «2000х900»); `strict` — без округления."""
    tol = 1e-3 if strict else 0.02
    return len(a) == len(b) and all(
        math.isclose(x, y, rel_tol=tol) for x, y in zip(sorted(a), sorted(b), strict=True)
    )


Found = tuple[str, int, int]  # (поле, начало, конец) фрагмента текста, который называет значение
Verdict = tuple[Found | None, str | None]  # (текст называет это значение, фрагмент против)


def _size_against(
    spec: CharacteristicSpec,
    value: str,
    field: str,
    text: str,
    extracted: list[Extracted],
    named: list[_Named],
    item: set[str],
    parts: dict[str, set[float]],
    strict: bool = False,
) -> Verdict:
    """Размер «1900х800 мм» против подсказки измерения: (где текст называет это
    значение, фрагмент против). Поле с уточнением названо, если уточнение в подписи размера
    («Размер спального места» — «Длина спального места», но не «Высота ножек»). Поле без
    уточнения («Ширина») — только размером самого изделия: подпись — слова наименования или
    «размер», «габариты» (не «Размер упаковки», не «Шпоночный паз»), и чисел три или подписано
    «размер» («Ключ 22х24 мм», «Сверло 12х120 мм» — не габариты). Два числа высоту не называют,
    если полей измерений больше двух. Размер, разложенный правилами по полям измерений,
    остальные поля не называет.

    Подсказка-размер («Размер спального места: 1900х800») сверяется с размером текста целиком:
    поле названо, если слова его имени, кроме «размер», в подписи («Спальное место 2000х900 мм»)."""
    target = normalize_unit(spec.unit) or spec.unit
    if target is None or _kind(target) != _LENGTH:
        return None, None
    dims = [
        n for n in named if n.kind == _LENGTH and not n.bounds and n.keys and n.keys[0] in _DIMS
    ]
    split = {fold(n.name) for n in dims}
    hinted = _size_numbers(value)
    rest = set(_keys(spec)) - _SIZE_WORDS
    against: str | None = None
    for m in _SIZE.finditer(text):
        unit = normalize_unit(m["word"]) if m["word"] else None
        if unit is None or _kind(unit) != _LENGTH:
            continue  # «3х2,5» у кабеля — жилы и сечение, а не размер изделия
        if _covered(extracted, field, m.start(), m.end(), split):
            continue
        words, _ = _label_before(text, m.start())
        label = {t for w in words for t in tokens(w)}
        nums = _SIZE_SEP.split(m["nums"])
        whole = label <= (_SIZE_WORDS | item) and (len(nums) == 3 or bool(label & _SIZE_WORDS))
        # Уточнение поля в подписи, которая вся из слов наименования («Электрод-петля 10 x 10 мм»
        # при «Длине электрода»), — слово товара, а не подпись размера: нужно «размер/габариты».
        sized = not label <= item or bool(label & _SIZE_WORDS)

        def qualified(keys: set[str], label: set[str] = label, sized: bool = sized) -> bool:
            return keys <= label and sized

        if hinted is not None:
            # Размер с другим числом измерений («700х1100» и «700х1100х4») — о другом.
            if len(nums) != len(hinted) or not (qualified(rest) if rest else whole):
                continue
            xs = [x for n in nums if (x := to_number(n)) is not None]
            vals = [v for x in xs if (v := convert(x, unit, target)) is not None]
            if len(vals) != len(nums):
                continue
            # Значения перечня СТЕ к единице поля не приведены («40х30х15» у поля в мм — это см):
            # совпадение и без пересчёта — не довод.
            if _same_size(vals, hinted, strict) or _same_size(xs, hinted, strict):
                return (field, m.start(), _end(m)), None
            against = against or text[m.start() : m.end()].strip()
            continue
        # Поля без уточнения размер не называет, если два его числа текст подписал измерениями с
        # уточнением: «Фургон 2445х1348х1520 мм», «Высота грузового отсека: 1520», «Длина грузового
        # отсека: 2445» — размер отсека. Одно совпадение — не довод: длина кровати и спального места
        # одна («370х800х1900 мм» и «Длина спального места: 1900 мм»).
        raw = {x for n in nums if (x := to_number(n)) is not None}
        own = whole and len(raw & set().union(*parts.values())) < 2
        named_here = [n for n in dims if (qualified(set(n.keys[1:])) if n.keys[1:] else own)]
        if len(nums) < len(named_here):
            named_here = [n for n in named_here if n.keys[0] != _HEIGHT]  # «1200х600» — без высоты
        if len(nums) < len(named_here) or spec.name not in {n.name for n in named_here}:
            continue
        for num in _NUMBER.finditer(m["nums"]):
            x = to_number(num[0])
            x = convert(x, unit, target) if x is not None else None
            agrees = _agrees(value, x, strict) if x is not None else False
            if agrees is None:
                return None, None  # подсказка не число — не сравнить
            if agrees:
                at = m.start("nums") + num.start()
                return (field, at, at + len(num[0])), None
        against = against or text[m.start() : m.end()].strip()
    return None, against


_NUMBER = _PLAIN_NUMBER


def _part_numbers(texts: Texts, named: list[_Named]) -> dict[str, set[float]]:
    """Числа, которые текст подписал измерением с уточнением («Высота грузового отсека: 1520»),
    по главному слову («высот»), без пересчёта единиц (у портала они бывают перепутаны)."""
    parts = {
        n.name: n
        for n in named
        if n.kind == _LENGTH and n.keys[1:] and n.keys[0] in _DIMS and not n.bounds
    }
    out: dict[str, set[float]] = {}
    if not parts:
        return out
    for _, text in texts:
        for m in _QUANTITY.finditer(text):
            words, colon = _label_before(text, m.start())
            near = set(tokens(words[-1])) if words else set()
            label = {t for w in words for t in tokens(w)}
            owners = _owners(label | near, near, named, label=label if colon else None)
            part = parts.get(next(iter(owners))) if len(owners) == 1 else None
            if part is not None and (x := to_number(m["num"])) is not None:
                out.setdefault(part.keys[0], set()).add(x)
    return out


def _end(m: re.Match[str]) -> int:
    """Конец числа с единицей без пробелов после: «500 г», «1900х800 мм», «176»."""
    if m["word"]:
        return m.end("word")
    return m.end("num") if "num" in m.re.groupindex else m.end("nums")


def _number_against(
    spec: CharacteristicSpec,
    value: str,
    texts: Texts,
    extracted: list[Extracted],
    named: list[_Named],
    alone: bool,
    strict: bool = False,
) -> Verdict:
    """`alone` — других полей этой величины в позиции нет: тогда и число без подписи о нём."""
    target = normalize_unit(spec.unit) or spec.unit
    names = {fold(n.name) for n in named}
    keys = set(_keys(spec))
    item = {t for f, text in texts if f == "name" for t in tokens(text)}
    parts = _part_numbers(texts, named)
    against: str | None = None
    for field, text in texts:
        found, size = _size_against(spec, value, field, text, extracted, named, item, parts, strict)
        if found is not None:
            return found, None
        against = against or size
        for m in _QUANTITY.finditer(text):
            if _covered(extracted, field, m.start(), m.end(), names):
                continue  # число уже в каком-то поле: если бы в этом — подсказки бы не было
            # Взято в поле вне позиции («5 м» в «Высоту до» КТРУ): довод, если подписано этим полем.
            taken = _covered(extracted, field, m.start(), m.end())
            word = m["word"] or ""
            # «7 TPI»: единица сама называет поле и величины не несёт
            by_unit = _UNIT_NAMES.get(word.casefold())
            words, colon = _label_before(text, m.start())
            if _value_first(text, m.start()):
                # «120 см ширина 75 см высота»: подпись стоит после числа, а не перед ним.
                after = _WORD.search(text, m.end())
                gap = text[m.end() : after.start()] if after else ""
                words = [after[0]] if after and not gap.strip(" \t") else []
            near = set(tokens(words[-1])) if words else set()
            if word and not _NEXT_NUMBER.match(text, m.end()):
                near |= set(tokens(word))  # «3 жилы»
            label = {t for w in words for t in tokens(w)}
            window = label | near
            # Явная подпись («Диаметр: 10 мм») должна назвать поле целиком: «Максимальный
            # диаметр зажима патрона» она не называет, хотя главное слово то же.
            owners = _owners(window, near, named, label=label if colon else None)
            own = owners == {spec.name} or (by_unit is not None and bool(keys) and keys <= by_unit)
            if by_unit is not None:
                word = ""
            if taken and not own:
                continue
            if (owners or colon) and not own:
                # «нагрузка до 120 кг», «ширина 600 мм», «Срок службы: 2 г» — про другое поле;
                # «Минимальная дальность» подписывает и «Максимальную» — не понять, о каком
                continue
            if window & _BOUNDS and not own:
                continue  # «Минимальная дальность 0,5 м» — не о «Максимальной»
            x = to_number(m["num"])
            unit = normalize_unit(word) if word else None
            if x is None or _article(text, m, unit) or _not_a_value(text, m, unit):
                continue
            if _negative(text, m.start()):
                x = -x
            if unit is not None and target is not None:
                x = convert(x, unit, target)
            elif unit is not None and unit not in COUNT_UNITS:
                continue  # «3 мм²» у поля без единицы — другая величина
            elif unit is None and not own:
                continue  # голое число без подписи — не довод
            if x is None:
                continue
            if not own and (not alone or target in COUNT_UNITS or unit in COUNT_UNITS):
                # «600 мм» без подписи у стола с высотой, шириной и толщиной — о любом из них;
                # «10 шт» — счёт чего угодно
                continue
            agrees = _agrees(value, x, strict)
            if agrees is None:
                return None, None
            if agrees:
                # текст называет и это значение: подсказка не спорит с ним
                return (field, m.start(), _end(m)), None
            against = against or text[m.start() : m.end()].strip()
    return None, against


def _owners(
    window: set[str], near: set[str], named: list[_Named], *, label: set[str] | None = None
) -> set[str]:
    """Чьё число по подписи: поля, все слова имени которых в подписи и одно из них рядом с числом
    («ширина 600» — «Ширина», а не «Ширина полки»; «Набор отверток WITTE 653867» — не о
    «Количестве отверток»); таких нет — поля, чьё главное слово рядом с числом («7 ящиков» — не о
    «Нагрузке на ящик», «белая 2048» — не о «Высоте ящика для белья»). `label` — явная подпись
    с двоеточием: только полное совпадение и без чужих слов."""
    fits = [n for n in named if n.keys and n.bounded(window)]
    full = [n for n in fits if set(n.keys) <= window and near & set(n.keys)]
    if label is not None:
        # «Рабочая длина: 1300 мм» — не «Длина»: в явной подписи нет чужих слов.
        full = [n for n in full if not label - n.words - _GENERIC]
    if full:  # «ширина полки 500» — о «Ширине полки», хотя и «Ширина» вся в подписи
        # Больше слов имени в подписи — точнее; при равенстве — та, чьи границы совпали с
        # подписью: «Высота 750 мм» — о «Высоте», а не о «Максимальной высоте».
        said = window & _BOUNDS
        best = max((len(n.keys), n.bounds == said) for n in full)
        return {n.name for n in full if (len(n.keys), n.bounds == said) == best}
    return set() if label is not None else {n.name for n in fits if n.keys[0] in near}


def _shape(word: str) -> str:
    """«E27» → «a9», «А4» → «a9», «дм3» → «aa9»: буквы поштучно, цифры — без длины."""
    return re.sub(r"\p{N}+", "9", re.sub(r"\p{L}", "a", word))


def _code_against(value: str, texts: Texts, extracted: list[Extracted]) -> Verdict:
    """Обозначение того же вида, что подсказка («E28» при «E27»), не взятое ни в одно поле."""
    folded = fold(value)
    if " " in folded or not re.search(r"\p{L}", folded) or not re.search(r"\p{N}", folded):
        return None, None
    shape = _shape(folded)
    against: str | None = None
    for field, text in texts:
        for m in _TOKEN.finditer(text):
            word = fold(m[0])
            if word == folded:
                return (field, m.start(), m.end()), None
            if (
                against is None
                and _shape(word) == shape
                and not _covered(extracted, field, m.start(), m.end())
            ):
                against = m[0]
    return None, against


def _yes_no_against(spec: CharacteristicSpec, value: str, texts: Texts) -> str | None:
    """«без Wi-Fi» при подсказке «Да», «с подлокотниками» при «Нет»."""
    keys = set(_keys(spec))
    if not keys:
        return None
    joined = "\n".join(text for _, text in texts)
    negated = negated_stems(joined) & keys
    if fold(value) == "да":
        return f"без {spec.name}" if negated else None
    present = set(tokens(joined))
    return spec.name if keys <= present and not negated else None


def _verdict(
    spec: CharacteristicSpec,
    value: str,
    texts: Texts,
    extracted: list[Extracted],
    specs: list[CharacteristicSpec],
    strict: bool = False,
) -> Verdict:
    """Числа, размеры и обозначения: где текст называет `value` и что говорит иное; `strict` —
    называет ровно `value`, а не близкое число."""
    # Число в качественном поле без единицы («Количество зубьев на дюйм: 11»); с единицей значения
    # качественного поля не приведены к ней («300» у «Вес, кг» — граммы) — не сравниваем.
    if spec.kind != "quantitative" and spec.unit and _size_numbers(value) is not None:
        # Размер «1900х800» у перечня с единицей («Размер спального места, мм»): с размером текста
        # числами; размера с единицей в тексте нет — как обозначение («2000х900» без «мм»).
        named = [_Named(s) for s in {spec.name: spec, **{s.name: s for s in specs}}.values()]
        item = {t for f, text in texts if f == "name" for t in tokens(text)}
        parts = _part_numbers(texts, named)
        against: str | None = None
        for field, text in texts:
            found, size = _size_against(
                spec, value, field, text, extracted, named, item, parts, strict
            )
            if found is not None:
                return found, None
            against = against or size
        return (None, against) if against else _code_against(value, texts, extracted)
    if spec.kind == "quantitative" or (not spec.unit and _PLAIN_NUMBER.fullmatch(value.strip())):
        every = {spec.name: spec, **{s.name: s for s in specs}}.values()
        named = [_Named(s) for s in every]
        kind = _kind(spec.unit)
        alone = kind is not None and not any(
            s.name != spec.name and s.kind == "quantitative" and _kind(s.unit) == kind
            for s in every
        )
        return _number_against(spec, value, texts, extracted, named, alone, strict)
    if fold(value) in _YES_NO:
        return None, _yes_no_against(spec, value, texts)
    return _code_against(value, texts, extracted)


def said_otherwise(
    spec: CharacteristicSpec,
    value: str,
    texts: Texts,
    extracted: list[Extracted],
    specs: list[CharacteristicSpec],
) -> str | None:
    """Фрагмент текста, который говорит о поле `spec` не `value`; None — текст не возражает."""
    if spec.group == "general":
        return None
    return _verdict(spec, value, texts, extracted, specs)[1]


# Слово значения короче — не довод: «M» (размер) совпало бы с «м» в «5 м».
_MIN_WORD = 3


# Между «не/без» и словом значения: «не из латуни», «без ручки для».
_CONNECTORS = frozenset({"из", "с", "со", "для", "от", "в", "на"})
_NEGATIONS = frozenset({"не", "без"})


def _negated(raw: list[tuple[re.Match[str], list[str]]], first: int) -> bool:
    """«не/без» за одно-два слова до значения через служебное слово: «корпус не из латуни»."""
    for j in (first - 1, first - 2):
        if j < 0:
            return False
        word = fold(raw[j][0][0])
        if word in _NEGATIONS:
            return True
        if word not in _CONNECTORS:
            return False
    return False


def _foreign_label(spec: CharacteristicSpec, text: str, start: int, named: list[_Named]) -> bool:
    """Перед словом подпись «X:», и X — не это поле: «Подкладка: натуральная кожа» не о
    «Материале верха», «Материал верха обуви: ЭВА» не о «Производителе»."""
    words, colon = _label_before(text, start)
    if not colon or not words:
        return False
    label = {t for w in words for t in tokens(w)}
    near = set(tokens(words[-1]))
    return _owners(label | near, near, named, label=label) != {spec.name}


def _words_found(
    spec: CharacteristicSpec, value: str, texts: Texts, specs: list[CharacteristicSpec]
) -> Found | None:
    """Все значимые слова значения подряд в тексте: «латунь», «Однорычажный», «рожковый». Слова
    сверяются стемами («лазерная» — «лазерный»). Не подтверждают: слово после «не/без» и слово под
    подписью другого поля."""
    want = tokens(value)
    if not want or (len(want) == 1 and len(want[0]) < _MIN_WORD):
        return None
    named = [_Named(s) for s in {spec.name: spec, **{s.name: s for s in specs}}.values()]
    for field, text in texts:
        raw = [(m, tokens(m[0])) for m in _TOKEN.finditer(text)]
        words = [(i, stems[0]) for i, (_, stems) in enumerate(raw) if stems]
        for k in range(len(words) - len(want) + 1):
            if [s for _, s in words[k : k + len(want)]] != want:
                continue
            first, last = words[k][0], words[k + len(want) - 1][0]
            start = raw[first][0].start()
            if _negated(raw, first) or _foreign_label(spec, text, start, named):
                continue
            return field, start, raw[last][0].end()
    return None


def _in_some_size(
    spec: CharacteristicSpec, value: str, texts: Texts, extracted: list[Extracted], names: set[str]
) -> Found | None:
    """Число измерения в любом размере текста с единицей длины: «Кровать 1900х800 мм» при
    «Длине спального места: 1900». Против подсказки такой размер не довод (подписи нет), но то
    же число — её подтверждение. Число, взятое правилами в другое поле позиции, — не её."""
    target = normalize_unit(spec.unit) or spec.unit
    keys = _keys(spec)
    if spec.kind != "quantitative" or target is None or _kind(target) != _LENGTH:
        return None
    if not keys or keys[0] not in _DIMS:
        return None
    for field, text in texts:
        for m in _SIZE.finditer(text):
            # без единицы («Кровать 1900х800») — в единице поля: подтверждает только равное число
            unit = (normalize_unit(m["word"]) if m["word"] else None) or target
            if _kind(unit) != _LENGTH:
                continue
            for num in _NUMBER.finditer(m["nums"]):
                at = m.start("nums") + num.start()
                if _covered(extracted, field, at, at + len(num[0]), names):
                    continue
                x = to_number(num[0])
                x = convert(x, unit, target) if x is not None else None
                if x is not None and _agrees(value, x, strict=True) is True:
                    return field, at, at + len(num[0])
    return None


def _equal_number(
    spec: CharacteristicSpec, value: str, texts: Texts, extracted: list[Extracted], names: set[str]
) -> Found | None:
    """Число той же величины с единицей, равное подсказке («Молоток слесарный 500 г» при «Вес:
    500»), не взятое правилами в другое поле позиции. Против подсказки такое число без подписи не
    довод (полей массы бывает несколько), а то же значение — подтверждение."""
    target = normalize_unit(spec.unit) or spec.unit
    if spec.kind != "quantitative" or target is None or target in COUNT_UNITS:
        return None
    kind = _kind(target)
    if kind == _LENGTH:
        return None  # «Стол 600 мм» — ширина, глубина или высота: длины — только с подписью
    for field, text in texts:
        for m in _QUANTITY.finditer(text):
            unit = normalize_unit(m["word"]) if m["word"] else None
            if (
                unit is None
                or _kind(unit) != kind
                or _covered(extracted, field, m.start(), m.end(), names)
            ):
                continue
            x = to_number(m["num"])
            if x is None or _article(text, m, unit) or _not_a_value(text, m, unit):
                continue
            x = convert(x, unit, target)
            if x is not None and _agrees(value, x, strict=True) is True:
                return field, m.start(), _end(m)
    return None


def said_same(
    spec: CharacteristicSpec,
    value: str,
    texts: Texts,
    extracted: list[Extracted],
    specs: list[CharacteristicSpec],
) -> Span | None:
    """Фрагмент текста поставщика, который называет `value` поля `spec`: та же
    подпись числа, размер и обозначение, что у `said_otherwise`, только в обратную сторону, и
    слова значения подряд («латунь», «однорычажный»). Тогда подсказка похожих СТЕ или двойника —
    значение из текста: в поле с подсветкой фрагмента, без «Принять». «Да/Нет» словом не
    подтверждаются; None — текст значение не называет."""
    found: Found | None = None
    numeric = bool(_PLAIN_NUMBER.fullmatch(value.strip())) or _size_numbers(value) is not None
    if spec.group != "general":
        # Качественное поле с единицей («Рост, см»): его перечень к единице не приведён — число
        # сверяется без пересчёта, по подписи («рост 176»).
        plain = (
            spec.model_copy(update={"unit": None})
            if spec.kind != "quantitative" and spec.unit and _PLAIN_NUMBER.fullmatch(value.strip())
            else spec
        )
        verdict = _verdict(plain, value, texts, extracted, specs, strict=True)
        if verdict[0] is None and verdict[1] is None:
            names = {fold(s.name) for s in specs}
            # «Размер спального места: 1900х800» — размер целиком, его числа — и длина, и ширина
            others = {fold(s.name) for s in specs if not set(tokens(s.name)) & _SIZE_WORDS}
            found = _in_some_size(spec, value, texts, extracted, others) or _equal_number(
                spec, value, texts, extracted, names
            )
        else:
            found = verdict[0]
    words = not numeric and spec.kind != "quantitative" and fold(value) not in _YES_NO
    if found is None and words:
        found = _words_found(spec, value, texts, specs)
    if found is None:
        return None
    field, start, end = found
    text = dict(texts)[field]
    part: Literal["name", "description"] = "name" if field == "name" else "description"
    return Span(field=part, start=start, end=end, text=text[start:end])


def keep_unsaid(
    hints: dict[str, Similar],
    specs: list[CharacteristicSpec],
    texts: Texts,
    extracted: list[Extracted],
) -> dict[str, Similar]:
    """Подсказки без тех, о которых текст поставщика говорит иное; подсказка, которую текст
    называет, — с фрагментом (`Similar.source`): это значение из текста."""
    by_name = {s.name: s for s in specs}
    kept: dict[str, Similar] = {}
    numbers: set[tuple[str, int, int]] = set()
    for name, hint in hints.items():
        spec = by_name.get(name)
        if spec is None:
            kept[name] = hint
            continue
        if said_otherwise(spec, hint.value, texts, extracted, specs) is not None:
            continue
        source = said_same(spec, hint.value, texts, extracted, specs)
        if source is not None and any(ch.isdigit() for ch in source.text):
            # Одно число текста — значение одного поля: «600» в «600х1200 мм» не ширина и глубина.
            span = (source.field, source.start, source.end)
            source = None if span in numbers else source
            numbers.add(span)
        kept[name] = hint.model_copy(update={"source": source}) if source else hint
    return kept
