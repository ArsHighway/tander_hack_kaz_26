import asyncio
import json
from pathlib import Path

import pytest

from app.catalog import Catalog, Position
from app.search import BM25Index, Candidate, build_bm25, find_candidates
from app.search.exemplars import ExemplarIndex

ITEMS = [
    ("СМЕС", "Смеситель для умывальника РМС однорычажный"),
    ("СМЕС", "Смеситель для кухни Grohe однорычажный"),
    ("СМЕС", "Смеситель для ванны с душем"),
    ("ПАП", "Папка-регистратор синяя 75 мм"),
    ("ПАП", "Папка уголок прозрачная"),
    ("ОТВ", "Отвертка крестовая Зубр"),
]


def test_vote_prefers_category_of_close_neighbours() -> None:
    idx = ExemplarIndex(ITEMS)
    votes = idx.vote("Смеситель однорычажный для мойки")
    assert votes[0][0] == "СМЕС"
    assert abs(sum(v for _, v in votes) - 1.0) < 1e-9


def test_vote_empty_when_no_word_is_known() -> None:
    assert ExemplarIndex(ITEMS).vote("Бетономешалка") == []


def test_coverage_counts_unknown_words_against_confidence() -> None:
    idx = ExemplarIndex(ITEMS)
    [(best, _)] = idx.neighbors("Отвертка Зубр", 1)
    assert idx.coverage("Отвертка Зубр", best) == 1.0
    [(best, _)] = idx.neighbors("Бетономешалка Зубр", 1)
    assert idx.coverage("Бетономешалка Зубр", best) < 0.6


def test_outside_class_on_top_means_not_goods() -> None:
    from app.search.candidates import exemplar_hits, outside

    idx = ExemplarIndex([*ITEMS, ("__services__7", "Услуги по ремонту смесителя")])
    assert outside(exemplar_hits("Услуги по ремонту смесителя на кухне", idx))
    assert not outside(exemplar_hits("Смеситель для кухни однорычажный", idx))


IDS: list[int | None] = [1, 2, 3, 4, 5, 6]
QUERY = "Смеситель однорычажный для мойки"
EOL = chr(10)

# Соседи запроса из двух категорий и класса «не товар», в СМЕС — повтор наименования с другим id:
# счёт категории и число соседей различаются, примеры идут по порядку соседей без повторов.
RICH = [
    *ITEMS,
    ("ЛЕЙ", "Лейка душевая для смесителя однорычажного"),
    ("__services__7", "Услуги по ремонту смесителя"),
    ("СМЕС", "Смеситель для умывальника РМС однорычажный"),
]
RICH_IDS: list[int | None] = [*IDS, 14, 15, 16]


def test_examples_follow_neighbour_order_without_repeated_names() -> None:
    idx = ExemplarIndex(RICH, ids=RICH_IDS)
    hits = idx.neighbors(QUERY)
    assert idx.examples(hits, "СМЕС", 3) == [
        (1, "Смеситель для умывальника РМС однорычажный"),
        (2, "Смеситель для кухни Grohe однорычажный"),
        (3, "Смеситель для ванны с душем"),
    ]
    assert idx.examples(hits, "ЛЕЙ") == [(14, "Лейка душевая для смесителя однорычажного")]


def test_examples_empty_for_category_without_neighbours() -> None:
    idx = ExemplarIndex(RICH, ids=RICH_IDS)
    assert idx.examples(idx.neighbors(QUERY), "ОТВ", 2) == []


def test_counts_per_category_cover_every_neighbour() -> None:
    idx = ExemplarIndex(RICH, ids=RICH_IDS)
    hits = idx.neighbors(QUERY)
    assert idx.counts(hits) == {"СМЕС": 4, "ЛЕЙ": 1, "__services__7": 1}
    assert len(hits) == 6


def test_no_ids_means_no_examples() -> None:
    idx = ExemplarIndex(ITEMS)
    assert idx.examples(idx.neighbors(QUERY), "СМЕС", 2) == []


