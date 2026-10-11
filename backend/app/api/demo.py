"""Стенд портала для демонстрации: очередь заявок модератора (`/api/demo`). Не часть модуля."""

import hashlib
import json
from collections import OrderedDict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.api.v1 import DepsDep
from app.demo import (
    ACCEPTED_CLEAN_NOTE,
    AlreadyDecided,
    DecisionIn,
    NotReturned,
    QueuedSubmission,
    Submission,
    SubmissionIn,
    SubmissionRepo,
    SubmissionView,
    Triage,
    triage,
)
from app.demo.photos import MAX_PHOTO_BYTES, ORPHAN_AGE, PhotoRepo, sniff_mime
from app.pipeline import Deps, check
from app.schemas import CheckOut

router = APIRouter(prefix="/demo", tags=["стенд портала"])

NOT_FOUND = "заявка не найдена"


def get_submissions(request: Request) -> SubmissionRepo:
    return request.app.state.submissions


RepoDep = Annotated[SubmissionRepo, Depends(get_submissions)]


def get_photos(request: Request) -> PhotoRepo:
    return request.app.state.photos


PhotosDep = Annotated[PhotoRepo, Depends(get_photos)]


async def _photo_exists(s: SubmissionIn, photos: PhotoRepo) -> None:
    """Заявка не ссылается на фото, которого нет (удалено очисткой или выдумано)."""
    if s.card.photo_id is not None and await photos.get(s.card.photo_id) is None:
        raise HTTPException(status_code=422, detail="Фото товара не найдено: загрузите его заново.")


def _checked(s: SubmissionIn, deps: Deps) -> SubmissionIn:
    """Карточку с блокирующими замечаниями модуль не пропускает: ни интерфейс, ни API."""
    result = check(s.card, deps)
    # Поле в тексте: «Значения «Белый» нет в перечне» без имени характеристики не найти.
    blocking = [
        f"«{i.field.removeprefix('characteristics.')}»: {i.message}"
        if i.field.startswith("characteristics.")
        else i.message
        for i in result.issues
        if i.severity == "block"
    ]
    if blocking:
        raise HTTPException(
            status_code=422,
            detail="карточку с блокирующими замечаниями отправить нельзя: "
            + " ".join(blocking[:3]),
        )
    # В заявку идут только характеристики самой карточки: модератор видит то, что проверено.
    in_card = {(c.name, c.value) for c in s.card.characteristics}
    accepted = [a for a in s.accepted if (a.name, a.value) in in_card]
    return s.model_copy(update={"accepted": accepted})


@router.post("/submissions", status_code=201, summary="Поставщик отправил заявку")
async def post_submission(
    s: SubmissionIn, repo: RepoDep, deps: DepsDep, photos: PhotosDep
) -> Submission:
    await _photo_exists(s, photos)
    return await repo.add(_checked(s, deps))


# Проверка заявки — ~20 мс (`check`), очередь перечитывается после каждого решения: результат
# запоминается по содержимому заявки (карточка, принятые значения, прежний возврат) на время
# жизни процесса — справочник и двойники меняются только с перезапуском.
Triaged = tuple[CheckOut, Triage]
_CACHE_SIZE = 4096


def get_triage_cache(request: Request) -> "OrderedDict[str, Triaged]":
    cache = getattr(request.app.state, "triage_cache", None)
    if cache is None:
        cache = request.app.state.triage_cache = OrderedDict()
    return cache


CacheDep = Annotated["OrderedDict[str, Triaged]", Depends(get_triage_cache)]


def _returned(sub: Submission) -> str | None:
    """Текст прежнего возврата у исправленной заявки (ждёт решения снова)."""
    return sub.decision_note if sub.status == "pending" else None


def _triaged(sub: Submission, deps: Deps, cache: "OrderedDict[str, Triaged]") -> Triaged:
    raw = json.dumps(
        [sub.card.model_dump(mode="json"), [a.model_dump(mode="json") for a in sub.accepted],
         _returned(sub)],
        ensure_ascii=False, sort_keys=True,
    )  # fmt: skip
    key = hashlib.sha1(raw.encode()).hexdigest()
    if (hit := cache.get(key)) is not None:
        cache.move_to_end(key)
        return hit
    card = sub.card.as_card_in()
    result = check(card, deps)
    found = (result, triage(card, sub.accepted, result, _returned(sub)))
    cache[key] = found
    if len(cache) > _CACHE_SIZE:
        cache.popitem(last=False)
    return found


def _pending_triage(
    subs: list[Submission], deps: Deps, cache: "OrderedDict[str, Triaged]"
) -> list[Triage | None]:
    return [_triaged(s, deps, cache)[1] if s.status == "pending" else None for s in subs]


