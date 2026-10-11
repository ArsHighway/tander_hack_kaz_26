import asyncio
from pathlib import Path

from app.catalog import Catalog
from app.llm import LLMError
from app.search import BM25Index, build_bm25, find_candidates, rrf, tokens

FAKE = Path(__file__).parent / "fixtures" / "fake"
CATALOG = Catalog.load([FAKE / "ktru.json"])
BM25 = build_bm25(CATALOG)
MIN_SCORE = 0.2


class FakeVectors:
    def __init__(self, hits: list[tuple[str, float]]) -> None:
        self.hits = hits

    async def search(self, vector: list[float], k: int = 20) -> list[tuple[str, float]]:
        return self.hits[:k]


def _find(text: str, vectors=None, embed=None):
    return asyncio.run(find_candidates(text, CATALOG, BM25, vectors, embed, min_score=MIN_SCORE))


async def _embed_ok(text: str) -> list[float]:
    return [0.1, 0.2]


async def _embed_down(text: str) -> list[float]:
    raise LLMError("модель эмбеддингов недоступна")


def test_tokens_fold_case_and_lookalikes() -> None:
    assert tokens("Бумага А4") == tokens("бумаги A4")


def test_tokens_drop_prepositions() -> None:
    assert tokens("для офисной техники") == tokens("офисной техники")


def test_rrf_prefers_items_ranked_by_both() -> None:
    assert rrf([["a", "b"], ["b", "c"]])[0][0] == "b"


def test_bm25_ranks_matching_document_first() -> None:
    index = BM25Index({"x": "бумага офисная белая", "y": "перчатки нитриловые"})
    assert index.search("белая бумага")[0][0] == "x"


def test_white_paper_query() -> None:
    found, _ = _find("Бумага офисная белая А4 80 г/м2", vectors=FakeVectors([]), embed=_embed_ok)
    assert found[0].code == "17.12.14.129-00000101"


def test_colour_paper_query() -> None:
    found, _ = _find("бумага цветная неон", vectors=FakeVectors([]), embed=_embed_ok)
    assert found[0].code == "17.12.14.129-00000102"


def test_gloves_query() -> None:
    found, _ = _find("перчатки нитриловые M", vectors=FakeVectors([]), embed=_embed_ok)
    assert found[0].code == "22.19.60.119-00000301"


def test_out_of_catalog_gives_no_candidates() -> None:
    found, _ = _find("услуги по уборке помещений", vectors=FakeVectors([]), embed=_embed_ok)
    assert found == []


def test_candidates_explain_matched_words() -> None:
    found, _ = _find("перчатки нитриловые M", vectors=FakeVectors([]), embed=_embed_ok)
    assert "перчатки" in found[0].why


def test_weak_tail_is_cut() -> None:
    found, _ = _find("бумага цветная неон", vectors=FakeVectors([]), embed=_embed_ok)
    assert all(c.code.startswith("17.12.14.129") for c in found)


def test_embeddings_down_falls_back_to_bm25_and_says_so() -> None:
    found, degraded = _find("перчатки нитриловые M", vectors=FakeVectors([]), embed=_embed_down)
    assert found[0].code == "22.19.60.119-00000301"
    assert degraded == ["embeddings"]


def test_no_vector_index_is_degraded_too() -> None:
    _, degraded = _find("перчатки нитриловые M")
    assert degraded == ["embeddings"]


def test_vector_hit_joins_the_ranking() -> None:
    # Описание без общих слов с позицией, но эмбеддинги узнают батарейку.
    vectors = FakeVectors([("27.20.11.000-00000501", 0.83)])
    found, degraded = _find(
        "пальчиковые элементы на полтора вольта", vectors=vectors, embed=_embed_ok
    )
    assert [c.code for c in found] == ["27.20.11.000-00000501"]
    assert degraded == []


def test_long_description_does_not_hide_the_category() -> None:
    # Описание с номерами и брендами размывает оценку; порог «вне справочника» решает наименование.
    text = (
        "Бумага офисная Туринский ЦБЗ А4 500л\nТолщина, мкм 104\nПредельные отклонения 0,5\nISO 98"
    )
    found, _ = asyncio.run(
        find_candidates(
            text,
            CATALOG,
            BM25,
            None,
            None,
            min_score=MIN_SCORE,
            gate="Бумага офисная Туринский ЦБЗ А4 500л",
        )
    )
    assert found and found[0].code.startswith("17.12.14.129")


