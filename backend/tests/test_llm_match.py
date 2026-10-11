"""Подгон значения поставщика к перечню КТРУ моделью: только предложение, и только проверенное."""

import asyncio
import time
from types import SimpleNamespace
from typing import Any

from app.catalog import CharacteristicSpec
from app.extract.llm_fill import MATCH_SYSTEM, match_to_list, suggest_list_value
from app.extract.model import ListMatch, ListSuggestion
from app.glossary import Glossary
from app.llm import LLMError, LLMInputError, LLMOutputError
from tests.fakes import FakeLLM

MATERIAL = CharacteristicSpec(
    name="Материал изделия",
    kind="qualitative",
    values=["Дерево", "Металл", "Пластик", "Нержавеющая сталь", "Комбинированный"],
)


def _match(
    description: str, value: str, quote: str, replies: list[Any], *, name: str = "Тумбочка"
) -> tuple[ListSuggestion | None, FakeLLM]:
    llm = FakeLLM(replies)
    got = asyncio.run(
        match_to_list(name, description, MATERIAL, value, quote, llm, Glossary([]))  # type: ignore[arg-type]
    )
    return got, llm


def test_clay_matches_nothing_in_the_list() -> None:
    got, llm = _match("Материал: глина", "глина", "глина", [ListMatch(value=None, quote="")])
    assert got is None
    assert len(llm.calls) == 1


def test_abbreviated_steel_is_suggested_as_the_list_value() -> None:
    got, _ = _match(
        "Материал: нерж. сталь",
        "нерж. сталь",
        "нерж. сталь",
        [ListMatch(value="Нержавеющая сталь", quote="Материал: нерж. сталь")],
    )
    assert got is not None
    assert got.value == "Нержавеющая сталь"  # ровно как в справочнике
    assert got.source.text == "Материал: нерж. сталь"
    assert got.source.field == "description"


def test_value_outside_the_list_is_rejected() -> None:
    got, _ = _match(
        "Материал: глина",
        "глина",
        "глина",
        [ListMatch(value="Керамика", quote="Материал: глина")],
    )
    assert got is None


def test_list_value_in_other_spelling_is_rejected() -> None:
    # «ровно из перечня»: «нержавеющая сталь» строчными — не значение справочника.
    got, _ = _match(
        "Материал: нерж. сталь",
        "нерж. сталь",
        "нерж. сталь",
        [ListMatch(value="нержавеющая сталь", quote="нерж. сталь")],
    )
    assert got is None


def test_quote_that_is_not_in_the_text_is_rejected() -> None:
    got, _ = _match(
        "Материал: нерж. сталь",
        "нерж. сталь",
        "нерж. сталь",
        [ListMatch(value="Нержавеющая сталь", quote="корпус из нержавеющей стали")],
    )
    assert got is None


def test_quote_without_the_suppliers_word_is_rejected() -> None:
    # Цитата настоящая, но слова поставщика («глина») в ней нет: не на чём держаться.
    got, _ = _match(
        "Материал: глина. Фурнитура: металл",
        "глина",
        "глина",
        [ListMatch(value="Металл", quote="Фурнитура: металл")],
    )
    assert got is None


def test_bad_structured_answer_twice_gives_nothing() -> None:
    bad = LLMOutputError("не JSON", raw="...")
    got, llm = _match("Материал: глина", "глина", "глина", [bad, bad])
    assert got is None
    assert len(llm.calls) == 2


def test_nothing_is_asked_without_a_list_or_a_value() -> None:
    llm = FakeLLM()
    free = CharacteristicSpec(name="Артикул", kind="qualitative")
    assert (
        asyncio.run(match_to_list("Т", "", free, "A1", "A1", llm, Glossary([])))  # type: ignore[arg-type]
        is None
    )
    assert (
        asyncio.run(match_to_list("Т", "", MATERIAL, "  ", "", llm, Glossary([])))  # type: ignore[arg-type]
        is None
    )
    assert llm.calls == []


def test_prompt_asks_for_the_same_meaning_or_none() -> None:
    _, llm = _match(
        "Материал: глина", "глина", "Материал: глина", [ListMatch(value=None, quote="")]
    )
    system, user = llm.calls[0]
    assert system["content"] == MATCH_SYSTEM
    assert "то же самое" in MATCH_SYSTEM and "ни одно" in MATCH_SYSTEM
    assert "Допустимые значения: Дерево; Металл" in user["content"]
    assert "Значение поставщика: глина" in user["content"]


# --- политика шага: выключатель модератора, пауза и бюджет ---


def _deps(llm: FakeLLM, *, enabled: bool = True, budget: float | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        llm=llm,
        glossary=Glossary([]),
        llm_enabled=enabled,
        llm_budget_s=budget,
        llm_paused_until=0.0,
    )


