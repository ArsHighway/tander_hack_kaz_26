"""Работы и услуги вместо товара: «не найдено» объясняет, почему."""

import asyncio
from pathlib import Path

from app.catalog import Catalog
from app.glossary import Glossary
from app.pipeline import AutofillIn, Deps, autofill
from app.search import build_bm25
from app.search.candidates import outside_kind
from app.search.exemplars import ExemplarIndex
from app.session import SessionStore
from tests.fakes import FakeLLM, FakeRedis

CATALOG = Catalog.load([Path(__file__).parent.parent / "app" / "catalog" / "demo" / "demo.json"])
EXEMPLARS = ExemplarIndex(
    [
        ("СМЕС", "Смеситель для кухни Grohe однорычажный"),
        ("СМЕС", "Смеситель для ванны с душем"),
        ("__works__5", "Ремонт мягкой кровли наплавляемыми материалами"),
        ("__works__5", "Ремонт кровли из металлочерепицы"),
        ("__services__7", "Услуги по уборке помещений"),
        ("__services__7", "Услуги по уборке территории"),
    ]
)


def test_outside_kind_tells_works_from_services() -> None:
    assert outside_kind("Ремонт мягкой кровли", EXEMPLARS) == "works"
    assert outside_kind("Услуги по уборке офиса", EXEMPLARS) == "services"
    assert outside_kind("Смеситель для кухни", EXEMPLARS) is None
    assert outside_kind("Ремонт мягкой кровли", None) is None


def _deps() -> Deps:
    return Deps(
        catalog=CATALOG,
        bm25=build_bm25(CATALOG),
        vectors=None,
        llm=FakeLLM([]),  # type: ignore[arg-type]
        glossary=Glossary([]),
        sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.2,
        exemplars=EXEMPLARS,
        llm_paused_until=float("inf"),
    )


def test_works_get_their_own_not_found_message() -> None:
    out = asyncio.run(autofill(AutofillIn(name="Ремонт мягкой кровли"), _deps()))
    assert out.ktru.final_code is None and out.ktru.question is None
    assert out.ktru.steps[0].startswith("Похоже на работы, а не на товар")
    first = out.issues[0]
    assert (first.field, first.severity) == ("ktru_code", "block")
    assert first.message.startswith("Похоже на работы")


def test_services_message_names_services() -> None:
    out = asyncio.run(autofill(AutofillIn(name="Услуги по уборке офиса"), _deps()))
    assert out.ktru.steps[0].startswith("Похоже на услугу, а не на товар")
    assert out.issues[0].message.startswith("Похоже на услугу")
