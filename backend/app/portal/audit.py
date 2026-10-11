"""Аудит живых карточек портала правилами модерации модуля: сколько карточек вернули бы на
исправление и за что, с примерами-ссылками.

Без позиции КТРУ (в карточках портала её нет) работают правила самой карточки и сверка
характеристик с наименованием: число из наименования против числа в характеристике. Противоречием
считается только типичная ошибка ввода (`input_slip`): единица перепутана (разница ровно в 10, 100
или 1000 раз), опечатка в числе (до 20 %), отрицательная величина. Иначе это, скорее всего, другая
величина с той же единицей («Ведро 15 л» и «Объем вкладыша 22 л»). Штуки и листы в сверку не идут:
«10+2 шт» и «3 шт серебро и 3 шт золото» — состав, а не противоречие.
"""

from collections import Counter, defaultdict

import regex as re
from pydantic import BaseModel

from app.checks.detector import RULE_NAME, detect
from app.ingest.characteristics import to_number
from app.ingest.units import normalize_unit
from app.portal.cards import PortalCard
from app.portal.fill import CharOutcome, compare_card
from app.portal.values import ValueIndex
from app.schemas import Fix
from app.values import COUNT_UNITS, input_slip

MAX_EXAMPLES = 8


class AuditExample(BaseModel):
    id: int
    url: str
    name: str
    field: str
    message: str
    fix: str | None = None


class AuditRule(BaseModel):
    rule: str
    cards: int
    share: float
    examples: list[AuditExample]


class PortalAudit(BaseModel):
    cards: int
    with_issues: int
    share: float
    rules: list[AuditRule]


def _fix_text(fix: Fix | None) -> str | None:
    if fix is None:
        return None
    parts = [p for p in (fix.name, fix.value, fix.unit) if p]
    return " · ".join(parts) or None


_UNIT_AFTER = re.compile(r"\s*[^\s\d,;()]+")


def _with_unit(name: str, o: CharOutcome) -> str:
    """Число из наименования вместе с единицей за ним: «500 мл», а не «500»."""
    start = o.source_start or 0
    end = start + len(o.source_text or "")
    m = _UNIT_AFTER.match(name, end)
    return name[start : m.end() if m else end].rstrip(".")


def _contradictions(card: PortalCard, outcomes: list[CharOutcome]) -> list[AuditExample]:
    out = []
    for o in outcomes:
        counted = normalize_unit(o.unit or "") in COUNT_UNITS  # «10+2 шт» — состав, а не спор
        if (
            o.source_field == "name"
            and o.numeric
            and not counted
            and o.extracted is not None
            and not o.correct
            and (x := to_number(o.extracted)) is not None
            and (y := to_number(o.truth)) is not None
            and input_slip(x, y)
        ):
            unit = f" {o.unit}" if o.unit else ""
            shown = _with_unit(card.name, o) if o.source_start is not None else o.source_text
            out.append(
                AuditExample(
                    id=card.id,
                    url=card.url,
                    name=card.name,
                    field=f"characteristics.{o.name}",
                    message=f"В наименовании «{shown}», в характеристике «{o.name}»: "
                    f"{o.truth}{unit}.",
                    fix=o.extracted,
                )
            )
    return out


def audit(
    cards: list[PortalCard],
    index: ValueIndex | None = None,
    outcomes: dict[int, list[CharOutcome]] | None = None,
) -> PortalAudit:
    index = index or ValueIndex(cards)
    hit_cards: Counter[str] = Counter()
    examples: defaultdict[str, list[AuditExample]] = defaultdict(list)
    with_issues = 0
    for card in cards:
        found: dict[str, AuditExample] = {}
        card_in, rejected = card.checked()
        for issue in [*rejected, *detect(card_in, None)]:
            if issue.severity == "info" or issue.rule in found:
                continue
            found[issue.rule] = AuditExample(
                id=card.id,
                url=card.url,
                name=card.name,
                field=issue.field,
                message=issue.message,
                fix=_fix_text(issue.fix),
            )
        card_outcomes = (outcomes or {}).get(card.id)
        if card_outcomes is None:
            card_outcomes = compare_card(card, index)
        contradictions = _contradictions(card, card_outcomes)
        if contradictions:
            found[RULE_NAME] = contradictions[0]
        if found:
            with_issues += 1
        for rule, example in found.items():
            hit_cards[rule] += 1
            if len(examples[rule]) < MAX_EXAMPLES:
                examples[rule].append(example)
    total = len(cards)
    rules = [
        AuditRule(rule=rule, cards=n, share=n / total, examples=examples[rule])
        for rule, n in sorted(hit_cards.items(), key=lambda rn: (-rn[1], rn[0]))
    ]
    return PortalAudit(
        cards=total, with_issues=with_issues, share=with_issues / total if total else 0.0,
        rules=rules,
    )  # fmt: skip
