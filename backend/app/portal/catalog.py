"""Справочник из живых карточек портала: позиция — категория каталога (КПГЗ), характеристики —
те, что поставщики заполняют, перечни — значения, повторяющиеся у нескольких карточек.

Это не КТРУ: перечни грязные (значения вводили поставщики), поэтому значение идёт в перечень,
только если встретилось хотя бы у `min_value_cards` карточек. Годится, чтобы прогнать модуль на
реальных именах характеристик до выдачи справочника организаторами (`CATALOG_PATHS`).
"""

from collections import Counter, defaultdict

from app.catalog import CharacteristicSpec, Position
from app.portal.cards import PortalCard
from app.portal.values import ValueIndex


def position_code(path: str) -> str:
    return f"КПГЗ {path.strip('.').split('.')[-1]}"


def build_positions(
    cards: list[PortalCard],
    index: ValueIndex,
    *,
    min_cards: int = 20,
    min_share: float = 0.3,
    min_value_cards: int = 2,
    required_share: float = 0.8,
) -> list[Position]:
    by_path: defaultdict[str, list[PortalCard]] = defaultdict(list)
    for card in cards:
        if card.category_path:
            by_path[card.category_path].append(card)
    positions = []
    for path, group in sorted(by_path.items()):
        if len(group) < min_cards:
            continue
        specs = []
        for name in index.names(path):
            share = index.share(path, name)
            if share < min_share:
                continue
            if index.numeric(path, name):
                spec = CharacteristicSpec(
                    name=name, kind="quantitative", unit=index.unit(path, name)
                )
            else:
                values = index.values(path, name, min_cards=min_value_cards)
                spec = CharacteristicSpec(name=name, kind="qualitative", values=values)
            spec.required = share >= required_share
            specs.append(spec)
        okpd = Counter(c.okpd for c in group if c.okpd).most_common(1)
        positions.append(
            Position(
                code=position_code(path),
                name=Counter(c.category for c in group).most_common(1)[0][0],
                okpd2=okpd[0][0] if okpd else "",
                characteristics=specs,
                source="portal",
            )
        )
    return positions
