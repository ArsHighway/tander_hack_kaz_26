"""Очередь заявок на модерацию — стенд портала для демонстрации, не часть модуля.

На настоящем портале очередь ведёт сам портал; модуль только проверяет карточку (`/api/v1/check`).
"""

import json
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, TypeAdapter, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.checks.detector import RULE_KTRU, RULE_KTRU_LINK, RULE_KTRU_REQUIRED, RULE_KTRU_VALUE
from app.pipeline import CharacteristicOut
from app.schemas import CardIn, CheckOut, StoredCard

Status = Literal["pending", "accepted", "returned"]


class SubmissionBatch(BaseModel):
    """Строка пакета, из которой поставщик открыл карточку в форме: «Мои заявки» делят заявки на
    единичные и из массовой загрузки."""

    job_id: str = Field(max_length=64)
    row: int = Field(ge=1)
    filename: str = Field(default="", max_length=255)


class SubmissionIn(BaseModel):
    card: CardIn
    accepted: list[CharacteristicOut] = []
    batch: SubmissionBatch | None = None


class Submission(BaseModel):
    id: int
    created_at: datetime
    card: StoredCard
    accepted: list[CharacteristicOut]
    status: Status
    decision_note: str | None = None
    batch: SubmissionBatch | None = None


# Что видят модератор и поставщик в журнале заявки, одобренной кнопкой «Одобрить чистые».
ACCEPTED_CLEAN_NOTE = "Одобрена в один клик: модуль не нашёл замечаний"


class Triage(BaseModel):
    """Нужен ли заявке взгляд модератора. Чистая — без блокирующих замечаний и предупреждений
    модуля (в т. ч. «такая СТЕ уже есть» и значения вне перечня позиции КТРУ): такую модератор
    одобряет вместе с остальными чистыми одной кнопкой, после подтверждения списка.

    Подсказки СТЕ портала чистоте не мешают, их только считают (`hinted`, модератор видит число и
    подпись «Принято из подсказки» у поля): в карточку подсказка попадает лишь по «Принять» самого
    поставщика, непринятая в заявку не уходит вовсе. Подсказки есть почти у всех карточек мастера:
    условие «принятая подсказка — не чистая» закрыло бы «Одобрить чистые» почти для всех заявок.
    """

    clean: bool
    reasons: list[str] = Field(description="почему заявку стоит открыть; пусто — чистая")
    hinted: int = Field(
        default=0, description="значений из подсказок СТЕ портала, принятых поставщиком без правки"
    )


# Длинный текст возврата в причине — только началом: целиком он над решением модератора.
_NOTE_SHOWN = 160
_WARNS_SHOWN = 3
_KTRU_RULES = (RULE_KTRU, RULE_KTRU_VALUE, RULE_KTRU_REQUIRED, RULE_KTRU_LINK)


def triage(
    card: CardIn,
    accepted: list[CharacteristicOut],
    result: CheckOut,
    returned: str | None = None,
) -> Triage:
    """`returned` — текст прежнего возврата у исправленной заявки: модуль не видит, за что вернул
    человек («фото не того товара»), поэтому такую заявку открывает модератор."""
    reasons = []
    if returned and returned.strip():
        note = " ".join(returned.split())
        shown = note if len(note) <= _NOTE_SHOWN else note[: _NOTE_SHOWN - 1] + "…"
        reasons.append(f"исправлена после возврата: «{shown}»")
    if blocks := sum(i.severity == "block" for i in result.issues):
        reasons.append(f"нарушения правил модерации: {blocks}")
    warns = [i for i in result.issues if i.severity == "warn"]
    # Замечания по позиции КТРУ — первыми: КТРУ эталон, и в первые три они попадают всегда.
    warns.sort(key=lambda i: i.rule not in _KTRU_RULES)
    reasons += [i.message for i in warns][:_WARNS_SHOWN]
    in_card = {(c.name, c.value) for c in card.characteristics}
    hinted = sum(
        1 for a in accepted if a.method in ("twin", "similar") and (a.name, a.value) in in_card
    )
    return Triage(clean=not reasons, reasons=reasons, hinted=hinted)


class QueuedSubmission(Submission):
    triage: Triage | None = Field(
        default=None, description="только у ждущей решения: чистая или почему открыть"
    )


class SubmissionView(Submission):
    check: CheckOut
    triage: Triage


class DecisionIn(BaseModel):
    decision: Literal["accept", "return"]
    note: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def _return_has_note(self) -> "DecisionIn":
        if self.decision == "return" and not (self.note or "").strip():
            raise ValueError("для возврата нужен текст замечаний поставщику")
        return self


class AlreadyDecided(RuntimeError):
    pass


class NotReturned(RuntimeError):
    """Исправить можно только возвращённую заявку: ждущую решения или принятую — нет."""


class SubmissionRepo(Protocol):
    async def add(self, s: SubmissionIn) -> Submission: ...
    async def list(self) -> list[Submission]: ...
    async def get(self, id: int) -> Submission | None: ...
    async def decide(self, id: int, d: DecisionIn) -> Submission | None: ...
    async def resubmit(self, id: int, s: SubmissionIn) -> Submission | None: ...


