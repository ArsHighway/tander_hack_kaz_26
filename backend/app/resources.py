"""Подключения к внешним сервисам: создаются один раз на процесс и живут в app.state."""

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from faststream.rabbit import RabbitBroker
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.catalog import Catalog
from app.config import Settings
from app.jobs import QUEUES
from app.llm import LLMClient
from app.portal.images import ImageCache, portal_client

if TYPE_CHECKING:
    from app.pipeline import Deps
    from app.search.exemplar_vectors import ExemplarVectors
    from app.search.exemplars import ExemplarIndex
    from app.search.similar import SimilarValues
    from app.search.twins import TwinIndex

log = logging.getLogger(__name__)

# Сколько порций пакета публикуется одновременно.
PUBLISH_SLICE = 200
# Как прокси картинок представляется порталу.


def create_broker(settings: Settings) -> RabbitBroker:
    return RabbitBroker(settings.rabbitmq_url)


def redis_client(settings: Settings) -> Redis:
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=settings.redis_socket_timeout_s,
    )


def load_exemplars(settings: Settings) -> "ExemplarIndex | None":
    """Похожие СТЕ из `EXEMPLARS_PATH`; путь задан, а файла нет — ошибка старта, не тихий откат."""
    from app.search.exemplars import ExemplarIndex

    if not settings.exemplars_path:
        return None
    path = Path(settings.exemplars_path)
    if not path.is_file():
        raise FileNotFoundError(f"EXEMPLARS_PATH: файл не найден: {path}")
    index = ExemplarIndex.load(path)
    log.info("похожие СТЕ: %d наименований из %s", len(index), path)
    return index


def load_exemplar_vectors(settings: Settings) -> "ExemplarVectors | None":
    """Векторы похожих СТЕ из `EXEMPLAR_VECTORS_PATH`; путь задан, а файла нет — ошибка старта."""
    from app.search.exemplar_vectors import ExemplarVectors

    if not settings.exemplar_vectors_path:
        return None
    path = Path(settings.exemplar_vectors_path)
    if not path.is_file():
        raise FileNotFoundError(f"EXEMPLAR_VECTORS_PATH: файл не найден: {path}")
    vectors = ExemplarVectors.load(path)
    log.info("векторы похожих СТЕ: %d из %s", len(vectors), path)
    return vectors


def load_similar(settings: Settings) -> "SimilarValues | None":
    """Характеристики похожих СТЕ рядом с индексом (`build_exemplar_values`); нет — без
    подсказок."""
    from app.search.similar import SimilarValues, values_path

    if not settings.exemplars_path:
        return None
    path = values_path(Path(settings.exemplars_path))
    if not path.is_file():
        log.info("подсказок похожих СТЕ нет: %s не собран (build_exemplar_values)", path)
        return None
    log.info("подсказки похожих СТЕ: %s", path)
    return SimilarValues(path)


def load_twins(settings: Settings) -> "TwinIndex | None":
    """Двойники из `TWINS_PATH`; путь задан, а файла нет — ошибка старта, не тихий откат."""
    from app.search.twins import TwinIndex

    if not settings.twins_path:
        return None
    path = Path(settings.twins_path)
    if not path.is_file():
        raise FileNotFoundError(f"TWINS_PATH: файл не найден: {path} (build_twins)")
    log.info("двойники СТЕ: %s", path)
    return TwinIndex(path)


def _review_client(settings: Settings) -> LLMClient | None:
    model = settings.llm_review_model.strip()
    if not model:
        return None
    update = {"llm_chat_model": model}
    return LLMClient.from_settings(settings.model_copy(update=update))


def load_ktru(settings: Settings) -> Catalog | None:
    """Справочник КТРУ из `KTRU_PATHS` (через запятую); пусто — второго этапа нет."""
    paths = [Path(p.strip()) for p in settings.ktru_paths.split(",") if p.strip()]
    return Catalog.load(paths) if paths else None


