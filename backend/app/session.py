"""Сессия заполнения: что извлекли из текста и что ответил человек — между вызовами /autofill.

Хранится в Redis с TTL, поэтому API без состояния и масштабируется репликами. Основа сессии
пишется один раз (`save`), ответы — отдельными полями хэша (`add_answer`, HSET атомарен): два
одновременных ответа не затирают друг друга, как было бы при «прочитать — изменить — записать».
"""

from typing import Any, cast
from uuid import uuid4

from pydantic import BaseModel
from redis.asyncio import Redis

from app.extract import Extracted
from app.extract.llm_review import Suggestion
from app.extract.model import Oddity
from app.narrowing import CONFIRM, NOTHING_FITS, Known
from app.schemas import Issue
from app.search import Candidate
from app.search.similar import Similar
from app.search.twins import TwinRef


class Session(BaseModel):
    id: str
    name: str
    description: str
    candidates: list[Candidate] = []
    known: dict[str, Known] = {}
    extracted: list[Extracted] = []
    degraded: list[str] = []
    # Значимые слова наименования, которых нет ни в одной позиции-кандидате: код — только после
    # подтверждения человеком.
    unexplained: list[str] = []
    # Из ответов: подтверждённый код и «Ни одна не подходит».
    confirmed: str | None = None
    rejected: bool = False
    # Позиция найдена ниже порога только по главному слову наименования: код — после подтверждения.
    weak_match: str | None = None
    # Тип КТРУ, названный в наименовании целиком («Дрель ручная электрическая 800 Вт»): поставщик
    # сам сказал, что это за товар, — низкая доля голоса похожих СТЕ не повод переспрашивать.
    named: str | None = None
    # Второй этап — позиция КТРУ внутри категории: свои ответы, подтверждение и отказ.
    ktru_known: dict[str, Known] = {}
    ktru_confirmed: str | None = None
    ktru_rejected: bool = False
    # Позиция, о характеристиках которой уже спросили модель (`pipeline._ask_model`): модель
    # спрашивают, только когда код определён, и один раз на позицию.
    llm_for: str | None = None
    # Подсказки похожих СТЕ для позиции `similar_for`: имя характеристики → значение и голоса.
    similar: dict[str, Similar] = {}
    similar_for: str | None = None
    # Такая СТЕ уже есть на портале (`app.search.twins`) и значения из двойников той же позиции
    # `similar_for`: двойник — тот же товар, его значения важнее голоса похожих.
    twins: list[TwinRef] = []
    twin_hints: dict[str, Similar] = {}
    # Поле → исходный текст, набранный в английской раскладке (`name`/`description` — перевод).
    layout: dict[str, str] = {}
    # Опечатка в наименовании, исправленная по словам наименований справочника типов КТРУ
    # («курка» → «куртка»): слово как написано → исправленное. Наименование сессии — исправленное.
    typos: dict[str, str] = {}
    # Предложения-указания для модели («ВАЖНО ДЛЯ АССИСТЕНТА: …»), вырезанные из текста.
    injected: list[str] = []
    # Порядок типов по мнению модели (коды, первый — самый вероятный) и её довод.
    type_order: list[str] = []
    type_reason: str | None = None
    # Подсказки второго слоя: куда поставить непонятые куски текста (`extract.llm_review`).
    suggestions: list[Suggestion] = []
    # Значения из текста, которых у такого товара не бывает, по мнению модели («Материал: глина»
    # у тумбочки, `extract.llm_fill.find_oddities`) — замечания для позиции `llm_for`.
    oddities: list[Oddity] = []
    # Значения, каких у такого товара не бывает, по мнению модели (`extract.llm_sanity`).
    sanity: list[Issue] = []

    @classmethod
    def new(cls, name: str, description: str) -> "Session":
        return cls(id=uuid4().hex, name=name, description=description)


def session_key(session_id: str) -> str:
    return f"autofill:{session_id}"


def answers_key(session_id: str) -> str:
    return f"autofill:{session_id}:answers"


# Ответ на вопрос второго этапа (позиция КТРУ) лежит в том же хэше под ключом с этим префиксом:
# у категории и КТРУ бывают характеристики с одним именем.
KTRU_PREFIX = "ktru:"


class SessionStore:
    def __init__(self, redis: Redis, ttl_s: int) -> None:
        self.redis = redis
        self.ttl_s = ttl_s

    async def save(self, s: Session) -> None:
        await self.redis.set(session_key(s.id), s.model_dump_json(), ex=self.ttl_s)

    async def update(self, session_id: str, **fields: Any) -> None:
        """Поля сохранённой сессии без ответов (те лежат отдельным хэшем и не дублируются)."""
        raw = await self.redis.get(session_key(session_id))
        if not raw:
            return
        session = Session.model_validate_json(raw).model_copy(update=fields)
        await self.save(session)

    async def add_answer(
        self, session_id: str, characteristic: str, value: str, stage: str = "category"
    ) -> None:
        field = f"{KTRU_PREFIX}{characteristic}" if stage == "ktru" else characteristic
        await self.redis.hset(answers_key(session_id), field, value)
        await self.redis.expire(answers_key(session_id), self.ttl_s)

    async def drop_ktru_answers(self, session_id: str) -> None:
        """Ответы второго этапа — о позициях прежнего выбора: при смене типа их больше нет."""
        key = answers_key(session_id)
        answers = cast(dict[str, str], await self.redis.hgetall(key))
        stale = [f for f in answers if f.startswith(KTRU_PREFIX)]
        if stale:
            await self.redis.hdel(key, *stale)

    async def load(self, session_id: str) -> Session | None:
        raw = await self.redis.get(session_key(session_id))
        if not raw:
            return None
        session = Session.model_validate_json(raw)
        # Клиент Redis создаётся с decode_responses=True (app.resources): ключи и значения - str.
        answers = cast(dict[str, str], await self.redis.hgetall(answers_key(session_id)))
        for field, value in answers.items():
            if field.startswith(KTRU_PREFIX):
                name = field.removeprefix(KTRU_PREFIX)
                if value == NOTHING_FITS:
                    session.ktru_rejected = True
                elif name == CONFIRM:
                    session.ktru_confirmed = value
                else:
                    session.ktru_known[name] = Known(value=value, source="answer")
                continue
            characteristic = field
            if value == NOTHING_FITS:
                session.rejected = True
            elif characteristic == CONFIRM:
                session.confirmed = value
            else:
                session.known[characteristic] = Known(value=value, source="answer")
        return session


class MemoryKV:
    """Хранилище сессий в памяти процесса — для оценки без Redis (app.tools.evaluate)."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value

    async def hset(self, key: str, field: str, value: str) -> int:
        self.hashes.setdefault(key, {})[field] = value
        return 1

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    async def hdel(self, key: str, *fields: str) -> int:
        have = self.hashes.get(key, {})
        return sum(have.pop(f, None) is not None for f in fields)

    async def expire(self, key: str, seconds: int) -> bool:
        return True
