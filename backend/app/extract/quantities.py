"""Числа из наименования: составные единицы, счёт листов и числа без единицы.

Правила модерации узнают «80 г/м2» и «500 листов». Здесь то же значение дочитывается, если его
написали иначе («г / м²», «грамм на квадратный метр», «500 штук») или без единицы («А4 80 500»),
и только когда оно однозначно ложится на характеристику кандидата. Неоднозначное число не
подставляется — о нём сообщают отдельно.
"""

import regex as re

from app.catalog import CharacteristicSpec
from app.checks.hygiene import PHONE
from app.extract.model import Extracted
from app.ingest.units import normalize_unit
from app.schemas import Span
from app.values import convert, parse_bucket

Field = str
_NUM = r"[0-9]+(?:[.,][0-9]+)?"
# «80 г/м²», «80 г / м2», «80 грамм на квадратный метр».
_AREAL = re.compile(
    rf"(?<![\p{{L}}\p{{N}}])(?P<num>{_NUM})\s*"
    rf"(?P<mass>грамм(?:а|ов)?|гр|г)\s*(?:/\s*|\s+на\s+)"
    rf"(?:м\s*[²2]|кв\.?\s*м(?:етр\w*)?|квадратн\w+\s+метр\w*)",
    re.IGNORECASE,
)
# «80 грамм» без площади — плотность, только если в позиции нет отдельной массы в граммах.
_GRAM = re.compile(
    rf"(?<![\p{{L}}\p{{N}}])(?P<num>{_NUM})\s*(?P<mass>грамм(?:а|ов)?|гр|г)(?![\p{{L}}/])",
    re.IGNORECASE,
)
_COUNT = re.compile(
    rf"(?<![\p{{L}}\p{{N}}])(?P<num>{_NUM})\s*"
    rf"(?P<unit>листов|листа|лист|штуки|штук|штука|шт\.?)(?![\p{{L}}])",
    re.IGNORECASE,
)
_RANGE = re.compile(rf"(?<![\p{{L}}\p{{N}}])(?P<a>{_NUM})\s*[-–—]\s*(?P<b>{_NUM})(?![\p{{L}}])")
_BARE = re.compile(rf"(?<![\p{{L}}\p{{N}}.,])(?P<num>{_NUM})(?![\p{{N}}])")
_YEAR = re.compile(r"^(19|20)\d\d$")
_MARKERS = {
    "гост",
    "госта",
    "ост",
    "ту",
    "iso",
    "din",
    "en",
    "сто",
    "артикул",
    "арт",
    "модель",
    "мод",
    "sku",
    "код",
    "серия",
    "номер",
    "№",
}
_WORD = re.compile(r"[\p{L}\p{N}№#]+")


def _number(text: str) -> float | None:
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


_BOUND_BEFORE = re.compile(r"(?:^|\s)(?:до|от|по)\s*$", re.IGNORECASE)


def _bound_before(text: str, start: int) -> bool:
    """«до 80 г/м2» — граница, не значение характеристики."""
    return bool(_BOUND_BEFORE.search(text[:start]))


def _covers(extracted: list[Extracted], field: Field, start: int, end: int) -> bool:
    return any(
        e.source.field == field and not (end <= e.source.start or start >= e.source.end)
        for e in extracted
    )


def _spec_unit(spec: CharacteristicSpec) -> str | None:
    return normalize_unit(spec.unit) or spec.unit


def _contains(spec: CharacteristicSpec, number: float) -> bool:
    if spec.kind != "quantitative":
        return False
    for raw in spec.values:
        bucket = parse_bucket(raw)
        if bucket is not None and bucket.contains(number):
            return True
    rng = spec.range
    if rng is None:
        return False
    if rng.min is not None and number < rng.min:
        return False
    return not (rng.max is not None and number > rng.max)


def _matching(specs: list[CharacteristicSpec], number: float) -> list[CharacteristicSpec]:
    return [s for s in specs if _contains(s, number)]


def _unique_unit(specs: list[CharacteristicSpec], unit: str) -> CharacteristicSpec | None:
    fits = [s for s in specs if s.kind == "quantitative" and _spec_unit(s) == unit]
    return fits[0] if len(fits) == 1 else None


def _extracted(
    spec: CharacteristicSpec,
    field: Field,
    text: str,
    start: int,
    end: int,
    number: str,
    unit: str | None,
) -> Extracted:
    fragment = text[start:end]
    return Extracted(
        name=spec.name,
        raw_value=number.replace(" ", ""),
        raw_unit=unit,
        source=Span(field=field, start=start, end=end, text=fragment),  # type: ignore[arg-type]
        method="rule",
    )


