"""Кандидаты позиций справочника по тексту поставщика: BM25 + эмбеддинги, слияние RRF."""

import logging
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel

from app.catalog import Catalog
from app.llm import LLMError
from app.search.bm25 import BM25Index
from app.search.exemplar_vectors import ExemplarVectors, merge_votes
from app.search.exemplars import ExemplarIndex
from app.search.text import negated_stems, tokens, without_negations, words_with_stems
from app.search.vectors import EmbeddingIndexEmpty

log = logging.getLogger(__name__)

# Отсечка слабого хвоста: кандидат с оценкой меньше этой доли от лучшего не сужается.
TAIL_SHARE = 0.5
# Векторный кандидат без общих слов принимается только при уверенной близости.
MIN_COSINE = 0.6
# Совпадение с наименованием позиции считается по словам не короче (коды «A4» не в счёт).
MIN_NAME_TOKEN = 3
# Похожие СТЕ (`ExemplarIndex`): доля веса слов наименования, которую должен покрыть ближайший
# сосед; категория-кандидат — с голосом не меньше доли от лучшей; вес их списка в RRF. Порог
# покрытия выключен: на 2000 отложенных СТЕ 0,3 стоил 2,7 п. верной категории и отсекал лишь
# четверть работ и услуг — «не товар» отсекают их собственные классы в индексе (`outside`).
EXEMPLAR_MIN_COVERAGE = 0.0
EXEMPLAR_TAIL = 0.15
EXEMPLAR_WEIGHT = 2.0
# Коды классов «не товар» в индексе похожих СТЕ (`build_exemplars --outside`): «__works__».
OUTSIDE_PREFIX = "__"
# Категория с такой долей голоса похожих СТЕ («совпадение» в интерфейсе) — единственный
# кандидат, шаг выбора категории пропускается. Решение пользователя 07.10.2026 — 70 %: на
# отложенной выборке (2000 СТЕ) так решается 54 % случаев, из них 9,5 % неверно (5,1 % всех),
# против 30 % и 4,7 % при прежних 90 %. Ниже порога — выбор из нескольких.
AUTO_SHARE = 0.7
# Кандидатов из похожих СТЕ не больше: дальше человек выбирать не станет.
MAX_SIMILAR = 6


class SimilarSku(BaseModel):
    """СТЕ портала, похожая на текст поставщика: подсказка к варианту категории."""

    id: int
    name: str


class Candidate(BaseModel):
    code: str
    name: str
    score: float
    why: list[str]
    # Доля голоса категории среди похожих СТЕ (`ExemplarIndex`); нет — подобрана без них.
    similar: float | None = None
    examples: list[SimilarSku] = []
    similar_count: int | None = None
    similar_total: int | None = None
    # Почему этот кандидат, кроме слов наименования (`why`): значения из текста и ответов, которые
    # к нему подходят; его обязательные характеристики, которых в тексте нет; значения из текста,
    # которые ему противоречат. Одного процента совпадения человеку мало.
    matched: list[str] = []
    missing: list[str] = []
    conflicts: list[str] = []
    # Позиции КТРУ типа: всего и различимых (шаблон и позиция из него с одинаковыми
    # характеристиками — одна, `ktru_meta.merge_same`). Нет — не тип КТРУ или не посчитано.
    ktru_total: int | None = None
    ktru_distinct: int | None = None


class VectorSearch(Protocol):
    async def search(self, vector: list[float], k: int = 20) -> list[tuple[str, float]]: ...


def build_bm25(catalog: Catalog) -> BM25Index:
    """Документ позиции: наименование дважды (оно важнее), ОКПД2, имена характеристик и значения."""
    docs = {}
    for p in catalog.all():
        parts = [p.name, p.name, p.okpd2]
        for c in p.characteristics:
            if c.group == "general":
                continue  # «Общие сведения» — поля карточки, а не признаки категории
            parts.append(c.name)
            parts.extend(c.values)
        docs[p.code] = " ".join(parts)
    return BM25Index(docs)


def rrf(
    rankings: list[list[str]], k: int = 60, weights: list[float] | None = None
) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking, weight in zip(rankings, weights or [1.0] * len(rankings), strict=True):
        for rank, code in enumerate(ranking):
            scores[code] = scores.get(code, 0.0) + weight / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def exemplar_hits(name: str, exemplars: ExemplarIndex | None) -> list[tuple[str, float]]:
    """Категории похожих СТЕ с долей голоса; пусто — соседи не похожи на наименование.

    Первым может стоять класс «не товар» (`OUTSIDE_PREFIX`: работы, услуги) — так индекс
    отвечает «это не из справочника товаров», вызывающий проверяет `outside`.
    """
    return exemplar_vote(name, exemplars)[0]


