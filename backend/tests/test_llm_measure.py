"""Инструмент замера бюджета модели: перцентили, сводка и чтение журнала извлечения."""

import asyncio
import logging

import pytest

from app.catalog import CharacteristicSpec
from app.extract import LLMItem, LLMItems, extract_llm
from app.glossary import Glossary
from app.tools.measure_llm import (
    Counters,
    Mode,
    _ReplyLog,
    legacy_messages,
    modes,
    percentile,
    summarize,
)
from tests.fakes import FakeLLM


def test_percentile_is_nearest_rank() -> None:
    values = [float(n) for n in range(1, 21)]
    assert percentile(values, 0.5) == 10.0
    assert percentile(values, 0.95) == 19.0
    assert percentile([3.0], 0.95) == 3.0
    assert percentile([], 0.5) is None


def _run(total: float, *, degraded: bool = False, calls: list[float] | None = None) -> dict:
    return {
        "total_s": total,
        "autofill_s": total,
        "degraded_llm": degraded,
        "chat_calls": len(calls or []),
        "chat_s": calls or [],
        "prompt_chars": [1000] * len(calls or []),
        "items": 4 if calls else 0,
        "accepted": 3 if calls else 0,
        "unparsed": 0,
        "filled": 2,
        "by_llm": 1 if calls else 0,
    }


def test_summary_counts_shares_and_percentiles() -> None:
    runs = [_run(1.0), _run(2.0, calls=[1.5]), _run(9.0, degraded=True, calls=[4.0]), _run(3.0)]
    got = summarize(runs)
    assert got["products"] == 4
    assert got["total_p50_s"] == 2.0
    assert got["total_max_s"] == 9.0
    assert got["asked_model"] == 2
    assert got["degraded_share"] == 0.25
    assert got["rejected_share"] == pytest.approx(0.25)  # 6 принято из 8
    assert got["chat_p95_s"] == 4.0


def test_plan_has_off_both_prompts_and_every_budget() -> None:
    plan = modes([4.0, 10.0])
    assert [m.label for m in plan] == ["off", "legacy", "new", "budget-4", "budget-10"]
    assert plan[0] == Mode("off", False, None, "new")
    assert plan[1].prompt == "legacy" and plan[2].prompt == "new"
    assert [m.budget_s for m in plan[3:]] == [4.0, 10.0]


def test_reply_log_counts_what_the_model_returned_and_what_code_took() -> None:
    spec = CharacteristicSpec(name="Цвет", kind="qualitative", values=["Белый", "Серый"])
    reply = LLMItems(
        items=[
            LLMItem(name="Цвет", value="Серый", unit=None, quote="цвет серый"),  # верный
            LLMItem(name="Цвет", value="Белый", unit=None, quote="Бумага"),  # цвета в цитате нет
        ]
    )
    counters = Counters()
    logger = logging.getLogger("app.extract.llm_fill")
    handler = _ReplyLog(counters)
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.INFO)
    try:
        asyncio.run(
            extract_llm("Бумага", "цвет серый", [spec], FakeLLM([reply]), Glossary([]))  # type: ignore[arg-type]
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
    assert (counters.items, counters.accepted) == (2, 1)


def test_legacy_prompt_is_the_one_before_the_rules() -> None:
    spec = CharacteristicSpec(name="Цвет", kind="qualitative", values=["Белый"])
    system, user = legacy_messages("Бумага", "", [spec], Glossary([]))
    assert "Чего нет в тексте — не придумывай" in system["content"]
    assert "перечень: Белый" in user["content"]