def _areal_spec(specs: list[CharacteristicSpec]) -> CharacteristicSpec | None:
    grams = [s for s in specs if _spec_unit(s) == "г"]
    if grams:
        return None
    return _unique_unit(specs, "г/м2")


def number_in_range(text: str, at: int) -> bool:
    """Число — край диапазона «90–100», а не отдельное значение."""
    return any(
        m.start("a") <= at < m.end("a") or m.start("b") <= at < m.end("b")
        for m in _RANGE.finditer(text)
    )


def phrase_values(
    field: Field, text: str, specs: list[CharacteristicSpec], found: dict[str, Extracted]
) -> list[Extracted]:
    """Составная плотность и число листов/штук, которые не забрал разбор наименования."""
    out: list[Extracted] = []
    used: list[tuple[int, int]] = []
    ranges = list(_RANGE.finditer(text))

    def in_range(start: int) -> bool:
        # «90–100 г/м²» — не значение 100.
        return any(m.start() <= start < m.end() for m in ranges)

    def take(spec: CharacteristicSpec, start: int, end: int, number: str, unit: str) -> None:
        if spec.name in found or any(e.name == spec.name for e in out):
            return
        if _bound_before(text, start) or _covers([*found.values(), *out], field, start, end):
            return
        out.append(_extracted(spec, field, text, start, end, number, unit))
        used.append((start, end))

    for m in _AREAL.finditer(text):
        spec = _areal_spec(specs)
        if spec is not None and not in_range(m.start("num")):
            take(spec, m.start(), m.end(), m.group("num"), "г/м2")
    for m in _GRAM.finditer(text):
        if any(a <= m.start() < b for a, b in used):
            continue
        spec = _areal_spec(specs)
        if spec is not None and not in_range(m.start("num")):
            take(spec, m.start(), m.end(), m.group("num"), "г/м2")
    for m in _COUNT.finditer(text):
        unit = normalize_unit(m.group("unit")) or "шт"
        number = _number(m.group("num"))
        if number is None:
            continue
        fits = [
            s
            for s in _matching(specs, number)
            if _spec_unit(s) is not None and convert(1.0, unit, _spec_unit(s) or "") is not None
        ]
        if len(fits) != 1 or in_range(m.start("num")):
            continue
        take(fits[0], m.start(), m.end(), m.group("num"), unit)
    return out


def _marked(text: str, start: int) -> bool:
    words = _WORD.findall(text[:start])
    prev = words[-1].casefold().replace("ё", "е").rstrip(".") if words else ""
    return prev in _MARKERS


def _with_unit(text: str, end: int) -> bool:
    """После числа единица («100 шт»): это не голое число — «100 шт» не «100 пар»."""
    nxt = _WORD.match(text, end + len(text[end:]) - len(text[end:].lstrip()))
    return bool(nxt and normalize_unit(nxt[0]))


def _dimension(text: str, start: int, end: int) -> bool:
    return bool(re.search(r"[xх×*]\s*$", text[:start], re.IGNORECASE)) or bool(
        re.match(r"\s*[xх×*]\s*\d", text[end:], re.IGNORECASE)
    )


def bare_values(
    field: Field, text: str, specs: list[CharacteristicSpec], found: dict[str, Extracted]
) -> list[Extracted]:
    """«А4 80 500»: число без единицы — в единственную характеристику, которой оно подходит."""
    out: list[Extracted] = []
    occupied = [(e.source.start, e.source.end) for e in found.values() if e.source.field == field]
    ranged = [(m.start(), m.end()) for m in _RANGE.finditer(text)]
    for m in _BARE.finditer(text):
        start, end = m.start(), m.end()
        if any(not (end <= a or start >= b) for a, b in (*occupied, *ranged)):
            continue
        if (
            _marked(text, start)
            or _dimension(text, start, end)
            or _with_unit(text, end)
            or _bound_before(text, start)
            or _YEAR.match(m.group("num"))
        ):
            continue
        number = _number(m.group("num"))
        if number is None:
            continue
        fits = _matching(specs, number)
        known = {e.name for e in found.values()} | {e.name for e in out}
        fits = [s for s in fits if s.name not in known]
        if len(fits) != 1:
            continue
        out.append(
            _extracted(fits[0], field, text, start, end, m.group("num"), _spec_unit(fits[0]))
        )
        occupied.append((start, end))
    return out


