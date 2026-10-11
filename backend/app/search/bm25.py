"""Okapi BM25 по позициям справочника и нормированная оценка для отсечки «вне справочника»."""

import math
from collections import Counter, defaultdict
from functools import lru_cache

from app.search.text import tokens
from app.values import damerau_levenshtein

# Вес незнакомого слова из букв в делителе нормировки (калибруется в app.tools.evaluate).
UNKNOWN_WEIGHT = 0.5
# Исправление опечатки: только слова из букв от этой длины, с той же первой буквой, расстояние 1
# (от LONG_WORD — 2). 4 — стем «бумга» («бумг») короче исходного слова, но это всё ещё опечатка.
MIN_TYPO_TOKEN = 4
LONG_WORD = 8


class BM25Index:
    def __init__(self, docs: dict[str, str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self._tf = {code: Counter(tokens(text)) for code, text in docs.items()}
        self._len = {code: sum(tf.values()) for code, tf in self._tf.items()}
        self._avg = (sum(self._len.values()) / len(self._len)) if self._len else 0.0
        df: Counter[str] = Counter()
        for tf in self._tf.values():
            df.update(tf.keys())
        n = len(self._tf)
        self._idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
        # Слово, которого нет ни в одном документе, — самое редкое: так оно тянет оценку вниз.
        self._idf_unknown = math.log(1 + (n + 0.5) / 0.5)
        self._by_len: defaultdict[int, list[str]] = defaultdict(list)
        for t in sorted(self._idf):
            self._by_len[len(t)].append(t)
        self.correct = lru_cache(maxsize=65536)(self._correct)
        # Вклад слова в документ считается при сборке, поиск только складывает вклады документов,
        # где слово есть: не 8,5 тыс. оценок на запрос, а сотни (было 30 мс на поиск).
        self._postings: defaultdict[str, list[tuple[str, float]]] = defaultdict(list)
        for code, tf in self._tf.items():
            for t, f in tf.items():
                self._postings[t].append((code, self._weight(t, f, code)))

    def _correct(self, token: str) -> str:
        """Незнакомое слово → единственное ближайшее слово словаря («бумааг» → «бумаг»)."""
        if token in self._idf or len(token) < MIN_TYPO_TOKEN or not token.isalpha():
            return token
        limit = 2 if len(token) >= LONG_WORD else 1
        best: list[str] = []
        best_d = limit + 1
        for size in range(len(token) - limit, len(token) + limit + 1):
            for t in self._by_len.get(size, []):
                if t[0] != token[0]:
                    continue  # первую букву опечатка почти не трогает: «колес» — не «солев»
                d = damerau_levenshtein(token, t)
                if d < best_d:
                    best, best_d = [t], d
                elif d == best_d:
                    best.append(t)
        return best[0] if len(best) == 1 else token  # два равных кандидата — не угадываем

    def knows(self, token: str) -> bool:
        """Слово есть в словаре справочника."""
        return token in self._idf

    def together(self, stems: list[str]) -> bool:
        """Все слова встречаются вместе хотя бы в одной позиции справочника."""
        codes: set[str] | None = None
        for stem in stems:
            found = {code for code, _ in self._postings.get(stem, ())}
            codes = found if codes is None else codes & found
            if not codes:
                return False
        return bool(codes)

    def query_tokens(self, text: str) -> list[str]:
        return list(dict.fromkeys(self.correct(t) for t in tokens(text)))

    def _weight(self, t: str, f: int, code: str) -> float:
        norm = self.k1 * (1 - self.b + self.b * self._len[code] / (self._avg or 1))
        return self._idf[t] * f * (self.k1 + 1) / (f + norm)

    def search(self, query: str, k: int = 20) -> list[tuple[str, float]]:
        scores: dict[str, float] = {}
        for t in self.query_tokens(query):
            for code, w in self._postings.get(t, ()):
                scores[code] = scores.get(code, 0.0) + w
        hits = sorted((h for h in scores.items() if h[1] > 0), key=lambda h: (-h[1], h[0]))
        return hits[:k]

    def known_overlap(self, query: str, code: str) -> int:
        """Сколько известных слов запроса есть в документе."""
        tf = self._tf.get(code) or Counter()
        return sum(1 for t in self.query_tokens(query) if t in self._idf and tf.get(t))

    def max_score(self, query: str, *, matched_known: int = 0) -> float:
        """Оценка документа, совпавшего со всеми словами запроса: делитель для нормировки.

        Незнакомое слово тянет оценку вниз (товар может быть вне справочника), но не в полную силу:
        бренды и модели («Туринский ЦБЗ») в справочнике не встречаются никогда. Незнакомые токены с
        цифрами (артикулы, «500л») не считаются совсем. Если в лучшем документе совпали хотя бы два
        известных слова, чужое слово не может обнулить запрос: «бумага … принтера» остаётся бумагой.
        """
        q = self.query_tokens(query)
        known = 0.0
        unknown = 0.0
        for t in q:
            if t in self._idf:
                known += self._idf[t]
            elif not any(ch.isdigit() for ch in t):
                unknown += UNKNOWN_WEIGHT * self._idf_unknown
        if matched_known >= 2 and known > 0:
            unknown = min(unknown, known)
        return (known + unknown) * (self.k1 + 1)

    def doc_tokens(self, code: str) -> set[str]:
        return set(self._tf.get(code, {}))

    def matched(self, query: str, code: str) -> set[str]:
        return {t for t in self.query_tokens(query) if t in self._tf.get(code, {})}
