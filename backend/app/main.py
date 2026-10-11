import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import demo, health, jobs, v1
from app.api import settings as module_settings
from app.config import get_settings
from app.demo import PgSubmissionRepo
from app.demo.photos import PgPhotoRepo
from app.resources import Resources, build_deps
from app.settings_store import make_store


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    app.state.resources = Resources.create(settings)
    app.state.deps = build_deps(app.state.resources)
    app.state.settings_store = make_store(app.state.resources)
    app.state.submissions = PgSubmissionRepo(app.state.resources.engine)
    app.state.photos = PgPhotoRepo(app.state.resources.engine)
    try:
        yield
    finally:
        await app.state.resources.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Модуль автозаполнения характеристик СТЕ",
        description=(
            "API для Портала поставщиков: описание товара → характеристики, код КТРУ, замечания."
        ),
        version="0.2.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.include_router(health.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    app.include_router(v1.router, prefix="/api")
    app.include_router(module_settings.router, prefix="/api")
    app.include_router(demo.router, prefix="/api")
    return app


app = create_app()