# «-1510 Вт», «минус три»; знак минуса U+2212 и тире вплотную к числу — тоже минус. С пробелом
# («оперативная память - 16 ГБ») тире — разделитель метки и значения.
_MINUS_BEFORE = re.compile(
    r"(?:(?<![\p{L}\p{N}])минус\s*|(?:(?<=[\s:|(=])|^)[-\u2212–])\Z", re.IGNORECASE
)
_TEMPERATURE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:°|градус|град(?!\p{L})|[cс](?!\p{L}))", re.IGNORECASE
)


def negative_at(text: str, start: int) -> bool:
    """Перед числом минус («Мощность: -1510 Вт», «минус три»), а не дефис диапазона «48 - 50»
    и не температура «-30 °C» (у неё минус бывает)."""
    before = text[:start]
    m = _MINUS_BEFORE.search(before)
    if m is None:
        return False
    if re.search(r"\d\s*\Z", before[: m.start()]):
        return False  # «48 - 50» — диапазон
    if re.search(r"(?:^|\s)(?:до|от)\s*\Z", before[: m.start()], re.IGNORECASE):
        return False  # «до -30» — граница температуры
    return not _TEMPERATURE.match(text, start)


def negative_notes(name: str, description: str) -> list[tuple[str, str, str]]:
    """Отрицательное число у величины, которая так не бывает: значение в поле не пишется, а
    поставщик видит замечание (`warn`)."""
    out = []
    for field, text in (("name", name), ("description", description)):
        for m in _BARE.finditer(text or ""):
            if negative_at(text, m.start()):
                piece = text[max(0, m.start() - 6) : m.end() + 4].strip()
                out.append(
                    (
                        field,
                        f"Отрицательное значение в «{piece}»: в поле не записано.",
                        "Минус у размера, массы, мощности или количества не бывает — проверьте "
                        "число.",
                    )
                )
    return out


def numeric_notes(
    name: str, description: str, specs: list[CharacteristicSpec], extracted: list[Extracted]
) -> list[tuple[str, str, str]]:
    """Числа, которые выглядят как характеристика, но не легли в одно поле.

    Год, артикул после «ГОСТ»/«ISO» и число, которого нет ни в одной характеристике кандидата,
    предупреждения не дают.
    """
    notes: list[tuple[str, str, str]] = []
    for field, text in (("name", name), ("description", description)):
        if not text:
            continue
        for m in _RANGE.finditer(text):
            a, b = _number(m.group("a")), _number(m.group("b"))
            if a is None or b is None or a == b:
                continue
            both = [s for s in specs if _contains(s, a) and _contains(s, b)]
            if len(both) != 1:
                continue
            shown = text[m.start() : m.end()]
            notes.append(
                (
                    field,
                    f"Диапазон «{shown}» не выбирает одно значение «{both[0].name}».",
                    "Оба края есть у одной характеристики, поэтому позиция по нему не сужается.",
                )
            )
        phones = [p.span() for p in PHONE.finditer(text)]
        for m in _BARE.finditer(text):
            start, end = m.start(), m.end()
            if _covers(extracted, field, start, end):
                continue
            if any(a <= start and end <= b for a, b in phones):
                continue  # цифры телефона — о нём замечание персональных данных, не о полях
            if any(rm.start() <= start and end <= rm.end() for rm in _RANGE.finditer(text)):
                continue
            if _marked(text, start) or _dimension(text, start, end) or _YEAR.match(m.group("num")):
                continue
            number = _number(m.group("num"))
            if number is None:
                continue
            fits = _matching(specs, number)
            if len(fits) < 2:
                # Ноль совпадений — число не про характеристику кандидата. Одно — его уже забрали
                # или оно внутри уже разобранного фрагмента.
                if len(fits) == 1 and not _covers(extracted, field, start, end):
                    notes.append(
                        (
                            field,
                            f"Число «{m.group('num')}» похоже на «{fits[0].name}», но не взято.",
                            "Его нельзя однозначно отделить от соседнего текста.",
                        )
                    )
                continue
            listed = ", ".join(f"«{s.name}»" for s in fits[:4])
            notes.append(
                (
                    field,
                    f"Число «{m.group('num')}» подходит нескольким характеристикам: {listed}.",
                    "Без единицы или подписи нельзя выбрать одну, поэтому значение не подставлено.",
                )
            )
    return notes
