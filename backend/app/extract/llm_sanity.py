"""Проверка на правдоподобие: значения, каких у такого товара не бывает.

Код сверяет значения с перечнями КТРУ и с пределами СТЕ портала, но не знает, что тумбочка из
глины, брюки из стали или тумба глубиной 100 метров — чушь: поле «Материал» свободное, а пределов
у редкой категории нет. Модель (второй слой, `LLM_REVIEW_MODEL`) читает тип товара и текст и
называет такие места. Её ответ — только замечание `warn` к полю или к описанию: значения она не
меняет, а цитату код ищет в тексте (не нашлась — замечания нет).
"""

import logging
from typing import Protocol

from pydantic import BaseModel

from app.extract.model import Extracted
from app.llm import ChatResult, LLMOutputError, Message
from app.schemas import Issue
from app.values import fold

log = logging.getLogger(__name__)

MAX_ODD = 5
SANITY_RULE = "llm.sanity"

SANITY_SYSTEM = (
    "Ты опытный модератор каталога товаров для госзакупок. Тебе дают тип товара и текст "
    "поставщика. Найди в тексте значения, которых у такого товара в реальности не бывает или "
    "которые явно ошибочны: материал, размеры, масса, мощность, назначение, состав, страна. "
    "Примеры: тумбочка из глины, брюки из стали, тумба высотой 3 метра или глубиной 100 метров, "
    "стол массой 2 кг при длине 20 м, страна «Ваканда», цвет «вчерашний». Обычные значения не "
    "трогай, к стилю и опечаткам не придирайся. Отвечай только JSON по схеме "
    '{"odd": [{"quote": "кусок текста как есть", "reason": "коротко почему не бывает"}]}; '
    'всё правдоподобно — {"odd": []}.'
)


class Odd(BaseModel):
    quote: str
    reason: str = ""


class Sanity(BaseModel):
    odd: list[Odd] = []


class SanityChat(Protocol):
    async def chat(
        self, messages: list[Message], *, schema: type[Sanity], temperature: float = 0.0
    ) -> ChatResult: ...


def build_sanity(kind: str, text: str) -> list[Message]:
    user = f"Тип товара: {kind}\n\nТекст поставщика:\n{text}"
    return [{"role": "system", "content": SANITY_SYSTEM}, {"role": "user", "content": user}]


async def sanity(kind: str, text: str, llm: SanityChat) -> Sanity | None:
    messages = build_sanity(kind, text)
    for attempt in (1, 2):
        try:
            result = await llm.chat(messages, schema=Sanity)
        except LLMOutputError as exc:
            log.warning(
                "ответ проверки правдоподобия не прошёл схему (попытка %d): %s", attempt, exc
            )
            continue
        return result.parsed if isinstance(result.parsed, Sanity) else None
    return None


def sanity_issues(answer: Sanity, text: str, extracted: list[Extracted]) -> list[Issue]:
    """Замечания по ответу: цитата есть в тексте; поле — то, чей фрагмент с ней пересекается."""
    low = fold(text)
    out: list[Issue] = []
    seen: set[str] = set()
    for odd in answer.odd[:MAX_ODD]:
        quote = odd.quote.strip().strip("«»\"' ")
        if len(quote) < 2 or fold(quote) not in low or fold(quote) in seen:
            continue
        seen.add(fold(quote))
        field = "description"
        for e in extracted:
            src = fold(e.source.text) if e.source else ""
            if src and (src in fold(quote) or fold(quote) in src):
                field = f"characteristics.{e.name}"
                break
        reason = odd.reason.strip().rstrip(".")[:200]
        out.append(
            Issue(
                field=field,
                kind="mismatch",
                severity="warn",
                message=f"Похоже на ошибку: «{quote}» — {reason}. Проверьте описание.",
                why="Модель проверила правдоподобие значений для такого товара.",
                rule=SANITY_RULE,
            )
        )
    return out