@router.get("/submissions", summary="Очередь модератора, новые сверху")
async def list_submissions(repo: RepoDep, deps: DepsDep, cache: CacheDep) -> list[QueuedSubmission]:
    """У ждущей решения заявки — `triage`: чистую можно одобрить в один клик, у остальных —
    почему открыть. Проверка — в пуле потоков: цикл событий API не ждёт очередь."""
    subs = await repo.list()
    verdicts = await run_in_threadpool(_pending_triage, subs, deps, cache)
    return [
        QueuedSubmission(**s.model_dump(), triage=t) for s, t in zip(subs, verdicts, strict=True)
    ]


class AcceptedClean(BaseModel):
    accepted: list[int]


class AcceptCleanIn(BaseModel):
    ids: list[int] | None = Field(
        default=None,
        description="заявки, которые модератор видел в списке подтверждения; нет — все чистые",
    )


@router.post("/submissions/accept-clean", summary="Одобрить в один клик все чистые заявки")
async def accept_clean(
    repo: RepoDep, deps: DepsDep, cache: CacheDep, inp: AcceptCleanIn | None = None
) -> AcceptedClean:
    """Ждущие решения заявки без замечаний модуля (`triage.clean`: ни блокирующих, ни
    предупреждений, ни подсказок без правки, не исправленные после возврата) одобряются с записью
    в журнале (`decision_note`); остальные ждут модератора. С `ids` — только из этого списка:
    заявка, пришедшая или изменившаяся после подтверждения, пачкой не уходит."""
    subs = await repo.list()
    if inp is not None and inp.ids is not None:
        confirmed = set(inp.ids)
        subs = [s for s in subs if s.id in confirmed]
    verdicts = await run_in_threadpool(_pending_triage, subs, deps, cache)
    done = []
    for sub, verdict in zip(subs, verdicts, strict=True):
        if verdict is None or not verdict.clean:
            continue
        try:
            decided = await repo.decide(
                sub.id, DecisionIn(decision="accept", note=ACCEPTED_CLEAN_NOTE)
            )
        except AlreadyDecided:
            continue  # решил другой модератор, пока шёл обход
        if decided is not None:
            done.append(sub.id)
    return AcceptedClean(accepted=sorted(done))


@router.get("/submissions/{id}", summary="Заявка с проверкой модуля")
async def get_submission(id: int, repo: RepoDep, deps: DepsDep, cache: CacheDep) -> SubmissionView:
    sub = await repo.get(id)
    if sub is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    result, verdict = await run_in_threadpool(_triaged, sub, deps, cache)
    return SubmissionView(**sub.model_dump(), check=result, triage=verdict)


@router.put("/submissions/{id}", summary="Поставщик исправил возвращённую заявку")
async def put_submission(
    id: int, s: SubmissionIn, repo: RepoDep, deps: DepsDep, photos: PhotosDep
) -> Submission:
    """Заявка снова ждёт решения модератора; прежний текст возврата остаётся в `decision_note`."""
    await _photo_exists(s, photos)
    try:
        sub = await repo.resubmit(id, _checked(s, deps))
    except NotReturned as exc:
        raise HTTPException(
            status_code=409, detail="исправить можно только возвращённую заявку"
        ) from exc
    if sub is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return sub


@router.post("/submissions/{id}/decision", summary="Решение модератора")
async def post_decision(id: int, d: DecisionIn, repo: RepoDep) -> Submission:
    try:
        sub = await repo.decide(id, d)
    except AlreadyDecided as exc:
        raise HTTPException(status_code=409, detail="по заявке уже есть решение") from exc
    if sub is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return sub


@router.post("/photos", summary="Фото товара для заявки")
async def post_photo(file: UploadFile, photos: PhotosDep) -> dict[str, int]:
    """Файл сохраняется сразу при выборе в форме, заявка потом ссылается на него (`photo_id`)."""
    data = await file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail="фото больше 5 МБ")
    mime = sniff_mime(data)
    if mime is None:
        raise HTTPException(status_code=415, detail="подойдёт JPEG, PNG или WebP")
    await photos.cleanup(ORPHAN_AGE)
    return {"photo_id": await photos.add(mime, data)}


@router.get(
    "/photos/{photo_id}",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}, "image/png": {}, "image/webp": {}}}},
    summary="Фото товара заявки",
)
async def get_photo(photo_id: int, photos: PhotosDep) -> Response:
    photo = await photos.get(photo_id)
    if photo is None:
        raise HTTPException(status_code=404, detail="фото не найдено")
    mime, data = photo
    return Response(data, media_type=mime, headers={"Cache-Control": "private, max-age=86400"})
