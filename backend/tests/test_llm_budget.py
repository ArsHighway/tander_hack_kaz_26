"""Бюджет времени LLM (ответ шага — до 7 с).

Модель спрашивают только о характеристиках определённой позиции и не дольше `llm_budget_s`;
отказ сервиса (квота, 429) не держит следующие запросы.
"""

import asyncio
import time
from pathlib import Path

import httpx
import pytest

from app.catalog import Catalog
from app.extract import LLMItem, LLMItems
from app.glossary import Glossary
from app.llm import LLMRateLimited
from app.llm.base import HttpProvider
from app.pipeline import AnswerIn, AutofillIn, Deps, answer, autofill
from app.search import build_bm25
from app.session import SessionStore
from tests.fakes import FakeLLM, FakeRedis

CATALOG = Catalog.load([Path(__file__).parent.parent / "app" / "catalog" / "demo" / "demo.json"])
DECIDED = "Перчатки смотровые нитриловые"  # код определён сразу
UNDECIDED = "Бумага офисная А4 500 листов"  # сначала вопрос «Цветность»


def _deps(llm: object, budget: float | None = 2.0) -> Deps:
    return Deps(
        catalog=CATALOG,
        bm25=build_bm25(CATALOG),
        vectors=None,
        llm=llm,  # type: ignore[arg-type]
        glossary=Glossary([]),
        sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.2,
        llm_budget_s=budget,
    )


class SlowLLM(FakeLLM):
    async def chat(self, messages, *, schema=None, temperature=0.0):  # type: ignore[no-untyped-def]
        self.calls.append(messages)
        await asyncio.sleep(10)
        raise AssertionError("медленная модель не должна дождаться ответа")


def test_slow_model_does_not_hold_the_step() -> None:
    llm = SlowLLM()
    deps = _deps(llm, budget=0.3)
    t0 = time.perf_counter()
    out = asyncio.run(autofill(AutofillIn(name=DECIDED), deps))
    assert time.perf_counter() - t0 < 1.5
    assert len(llm.calls) == 1 and "llm" in out.degraded
    assert out.ktru.final_code  # карточка есть и без модели


def test_model_is_asked_only_once_the_position_is_decided() -> None:
    # Пока код не определён, модель не спрашивают (лишний запрос по чужим характеристикам);
    # ответ поставщика определил код — спрашивают один раз и только о характеристиках этой позиции.
    llm = FakeLLM([LLMItems(items=[])])
    deps = _deps(llm)

    async def scenario() -> tuple[int, int, str | None, str]:
        out = await autofill(AutofillIn(name=UNDECIDED), deps)
        assert out.ktru.final_code is None and out.ktru.question is not None
        before = len(llm.calls)
        question = out.ktru.question
        done = await answer(
            out.session_id,
            AnswerIn(characteristic=question.characteristic, value=question.options[0].value),
            deps,
        )
        again = await answer(
            out.session_id,
            AnswerIn(characteristic=question.characteristic, value=question.options[0].value),
            deps,
        )
        assert again.ktru.final_code == done.ktru.final_code
        prompt = llm.calls[-1][-1]["content"] if llm.calls else ""
        return before, len(llm.calls), done.ktru.final_code, prompt

    before, after, final, prompt = asyncio.run(scenario())
    assert (before, after) == (0, 1) and final
    position = CATALOG.get(final)
    assert position is not None
    others = {
        c.name
        for p in CATALOG.all()
        if p.code != final
        for c in p.characteristics
        if c.name not in {s.name for s in position.characteristics}
    }
    assert not [n for n in others if n in prompt]


def test_model_answer_after_the_decision_reaches_the_card() -> None:
    # Ответ модели, полученный в /answer, сохраняется в сессии и виден в карточке
    reply = LLMItems(items=[LLMItem(name="Белизна", value="90", quote="плотная 90")])
    llm = FakeLLM([reply])
    deps = _deps(llm)

    async def scenario() -> tuple[str | None, str | None, str | None]:
        out = await autofill(AutofillIn(name=UNDECIDED, description="плотная 90"), deps)
        done = await answer(
            out.session_id, AnswerIn(characteristic="Цветность", value="Белая"), deps
        )
        session = await deps.sessions.load(out.session_id)
        assert session is not None
        white = next(c for c in done.characteristics if c.name == "Белизна")
        return done.ktru.final_code, session.llm_for, f"{white.value}/{white.method}"

    final, asked_for, white = asyncio.run(scenario())
    assert final == asked_for == "17.12.14.129-00000031"
    assert white == "90/llm"


