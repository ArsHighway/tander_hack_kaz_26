"""Заполнение на живых карточках портала: что модуль извлёк из наименования и описания против того,
что поставщик ввёл в характеристики.

Значение портала — не эталон (поставщики ошибаются), поэтому метрика называется «совпадение с
порталом». Полнота считается по значениям, которые видны в тексте карточки: характеристику, о
которой текст молчит, модуль честно оставляет пустой.
"""

from typing import Literal

import regex as re
from pydantic import BaseModel

from app.evaluation.metrics import MetricRow
from app.extract import extract_rules
from app.ingest.characteristics import to_number
from app.ingest.units import normalize_unit
from app.portal.cards import PortalCard, PortalChar
from app.portal.values import ValueIndex, card_specs, strip_gloss
from app.search.text import tokens
from app.values import convert, fit_value, fold

# Основа слова и форма: «белая» — «Белый», не длиннее основы на столько букв.
MAX_ENDING = 3
MIN_STEM = 4


class CharOutcome(BaseModel):
    card_id: int
    name: str
    truth: str
    unit: str | None = None
    numeric: bool = False
    extracted: str | None = None
    source_field: Literal["name", "description"] | None = None
    source_text: str | None = None
    source_start: int | None = None
    evidenced: bool = False
    correct: bool = False
    # Извлечено часть значения или значение с лишним словом: «жидкое» против «мыло жидкое».
    partial: bool = False


def _same_number(a: str, b: str) -> bool:
    x, y = to_number(a), to_number(b)
    return x is not None and y is not None and abs(x - y) <= 1e-9 * max(1.0, abs(y))


def _stem_match(word: str, stem: str) -> bool:
    return word == stem or (
        len(stem) >= MIN_STEM and word.startswith(stem) and len(word) - len(stem) <= MAX_ENDING
    )


_GLOSS_TEXT = re.compile(r"\(([^)]*)\)")


def _gloss(value: str) -> list[str]:
    """Пояснения в скобках: «Нитрильный латекс (нитрил)» → «нитрил»."""
    return _GLOSS_TEXT.findall(value)


def _squash(value: str) -> str:
    """Без пробелов, дефисов и скобок: «5W-30» и «5W30», «10 (XL)» и «10 XL»."""
    return _NOT_WORD.sub("", fold(value))


_NOT_WORD = re.compile(r"[\W_]+")


def same_value(value: str, truth: PortalChar, unit: str | None = None) -> bool:
    """Совпадение с портальным значением; пояснение в скобках («желтый (yellow)») не в счёт.

    `unit` — единица извлечённого числа: «80» мм и «8» см — одно значение.
    """
    if truth.numeric:
        src, dst = normalize_unit(unit) or unit, normalize_unit(truth.unit) or truth.unit
        x = to_number(value)
        if x is not None and src and dst and src != dst and (c := convert(x, src, dst)) is not None:
            want = to_number(truth.value)
            return want is not None and _close(c, want)
        return _same_number(value, truth.value)
    variants = {_squash(truth.value), _squash(strip_gloss(truth.value))}
    variants |= {_squash(g) for g in _gloss(truth.value)}
    if _squash(value) and _squash(value) in variants:
        return True
    value, expected = strip_gloss(value), strip_gloss(truth.value)
    if fold(value) == fold(expected):
        return True
    a, b = tokens(value), tokens(expected)
    return (
        bool(a) and len(a) == len(b) and all(_stem_match(x, y) for x, y in zip(a, b, strict=True))
    )


def partial_value(value: str, truth: PortalChar) -> bool:
    """Слова одного значения входят в другое, но значения не равны: «жидкое» и «мыло жидкое»."""
    if truth.numeric or same_value(value, truth):
        return False
    a, b = set(tokens(value)), set(tokens(strip_gloss(truth.value)))
    return bool(a) and bool(b) and (a < b or b < a)


_N = r"[0-9]+(?:[.,][0-9]+)?"
# Число — не часть кода («SVA30», «ВА47») и не хвост дроби («1.5» — не «5»).
# Число — не часть кода: «SVA30», «ВА47», «SL-P-01», «UM330-B-0», «9393-001».
_FREE = r"(?<![\p{L}\p{N}])(?<![\p{N}][.,])(?<![\p{L}\p{N}]-)(?!0[0-9])"
_DIMS = re.compile(rf"{_FREE}({_N})(?:\s*[xхХX×*]\s*({_N}))+(?:\s*(\p{{L}}+))?")
_NUMBER = re.compile(rf"{_FREE}({_N})(?![\p{{N}}]|[.,][0-9])(?:\s*([\p{{L}}°%][\p{{L}}·*]*))?")
_COUNT = {"шт", "лист", "пар"}
_LENGTH = "мм"


