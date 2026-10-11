"""Контракт модуля для Портала поставщиков: `/api/v1`. Описан в OpenAPI (`/api/docs`)."""

import json
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.batch import MAX_BYTES as MAX_BATCH_BYTES
from app.batch import (
    BatchAccepted,
    BatchFormatError,
    BatchResultPage,
    BatchRowIn,
    BatchState,
    BatchTooLarge,
    RowStatus,
    parse_upload_json,
    result_file,
)
from app.catalog import Position
from app.evaluation import MetricsOut
from app.evaluation.metrics import MetricRow
from app.ingest.documents import DocumentError, Kind, UnsupportedDocument, extract_document
from app.pipeline import (
    AnswerIn,
    AutofillIn,
    AutofillOut,
    BadAnswer,
    Deps,
    SessionNotFound,
    answer,
    autofill,
    check,
)
from app.portal import PortalReport
from app.portal.images import ImageCache
from app.schemas import CardIn, CheckOut

router = APIRouter(prefix="/v1", tags=["модуль"])

SESSION_GONE = "сессия заполнения не найдена или истекла"


async def get_deps(request: Request) -> Deps:
    deps: Deps = request.app.state.deps
    await request.app.state.settings_store.refresh(deps)  # выключатель модели из Redis
    return deps


DepsDep = Annotated[Deps, Depends(get_deps)]


@router.post("/autofill", summary="Описание поставщика → черновик карточки")
async def post_autofill(inp: AutofillIn, deps: DepsDep) -> AutofillOut:
    """Кандидаты КТРУ, характеристики с источником в тексте, замечания и вопрос для сужения."""
    return await autofill(inp, deps)


@router.post("/autofill/{session_id}/answer", summary="Ответ на уточняющий вопрос")
async def post_answer(session_id: str, inp: AnswerIn, deps: DepsDep) -> AutofillOut:
    try:
        return await answer(session_id, inp, deps)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail=SESSION_GONE) from exc
    except BadAnswer as exc:
        raise HTTPException(status_code=422, detail=f"ответ не подходит к вопросу: {exc}") from exc


@router.post("/check", summary="Проверка готовой карточки")
async def post_check(card: CardIn, deps: DepsDep) -> CheckOut:
    """Замечания с обоснованием и ссылкой на правило; нужна поставщику и модератору."""
    return check(card, deps)


@router.get("/ktru/{code}", summary="Позиция справочника или КТРУ")
async def get_ktru(code: str, deps: DepsDep) -> Position:
    """Категория справочника (`category_code` карточки) или позиция КТРУ второго этапа
    (`ktru_code`): название, путь категории и характеристики для показа рядом с кодом."""
    position = deps.catalog.get(code) or (deps.ktru.get(code) if deps.ktru else None)
    if position is None:
        raise HTTPException(status_code=404, detail="позиция не найдена")
    return position


def get_images(request: Request) -> ImageCache:
    return request.app.state.resources.images


ImagesDep = Annotated[ImageCache, Depends(get_images)]
NO_IMAGE = "картинки нет или портал недоступен"
IMAGE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {"content": {"image/jpeg": {}}, "description": "JPEG 300×300"},
    404: {"description": NO_IMAGE},
}


def _jpeg(data: bytes | None) -> Response:
    if data is None:
        # Фото может скачаться в фоне к следующему показу: 404 браузер не кэширует.
        raise HTTPException(status_code=404, detail=NO_IMAGE, headers={"Cache-Control": "no-store"})
    # Картинка у id не меняется: браузер держит её сутки, портал не дёргается.
    return Response(
        data, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"}
    )


@router.get(
    "/images/sku/{sku_id}",
    response_class=Response,
    responses=IMAGE_RESPONSES,
    summary="Фото СТЕ портала (подсказка «похожие» у варианта категории)",
)
async def get_sku_image(
    sku_id: Annotated[int, Path(ge=1, le=2**53)], images: ImagesDep
) -> Response:
    return _jpeg(await images.soon("sku", sku_id))


