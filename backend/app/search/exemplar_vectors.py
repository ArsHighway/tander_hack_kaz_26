"""Векторы наименований похожих СТЕ: второй голос категорий к BM25 по тем же наименованиям.

BM25 голосует по совпавшим словам и промахивается, когда поставщик называет товар другими словами
(«тайтсы» — «лосины»). Векторы bge-m3 тех же наименований ловят смысл. Голос — сумма
(оценка/лучшая)^4 соседей BM25 и (сходство/лучшее)^32 соседей векторов с весом `WEIGHT`. На
отложенной выборке портала (2 000 СТЕ, вес 1, без синтетики) верная категория среди пяти — 81,3 %
против 78,6 % у одного BM25, уверенных ошибок вдвое меньше (3,8 % против 6,1 %); хватает 10
наименований на категорию (`build_exemplar_vectors`), полный набор даёт то же. Степень 32 нужна,
потому что косинусы соседей близки (0,80–0,85): без неё голосует масса большой категории.
"""

from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

import numpy as np

NEIGHBORS = 30
POWER = 32
BM25_POWER = 4
# Вес голоса векторов против BM25 (у BM25 — 1). Отложенная выборка (2 000 СТЕ,
# похожие с синтетикой; без векторов — первой 75,0 %, среди пяти 87,1 %, уверенных ошибок 3,6 %, код
# без вопроса у 50,9 %): вес 1 — 74,5 % / 87,9 % / 2,3 % / 41,3 %, крупные категории теряют
# 1,5 п.п.; вес 0,5 — 75,0 % / 87,7 % / 2,4 % / 43,8 %; вес 0,3 — 75,1 % / 87,6 % / 2,7 % /
# 46,2 %. Взят 0,3: порог «без вопроса» 70 %, а с весом 0,5 доля голоса у
# демо-смесителя падает до 69,9 %.
WEIGHT = 0.3


class ExemplarVectors:
    def __init__(self, codes: Sequence[str], matrix: np.ndarray) -> None:
        if len(codes) != matrix.shape[0]:
            raise ValueError(f"векторов {matrix.shape[0]} на {len(codes)} кодов")
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        # Нулевой вектор (модель вернула NaN при сборке) ни на что не похож и не голосует.
        self.matrix = (matrix / np.maximum(norms, 1e-12)).astype(np.float32)
        self.codes = list(codes)

    @classmethod
    def load(cls, path: Path) -> "ExemplarVectors":
        with np.load(path, allow_pickle=False) as data:
            return cls([str(c) for c in data["codes"]], data["vectors"])

    def save(self, path: Path) -> None:
        np.savez(path, codes=np.array(self.codes), vectors=self.matrix)

    def __len__(self) -> int:
        return len(self.codes)

    def hits(self, query: Sequence[float], k: int = NEIGHBORS) -> list[tuple[str, float]]:
        """Ближайшие наименования: (код категории, косинус) по убыванию."""
        q = np.asarray(query, dtype=np.float32)
        norm = float(np.linalg.norm(q))
        if not len(self.codes) or norm == 0.0 or not np.isfinite(norm):
            return []
        sims = self.matrix @ (q / norm)
        k = min(k, len(sims))
        top = np.argpartition(-sims, k - 1)[:k]
        top = top[np.argsort(-sims[top])]
        return [(self.codes[i], float(sims[i])) for i in top if sims[i] > 0]


def merge_votes(
    bm25: Sequence[tuple[str, float]], vectors: Sequence[tuple[str, float]]
) -> list[tuple[str, float]]:
    """Доли голоса категорий по соседям обоих поисков: (код, доля) по убыванию, сумма долей — 1.

    `bm25` — (код соседа, оценка BM25), `vectors` — (код соседа, косинус); вес соседа —
    (оценка / лучшая) в своей степени, у каждого поиска лучший сосед весит 1.
    """
    votes: defaultdict[str, float] = defaultdict(float)
    for hits, power, weight in ((bm25, BM25_POWER, 1.0), (vectors, POWER, WEIGHT)):
        if not hits or hits[0][1] <= 0:
            continue
        best = hits[0][1]
        for code, score in hits:
            votes[code] += weight * (score / best) ** power
    total = sum(votes.values())
    if not total:
        return []
    return sorted(((c, v / total) for c, v in votes.items()), key=lambda cv: -cv[1])
