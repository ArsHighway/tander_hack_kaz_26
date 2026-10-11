"""Векторы похожих СТЕ: соседи, голос вместе с BM25, файл индекса."""

from pathlib import Path

import numpy as np
import pytest

from app.search.candidates import exemplar_vote
from app.search.exemplar_vectors import ExemplarVectors, merge_votes
from app.search.exemplars import ExemplarIndex


def index() -> ExemplarVectors:
    matrix = np.array([[1, 0, 0], [0.9, 0.1, 0], [0, 1, 0], [0, 0, 0]], dtype=np.float32)
    return ExemplarVectors(["A", "A", "B", "C"], matrix)


def test_hits_are_nearest_by_cosine_and_zero_vectors_never_vote() -> None:
    hits = index().hits([1.0, 0.05, 0.0], k=4)
    assert [c for c, _ in hits] == ["A", "A", "B"]
    assert hits[0][1] > hits[1][1] > hits[2][1] > 0


def test_empty_or_broken_query_has_no_hits() -> None:
    assert index().hits([0.0, 0.0, 0.0]) == []
    assert index().hits([float("nan"), 0.0, 0.0]) == []


def test_merge_votes_shares_sum_to_one_and_both_searches_vote() -> None:
    merged = merge_votes([("A", 10.0), ("B", 9.0)], [("B", 0.9), ("B", 0.89), ("A", 0.7)])
    assert pytest.approx(sum(v for _, v in merged)) == 1.0
    assert merged[0][0] == "B"  # BM25 почти поровну, векторы — за B


def test_merge_votes_without_vectors_is_the_bm25_vote() -> None:
    assert [c for c, _ in merge_votes([("A", 10.0), ("B", 5.0)], [])] == ["A", "B"]
    assert merge_votes([], []) == []


def test_exemplar_vote_takes_vector_neighbours_into_account() -> None:
    exemplars = ExemplarIndex([("01.01", "лосины спортивные женские"), ("01.02", "лосины детские")])
    alone, _ = exemplar_vote("лосины спортивные", exemplars)
    both, _ = exemplar_vote("лосины спортивные", exemplars, [("01.02", 0.95), ("01.02", 0.94)])
    assert alone[0][0] == "01.01"
    assert {c for c, _ in both} == {"01.01", "01.02"}
    assert dict(both)["01.02"] > dict(alone).get("01.02", 0.0)


def test_npz_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "v.npz"
    index().save(path)
    loaded = ExemplarVectors.load(path)
    assert loaded.codes == ["A", "A", "B", "C"] and loaded.matrix.shape == (4, 3)