@router.get(
    "/images/category/{portal_id}",
    response_class=Response,
    responses=IMAGE_RESPONSES,
    summary="Картинка категории портала (у карточки без своего фото)",
)
async def get_category_image(
    portal_id: Annotated[int, Path(ge=1, le=2**53)], deps: DepsDep, images: ImagesDep
) -> Response:
    position = next((p for p in deps.catalog.all() if p.portal_id == portal_id), None)
    if position is None or position.image_id is None:
        return _jpeg(None)
    return _jpeg(await images.soon("category", position.image_id))


BATCH_GONE = "пакет не найден или истёк срок хранения"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ExtractOut(BaseModel):
    text: str = Field(description="текст товара для шага 1: наименование первой строкой")
    items: int = Field(
        description=(
            "сколько товаров в файле; больше одного — открыть массовую загрузку; таблица "
            "читается не дальше 10 000 строк, 10 001 значит «больше 10 000»"
        )
    )
    kind: Kind


MAX_EXTRACT_BYTES = 10 * 1024 * 1024


@router.post(
    "/extract",
    summary="Текст товара из файла: Word, PDF с текстом, таблица",
    responses={
        413: {"description": "больше 10 МБ"},
        415: {"description": "не Word, не PDF и не таблица"},
        422: {"description": "текста нет (скан PDF) или файл повреждён"},
    },
)
async def post_extract(file: UploadFile) -> ExtractOut:
    data = await file.read(MAX_EXTRACT_BYTES + 1)
    if len(data) > MAX_EXTRACT_BYTES:
        raise HTTPException(status_code=413, detail="файл больше 10 МБ")
    try:
        out = await run_in_threadpool(extract_document, file.filename or "", data)
    except UnsupportedDocument as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except DocumentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ExtractOut(text=out.text, items=out.items, kind=out.kind)


@router.post("/batch", status_code=202, summary="Массовая загрузка: файл в очередь")
async def post_batch(file: UploadFile, deps: DepsDep) -> BatchAccepted:
    """xlsx или csv со списком СТЕ; роли колонок угадываются по содержимому.

    Обработка — воркером через очередь; состояние — `GET /api/v1/batch/{job_id}`.
    """
    if deps.batches is None or deps.enqueue_batch is None:
        raise HTTPException(status_code=503, detail="очередь пакетов не настроена")
    # Больше предела не читаем: файл на гигабайт не ложится в память целиком.
    data = await file.read(MAX_BATCH_BYTES + 1)
    if len(data) > MAX_BATCH_BYTES:
        raise HTTPException(
            status_code=413, detail=f"файл больше {MAX_BATCH_BYTES // (1024 * 1024)} МБ"
        )
    filename = file.filename or "upload.xlsx"
    try:
        rows = await run_in_threadpool(parse_upload_json, filename, data)
    except BatchTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except BatchFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    job_id = await deps.batches.create(filename, rows)
    try:
        await deps.enqueue_batch(job_id, len(rows))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"очередь недоступна: {exc}") from exc
    return BatchAccepted(job_id=job_id, status_url=f"/api/v1/batch/{job_id}")


@router.get("/batch", summary="Последние пакеты: «Мои заявки → Пакеты»")
async def list_batches(deps: DepsDep) -> list[BatchState]:
    """Пакеты новые сверху, только счётчики по статусам (строки — `GET /batch/{job_id}/results`).
    Пакет живёт свой срок в Redis; истёкший из списка пропадает."""
    return await deps.batches.recent() if deps.batches else []


@router.delete("/batch/{job_id}", summary="Прервать пакет, сохранив частичный результат")
async def cancel_batch(job_id: str, deps: DepsDep) -> BatchState:
    """Новые строки пропускаются; уже начатые могут завершиться. Повторная отмена безопасна."""
    state = await deps.batches.cancel(job_id) if deps.batches else None
    if state is None:
        raise HTTPException(status_code=404, detail=BATCH_GONE)
    return state


@router.get("/batch/{job_id}", summary="Состояние пакета")
async def get_batch(
    job_id: str,
    deps: DepsDep,
    limit: Annotated[
        int | None,
        Query(ge=0, description="сколько первых строк вернуть: 0 — только счётчики; нет — все"),
    ] = None,
) -> BatchState:
    """Счётчики (`processed`, `counts`) и строки. Большой пакет опрашивают с `limit=0`."""
    state = await deps.batches.state(job_id, limit=limit) if deps.batches else None
    if state is None:
        raise HTTPException(status_code=404, detail=BATCH_GONE)
    return state


