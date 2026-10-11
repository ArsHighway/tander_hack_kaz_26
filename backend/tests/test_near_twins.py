"""100 почти одинаковых товаров (блокер команды 06.10): верная позиция по одному признаку."""

import asyncio
from collections import Counter

from app.evaluation.near_twins import THICKNESS, near_cases, near_catalog, score
from app.glossary import Glossary
from app.pipeline import AutofillIn, Deps, autofill
from app.search import build_bm25
from app.session import SessionStore
from tests.fakes import FakeLLM, FakeRedis


def _deps() -> Deps:
    catalog = near_catalog()
    return Deps(
        catalog=catalog,
        bm25=build_bm25(catalog),
        vectors=None,
        llm=FakeLLM([]),  # type: ignore[arg-type]
        glossary=Glossary([]),
        sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.2,
        llm_paused_until=float("inf"),
    )


def test_thickness_in_any_notation_picks_exactly_that_position() -> None:
    deps = _deps()
    total: Counter[str] = Counter()
    for case in near_cases():
        out = asyncio.run(autofill(AutofillIn(name=case.text), deps))
        q = out.ktru.question.characteristic if out.ktru.question else None
        total += score(case, out.ktru.final_code, q)
    assert (total["with_value"], total["right"], total["wrong"]) == (300, 300, 0)
    assert total["no_value_asked"] == 1


def test_without_thickness_the_module_asks_instead_of_guessing() -> None:
    out = asyncio.run(autofill(AutofillIn(name="Перчатки смотровые нитриловые, размер M"), _deps()))
    assert out.ktru.final_code is None
    assert out.ktru.question is not None and out.ktru.question.characteristic == THICKNESS
    assert len(out.ktru.question.options) >= 2  # толщины на выбор, а не одна угаданная