def _num(text: str) -> float:
    return float(text.replace(",", "."))


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-6 * max(1.0, abs(b))


def _number_seen(truth: PortalChar, text: str) -> bool:
    """То же число в тексте с совместимой единицей (с пересчётом: «1.8 м» — «1800 мм»).

    Число внутри кода модели, с чужой единицей («50А» для «50 Гц») или из размеров «148x120мм» для
    не длины — не улика.
    """
    want = to_number(truth.value)
    if want is None:
        return False
    target = normalize_unit(truth.unit) or truth.unit
    is_length = target is not None and convert(1.0, _LENGTH, target) is not None
    taken: list[tuple[int, int]] = []
    for m in _DIMS.finditer(text):
        taken.append(m.span())
        if not is_length or target is None:
            continue
        unit = normalize_unit(m[3]) or target
        for n in [m[1], *m.captures(2)]:
            got = convert(_num(n), unit, target)
            if got is not None and _close(got, want):
                return True
    for m in _NUMBER.finditer(text):
        if any(a <= m.start() < b for a, b in taken):
            continue
        n = _num(m[1])
        num_unit = normalize_unit(m[2]) if m[2] else None
        if num_unit is None:
            # Без единицы или со счётным словом («6 розеток») — улика для числа без единицы или
            # счётного; «12 CH» для «12 мес» — нет.
            if _close(n, want) and (not m[2] or target is None or target in _COUNT):
                return True
            continue
        if target is None:
            # У поставщика без единицы, в тексте с единицей: «Напряжение = 12» при «12В».
            if _close(n, want):
                return True
            continue
        got = convert(n, num_unit, target)
        # «500л» в наименовании бумаги — листы, а не литры.
        if got is None and num_unit == "л" and target in _COUNT:
            got = n
        if got is not None and _close(got, want):
            return True
    return False


def evidenced(truth: PortalChar, text: str) -> bool:
    """Значение видно в тексте карточки: то же число или те же слова (в любой форме)."""
    if truth.numeric:
        return _number_seen(truth, text)
    need = tokens(truth.value)
    words = tokens(text)
    return bool(need) and all(any(_stem_match(w, s) for w in words) for s in need)


def compare_card(card: PortalCard, index: ValueIndex) -> list[CharOutcome]:
    specs = {fold(s.name): s for s in card_specs(card, index)}
    found = {
        fold(e.name): e for e in extract_rules(card.name, card.description, list(specs.values()))
    }
    out = []
    for ch in card.characteristics:
        spec = specs.get(fold(ch.name))
        if spec is None:
            continue
        e = found.get(fold(ch.name))
        extracted = None
        if e is not None:
            fit = fit_value(e.raw_value, e.raw_unit, spec)
            extracted = fit.value or e.raw_value
        out.append(
            CharOutcome(
                card_id=card.id,
                name=ch.name,
                truth=ch.value,
                unit=ch.unit,
                numeric=ch.numeric,
                extracted=extracted,
                source_field=e.source.field if e else None,
                source_text=e.source.text if e else None,
                source_start=e.source.start if e else None,
                evidenced=evidenced(ch, card.text),
                correct=extracted is not None and same_value(extracted, ch, spec.unit),
                partial=extracted is not None and partial_value(extracted, ch),
            )
        )
    return out


def fill_rows(outcomes: list[CharOutcome]) -> list[MetricRow]:
    extracted = [o for o in outcomes if o.extracted is not None]
    seen = [o for o in outcomes if o.evidenced]
    return [
        MetricRow(
            key="portal_evidence",
            title="Портал: значение характеристики видно в тексте карточки",
            value=len(seen) / len(outcomes) if outcomes else 0.0,
            n=len(outcomes),
            how="доля значений, введённых поставщиком, которые есть в наименовании или описании "
            "(то же число или те же слова); остальное текст карточки не содержит",
        ),
        MetricRow(
            key="portal_fill_precision",
            title="Портал: точность заполнения",
            value=sum(o.correct for o in extracted) / len(extracted) if extracted else 0.0,
            n=len(extracted),
            how="доля значений, извлечённых модулем правилами, которые совпали с введёнными "
            "поставщиком; перечень характеристики взят из других карточек категории",
        ),
        MetricRow(
            key="portal_fill_recall",
            title="Портал: полнота по видимым в тексте значениям",
            value=sum(o.correct for o in seen) / len(seen) if seen else 0.0,
            n=len(seen),
            how="доля значений, видимых в тексте карточки, которые модуль заполнил так же, "
            "как поставщик",
        ),
    ]
