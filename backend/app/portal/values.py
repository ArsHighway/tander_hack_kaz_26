"""Наблюдаемые значения характеристик по категориям портала и спецификации для одной карточки.

Это не справочный перечень: значения ввели поставщики, с их опечатками. Перечнем служат значения,
которые повторяются у нескольких карточек категории. Для оценки перечень карточки строится без
неё самой (leave-one-out): иначе её собственное значение попадает в перечень и заполнение
выглядит лучше, чем есть.
"""

from collections import Counter, defaultdict
from collections.abc import Callable, Mapping

import regex as re

from app.catalog import CharacteristicSpec
from app.portal.cards import PortalCard
from app.values import fold, nearest_allowed

Key = tuple[str, str]
_GLOSS = re.compile(r"\s*\([^)]*\)")
_DIGITS = re.compile(r"[0-9]+")


def strip_gloss(value: str) -> str:
    """Значение без пояснения в скобках: «желтый (yellow)» → «желтый», «Совместимый(ая)»."""
    return _GLOSS.sub("", value).strip()


def value_key(value: str) -> str:
    """Ключ склейки вариантов написания: без пояснения в скобках и хвостовой пунктуации."""
    return fold(strip_gloss(value).strip(" .;,")) or fold(value)


def canonical_map(counts: Mapping[str, int], spelling: Callable[[str], str]) -> dict[str, str]:
    """Ключ варианта (`value_key`) → значение, к которому он приклеен.

    Варианты идут по убыванию частоты; опечатка присоединяется к более частому значению
    («Оригианльный» → «Оригинальный»); значение — в самом частом написании (`spelling`) без
    хвостовой точки и точки с запятой («Экокожа.»).
    """
    ranked = []
    for vkey, n in counts.items():
        spelled = spelling(vkey)
        ranked.append((spelled.rstrip(" .;,") or spelled, vkey, n))
    ranked.sort(key=lambda t: (-t[2], t[0]))
    canon: dict[str, str] = {}
    kept: list[str] = []
    for spelled, vkey, _ in ranked:
        # Опечатка — только в буквах: «CR2032» и «CR2025», «Марка 2» и «Марка 3» — разные.
        digits = _DIGITS.findall(spelled)
        near = nearest_allowed(spelled, [k for k in kept if _DIGITS.findall(k) == digits])
        if near is None:
            kept.append(spelled)
        canon[vkey] = near[0] if near is not None else spelled
    return canon


class ValueIndex:
    def __init__(self, cards: list[PortalCard]) -> None:
        self.cards_in: Counter[str] = Counter()
        self.present: Counter[Key] = Counter()
        self._values: defaultdict[Key, Counter[str]] = defaultdict(Counter)
        self._spelling: defaultdict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
        self._units: defaultdict[Key, Counter[str]] = defaultdict(Counter)
        self._numeric: Counter[Key] = Counter()
        self._names: defaultdict[Key, Counter[str]] = defaultdict(Counter)
        self._clusters: dict[Key, dict[str, str]] = {}
        for card in cards:
            path = card.category_path
            self.cards_in[path] += 1
            seen: set[Key] = set()
            for c in card.characteristics:
                key = (path, fold(c.name))
                if key in seen:
                    continue
                seen.add(key)
                self.present[key] += 1
                self._names[key][c.name] += 1
                self._values[key][value_key(c.value)] += 1
                self._spelling[(*key, value_key(c.value))][c.value] += 1
                if c.unit:
                    self._units[key][c.unit] += 1
                if c.numeric:
                    self._numeric[key] += 1

    def values(
        self, path: str, name: str, exclude: PortalCard | None = None, min_cards: int = 1
    ) -> list[str]:
        """Значения характеристики в категории по убыванию числа карточек.

        Варианты написания склеены (`value_key`), опечатка присоединяется к более частому значению
        («Оригианльный» → «Оригинальный»); значение — в самом частом написании.
        """
        key = (path, fold(name))
        canon = self._canonical(key)
        merged: Counter[str] = Counter()
        for vkey, n in self._values.get(key, {}).items():
            merged[canon[vkey]] += n
        if exclude is not None:
            own = next((c for c in exclude.characteristics if fold(c.name) == key[1]), None)
            target = canon.get(value_key(own.value)) if own is not None else None
            if target is not None and merged[target] > 0:
                merged[target] -= 1
        kept = [(v, n) for v, n in merged.items() if n >= max(min_cards, 1)]
        return [v for v, _ in sorted(kept, key=lambda vn: (-vn[1], vn[0]))]

    def _canonical(self, key: Key) -> dict[str, str]:
        """Ключ варианта → значение, к которому он приклеен; считается раз на характеристику."""
        if key not in self._clusters:
            counts: Mapping[str, int] = self._values.get(key, {})
            self._clusters[key] = canonical_map(
                counts, lambda v: self._spelling[(*key, v)].most_common(1)[0][0]
            )
        return self._clusters[key]

    def names(self, path: str) -> list[str]:
        """Характеристики категории по убыванию числа карточек, где они заполнены."""
        keys = [k for k in self.present if k[0] == path]
        keys.sort(key=lambda k: (-self.present[k], k[1]))
        return [self._names[k].most_common(1)[0][0] for k in keys]

    def share(self, path: str, name: str) -> float:
        total = self.cards_in[path]
        return self.present[(path, fold(name))] / total if total else 0.0

    def unit(self, path: str, name: str) -> str | None:
        units = self._units.get((path, fold(name)))
        return units.most_common(1)[0][0] if units else None

    def numeric(self, path: str, name: str) -> bool:
        key = (path, fold(name))
        return self.present[key] > 0 and self._numeric[key] * 2 > self.present[key]


def card_specs(card: PortalCard, index: ValueIndex) -> list[CharacteristicSpec]:
    """Спецификации по характеристикам самой карточки; перечень — из других карточек категории."""
    specs = []
    for c in card.characteristics:
        if c.boolean:
            continue  # «да/нет» в тексте поставщика почти не встречается
        if c.numeric:
            specs.append(CharacteristicSpec(name=c.name, kind="quantitative", unit=c.unit))
        else:
            values = index.values(card.category_path, c.name, exclude=card)
            specs.append(
                CharacteristicSpec(name=c.name, kind="qualitative", unit=c.unit, values=values)
            )
    return specs