@router.get("/batch/{job_id}/results", summary="Строки пакета с фильтром по статусу")
async def get_batch_results(
    job_id: str,
    deps: DepsDep,
    status: Annotated[RowStatus | None, Query(description="статус строки; нет — все")] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="размер страницы")] = 50,
    after_row: Annotated[int, Query(ge=0, description="после номера строки, исключительно")] = 0,
) -> BatchResultPage:
    page = (
        await deps.batches.result_page(job_id, status=status, limit=limit, after_row=after_row)
        if deps.batches
        else None
    )
    if page is None:
        raise HTTPException(status_code=404, detail=BATCH_GONE)
    return page


@router.get("/batch/{job_id}/rows/{row}", summary="Строка файла: открыть её в форме карточки")
async def get_batch_row(job_id: str, row: int, deps: DepsDep) -> BatchRowIn:
    """Наименование, описание и характеристики строки как в файле поставщика (номер с 1)."""
    found = await deps.batches.row(job_id, row) if deps.batches else None
    if found is None:
        raise HTTPException(status_code=404, detail="строки нет: пакет устарел или номер неверный")
    return BatchRowIn(
        row=found.row,
        name=found.name,
        description=found.description,
        characteristics=found.characteristics,
    )


@router.get(
    "/batch/{job_id}/result.xlsx",
    summary="Файл с результатом",
    response_class=Response,
    responses={200: {"content": {XLSX: {}}}},
)
async def get_batch_result(job_id: str, deps: DepsDep) -> Response:
    state = await deps.batches.state(job_id, limit=0) if deps.batches else None
    if state is None or deps.batches is None:
        raise HTTPException(status_code=404, detail=BATCH_GONE)
    if state.status not in ("done", "cancelled"):
        raise HTTPException(status_code=409, detail="пакет ещё обрабатывается")
    content = await result_file(deps.batches, job_id, cancelled=state.status == "cancelled")
    disposition = 'attachment; filename="result.xlsx"'
    return Response(content, media_type=XLSX, headers={"Content-Disposition": disposition})


@router.get("/metrics", summary="Качество на золотом наборе")
async def get_metrics(deps: DepsDep) -> MetricsOut:
    """Результат последнего прогона `python -m app.tools.evaluate`.

    У каждой метрики — значение, размер выборки и способ расчёта.
    """
    if not deps.metrics_path.is_file():
        raise HTTPException(status_code=404, detail="оценка ещё не запускалась")
    return MetricsOut.model_validate_json(deps.metrics_path.read_text(encoding="utf-8"))


class HoldoutOut(BaseModel):
    created_at: datetime
    catalog: str
    cases: int
    llm: str | None = None
    rows: list[MetricRow]