def test_service_refusal_pauses_the_model_for_the_next_requests() -> None:
    llm = FakeLLM([LLMRateLimited("429 usage limit")])  # второй вызов был бы AssertionError
    deps = _deps(llm)
    first = asyncio.run(autofill(AutofillIn(name=DECIDED), deps))
    second = asyncio.run(autofill(AutofillIn(name=DECIDED), deps))
    assert "llm" in first.degraded and "llm" in second.degraded
    assert len(llm.calls) == 1 and deps.llm_paused_until > time.monotonic()


def test_exhausted_quota_is_not_retried() -> None:
    # «You reached the Free usage limit» не пройдёт за 30 с — шесть повторов с паузой только
    # держали запрос минуту.
    hits = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request)
        return httpx.Response(429, json={"error": "You reached the Free usage limit. Pro is $20"})

    provider = HttpProvider("http://llm", transport=httpx.MockTransport(handler))
    t0 = time.perf_counter()
    with pytest.raises(LLMRateLimited):
        asyncio.run(provider.request("POST", "/api/chat", {"model": "m"}))
    assert len(hits) == 1 and time.perf_counter() - t0 < 1.0


def test_model_value_from_answer_survives_reload() -> None:
    # Значение модели, полученное в /answer, лежит в Redis, а не только в ответе
    from app.pipeline import render

    reply = LLMItems(items=[LLMItem(name="Белизна", value="90", quote="плотная 90")])
    deps = _deps(FakeLLM([reply]))

    async def scenario() -> str:
        out = await autofill(AutofillIn(name=UNDECIDED, description="плотная 90"), deps)
        await answer(out.session_id, AnswerIn(characteristic="Цветность", value="Белая"), deps)
        session = await deps.sessions.load(out.session_id)
        assert session is not None
        white = next(c for c in render(session, deps).characteristics if c.name == "Белизна")
        return f"{white.value}/{white.method}"

    assert asyncio.run(scenario()) == "90/llm"


def test_bad_input_does_not_pause_the_model_for_everyone() -> None:
    # 400 «контекст переполнен» и ошибка на тексте — беда этого входа, а не сервиса
    from app.llm import LLMInputError

    llm = FakeLLM([LLMInputError("400 context length exceeded"), LLMItems(items=[])])
    deps = _deps(llm)
    asyncio.run(autofill(AutofillIn(name=DECIDED), deps))
    asyncio.run(autofill(AutofillIn(name=DECIDED), deps))
    assert len(llm.calls) == 2 and deps.llm_paused_until == 0.0


def test_step_after_a_refusal_asks_the_model_again() -> None:
    # Модель отказала (пауза) — позиция не помечается «спрошено»: после паузы следующий шаг спросит
    reply = LLMItems(items=[LLMItem(name="Белизна", value="90", quote="плотная 90")])
    llm = FakeLLM([LLMRateLimited("429"), reply])
    deps = _deps(llm)

    async def scenario() -> tuple[str | None, str | None]:
        out = await autofill(AutofillIn(name=UNDECIDED, description="плотная 90"), deps)
        first = await answer(
            out.session_id, AnswerIn(characteristic="Цветность", value="Белая"), deps
        )
        deps.llm_paused_until = 0.0  # пауза кончилась
        again = await answer(
            out.session_id, AnswerIn(characteristic="Цветность", value="Белая"), deps
        )
        get = lambda o: next(c for c in o.characteristics if c.name == "Белизна").value  # noqa: E731
        return get(first), get(again)

    assert asyncio.run(scenario()) == (None, "90")


def test_short_retry_after_is_not_an_exhausted_quota() -> None:
    # «quota» в тексте с Retry-After 1 с — поминутная квота: подождать и повторить
    hits = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request)
        if len(hits) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "check quota"})
        return httpx.Response(200, json={"ok": True})

    provider = HttpProvider("http://llm", transport=httpx.MockTransport(handler))
    assert asyncio.run(provider.request("POST", "/api/chat", {"model": "m"})) == {"ok": True}
    assert len(hits) == 2


def test_model_values_of_the_previous_position_are_dropped() -> None:
    # Код сменился ответом: значения модели о прежней позиции — вон (их имена могли совпасть)
    from app.extract import Extracted
    from app.pipeline import _fill_missing
    from app.schemas import Span
    from app.session import Session

    session = Session.new(name=DECIDED, description="")
    span = Span(field="name", start=0, end=4, text="Перч")
    session.extracted = [Extracted(name="Размер", raw_value="M", source=span, method="llm")]
    deps = _deps(FakeLLM([LLMItems(items=[])]))
    asyncio.run(_fill_missing(session, "22.19.60.119-00000301", deps))
    assert [e for e in session.extracted if e.method == "llm"] == []
    assert session.llm_for == session.similar_for == "22.19.60.119-00000301"
