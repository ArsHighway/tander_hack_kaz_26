"""Категория по похожим СТЕ: инвертированный индекс наименований живых СТЕ и голосование соседей.

Текст позиции справочника — название категории и её характеристики — плохо похож на то, как
поставщик называет товар («Смеситель РМС SUS124-001F-1 однорычажный»). Похожие наименования
уже размеченных СТЕ похожи: ближайшие соседи голосуют за свои категории. Данные — выгрузка
`build_exemplars` (наименование, код позиции и id СТЕ, без отложенной выборки).
"""

import heapq
import json
import math
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path

from app.search.text import tokens

# Слово чаще этой доли документов голоса почти не несёт, а перебор его списка дорог.
MAX_DF_SHARE = 0.05
MIN_MAX_DF = 1000
# Соседей и степень веса подобраны на отложенной выборке (2000 СТЕ, `docs/plans/
# 2026-10-06-exemplar-search.md`): сумма (оценка/лучшая)^4 по 30 соседям — верная категория
# первой в 74 % против 66 % у простой суммы; степень гасит «массу» больших категорий.
NEIGHBORS = 30
POWER = 4


class ExemplarIndex:
    def __init__(
        self,
        items: list[tuple[str, str]],
        ids: Sequence[int | None] | None = None,
        k1: float = 1.2,
        b: float = 0.75,
    ) -> None:
        # id СТЕ портала — для подсказки «похожие» у варианта категории (`examples`);
        # нет id — нет примеров.
        self.ids: list[int | None] = list(ids) if ids is not None else [None] * len(items)
        if len(self.ids) != len(items):
            raise ValueError(f"ids: {len(self.ids)} на {len(items)} наименований")
        self.codes: list[str] = []
        self.names: list[str] = []
        postings: defaultdict[str, list[tuple[int, int]]] = defaultdict(list)
        lengths: list[int] = []
        for i, (code, name) in enumerate(items):
            tf = Counter(tokens(name))
            self.codes.append(code)
            self.names.append(name)
            lengths.append(sum(tf.values()))
            for t, f in tf.items():
                postings[t].append((i, f))
        n = len(items)
        avg = sum(lengths) / n if n else 1.0
        self._postings: dict[str, list[tuple[int, float]]] = {}
        self._idf: dict[str, float] = {}
        for t, plist in postings.items():
            idf = math.log(1 + (n - len(plist) + 0.5) / (len(plist) + 0.5))
            self._idf[t] = idf
            # Вклад слова в документ считается один раз при сборке: запрос только складывает.
            self._postings[t] = [
                (i, idf * f * (k1 + 1) / (f + k1 * (1 - b + b * lengths[i] / avg)))
                for i, f in plist
            ]
        self._max_df = max(MIN_MAX_DF, int(MAX_DF_SHARE * n))

    @classmethod
    def load(cls, path: Path) -> "ExemplarIndex":
        with path.open(encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        # Старая выгрузка без `id` читается: голос тот же, примеров у вариантов не будет.
        return cls([(r["code"], r["name"]) for r in rows], ids=[r.get("id") for r in rows])

    def __len__(self) -> int:
        return len(self.codes)

    def df(self, token: str) -> int:
        """В скольких наименованиях СТЕ есть слово (стем `tokens`)."""
        return len(self._postings.get(token, ()))

    def _query(self, query: str) -> list[str]:
        return [t for t in dict.fromkeys(tokens(query)) if t in self._postings]

    def neighbors(self, query: str, k: int = NEIGHBORS) -> list[tuple[int, float]]:
        """Ближайшие СТЕ по BM25: (номер, оценка), по убыванию."""
        scores: defaultdict[int, float] = defaultdict(float)
        for t in self._query(query):
            plist = self._postings[t]
            if len(plist) > self._max_df:
                continue
            for i, w in plist:
                scores[i] += w
        return heapq.nlargest(k, scores.items(), key=lambda kv: kv[1])

    def coverage(self, query: str, best: int) -> float:
        """Доля веса слов запроса (idf), которые есть в наименовании соседа `best`: 1 — все слова
        запроса нашлись у одного товара. Незнакомые слова тоже в знаменателе: «Бетономешалка
        Зубр» при соседе «Зубр отвертка» — 0,5, не уверенность."""
        q = list(dict.fromkeys(tokens(query)))
        if not q:
            return 0.0
        own = set(tokens(self.names[best]))
        unknown = max(self._idf.values(), default=1.0)
        total = sum(self._idf.get(t, unknown) for t in q)
        return sum(self._idf.get(t, unknown) for t in q if t in own) / total

    def examples(
        self, hits: list[tuple[int, float]], code: str, k: int = 2
    ) -> list[tuple[int, str]]:
        """До `k` соседей категории `code` из уже найденных `hits`: (id СТЕ, наименование), в
        порядке соседей, без повторов наименования; соседи без id не показываются."""
        out: list[tuple[int, str]] = []
        seen: set[str] = set()
        for i, _ in hits:
            sku, name = self.ids[i], self.names[i]
            if self.codes[i] != code or sku is None or name in seen:
                continue
            seen.add(name)
            out.append((sku, name))
            if len(out) == k:
                break
        return out

    def counts(self, hits: list[tuple[int, float]]) -> dict[str, int]:
        """Сколько из найденных соседей в каждой категории: «14 из 30 похожих»."""
        return dict(Counter(self.codes[i] for i, _ in hits))

    def vote(
        self, query: str, k: int = NEIGHBORS, hits: list[tuple[int, float]] | None = None
    ) -> list[tuple[str, float]]:
        """Категории соседей по убыванию доли голоса; вес соседа — (оценка / лучшая) ** POWER.
        `hits` — уже найденные соседи того же запроса: второй поиск не нужен, `query` и `k` тогда
        не используются."""
        if hits is None:
            hits = self.neighbors(query, k)
        if not hits:
            return []
        best = hits[0][1]
        votes: defaultdict[str, float] = defaultdict(float)
        for i, s in hits:
            votes[self.codes[i]] += (s / best) ** POWER
        total = sum(votes.values())
        return sorted(((c, v / total) for c, v in votes.items()), key=lambda cv: -cv[1])