def test_explanation_lists_words_not_numbers() -> None:
    found, _ = _find("Бумага офисная белая А4 80 г/м2", vectors=FakeVectors([]), embed=_embed_ok)
    assert all(not w.isdigit() for w in found[0].why)


def test_typo_with_extra_letter_still_finds_the_position() -> None:
    # «Бумаага», «перчаткии» — лишняя буква, и позиция не находилась совсем.
    found, _ = _find("Бумаага офисная белая А4", vectors=FakeVectors([]), embed=_embed_ok)
    assert found and found[0].code == "17.12.14.129-00000101"
    assert "бумаага" in found[0].why  # объяснение словами поставщика
    found, _ = _find("перчаткии нитриловые M", vectors=FakeVectors([]), embed=_embed_ok)
    assert found and found[0].code == "22.19.60.119-00000301"


def test_transposed_typo_is_used_in_search() -> None:
    index = BM25Index({"paper": "бумага офисная белая"})
    assert index.correct(tokens("Бумга")[0]) == tokens("Бумага")[0]
    assert index.search("Бумга офисная")[0][0] == "paper"


def test_missing_letter_typo_corrects_a_known_word() -> None:
    index = BM25Index({"printer": "принтер лазерный"})
    assert index.correct(tokens("прнтер")[0]) == tokens("принтер")[0]
    assert index.search("прнтер лазерный")[0][0] == "printer"


def test_latin_lookalike_matches_the_cyrillic_word() -> None:
    index = BM25Index({"pack": "пачка бумаги"})
    assert index.search("пачкa")[0][0] == "pack"  # латинская a


def test_typo_correction_does_not_invent_matches() -> None:
    # Короткие и далёкие от словаря слова не исправляются: «уборке» не станет «бумаге».
    assert BM25.correct("убор") == "убор"
    assert BM25.correct("помещен") == "помещен"
    found, _ = _find("услуги по уборке помещений", vectors=FakeVectors([]), embed=_embed_ok)
    assert found == []


# Категории портала (КПГЗ): у живых наименований бренд и модель длиннее самого товара, и
# нормированная оценка падает ниже порога, хотя главное слово совпало.
def _portal_catalog() -> tuple[Catalog, BM25Index]:
    from app.catalog import Position

    names = [
        "Отвертки", "Стулья на металлическом каркасе", "Бинты эластичные и гипсовые",
        "Соки из фруктов и (или) овощей", "Салфетки, насадки для уборки, полотна технические",
        "Бумага для офисной техники белая", "Перчатки медицинские смотровые",
    ]  # fmt: skip
    positions = [
        Position(code=f"КПГЗ {i}", name=n, okpd2="", unit=None, characteristics=[], source="portal")
        for i, n in enumerate(names)
    ]
    catalog = Catalog(positions, "t")
    return catalog, build_bm25(catalog)


def _find_in(text: str, catalog: Catalog, bm25: BM25Index) -> list:
    found, _ = asyncio.run(find_candidates(text, catalog, bm25, None, None, min_score=MIN_SCORE))
    return [c.name for c in found]


def test_head_noun_passes_the_gate_despite_brand_and_model_words() -> None:
    catalog, bm25 = _portal_catalog()
    assert _find_in('Отвертка SPARTA "Point", SL5,0х100 мм, 2к рукоятка', catalog, bm25)[:1] == [
        "Отвертки"
    ]
    assert _find_in("Бинт самофиксирующийся LUXSAN 10см х 10 м", catalog, bm25)[:1] == [
        "Бинты эластичные и гипсовые"
    ]


def test_head_noun_elsewhere_in_the_name_does_not_pass_the_gate() -> None:
    # Совпало не первое слово наименования — порог прежний: «набор» отвёрткой не становится.
    catalog, bm25 = _portal_catalog()
    assert (
        _find_in('Набор SPARTA "Point Pro" SL5,0х100 мм отвертка 2к рукоятка', catalog, bm25) == []
    )
