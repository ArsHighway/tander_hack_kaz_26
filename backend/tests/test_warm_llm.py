"""Прогрев кэша ответов модели: тексты идут в настоящий API, на вопросы — первые варианты."""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.api.settings import get_store
from app.api.v1 import get_deps
from app.catalog import Catalog
from app.extract import LLMItems
from app.glossary import Glossary
from app.llm import ChatResult, Message
from app.main import create_app
from app.pipeline import Deps
from app.search import build_bm25
from app.session import SessionStore
from app.settings_store import SettingsStore
from app.tools.warm_llm import ModelSwitchedOff, summary, warm, warm_text
from tests.fakes import FakeRedis

CATALOG = Catalog.load([Path(__file__).parent.parent / "app" / "catalog" / "demo" / "demo.json"])


class CountingLLM:
    """Модель, которая каждый раз отвечает «ничего не нашла» и считает вызовы."""

    chat_model = "fake-chat"
    embed_model = "fake-embed"

    def __init__(self, failure: Exception | None = None) -> None:
        self.calls = 0
        self.failure = failure

    async def chat(
        self, messages: list[Message], *, schema: Any = None, temperature: float = 0.0
    ) -> ChatResult:
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        reply = LLMItems(items=[])
        return ChatResult(text="{}", model="fake", latency_ms=1.0, parsed=reply)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("эмбеддинги не ожидались")


def _deps(llm: CountingLLM, *, enabled: bool = True) -> Deps:
    return Deps(
        catalog=CATALOG,
        bm25=build_bm25(CATALOG),
        vectors=None,
        llm=llm,  # type: ignore[arg-type]
        glossary=Glossary([]),
        sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.2,
        llm_enabled=enabled,
        llm_budget_s=0.5,
    )


def _with_api[T](deps: Deps, job: Callable[[httpx.AsyncClient], Awaitable[T]]) -> T:
    store = SettingsStore(FakeRedis(), deps.llm_enabled)  # type: ignore[arg-type]
    app = create_app()
    app.dependency_overrides[get_deps] = lambda: deps
    app.dependency_overrides[get_store] = lambda: store

    async def run() -> T:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
            return await job(client)

    return asyncio.run(run())


def test_decided_text_is_sent_to_the_model_through_the_api() -> None:
    llm = CountingLLM()
    deps = _deps(llm)
    reports = _with_api(deps, lambda c: warm(c, ["Перчатки смотровые нитриловые"]))
    assert [(r.paths, r.decided) for r in reports] == [(1, 1)]
    assert llm.calls >= 1
    assert reports[0].llm["on"] == 1


def test_question_is_followed_through_the_first_options_only() -> None:
    llm = CountingLLM()
    deps = _deps(llm)
    report = _with_api(deps, lambda c: warm_text(c, "Перчатки", branch=2, max_paths=5))
    assert 1 < report.paths <= 5


def test_switched_off_model_is_not_switched_on_and_not_asked() -> None:
    llm = CountingLLM()
    deps = _deps(llm, enabled=False)
    with pytest.raises(ModelSwitchedOff):
        _with_api(deps, lambda c: warm(c, ["Перчатки смотровые нитриловые"]))
    assert llm.calls == 0
    assert deps.llm_enabled is False


def test_unanswered_model_is_reported_not_hidden() -> None:
    llm = CountingLLM(failure=TimeoutError())
    deps = _deps(llm)
    reports = _with_api(deps, lambda c: warm(c, ["Перчатки смотровые нитриловые"]))
    assert reports[0].unavailable == 1
    assert "модель не ответила" in summary(reports)
