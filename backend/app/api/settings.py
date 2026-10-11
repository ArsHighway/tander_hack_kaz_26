"""Настройки модуля, которые меняет модератор: `/api/v1/settings`.

Модель (LLM) модератор включает в дни небольшой нагрузки и выключает, когда поставщиков много: без
неё карточка собирается правилами и справочником КТРУ, ответ быстрее. Поставщик выключателя не
видит. Значение лежит в Redis (`app.settings_store`): оно общее для API и воркеров пакетов и
переживает перезапуск процессов.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, StrictBool

from app.api.v1 import DepsDep
from app.pipeline import Deps
from app.settings_store import SettingsStore

router = APIRouter(prefix="/v1", tags=["модуль"])


def get_store(request: Request) -> SettingsStore:
    return request.app.state.settings_store


StoreDep = Annotated[SettingsStore, Depends(get_store)]


class ModuleSettings(BaseModel):
    llm_enabled: bool = Field(description="модель включена: дозаполняет то, чего не нашли правила")
    llm_budget_s: float | None = Field(
        description="сколько шаг мастера ждёт модель, с; пусто — без предела"
    )


class ModuleSettingsIn(BaseModel):
    llm_enabled: StrictBool


def _settings(deps: Deps) -> ModuleSettings:
    return ModuleSettings(llm_enabled=deps.llm_enabled, llm_budget_s=deps.llm_budget_s)


@router.get("/settings", summary="Настройки модуля")
async def get_settings(deps: DepsDep, store: StoreDep) -> ModuleSettings:
    await store.refresh(deps)
    return _settings(deps)


@router.put("/settings", summary="Включить или выключить модель (модератор)")
async def put_settings(inp: ModuleSettingsIn, deps: DepsDep, store: StoreDep) -> ModuleSettings:
    try:
        await store.set_llm_enabled(inp.llm_enabled)
    except Exception as exc:  # любой сбой Redis: настройка не сохранена
        raise HTTPException(
            status_code=503, detail="настройка не сохранена: хранилище недоступно"
        ) from exc
    deps.llm_enabled = inp.llm_enabled
    return _settings(deps)