def _suggest(deps: SimpleNamespace) -> ListSuggestion | None:
    return asyncio.run(
        suggest_list_value(deps, "Тумбочка", "Материал: нерж. сталь", MATERIAL, "нерж. сталь", "")
    )


def test_switched_off_model_is_not_asked() -> None:
    llm = FakeLLM()  # любой вызов — AssertionError
    assert _suggest(_deps(llm, enabled=False)) is None
    assert llm.calls == []


def test_paused_model_is_not_asked() -> None:
    llm = FakeLLM()
    deps = _deps(llm)
    deps.llm_paused_until = time.monotonic() + 60
    assert _suggest(deps) is None
    assert llm.calls == []


def test_enabled_model_gives_a_suggestion() -> None:
    reply = ListMatch(value="Нержавеющая сталь", quote="Материал: нерж. сталь")
    llm = FakeLLM([reply])
    got = _suggest(_deps(llm))
    assert got is not None and got.value == "Нержавеющая сталь"
    assert len(llm.calls) == 1


def test_service_failure_pauses_the_model_but_bad_input_does_not() -> None:
    down = _deps(FakeLLM([LLMError("429 usage limit")]))
    assert _suggest(down) is None
    assert down.llm_paused_until > time.monotonic()

    bad = _deps(FakeLLM([LLMInputError("контекст переполнен")]))
    assert _suggest(bad) is None
    assert bad.llm_paused_until == 0.0


def test_slow_model_is_cut_by_the_budget_and_not_paused() -> None:
    class Slow(FakeLLM):
        async def chat(self, messages: Any, **kwargs: Any) -> Any:
            await asyncio.sleep(5)

    deps = _deps(Slow(), budget=0.05)
    assert _suggest(deps) is None
    assert deps.llm_paused_until == 0.0


# --- в конвейере: слово вне перечня приводится к перечню, исходное — в пометке ---

POWER = CharacteristicSpec(
    name="Тип питания", kind="qualitative", values=["От сети", "От аккумуляторных батарей"]
)


def _rule(value: str, quote: str) -> Any:
    from app.extract.model import Extracted
    from app.schemas import Span

    at = TEXT.index(quote)
    return Extracted(
        name="Тип питания",
        raw_value=value,
        source=Span(field="description", start=at, end=at + len(quote), text=quote),
        method="rule",
    )


TEXT = "Тип питания: встроенный аккумулятор."


def _match_lists(reply: Any) -> list[Any]:
    from app.catalog import Position
    from app.pipeline import _match_lists

    position = Position(
        code="T-1", name="Шуруповерт", okpd2="25.73", characteristics=[POWER], source="test"
    )
    session = SimpleNamespace(name="Шуруповерт", description=TEXT)
    deps = _deps(FakeLLM([reply]))
    extracted = [_rule("встроенный аккумулятор", "встроенный аккумулятор")]
    return asyncio.run(_match_lists(session, extracted, position, deps))  # type: ignore[arg-type]


def test_word_outside_the_list_becomes_the_list_value_with_a_note() -> None:
    reply = ListMatch(value="От аккумуляторных батарей", quote="встроенный аккумулятор")
    [got] = _match_lists(reply)
    assert got.raw_value == "От аккумуляторных батарей"
    assert got.method == "llm"
    assert got.was == "встроенный аккумулятор"
    assert "встроенный аккумулятор" in (got.note or "")
    assert got.source.text == "встроенный аккумулятор"  # откуда взято — фрагмент текста


def test_no_match_keeps_the_supplier_value() -> None:
    [got] = _match_lists(ListMatch(value=None, quote=""))
    assert got.raw_value == "встроенный аккумулятор"
    assert got.method == "rule" and got.was is None


def test_number_outside_a_numeric_list_is_not_sent_to_the_model() -> None:
    """Число к перечню приводит код, а не модель: «1010 г» при перечне «0.3, 0.4» она «приводила»
    к 0.3 кг."""
    from app.catalog import CharacteristicSpec, Position
    from app.extract.model import Extracted
    from app.pipeline import _match_lists
    from app.schemas import Span

    weight = CharacteristicSpec(name="Вес", kind="quantitative", unit="кг", values=["0.3", "0.4"])
    position = Position(
        code="T-2", name="Отвертка", okpd2="25.73", characteristics=[weight], source="test"
    )
    text = "Вес: 1010.00000 г"
    rule = Extracted(
        name="Вес",
        raw_value="1010.00000",
        raw_unit="г",
        source=Span(field="description", start=5, end=15, text="1010.00000"),
        method="rule",
    )
    llm = FakeLLM()  # любой вызов модели — ошибка теста
    session = SimpleNamespace(name="Отвертка", description=text)
    got = asyncio.run(_match_lists(session, [rule], position, _deps(llm)))  # type: ignore[arg-type]
    assert got == [rule] and llm.calls == []
