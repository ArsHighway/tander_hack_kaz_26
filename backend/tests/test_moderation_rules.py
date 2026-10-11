"""Правила модерации, перенесённые из прототипа на TypeScript.

Главная проверка — сверка с эталоном: `rules_golden.json.gz` содержит вывод TS-правил на 2 549
наименованиях портала, 2 549 карточках с характеристиками, 37 карточках пакета и на входах всех
тестов прототипа (`frontend/prototype/checks/export-golden.ts`). Python обязан совпасть
на каждой записи.
"""

import gzip
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

from app.checks.moderation import (
    RULE_PROBLEM,
    RULE_TEXT,
    Card,
    Char,
    auto_fix,
    check_card,
    extract_from_title,
    levenshtein,
    moderation_verdict,
    nearest_allowed,
    plural,
    title_value_units,
    unit_in_char_name,
)

GOLDEN = Path(__file__).parent / "fixtures" / "rules_golden.json.gz"


def _clean(x: Any) -> Any:
    """Сравнение по смыслу: поле без значения (null) и отсутствующее поле — одно и то же."""
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items() if v is not None}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    return x


def _dump(x: Any) -> Any:
    if isinstance(x, list):
        return [_dump(v) for v in x]
    if isinstance(x, tuple):
        return [_dump(v) for v in x]
    if hasattr(x, "model_dump"):
        return x.model_dump(exclude_none=True)
    return x


def _card(raw: dict[str, Any]) -> Card:
    return Card.model_validate(raw)


def _call(fn: str, args: list[Any]) -> Any:
    if fn == "checkCard":
        return _dump(check_card(_card(args[0])))
    if fn == "titleValueUnits":
        return title_value_units(args[0])
    if fn == "extractFromTitle":
        title, added = extract_from_title(_card(args[0]))
        return {"title": title, "added": _dump(added)}
    if fn == "autoFix":
        card, changes = auto_fix(_card(args[0]))
        return {"card": _dump(card), "changes": _dump(changes)}
    if fn == "moderationVerdict":
        from app.checks.moderation import Finding

        v = moderation_verdict([Finding.model_validate(f) for f in args[0]])
        return {"returns": v.returns, "reasons": _dump(v.reasons)}
    if fn == "nearestAllowed":
        near = nearest_allowed(args[0], args[1])
        return None if near is None else {"value": near[0], "distance": near[1]}
    if fn == "unitInCharName":
        return unit_in_char_name(args[0])
    if fn == "levenshtein":
        return levenshtein(args[0], args[1])
    if fn == "plural":
        return plural(*args)
    raise AssertionError(f"нет обработчика для {fn}")


def _golden() -> dict[tuple[str, str], list[dict[str, Any]]]:
    with gzip.open(GOLDEN, "rt", encoding="utf-8") as fh:
        entries = json.load(fh)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in entries:
        groups[(e["source"], e["fn"])].append(e)
    return groups


GROUPS = _golden()


@pytest.mark.parametrize("key", sorted(GROUPS), ids=lambda k: f"{k[0]}-{k[1]}")
def test_golden_parity(key: tuple[str, str]) -> None:
    entries = GROUPS[key]
    mismatches = []
    for e in entries:
        got = _clean(_call(e["fn"], e["args"]))
        if got != _clean(e["result"]):
            mismatches.append((e["args"], e["result"], got))
    shown = "\n".join(
        f"вход: {json.dumps(a, ensure_ascii=False)[:300]}\n  TS: "
        f"{json.dumps(w, ensure_ascii=False)[:300]}\n"
        f"  PY: {json.dumps(g, ensure_ascii=False)[:300]}"
        for a, w, g in mismatches[:5]
    )
    assert not mismatches, f"{len(mismatches)} из {len(entries)} не совпали:\n{shown}"


def test_golden_covers_every_source() -> None:
    sources = {s for s, _ in GROUPS}
    assert sources == {"names", "skus", "batch", "tests"}
    assert sum(len(v) for v in GROUPS.values()) > 13_000


# Именованные случаи: читаются без эталона.


def test_rule_texts_are_the_portal_rules() -> None:
    assert RULE_TEXT["min-chars"] == "не меньше 4 характеристик"
    assert RULE_PROBLEM["value-unit-in-title"] == "значение с единицей в наименовании"


def test_card_with_three_chars_is_returned() -> None:
    card = Card(name="Бумага", chars=[Char(name=f"x{i}", value="1") for i in range(3)])
    verdict = moderation_verdict(check_card(card))
    assert verdict.returns
    assert verdict.reasons[0].text == "В карточке 3 характеристики, нужно не меньше 4."


def test_unit_in_characteristic_name() -> None:
    assert unit_in_char_name("Толщина, мкм") == "мкм"
    assert unit_in_char_name("Цвет, оттенок") is None


def test_year_is_not_grams() -> None:
    assert title_value_units("Календарь квартальный BRAUBERG, 2027 г., 3 блока") == []


def test_litres_and_sheets() -> None:
    _, added = extract_from_title(Card(name="Тетрадь 48 л. в клетку", chars=[]))
    assert [(c.name, c.value, c.unit) for c in added] == [("Количество листов", "48", "лист")]
    _, added = extract_from_title(Card(name="Корзина для бумаг 12 л", chars=[]))
    assert [(c.name, c.unit) for c in added] == [("Объём", "л")]


def test_auto_fix_moves_unit_out_of_name() -> None:
    card = Card(name="Бумага", chars=[Char(name="Толщина, мкм", value="104")])
    fixed, changes = auto_fix(card)
    assert fixed.chars[0].name == "Толщина"
    assert fixed.chars[0].unit == "мкм"
    assert changes[0].kind == "unit-from-name"


def test_plural_forms() -> None:
    assert [plural(n, "a", "b", "c") for n in (1, 2, 5, 11, 21, 22, 112)] == [
        "a",
        "b",
        "c",
        "c",
        "a",
        "b",
        "c",
    ]