@dataclass
class Resources:
    settings: Settings
    engine: AsyncEngine
    redis: Redis
    broker: RabbitBroker
    llm: LLMClient
    catalog: Catalog
    # Второй слой модели (`LLM_REVIEW_MODEL`); None — выключен.
    review_llm: LLMClient | None = None
    exemplars: "ExemplarIndex | None" = None
    exemplar_vectors: "ExemplarVectors | None" = None
    similar: "SimilarValues | None" = None
    twins: "TwinIndex | None" = None
    ktru: Catalog | None = None
    # Картинки портала через свой прокси с дисковым кэшем (`IMAGE_CACHE_DIR`).
    images: ImageCache | None = None
    broker_started: bool = field(default=False)

    @classmethod
    def create(cls, settings: Settings) -> "Resources":
        portal = portal_client(settings.image_timeout_s)
        return cls(
            settings=settings,
            engine=create_async_engine(settings.database_url, pool_pre_ping=True),
            redis=redis_client(settings),
            broker=create_broker(settings),
            llm=LLMClient.from_settings(settings),
            review_llm=_review_client(settings),
            catalog=Catalog.load(settings.catalog_files()),
            exemplars=load_exemplars(settings),
            exemplar_vectors=load_exemplar_vectors(settings),
            similar=load_similar(settings),
            twins=load_twins(settings),
            ktru=load_ktru(settings),
            images=ImageCache(
                Path(settings.image_cache_dir),
                portal,
                timeout_s=settings.image_timeout_s,
                wait_s=settings.image_wait_s,
            ),
        )

    async def ensure_broker(self) -> RabbitBroker:
        """Подключает брокер при первом обращении: API стартует и без поднятого RabbitMQ."""
        if not self.broker_started:
            await self.broker.start()
            for queue in QUEUES:
                await self.broker.declare_queue(queue)
            self.broker_started = True
        return self.broker

    async def close(self) -> None:
        if self.broker_started:
            await self.broker.stop()
        await self.llm.aclose()
        if self.review_llm is not None:
            await self.review_llm.aclose()
        if self.images is not None:
            await self.images.close()
        await self.redis.aclose()
        await self.engine.dispose()


def build_deps(res: Resources) -> "Deps":
    """Зависимости конвейера из подключений процесса: индекс BM25 строится один раз при старте."""
    from app.batch import BatchStore
    from app.glossary import Glossary
    from app.jobs import BATCH_QUEUE, batch_chunks
    from app.llm.cache import CachedChat
    from app.pipeline import Deps
    from app.search import build_bm25
    from app.search.vectors import EmbeddingIndex
    from app.session import SessionStore

    async def enqueue_batch(job_id: str, total: int) -> None:
        """Порции пакета в очередь: их разбирают все воркеры. Публикуем пачками параллельно —
        5000 порций (100 тыс. строк) уходят за секунды, а не по одной с ожиданием подтверждения."""
        broker = await res.ensure_broker()
        chunks = batch_chunks(job_id, total, res.settings.batch_chunk_rows)
        for i in range(0, len(chunks), PUBLISH_SLICE):
            await asyncio.gather(
                *(broker.publish(c, queue=BATCH_QUEUE) for c in chunks[i : i + PUBLISH_SLICE])
            )

    return Deps(
        catalog=res.catalog,
        exemplars=res.exemplars,
        exemplar_vectors=res.exemplar_vectors,
        similar=res.similar,
        twins=res.twins,
        ktru=res.ktru,
        bm25=build_bm25(res.catalog),
        vectors=EmbeddingIndex(res.engine, res.llm.embed_model, res.catalog.version),
        llm=CachedChat(res.llm, res.redis, ttl_s=res.settings.llm_cache_ttl_s),
        glossary=Glossary.load(),
        sessions=SessionStore(res.redis, ttl_s=res.settings.job_ttl_s),
        min_score=res.settings.search_min_score,
        llm_budget_s=res.settings.llm_budget_s,
        llm_enabled=res.settings.llm_enabled,
        rerank_types=res.settings.llm_rerank_types,
        review_llm=CachedChat(res.review_llm, res.redis, ttl_s=res.settings.llm_cache_ttl_s)
        if res.review_llm is not None
        else None,
        review_budget_s=res.settings.llm_review_budget_s,
        batches=BatchStore(
            res.redis,
            ttl_s=res.settings.batch_ttl_s,
            pending_ttl_s=res.settings.batch_pending_ttl_s,
        ),
        enqueue_batch=enqueue_batch,
        metrics_path=Path(res.settings.metrics_path),
        portal_report_path=Path(res.settings.portal_report_path),
        holdout_report_path=Path(res.settings.holdout_report_path),
    )
