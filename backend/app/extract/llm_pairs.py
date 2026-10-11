"""Первый шаг разбора: модель делит текст поставщика на пары «название: значение».

Дальше пары раскладывает код, а не модель: строка «Название: значение» — формат, который правила
читают надёжнее всего. Имя ищется среди характеристик КТРУ и типа (`rules`), затем среди имён
характеристик портала, и только потом заводится новая характеристика (`extra`, её подтверждает
модератор; ту, что к товару не относится, отсеивает `llm_fill.find_oddities`).

Ответ модели — не истина. Пара принимается, только если цитата дословно есть в тексте, а значение
дословно есть в цитате: модель решает, где проходит граница между названием и значением и как
назвать свойство без подписи, но значения не придумывает. Куски текста, которые не вошли ни в одну
принятую пару, остаются в описании как были — ничего не теряется.
"""

import logging
from typing import Protocol

import regex as re
from pydantic import BaseModel

from app.ingest.units import normalize_unit
from app.llm import ChatResult, LLMOutputError, Message
from app.schemas import Span
from app.values import fold

log = logging.getLogger(__name__)

MAX_NAME = 60
MAX_VALUE = 120
MAX_PAIRS = 40
# Короче — нечего делить: наименование без описания читают правила.
MIN_TEXT = 40

SYSTEM = (
    "Ты разбираешь текст поставщика о товаре для каталога госзакупок: делишь его на характеристики "
    'вида «название: значение». Отвечай только JSON по схеме {"pairs": [{"name", "value", '
    '"quote"}]}.\n'
    "1. quote — фрагмент текста, скопированный без изменений, в котором эта характеристика "
    "записана целиком: подпись и значение.\n"
    "2. value — значение характеристики, скопированное из quote без изменений: число вместе с "
    "единицей, перечень через запятую — целиком одним значением.\n"
    "3. name — название характеристики. Если в тексте есть подпись — возьми её как написана. Если "
    "подписи нет, а по смыслу ясно, какое это свойство, назови его коротко общепринятым "
    "названием.\n"
    "4. Каждое свойство товара из текста — отдельной парой: ничего не пропускай и ничего не "
    "добавляй от себя. Число внутри перечня или обозначения модели — не отдельная "
    "характеристика.\n"
    "5. Само наименование товара, реклама, цена, доставка, номера и коды учёта — не "
    "характеристики: их не включай."
)


class Pair(BaseModel):
    name: str
    value: str
    quote: str


class Pairs(BaseModel):
    pairs: list[Pair]


class TextPair(BaseModel):
    """Пара, принятая кодом: название, значение и место цитаты в тексте поставщика."""

    name: str
    value: str
    source: Span


class PairsChat(Protocol):
    async def chat(
        self, messages: list[Message], *, schema: type[Pairs], temperature: float = 0.0
    ) -> ChatResult: ...


_WORD = re.compile(r"\p{L}{3,}")
_PIECE = re.compile(r"[^\n;|]+")
_CONTENT = re.compile(r"[\p{L}\p{N}]{2,}")


def _locate(quote: str, name: str, description: str) -> Span | None:
    q = quote.strip()
    if not q:
        return None
    for field, text in (("description", description), ("name", name)):
        at = text.find(q)
        if at >= 0:
            return Span(field=field, start=at, end=at + len(q), text=q)  # type: ignore[arg-type]
    return None


_NUMBER_TAIL = re.compile(r"(?:[.,]?\d)+")
_UNIT_NEXT = re.compile(r"\s*([^\s\d,;|()]+)")


def _whole_quantity(value: str, quote: str) -> str:
    """Число — целиком и с единицей, как в цитате: «1010» из «Вес 1010.00000 г» → «1010.00000 г».

    Модель обрывала число на точке и теряла единицу: 1010 граммов становились 1010 килограммами
    позиции. Границу числа и его единицу код берёт из текста поставщика сам."""
    at = quote.casefold().find(value.casefold())
    if at < 0 or not value[-1:].isdigit():
        return value
    end = at + len(value)
    if tail := _NUMBER_TAIL.match(quote, end):
        end = tail.end()
    unit = _UNIT_NEXT.match(quote, end)
    if unit and normalize_unit(unit.group(1).rstrip(".")) is not None:
        end = unit.end()
    return quote[at:end]


def accept_pairs(reply: Pairs, name: str, description: str) -> list[TextPair]:
    """Пары ответа, прошедшие проверку кодом; порядок — как в тексте поставщика."""
    out: list[TextPair] = []
    seen: set[str] = set()
    for p in reply.pairs:
        label, value = " ".join(p.name.split()).strip(" :"), " ".join(p.value.split())
        span = _locate(p.quote, name, description)
        if span is None or not value or fold(value) not in fold(" ".join(span.text.split())):
            continue  # цитаты нет в тексте или значения нет в цитате — придумано
        # Число модель могла оборвать вместе с цитатой («Вес 1010» из «Вес 1010.00000 г»): хвост
        # числа и единицу код дочитывает из самого текста, цитата растёт до конца значения.
        text = name if span.field == "name" else description
        at = span.text.casefold().find(value.casefold())
        if at >= 0:
            whole = _whole_quantity(value, text[span.start + at :])
            end = max(span.end, span.start + at + len(whole))
            value = whole
            span = Span(field=span.field, start=span.start, end=end, text=text[span.start : end])
        if not _WORD.search(label) or len(label) > MAX_NAME or len(value) > MAX_VALUE:
            continue
        if fold(label) == fold(value) or fold(label) in seen:
            continue
        seen.add(fold(label))
        out.append(TextPair(name=label, value=value, source=span))
    out.sort(key=lambda t: (t.source.field != "name", t.source.start))
    return out[:MAX_PAIRS]


async def split_pairs(name: str, description: str, llm: PairsChat) -> list[TextPair]:
    """Пары «название: значение» из текста поставщика; пусто — модель не помогла.

    LLMError (нет связи) — наружу."""
    if len(f"{name} {description}".strip()) < MIN_TEXT:
        return []
    messages: list[Message] = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": f"Наименование: {name}\nОписание: {description or '(нет)'}",
        },
    ]
    for attempt in (1, 2):
        try:
            result = await llm.chat(messages, schema=Pairs)
        except LLMOutputError as exc:
            log.warning("разбор текста на пары не прошёл схему (попытка %d): %s", attempt, exc)
            continue
        if isinstance(result.parsed, Pairs):
            pairs = accept_pairs(result.parsed, name, description)
            log.info("пары модели: %d, принято %d", len(result.parsed.pairs), len(pairs))
            return pairs
        break
    return []


def structured_text(description: str, pairs: list[TextPair]) -> str:
    """Описание для разбора кодом: принятые пары строками «Название: значение», затем куски
    исходного описания, которые не вошли ни в одну пару, — как были."""
    if not pairs:
        return description
    covered = sorted(
        (p.source.start, p.source.end) for p in pairs if p.source.field == "description"
    )
    # Остаток — всё, что не покрыто цитатами принятых пар, посимвольно: кусок между двумя парами
    # одной строки («…, обивка из ткани, …», «Звоните!») не теряется.
    gaps: list[str] = []
    at = 0
    for start, end in covered:
        gaps.append(description[at:start])
        at = max(at, end)
    gaps.append(description[at:])
    left = [
        text
        for gap in gaps
        for piece in _PIECE.finditer(gap)
        if _CONTENT.search(text := piece.group().strip(" \t,.|"))
    ]
    return "\n".join([*(f"{p.name}: {p.value}" for p in pairs), *left])