def _status(d: DecisionIn) -> Status:
    return "accepted" if d.decision == "accept" else "returned"


class MemorySubmissionRepo:
    """Хранилище в памяти: тесты и запуск без базы."""

    def __init__(self) -> None:
        self._items: dict[int, Submission] = {}

    async def add(self, s: SubmissionIn) -> Submission:
        sub = Submission(
            id=len(self._items) + 1,
            created_at=datetime.now(UTC),
            card=StoredCard.model_validate(s.card.model_dump()),
            accepted=s.accepted,
            status="pending",
            batch=s.batch,
        )
        self._items[sub.id] = sub
        return sub

    async def list(self) -> list[Submission]:
        return sorted(self._items.values(), key=lambda s: s.id, reverse=True)

    async def get(self, id: int) -> Submission | None:
        return self._items.get(id)

    async def decide(self, id: int, d: DecisionIn) -> Submission | None:
        sub = self._items.get(id)
        if sub is None:
            return None
        if sub.status != "pending":
            raise AlreadyDecided(id)
        sub = sub.model_copy(update={"status": _status(d), "decision_note": d.note})
        self._items[id] = sub
        return sub

    async def resubmit(self, id: int, s: SubmissionIn) -> Submission | None:
        sub = self._items.get(id)
        if sub is None:
            return None
        if sub.status != "returned":
            raise NotReturned(id)
        card = StoredCard.model_validate(s.card.model_dump())
        sub = sub.model_copy(update={"card": card, "accepted": s.accepted, "status": "pending"})
        self._items[id] = sub
        return sub


_ACCEPTED = TypeAdapter(list[CharacteristicOut])
_COLUMNS = "id, created_at, card, accepted, status, decision_note, batch"


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _batch(raw: Any) -> SubmissionBatch | None:
    """Строка пакета заявки; без колонки (до миграции f6a7b8c9d0e1) — единичная заявка."""
    return SubmissionBatch.model_validate(_json(raw)) if raw else None


def _row(r: Any) -> Submission:
    return Submission(
        id=r.id,
        created_at=r.created_at,
        card=StoredCard.model_validate(_json(r.card)),
        accepted=_ACCEPTED.validate_python(_json(r.accepted)),
        status=r.status,
        decision_note=r.decision_note,
        batch=_batch(getattr(r, "batch", None)),
    )


class PgSubmissionRepo:
    """Таблица submission (миграция c3d4e5f6a7b8)."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def add(self, s: SubmissionIn) -> Submission:
        async with self.engine.begin() as conn:
            r = await conn.execute(
                text(
                    "INSERT INTO submission (card, accepted, status, batch) "
                    "VALUES (CAST(:card AS jsonb), CAST(:accepted AS jsonb), 'pending', "
                    "CAST(:batch AS jsonb)) "
                    f"RETURNING {_COLUMNS}"
                ),
                {
                    "card": s.card.model_dump_json(),
                    "accepted": _ACCEPTED.dump_json(s.accepted).decode(),
                    "batch": s.batch.model_dump_json() if s.batch else None,
                },
            )
            return _row(r.one())

    async def list(self) -> list[Submission]:
        async with self.engine.connect() as conn:
            r = await conn.execute(text(f"SELECT {_COLUMNS} FROM submission ORDER BY id DESC"))
            return [_row(x) for x in r]

    async def get(self, id: int) -> Submission | None:
        async with self.engine.connect() as conn:
            r = await conn.execute(
                text(f"SELECT {_COLUMNS} FROM submission WHERE id = :id"), {"id": id}
            )
            row = r.one_or_none()
            return _row(row) if row else None

    async def decide(self, id: int, d: DecisionIn) -> Submission | None:
        async with self.engine.begin() as conn:
            r = await conn.execute(
                text(
                    "UPDATE submission SET status = :status, decision_note = :note "
                    f"WHERE id = :id AND status = 'pending' RETURNING {_COLUMNS}"
                ),
                {"status": _status(d), "note": d.note, "id": id},
            )
            row = r.one_or_none()
            if row is not None:
                return _row(row)
            exists = await conn.execute(text("SELECT 1 FROM submission WHERE id = :id"), {"id": id})
            if exists.one_or_none() is not None:
                raise AlreadyDecided(id)
            return None

    async def resubmit(self, id: int, s: SubmissionIn) -> Submission | None:
        """Исправленная карточка снова ждёт решения; прежний текст возврата остаётся."""
        async with self.engine.begin() as conn:
            r = await conn.execute(
                text(
                    "UPDATE submission SET card = CAST(:card AS jsonb), "
                    "accepted = CAST(:accepted AS jsonb), status = 'pending' "
                    f"WHERE id = :id AND status = 'returned' RETURNING {_COLUMNS}"
                ),
                {
                    "card": s.card.model_dump_json(),
                    "accepted": _ACCEPTED.dump_json(s.accepted).decode(),
                    "id": id,
                },
            )
            row = r.one_or_none()
            if row is not None:
                return _row(row)
            exists = await conn.execute(text("SELECT 1 FROM submission WHERE id = :id"), {"id": id})
            if exists.one_or_none() is not None:
                raise NotReturned(id)
            return None