# Ключ отчёта `evaluate_holdout` → (ключ строки, название, способ расчёта). Доли — от всех случаев,
# точность автовыбора — от решённых без вопроса.
_HOLDOUT_ROWS = [
    (
        "category_top1",
        "category_top1",
        "Категория первой",
        "доля СТЕ, где верная категория — первый вариант",
    ),
    (
        "category_top5",
        "category_top5",
        "Категория среди пяти",
        "верная категория среди пяти первых вариантов",
    ),
    ("decided", "decided", "Категория без вопроса", "доля СТЕ, где модуль поставил код сам"),
    (
        "decided_precision",
        "decided_precision",
        "Точность автовыбора",
        "верных кодов среди поставленных без вопроса",
    ),
    (
        "confident_errors",
        "confident_errors",
        "Уверенные ошибки",
        "доля всех СТЕ: неверный код без вопроса",
    ),
    ("not_found", "not_found", "«Не найдено»", "доля СТЕ без единого кандидата"),
    (
        "given_recall",
        "given_recall",
        "Значения поставщика найдены",
        "значения из описания, попавшие в своё поле",
    ),
    (
        "target_recall",
        "target_recall",
        "Чего нет в тексте: заполнено верно",
        "характеристики настоящей СТЕ, которых нет в описании, заполненные модулем верно",
    ),
    (
        "target_precision",
        "target_precision",
        "Точность сверх текста",
        "верных среди значений модуля, которых не было в описании",
    ),
    (
        "zero_touch",
        "zero_touch",
        "Без единого действия",
        "доля СТЕ: верный код без вопроса и все обязательные характеристики заполнены",
    ),
    (
        "required_auto",
        "required_auto",
        "Обязательные заполнены сами",
        "обязательных характеристик со значением модуля, при верной категории",
    ),
    (
        "manual_fields_per_card",
        "manual_fields",
        "Характеристик вручную",
        "характеристик настоящей СТЕ, которые поставщик вводит или исправляет сам, на одну СТЕ",
    ),
    (
        "manual_required_per_card",
        "manual_required",
        "Обязательных вручную",
        "сколько обязательных характеристик поставщик вводит сам, на одну СТЕ",
    ),
    ("latency_ms_p50", "latency_p50", "Задержка, медиана", "время /autofill на одну СТЕ"),
    (
        "latency_ms_p95",
        "latency_p95",
        "Задержка, 95-й перцентиль",
        "время /autofill на одну СТЕ",
    ),
    ("over_7s", "over_7s", "Дольше 7 с", "доля СТЕ, где ответ дольше бюджета шага 7 с"),
]


@router.get("/metrics/holdout", summary="Отложенная выборка СТЕ портала")
async def get_holdout_report(deps: DepsDep) -> HoldoutOut:
    """Результат последнего прогона `python -m app.tools.evaluate_holdout`: СТЕ портала, вынутые
    вместе с двойниками из справочника и индекса похожих СТЕ, — честная мера на живых
    наименованиях."""
    path = deps.holdout_report_path
    if not path.is_file():
        raise HTTPException(status_code=404, detail="отложенная выборка ещё не оценивалась")
    report = json.loads(path.read_text(encoding="utf-8"))
    n = int(report.get("cases") or 0)
    # Выборка строки: точность автовыбора — от решённых без вопроса, значения — от их числа.
    base = {
        "decided_precision": "decided_cases",
        "given_recall": "givens",
        "required_auto": "required_fields",
        "manual_required_per_card": "right_category_cases",
        "manual_fields_per_card": "right_category_cases",
        "target_recall": "targets",
        "target_precision": "target_filled",
    }
    rows = [
        MetricRow(
            key=key,
            title=title,
            value=float(report[src]),
            n=int(report.get(base.get(src, ""), n)),
            how=how,
        )
        for src, key, title, how in _HOLDOUT_ROWS
        if report.get(src) is not None
    ]
    # Значения такой же СТЕ портала (двойника): точность — от того, сколько они заполнили.
    if twin := (report.get("by_method") or {}).get("twin"):
        after = next((i + 1 for i, r in enumerate(rows) if r.key == "target_precision"), len(rows))
        rows.insert(
            after,
            MetricRow(
                key="twin_precision",
                title="Значения такой же СТЕ портала",
                value=float(twin["precision"]),
                n=int(twin["filled"]),
                how="верных среди значений, взятых у СТЕ портала с тем же наименованием",
            ),
        )
    created = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return HoldoutOut(created_at=created, catalog=str(report.get("catalog") or ""), cases=n,
                      llm=report.get("llm"), rows=rows)  # fmt: skip


@router.get("/metrics/portal", summary="Живые карточки портала: аудит и заполнение")
async def get_portal_report(deps: DepsDep) -> PortalReport:
    """Результат последнего прогона `python -m app.tools.portal_report`.

    Карточки СТЕ с публичного API портала: сколько нарушают правила модерации (с примерами-ссылками)
    и насколько значения, извлечённые модулем из текста, совпадают с введёнными поставщиком.
    """
    if not deps.portal_report_path.is_file():
        raise HTTPException(status_code=404, detail="отчёт по карточкам портала ещё не собран")
    return PortalReport.model_validate_json(deps.portal_report_path.read_text(encoding="utf-8"))