def test_load_reads_ids_when_present(tmp_path: Path) -> None:
    old = tmp_path / "old.jsonl"
    old.write_text(
        "".join(json.dumps({"code": c, "name": n}, ensure_ascii=False) + EOL for c, n in ITEMS),
        "utf-8",
    )
    new = tmp_path / "new.jsonl"
    new.write_text(
        "".join(
            json.dumps({"code": c, "name": n, "id": i}, ensure_ascii=False) + EOL
            for (c, n), i in zip(ITEMS, IDS, strict=True)
        ),
        "utf-8",
    )
    old_idx, new_idx = ExemplarIndex.load(old), ExemplarIndex.load(new)
    assert old_idx.examples(old_idx.neighbors(QUERY), "СМЕС") == []
    assert new_idx.examples(new_idx.neighbors(QUERY), "СМЕС")


def _catalog() -> tuple[Catalog, BM25Index]:
    names = {"СМЕС": "Смесители для мойки и умывальника", "ЛЕЙ": "Лейки душевые", "ОТВ": "Отвертки"}
    positions = [
        Position(code=c, name=n, okpd2="", unit=None, characteristics=[], source="portal")
        for c, n in names.items()
    ]
    catalog = Catalog(positions, "t")
    return catalog, build_bm25(catalog)


def _candidates(text: str, index: ExemplarIndex) -> list[Candidate]:
    catalog, bm25 = _catalog()
    found, _ = asyncio.run(
        find_candidates(text, catalog, bm25, None, None, min_score=0.2, exemplars=index)
    )
    return found


def test_candidates_carry_examples_and_honest_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    # СМЕС набирает 75 % — по умолчанию она была бы единственной; здесь важны примеры обеих
    monkeypatch.setattr("app.search.candidates.AUTO_SHARE", 1.01)
    found = {c.code: c for c in _candidates(QUERY, ExemplarIndex(RICH, ids=RICH_IDS))}
    mixer, can = found["СМЕС"], found["ЛЕЙ"]
    assert [e.id for e in mixer.examples] == [1, 2]
    assert (mixer.similar_count, mixer.similar_total) == (4, 6)
    assert [e.id for e in can.examples] == [14]
    assert (can.similar_count, can.similar_total) == (1, 6)


def test_without_ids_there_is_no_count_line_either() -> None:
    # Review Focus 2: без примеров нет и подписи «N из M похожих», даже если категория голосовала.
    [top, *_] = _candidates(QUERY, ExemplarIndex(ITEMS))
    assert top.code == "СМЕС" and top.similar is not None
    assert top.examples == []
    assert (top.similar_count, top.similar_total) == (None, None)


def test_candidate_found_only_by_text_has_no_examples() -> None:
    found = _candidates("Лейка душевая", ExemplarIndex(ITEMS, ids=IDS))
    assert [c.code for c in found] == ["ЛЕЙ"]
    assert found[0].examples == []
    assert found[0].similar_count is None and found[0].similar_total is None


def test_one_neighbour_search_per_request(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    original = ExemplarIndex.neighbors

    def counted(self: ExemplarIndex, query: str, k: int = 30) -> list[tuple[int, float]]:
        calls.append(query)
        return original(self, query, k)

    monkeypatch.setattr(ExemplarIndex, "neighbors", counted)
    _candidates(QUERY, ExemplarIndex(RICH, ids=RICH_IDS))
    assert len(calls) == 1


MID = [
    ("СМЕС", "Смеситель однорычажный хром"),
    ("СМЕС", "Смеситель однорычажный латунь"),
    ("СМЕС", "Смеситель для кухни"),
    ("ЛЕЙ", "Лейка хром для смесителя"),
    ("ЛЕЙ", "Лейка душевая"),
    ("ОТВ", "Отвертка крестовая"),
    ("ОТВ", "Отвертка плоская"),
]


def test_category_with_70_percent_of_similar_is_the_only_candidate() -> None:
    index = ExemplarIndex(MID)
    query = "Смеситель хром для кухни"
    assert 0.7 <= index.vote(query)[0][1] < 0.9  # контроль: между прежним и новым порогом
    assert [c.code for c in _candidates(query, index)] == ["СМЕС"]


def test_below_70_percent_the_supplier_chooses() -> None:
    index = ExemplarIndex(MID)
    assert index.vote("Смеситель хром")[0][1] < 0.7
    assert {"СМЕС", "ЛЕЙ"} <= {c.code for c in _candidates("Смеситель хром", index)}