def exemplar_vote(
    name: str,
    exemplars: ExemplarIndex | None,
    vector_hits: list[tuple[str, float]] | None = None,
) -> tuple[list[tuple[str, float]], list[tuple[int, float]]]:
    """Голос категорий (как `exemplar_hits`) и сами соседи — одним поиском по индексу: примеры
    и счёт «N из M похожих» берутся из тех же соседей, что голосовали.

    `vector_hits` — соседи по векторам тех же наименований (`ExemplarVectors.hits`): голосуют
    вместе с BM25. Порог «соседи не похожи» — по BM25: вектор ближайшего соседа есть всегда.
    """
    if exemplars is None:
        return [], []
    hits = exemplars.neighbors(name)
    if not hits or exemplars.coverage(name, hits[0][0]) < EXEMPLAR_MIN_COVERAGE:
        return [], []
    votes = (
        merge_votes([(exemplars.codes[i], s) for i, s in hits], vector_hits)
        if vector_hits
        else exemplars.vote(name, hits=hits)
    )
    best = votes[0][1] if votes else 0.0
    return [(code, v) for code, v in votes if v >= EXEMPLAR_TAIL * best][:MAX_SIMILAR], hits


def outside(hits: list[tuple[str, float]]) -> bool:
    """Больше всего голосов у класса «не товар»: работа или услуга, а не позиция справочника."""
    return bool(hits) and hits[0][0].startswith(OUTSIDE_PREFIX)


def outside_kind(name: str, exemplars: ExemplarIndex | None) -> Literal["works", "services"] | None:
    """Похожие СТЕ наименования — работы или услуги («Ремонт мягкой кровли», «Услуги по
    уборке»): объяснить поставщику, почему позиции нет, а не предлагать похожие товары."""
    voted, _ = exemplar_vote(name, exemplars)
    if not outside(voted):
        return None
    return "services" if voted[0][0].startswith(f"{OUTSIDE_PREFIX}services") else "works"


async def _vector_hits(
    text: str,
    vectors: VectorSearch | None,
    embed: Callable[[str], Awaitable[list[float]]] | None,
    k: int,
) -> tuple[list[tuple[str, float]], bool]:
    """Векторные кандидаты и признак отказа (нет индекса, нет модели, сбой)."""
    if vectors is None or embed is None:
        return [], True
    try:
        ready = getattr(vectors, "ready", None)
        if ready is not None and not await ready():
            return [], True  # индекс не построен: модель эмбеддингов не дёргаем зря
        return await vectors.search(await embed(text), k), False
    except (LLMError, EmbeddingIndexEmpty, httpx.HTTPError) as exc:
        # Отказ сервиса эмбеддингов — работаем по BM25 и говорим об этом. Ошибки кода не глотаем.
        log.warning("эмбеддинги недоступны, поиск только по BM25: %s", exc)
        return [], True


def _once(
    embed: Callable[[str], Awaitable[list[float]]],
) -> Callable[[str], Awaitable[list[float]]]:
    """Тот же текст в одном поиске не отправляется в модель дважды (справочник и похожие СТЕ)."""
    done: dict[str, list[float]] = {}

    async def cached(text: str) -> list[float]:
        if text not in done:
            done[text] = await embed(text)
        return done[text]

    return cached


async def _exemplar_vector_hits(
    name: str,
    index: ExemplarVectors | None,
    embed: Callable[[str], Awaitable[list[float]]] | None,
) -> list[tuple[str, float]]:
    """Соседи наименования по векторам похожих СТЕ; нет индекса, модели или сбой — пусто (голос
    тогда один BM25)."""
    if index is None or embed is None:
        return []
    try:
        return index.hits(await embed(name))
    except (LLMError, httpx.HTTPError) as exc:
        log.warning("векторы похожих СТЕ недоступны, голос только BM25: %s", exc)
        return []


# Главное слово наименования (первое, «Отвертка SPARTA …») совпало с позицией — порог мягче:
# бренд и модель длиннее самого товара и тянут нормированную оценку вниз.
HEAD_GATE_SHARE = 0.5


def _gate_open(
    gate_text: str,
    gate_hits: list[tuple[str, float]],
    gate_score: float,
    min_score: float,
    catalog: Catalog,
    bm25: BM25Index,
) -> bool:
    if gate_score >= min_score:
        return True
    if not gate_hits or gate_score < min_score * HEAD_GATE_SHARE:
        return False
    head = bm25.query_tokens(gate_text)[:1]
    top = catalog.get(gate_hits[0][0])
    return bool(head) and top is not None and head[0] in tokens(top.name)


