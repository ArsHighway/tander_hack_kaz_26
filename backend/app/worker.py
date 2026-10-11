"""Воркер фоновых задач: `faststream run app.worker:app`.

Реплик несколько (`docker compose up -d --scale worker=N` или `WORKER_REPLICAS`): порции пакета
из очереди jobs.batch разбирают все.
"""

import asyncio
import logging
import math
import socket
import time

from faststream import AckPolicy, Context, ContextRepo, FastStream
from faststream.rabbit import Channel
from redis.asyncio import Redis

from app.batch import run_chunk
from app.config import get_settings
from app.jobs import BATCH_QUEUE, PING_QUEUE, BatchJob, JobState, PingJob, job_key
from app.pipeline import Deps
from app.resources import Resources, build_deps, create_broker, redis_client
from app.settings_store import SettingsStore, make_store

settings = get_settings()
broker = create_broker(settings)
app = FastStream(broker)
log = logging.getLogger(__name__)


@app.on_startup
async def open_redis(context: ContextRepo) -> None:
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    context.set_global("redis", redis_client(settings))
    # Те же справочник, индекс и модель, что у API: строки пакета идут тем же конвейером.
    resources = Resources.create(settings)
    context.set_global("resources", resources)
    context.set_global("deps", build_deps(resources))
    context.set_global("settings_store", make_store(resources))


@app.after_shutdown
async def close_redis(redis: Redis = Context(), resources: Resources = Context()) -> None:
    await redis.aclose()
    await resources.close()


@broker.subscriber(PING_QUEUE)
async def handle_ping(job: PingJob, redis: Redis = Context()) -> None:
    state = JobState(job_id=job.job_id, status="done", detail=f"воркер {socket.gethostname()}")
    await redis.set(job_key(job.job_id), state.model_dump_json(), ex=settings.job_ttl_s)
    log.info("ping %s обработан", job.job_id)


# Строки, которые воркер обрабатывает одновременно (общий лимит на все порции процесса).
row_slots = asyncio.Semaphore(settings.worker_concurrency)
# Порций в руках воркера: чтобы занять все места и перекрыть хвост порции, плюс одна про запас.
# Остальные порции ждут в очереди — их заберёт освободившийся воркер, а не этот.
PREFETCH = math.ceil(settings.worker_concurrency / settings.batch_chunk_rows) + 1


@broker.subscriber(
    BATCH_QUEUE,
    channel=Channel(prefetch_count=PREFETCH),
    # Исключение (Redis недоступен) — порция возвращается в очередь; строки, что уже записаны,
    # повторная доставка пропустит. Сбой самой строки сюда не доходит: у строки статус failed.
    ack_policy=AckPolicy.NACK_ON_ERROR,
)
async def handle_batch(
    job: BatchJob, deps: Deps = Context(), settings_store: SettingsStore = Context()
) -> None:
    started = time.perf_counter()
    await settings_store.refresh(deps)  # модератор мог включить или выключить модель
    done = await run_chunk(job, deps, row_slots)
    log.info(
        "пакет %s: строки %d–%s, обработано %d за %.1f с, воркер %s",
        job.job_id, job.start + 1, job.stop if job.stop is not None else "конец", done,
        time.perf_counter() - started, socket.gethostname(),
    )  # fmt: skip
