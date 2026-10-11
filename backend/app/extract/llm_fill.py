"""Извлечение характеристик моделью — только для того, что не взяли правила.

Ответ модели — не истина: элемент принимается, если имя есть среди характеристик позиции, цитата
дословно есть в тексте поставщика и содержит значение, а значение подгоняется к перечню
(`fit_value`). Позицию КТРУ такие значения не выбирают (`pipeline._known_from_text`).
Остальное отбрасывается.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol

import regex as re

from app.catalog import CharacteristicSpec
from app.extract.model import (
    Extracted,
    ListMatch,
    ListSuggestion,
    LLMItems,
    TypeOrder,
    TypeRanking,
)
from app.extract.quantities import negative_at
from app.extract.rules import foreign_unit_in_quote, unit_in_quote
from app.glossary import Glossary
from app.ingest.units import normalize_unit
from app.llm import ChatResult, LLMError, LLMInputError, LLMOutputError, Message
from app.schemas import Span
from app.search.text import tokens
from app.values import convert, fit_value, fold, parse_bucket

log = logging.getLogger(__name__)

_REJECTED = {"not_in_list", "unit_mismatch", "unparsed"}
_MAX_VALUES = 30
_NUM = re.compile(r"[0-9]+(?:[.,][0-9]+)?")
# Общее начало слова цитаты и значения: «неопудренных» — «Неопудренные», «нитриловые» — «Нитрил».
MIN_COMMON_PREFIX = 4

SYSTEM = (
    "Ты помогаешь заполнить карточку товара в каталоге госзакупок: находишь в тексте поставщика "
    "значения характеристик из заданного списка. О товаре ты знаешь только этот текст. "
    'Отвечай только JSON по схеме {"items": [{"name", "value", "unit", "quote"}]}; пустой список — '
    "нормальный ответ.\n"
    "Правила:\n"
    "1. name — строго из списка характеристик, как он там написан.\n"
    "2. quote — точный фрагмент текста поставщика, скопированный без изменений; значение должно "
    "быть видно в нём. Нет такой цитаты — не включай элемент.\n"
    "3. Включай только то, что текст говорит прямо. Не выбирай значение «как обычно у такого "
    "товара», не выводи его из названия товара или бренда, не добавляй цвет, материал, размер или "
    "количество, которых в тексте нет.\n"
    "4. Если у характеристики есть перечень допустимых значений, value берётся из перечня, и "
    "только когда текст говорит то же самое по смыслу: другая форма слова, сокращение, другая "
    "единица. Если в тексте иное слово, которого в перечне нет, не подставляй близкое или более "
    "частое значение — не включай элемент.\n"
    "5. Число и единицу пиши так, как они записаны в тексте. Единицы не пересчитывай и не "
    "округляй: пересчёт делает программа.\n"
    "6. Одно значение на характеристику. Если текст называет несколько разных значений одной "
    "характеристики, между ними не выбирай — не включай элемент.\n"
    "7. Одна цитата — одна характеристика. Значение с подписью («ширина 40 мм») относится только "
    "к характеристике с этой подписью: не клади его в «Толщину», «Длину» или другую.\n"
    "8. Подпись в тексте может быть с опечаткой или другим словом («Мощьность», «Ширина "
    "полотна» — «Ширина лезвия»): относи значение к характеристике из списка по смыслу подписи.\n"
    "9. Значение перечня, сказанное другими словами с тем же смыслом («встроенный аккумулятор» "
    "= «От аккумуляторных батарей», «дерево» = «Массив дерева» только если в тексте сказано про "
    "массив), пиши как в перечне, а quote — слова текста.\n"
    "10. Диапазон («170–176», «48–50») и состав («65% хлопок») пиши как в тексте, не выбирай край."
)


class ChatLLM(Protocol):
    async def chat(
        self, messages: list[Message], *, schema: type[LLMItems], temperature: float = 0.0
    ) -> ChatResult: ...


def _spec_line(s: CharacteristicSpec) -> str:
    parts = [f"- {s.name}"]
    if s.unit:
        parts.append(f"единица позиции {s.unit}")
    if s.values and s.group != "general":  # производители и страны — подсказки, а не выбор
        shown = s.values[:_MAX_VALUES]
        parts.append(
            "допустимые значения: "
            + "; ".join(shown)
            + (" …" if len(s.values) > _MAX_VALUES else "")
        )
    elif s.range is not None:
        parts.append(f"диапазон {s.range.min}–{s.range.max}")
    return ", ".join(parts)


def build_messages(
    name: str, description: str, specs: list[CharacteristicSpec], glossary: Glossary
) -> list[Message]:
    text = f"Наименование: {name}\nОписание: {description or '(нет)'}"
    abbrs = glossary.prompt_block(f"{name}\n{description}")
    user = (
        "Характеристики позиции (имя, единица позиции, допустимые значения). Единицу в ответе "
        "указывай ту, что в тексте; допустимое значение выбирай, только если смысл в тексте тот "
        "же:\n" + "\n".join(_spec_line(s) for s in specs)
    )
    if abbrs:
        user += f"\n\nСокращения в тексте:\n{abbrs}"
    user += (
        f"\n\nТекст поставщика:\n{text}\n\n"
        "Выведи только характеристики, значение которых названо в этом тексте, с точной цитатой."
    )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def _quote_has_value(quote: str, value: str, glossary: Glossary) -> bool:
    """Значение видно в цитате: числа — те же, слова — та же основа (с расшифровкой сокращений)."""
    nums = {n.replace(",", ".") for n in _NUM.findall(value)}
    if nums:
        found = {n.replace(",", ".") for n in _NUM.findall(quote)}
        bucket = parse_bucket(value)
        if bucket is not None and not bucket.is_point:  # «>=80 ... <90» и «80 г/м2» в цитате
            return any(bucket.contains(float(n)) for n in found)
        return nums <= found
    words = set(tokens(glossary.expand(quote))) | set(tokens(quote))
    if len(value.strip()) <= 2:  # «A», «M», «XL»: только целым словом
        return fold(value.strip()) in {fold(w) for w in re.findall(r"[\p{L}\p{N}]+", quote)}
    need = tokens(value)
    return bool(need) and any(any(_same_stem(v, w) for w in words) for v in need)


def _same_stem(a: str, b: str) -> bool:
    if a == b:
        return True
    n = min(len(a), len(b))
    return n >= MIN_COMMON_PREFIX and a[:n] == b[:n]


def _unit_invented(quote: str, value: str, unit: str | None) -> bool:
    """Единица модели другой величины, чем единица у числа в цитате (у характеристики портала
    единицы может не быть, и проверка чужой единицы её не видит)."""
    given, said = normalize_unit(unit), unit_in_quote(quote, value)
    if given is None or said is None or given == said:
        return False
    return convert(1.0, said, given) is None


def _unit_as_written(
    quote: str, value: str, unit: str | None, spec: CharacteristicSpec
) -> str | None:
    """Единица так, как её записал поставщик. Модель могла подписать число «мм», а в тексте «м»:
    пересчитывает код (`fit_value`), и без единицы текста «20 метров» стали бы «20 мм»."""
    said = unit_in_quote(quote, value)
    if said is None:
        return unit
    given = normalize_unit(unit)
    if given is not None:
        return said if convert(1.0, said, given) is not None else unit
    target = normalize_unit(spec.unit)
    if target is not None and convert(1.0, said, target) is not None:
        return said
    return unit


def _locate(quote: str, name: str, description: str) -> Span | None:
    q = quote.strip()
    if not q:
        return None
    fields: tuple[tuple[Literal["name", "description"], str], ...] = (
        ("name", name),
        ("description", description),
    )
    for field, text in fields:
        at = text.find(q)
        if at >= 0:
            return Span(field=field, start=at, end=at + len(q), text=q)
    return None


MATCH_SYSTEM = (
    "Ты сверяешь значение характеристики товара, записанное поставщиком, с допустимыми значениями "
    'справочника госзакупок. Отвечай только JSON по схеме {"value", "quote"}.\n'
    "Выбери из допустимых то значение, которое значит то же самое, что слово поставщика: другая "
    "форма слова, сокращение, синоним. Если ни одно не значит то же самое, верни "
    '{"value": null, "quote": ""}.\n'
    "Не выбирай близкое по смыслу, более распространённое или подходящее товару значение: «глина» "
    "не «дерево» и не «керамика», если глины в списке нет. Значение пиши ровно как в списке.\n"
    "quote — точный фрагмент текста поставщика, где стоит его слово."
)


class ListChat(Protocol):
    async def chat(
        self, messages: list[Message], *, schema: type[ListMatch], temperature: float = 0.0
    ) -> ChatResult: ...


def build_match_messages(
    name: str, description: str, spec: CharacteristicSpec, supplier_value: str, quote: str
) -> list[Message]:
    shown = spec.values[:_MAX_VALUES]
    user = (
        f"Характеристика: {spec.name}\n"
        "Допустимые значения: "
        + "; ".join(shown)
        + (" …" if len(spec.values) > _MAX_VALUES else "")
        + f"\nЗначение поставщика: {supplier_value}"
        + (f"\nГде оно записано: {quote}" if quote.strip() else "")
        + f"\n\nТекст поставщика:\nНаименование: {name}\nОписание: {description or '(нет)'}"
    )
    return [{"role": "system", "content": MATCH_SYSTEM}, {"role": "user", "content": user}]


async def match_to_list(
    name: str,
    description: str,
    spec: CharacteristicSpec,
    supplier_value: str,
    quote: str,
    llm: ListChat,
    glossary: Glossary,
) -> ListSuggestion | None:
    """Какое допустимое значение КТРУ значит то же, что слово поставщика, — или None.

    Принимается, только если значение ровно из перечня, а цитата ответа есть в тексте поставщика и
    содержит его исходное слово. Результат — предложение: код не может проверить, что смысл не
    сменился, поэтому в поле значение не подставляется, а показывается кнопкой. LLMError
    (нет связи) — наружу."""
    if not spec.values or not supplier_value.strip():
        return None
    messages = build_match_messages(name, description, spec, supplier_value, quote)
    reply: ListMatch | None = None
    for attempt in (1, 2):
        try:
            result = await llm.chat(messages, schema=ListMatch)
        except LLMOutputError as exc:
            log.warning("ответ модели о перечне не прошёл схему (попытка %d): %s", attempt, exc)
            continue
        reply = result.parsed if isinstance(result.parsed, ListMatch) else None
        break
    if reply is None or reply.value is None or reply.value not in spec.values:
        return None
    span = _locate(reply.quote, name, description)
    if span is None or not _quote_has_value(reply.quote, supplier_value, glossary):
        return None  # цитаты нет в тексте или в ней нет слова поставщика: сопоставление выдумано
    return ListSuggestion(value=reply.value, source=span)


class ModelPolicy(Protocol):
    """Что нужно от `Deps`, чтобы спрашивать модель по тем же правилам, что и `extract_llm`."""

    llm: Any
    glossary: Glossary
    llm_enabled: bool
    llm_budget_s: float | None
    llm_paused_until: float


async def _guarded[T](
    deps: ModelPolicy, call: Callable[[], Awaitable[T | None]], fallback: str
) -> T | None:
    """Вопрос к модели по правилам шага: выключена модератором или стоит на паузе — запроса нет;
    не дольше `llm_budget_s`; отказ сервиса ставит паузу, плохой вход (400, схема) — нет."""
    if not deps.llm_enabled or time.monotonic() < deps.llm_paused_until:
        return None
    try:
        if deps.llm_budget_s is None:
            return await call()
        return await asyncio.wait_for(call(), timeout=deps.llm_budget_s)
    except TimeoutError:
        log.warning("LLM не ответила за %.1f с — %s", deps.llm_budget_s or 0.0, fallback)
        return None
    except LLMError as exc:
        log.warning("LLM недоступна, %s: %s", fallback, exc)
        if not isinstance(exc, LLMInputError | LLMOutputError):
            from app.pipeline import LLM_COOLDOWN_S  # здесь: pipeline импортирует этот модуль

            deps.llm_paused_until = time.monotonic() + LLM_COOLDOWN_S
        return None


async def suggest_list_value(
    deps: ModelPolicy,
    name: str,
    description: str,
    spec: CharacteristicSpec,
    supplier_value: str,
    quote: str,
) -> ListSuggestion | None:
    """`match_to_list` по правилам шага (`_guarded`)."""
    if not spec.values:
        return None
    return await _guarded(
        deps,
        lambda: match_to_list(
            name, description, spec, supplier_value, quote, deps.llm, deps.glossary
        ),
        "значение останется «нет в КТРУ»",
    )


RERANK_SYSTEM = (
    "Ты помогаешь выбрать тип товара из справочника госзакупок по описанию поставщика. Ниже "
    'наименования типов. Отвечай только JSON по схеме {"order": [...], "reason": "..."}.\n'
    "order — наименования из списка, ровно как они написаны, от самого подходящего описанию к "
    "менее подходящему. Не добавляй наименований, которых нет в списке. Выбирай по тому, что "
    "сказано в тексте, а не по тому, какой тип встречается чаще.\n"
    "reason — одна короткая фраза: чем первый тип подходит лучше остальных, словами текста. Числа "
    "и единицы в reason не пиши и не исправляй: «50 км» не «50 см»."
)
_REASON_MAX = 200


class RerankChat(Protocol):
    async def chat(
        self, messages: list[Message], *, schema: type[TypeRanking], temperature: float = 0.0
    ) -> ChatResult: ...


async def rerank_types(text: str, names: list[str], llm: RerankChat) -> TypeOrder | None:
    """Порядок наименований типов КТРУ по мнению модели; None — оставить исходный порядок.

    Ответ принимается, только если все его элементы — наименования из списка (иначе модель
    придумала тип). Пропущенные наименования идут следом в исходном порядке: кандидат не теряется.
    LLMError (нет связи) — наружу."""
    if len(names) < 2:
        return None
    user = "Типы товара:\n" + "\n".join(f"- {n}" for n in names) + f"\n\nТекст поставщика:\n{text}"
    messages: list[Message] = [
        {"role": "system", "content": RERANK_SYSTEM},
        {"role": "user", "content": user},
    ]
    reply: TypeRanking | None = None
    for attempt in (1, 2):
        try:
            result = await llm.chat(messages, schema=TypeRanking)
        except LLMOutputError as exc:
            log.warning("ответ модели о типах не прошёл схему (попытка %d): %s", attempt, exc)
            continue
        reply = result.parsed if isinstance(result.parsed, TypeRanking) else None
        break
    if reply is None or not reply.order:
        return None
    known = set(names)
    if any(n not in known for n in reply.order):
        return None
    chosen = list(dict.fromkeys(reply.order))
    ordered = [*chosen, *(n for n in names if n not in set(chosen))]
    return TypeOrder(names=ordered, reason=reply.reason.strip()[:_REASON_MAX])


async def suggest_type_order(deps: ModelPolicy, text: str, names: list[str]) -> TypeOrder | None:
    """`rerank_types` по правилам шага (`_guarded`)."""
    if len(names) < 2:
        return None
    return await _guarded(
        deps, lambda: rerank_types(text, names, deps.llm), "порядок типов остаётся прежним"
    )


def _lost_minus(quote: str, value: str) -> bool:
    """В цитате у числа значения минус, а в значении его нет."""
    number = value.strip().split(" ")[0]
    if not number[:1].isdigit():
        return False
    at = quote.find(number)
    return at >= 0 and negative_at(quote, at)


_LABEL_SKIP = set(tokens("вид тип наличие количество материал значение характеристика"))


def _label_words(name: str) -> set[str]:
    """Основы имени характеристики, по которым её узнают в тексте («Ширина» → «ширин»)."""
    return {s for s in tokens(name) if len(s) >= 4 and s not in _LABEL_SKIP}


def _other_label(quote: str, spec: CharacteristicSpec, specs: list[CharacteristicSpec]) -> bool:
    """Цитата подписана другой характеристикой, а своей подписи в ней нет: «ширина 40 мм» не о
    толщине. Модель так перекладывает одно значение во второе поле."""
    words = set(tokens(quote))
    if _label_words(spec.name) & words:
        return False
    return any(_label_words(s.name) & words for s in specs if s.name != spec.name)


MAX_LLM_VALUE = 120


def _whole_text(value: str) -> bool:
    """Значение — кусок описания с другими подписями («Тип: …. Вид: …») или длиннее
    `MAX_LLM_VALUE`: модель переписала текст в поле, а не назвала значение."""
    return len(value) > MAX_LLM_VALUE or value.count(":") >= 2


async def extract_llm(
    name: str,
    description: str,
    specs: list[CharacteristicSpec],
    llm: ChatLLM,
    glossary: Glossary,
    all_specs: list[CharacteristicSpec] | None = None,
) -> list[Extracted]:
    """Элементы ответа модели, прошедшие проверку кодом; LLMError (нет связи) — наружу.
    `all_specs` — все характеристики позиции (и уже заполненные): чужая подпись в цитате."""
    if not specs:
        return []
    messages = build_messages(name, description, specs, glossary)
    reply: LLMItems | None = None
    for attempt in (1, 2):
        try:
            result = await llm.chat(messages, schema=LLMItems)
        except LLMOutputError as exc:
            log.warning("ответ модели не прошёл схему (попытка %d): %s", attempt, exc)
            continue
        reply = result.parsed if isinstance(result.parsed, LLMItems) else None
        break
    if reply is None:
        log.warning("извлечение LLM не удалось, оставлены только правила")
        return []
    by_name = {s.name: s for s in specs}
    out: list[Extracted] = []
    for item in reply.items:
        spec = by_name.get(item.name)
        span = _locate(item.quote, name, description)
        if spec is None or span is None or item.name in {e.name for e in out}:
            continue
        if not _quote_has_value(item.quote, item.value, glossary):
            continue  # цитата есть в тексте, но значения в ней нет — значение придумано
        if fit_value(item.value, item.unit, spec).status in _REJECTED:
            continue
        if foreign_unit_in_quote(item.quote, item.value, spec) is not None:
            continue  # в тексте у числа другая единица: «100 шт» — не «100 пар»
        if _unit_invented(item.quote, item.value, item.unit):
            continue  # модель приписала единицу, которой у числа в тексте нет: «4 шт» → «4 В»
        if _whole_text(item.value):
            continue  # «Описание = Тип конструкции: прямая. Вид материала: ЛДСП…» — не значение
        if _lost_minus(item.quote, item.value):
            continue  # «Мощность: -1510 Вт» → «1510»: минус потерян, о нём — замечание
        if _other_label(item.quote, spec, all_specs or specs):
            continue  # «ширина 40 мм» → «Толщина»: в цитате подпись другой характеристики
        unit = _unit_as_written(item.quote, item.value, item.unit, spec)
        out.append(
            Extracted(
                name=spec.name,
                raw_value=item.value,
                raw_unit=unit,
                source=span,
                method="llm",
                checked=_label_words(spec.name) & set(tokens(item.quote)) != set(),
            )
        )
    log.info("ответ LLM: элементов=%d принято=%d", len(reply.items), len(out))
    return out