async def find_candidates(
    text: str,
    catalog: Catalog,
    bm25: BM25Index,
    vectors: VectorSearch | None,
    embed: Callable[[str], Awaitable[list[float]]] | None,
    k: int = 20,
    min_score: float = 0.2,
    gate: str | None = None,
    exemplars: ExemplarIndex | None = None,
    exemplar_vectors: ExemplarVectors | None = None,
) -> tuple[list[Candidate], list[Literal["embeddings"]]]:
    """Кандидаты по убыванию RRF; пусто — товар вне справочника.

    Отказ честный: «ближайшая» позиция с уверенностью не подставляется.
    """
    # Порог «вне справочника» — по наименованию (gate): длинное описание с номерами и брендами
    # размывает нормированную оценку. Ранжирование — по всему тексту. Отрицаемые слова («не
    # нитриловые», «без хлора») в поиск не идут и исключают позиции, где они есть.
    gate_text = without_negations(gate or text)
    query = without_negations(text)
    negated = negated_stems(f"{gate or ''} {text}")
    gate_hits = bm25.search(gate_text, 1)
    overlap = bm25.known_overlap(gate_text, gate_hits[0][0]) if gate_hits else 0
    gate_score = (
        gate_hits[0][1] / (bm25.max_score(gate_text, matched_known=overlap) or 1.0)
        if gate_hits
        else 0.0
    )
    raw_hits = (
        bm25.search(query, k)
        if _gate_open(gate_text, gate_hits, gate_score, min_score, catalog, bm25)
        else []
    )
    hits = raw_hits
    # Второе условие другой природы: хотя бы одно слово запроса есть в наименовании позиции,
    # а не только в значениях её характеристик («белый» есть в перечнях у многих позиций).
    asked = {t for t in bm25.query_tokens(gate_text) if len(t) >= MIN_NAME_TOKEN}
    hits = [
        (c, s)
        for c, s in hits
        if (p := catalog.get(c)) is not None
        and asked & set(tokens(p.name))
        and not negated & bm25.doc_tokens(c)
    ]
    best = hits[0][1] if hits else 0.0
    lexical = [(c, s) for c, s in hits if s >= TAIL_SHARE * best]
    embed = _once(embed) if embed is not None else None
    vector_hits, failed = await _vector_hits(text, vectors, embed, k)
    vector_hits = [(c, s) for c, s in vector_hits if s >= MIN_COSINE and catalog.get(c)]
    degraded: list[Literal["embeddings"]] = ["embeddings"] if failed else []
    log.info(
        "поиск: gate=%.3f bm25=%d после_имени=%d лексических=%d векторных=%d эмбеддинги=%s",
        gate_score,
        len(raw_hits),
        len(hits),
        len(lexical),
        len(vector_hits),
        "сбой" if failed else "ok",
    )
    # Похожие СТЕ — по наименованию: в описании характеристики, а не то, чем товар назван. Порог
    # «вне справочника» и слово в наименовании позиции к ним не применяются — у них свой порог.
    voted, neighbors = exemplar_vote(
        gate or text, exemplars, await _exemplar_vector_hits(gate or text, exemplar_vectors, embed)
    )
    if outside(voted):
        # Соседи — работы или услуги: позиция справочника товаров была бы чужой.
        return [], degraded
    similar = [(c, v) for c, v in voted if catalog.get(c) and not negated & bm25.doc_tokens(c)]
    if not lexical and not vector_hits and not similar:
        return [], degraded
    share = dict(similar)
    # Подсказка к варианту: до двух похожих СТЕ и «N из M похожих» — из тех же соседей, что
    # голосовали, у любой категории с соседями (а не только прошедшей хвост голоса). Без
    # показываемых примеров (выгрузка без id) нет и счёта: строка «N из M» без СТЕ не проверить.
    counts = exemplars.counts(neighbors) if exemplars is not None else {}
    examples = (
        {c: [SimilarSku(id=i, name=n) for i, n in exemplars.examples(neighbors, c)] for c in counts}
        if exemplars is not None
        else {}
    )
    if similar and similar[0][1] >= AUTO_SHARE:
        # Похожие СТЕ почти единогласны: остальные списки только размыли бы ответ.
        lexical, vector_hits, similar = [], [], similar[:1]
    words = words_with_stems(text)
    out = []
    rankings = [[c for c, _ in lexical], [c for c, _ in vector_hits], [c for c, _ in similar]]
    for code, score in rrf(rankings, weights=[1.0, 1.0, EXEMPLAR_WEIGHT]):
        position = catalog.get(code)
        if position is None:
            continue
        matched = bm25.matched(text, code)
        why = list(
            dict.fromkeys(
                w.lower() for w, stem in words if bm25.correct(stem) in matched and not w.isdigit()
            )
        )
        shown = examples.get(code, [])
        out.append(
            Candidate(
                code=code, name=position.name, score=round(score, 6), why=why,
                similar=round(share[code], 4) if code in share else None,
                examples=shown,
                similar_count=counts.get(code) if shown else None,
                similar_total=len(neighbors) if shown else None,
            )
        )  # fmt: skip
    return out, degraded
