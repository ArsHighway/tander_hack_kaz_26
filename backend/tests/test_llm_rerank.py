"""Переранжирование типов КТРУ моделью: порядок принимается, только если все имена из списка."""

import asyncio
from types import SimpleNamespace
from typing import Any

from app.extract.llm_fill import rerank_types, suggest_type_order
from app.extract.model import TypeOrder, TypeRanking
from app.glossary import Glossary
from app.llm import LLMError, LLMOutputError
from tests.fakes import FakeLLM

NAMES = [
    "Тумба прикроватная",
    "Тумба офисная деревянная",
    "Тумбочка прикроватная",
    "Тумба для телевизора",
    "Тумба с мойкой",
]
TEXT = "Тумбочка у кровати из дерева, 2 ящика"


def _rerank(replies: list[Any], names: list[str] | None = None) -> tuple[TypeOrder | None, FakeLLM]:
    llm = FakeLLM(replies)
    got = asyncio.run(rerank_types(TEXT, names or NAMES, llm))  # type: ignore[arg-type]
    return got, llm


def test_model_order_with_reason_is_accepted() -> None:
    reply = TypeRanking(
        order=["Тумбочка прикроватная", "Тумба прикроватная"],
        reason="Текст говорит о тумбочке у кровати",
    )
    got, llm = _rerank([reply])
    assert got is not None
    # Названные модель ставит первыми, остальные — следом в исходном порядке: никто не потерян.
    assert got.names == [
        "Тумбочка прикроватная",
        "Тумба прикроватная",
        "Тумба офисная деревянная",
        "Тумба для телевизора",
        "Тумба с мойкой",
    ]
    assert got.reason == "Текст говорит о тумбочке у кровати"
    assert sorted(got.names) == sorted(NAMES)
    assert len(llm.calls) == 1


def test_invented_type_rejects_the_whole_answer() -> None:
    reply = TypeRanking(order=["Тумба прикроватная", "Тумба резная"], reason="…")
    got, _ = _rerank([reply])
    assert got is None  # исходный порядок остаётся у вызывающего


def test_repeated_names_are_taken_once() -> None:
    reply = TypeRanking(order=["Тумба с мойкой", "Тумба с мойкой", "Тумба прикроватная"])
    got, _ = _rerank([reply])
    assert got is not None
    assert got.names[:2] == ["Тумба с мойкой", "Тумба прикроватная"]
    assert len(got.names) == len(NAMES)


def test_empty_order_keeps_the_original() -> None:
    got, _ = _rerank([TypeRanking(order=[], reason="не знаю")])
    assert got is None


def test_reason_is_trimmed() -> None:
    got, _ = _rerank([TypeRanking(order=[NAMES[2]], reason="потому что " * 100)])
    assert got is not None and len(got.reason) <= 200


def test_one_candidate_is_not_sent_to_the_model() -> None:
    llm = FakeLLM()
    assert asyncio.run(rerank_types(TEXT, ["Тумба прикроватная"], llm)) is None  # type: ignore[arg-type]
    assert llm.calls == []


def test_bad_structured_answer_twice_keeps_the_original() -> None:
    bad = LLMOutputError("не JSON", raw="...")
    got, llm = _rerank([bad, bad])
    assert got is None
    assert len(llm.calls) == 2


def test_prompt_lists_names_and_text() -> None:
    _, llm = _rerank([TypeRanking(order=[NAMES[0]])])
    system, user = llm.calls[0]
    assert "Не добавляй наименований" in system["content"]
    assert "- Тумба с мойкой" in user["content"]
    assert TEXT in user["content"]


def _deps(llm: FakeLLM, *, enabled: bool = True) -> Any:
    return SimpleNamespace(
        llm=llm,
        glossary=Glossary([]),
        llm_enabled=enabled,
        llm_budget_s=None,
        llm_paused_until=0.0,
    )


def test_switched_off_model_does_not_rerank() -> None:
    llm = FakeLLM()
    assert asyncio.run(suggest_type_order(_deps(llm, enabled=False), TEXT, NAMES)) is None
    assert llm.calls == []


def test_service_failure_leaves_the_order_and_pauses_the_model() -> None:
    llm = FakeLLM([LLMError("429 usage limit")])
    deps = _deps(llm)
    assert asyncio.run(suggest_type_order(deps, TEXT, NAMES)) is None
    assert deps.llm_paused_until > 0.0
