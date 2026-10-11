"""Товары рядом со справочником, но не из него: итоговый код без подтверждения не ставится.

Примеры с реальных строк портала: «Белая глина» стала офисной бумагой, «Шланг
высокого давления» — мешком из ПВД, «Стержень гелевый» — ручкой. Код КТРУ — авторитетный результат,
поэтому совпадение одного прилагательного его не даёт.
"""

import asyncio
from pathlib import Path

import pytest

from app.catalog import Catalog
from app.checks.detector import not_found
from app.extract import LLMItems
from app.glossary import Glossary
from app.narrowing import CONFIRM, NOTHING_FITS, NearPosition, not_found_steps
from app.pipeline import AnswerIn, AutofillIn, AutofillOut, Deps, answer, autofill
from app.search import build_bm25
from app.session import SessionStore
from tests.fakes import FakeLLM, FakeRedis

CATALOG = Catalog.load([Path(__file__).parent.parent / "app" / "catalog" / "demo" / "demo.json"])
WHITE = "17.12.14.129-00000031"
NITRILE = "22.19.60.119-00000301"
BATTERY = "27.20.11.000-00000501"


def _deps() -> Deps:
    return Deps(
        catalog=CATALOG,
        bm25=build_bm25(CATALOG),
        vectors=None,
        llm=FakeLLM([LLMItems(items=[])] * 5),  # type: ignore[arg-type]
        glossary=Glossary([]),
        sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.2,
    )


def _fill(name: str, deps: Deps | None = None) -> AutofillOut:
    return asyncio.run(autofill(AutofillIn(name=name), deps or _deps()))


@pytest.mark.parametrize(
    "name",
    [
        "Белая глина",
        "Куприн. Белый пудель",
        "Кожа натуральная Наппа, белый",
        "Бумага туалетная 4 рулона",
        "Шланг высокого давления 1SN",
        "Стержень гелевый синий",
        "Маркер перманентный черный",
        "Перчатки латексные хозяйственные",
        "Папка уголок А4",
        "Бумага наждачная",
    ],
)
def test_near_domain_product_gets_no_final_code(name: str) -> None:
    out = _fill(name)
    assert out.ktru.final_code is None


def test_unexplained_word_asks_to_confirm_the_position() -> None:
    out = _fill("Бумага туалетная 4 рулона")
    q = out.ktru.question
    if out.ktru.candidates:  # кандидат нашёлся по «бумага» — его надо подтвердить или отклонить
        assert q is not None
        assert q.characteristic == CONFIRM or NOTHING_FITS in [o.value for o in q.options]
        assert any("туалетн" in s for s in out.ktru.steps)


def test_negation_excludes_the_negated_material() -> None:
    out = _fill("Перчатки смотровые не нитриловые, размер M")
    assert NITRILE not in [c.code for c in out.ktru.candidates]


def test_every_question_offers_nothing_fits() -> None:
    out = _fill("Бумага офисная А4 500 листов")
    assert out.ktru.question is not None
    assert NOTHING_FITS in [o.value for o in out.ktru.question.options]


def test_nothing_fits_answer_is_an_honest_refusal() -> None:
    deps = _deps()
    out = _fill("Бумага офисная А4 500 листов", deps)
    q = out.ktru.question
    assert q is not None
    after = asyncio.run(
        answer(out.session_id, AnswerIn(characteristic=q.characteristic, value=NOTHING_FITS), deps)
    )
    assert after.ktru.final_code is None
    assert after.ktru.candidates == []
    assert after.ktru.steps[0].startswith("Категория не найдена")


def test_fully_explained_name_still_gets_final_code() -> None:
    assert _fill("Бумага офисная белая А4 500 листов").ktru.final_code == WHITE
    nitrile = "Перчатки смотровые нитриловые нестерильные неопудренные, размер M"
    assert _fill(nitrile).ktru.final_code == NITRILE


def test_brand_and_model_words_do_not_block_the_code() -> None:
    # «Туринский ЦБЗ», «IQ COLOR» — бренды: их нет в справочнике, но подтверждать позицию
    # из-за них не нужно.
    assert _fill("Бумага офисная белая Туринский ЦБЗ А4 500 листов").ktru.final_code == WHITE


def test_confirming_the_position_gives_final_code() -> None:
    deps = _deps()
    out = _fill("Батарейка AA щелочная 1,5 В", deps)
    q = out.ktru.question
    assert q is not None and q.characteristic == CONFIRM
    assert BATTERY in [o.value for o in q.options]
    after = asyncio.run(
        answer(out.session_id, AnswerIn(characteristic=CONFIRM, value=BATTERY), deps)
    )
    assert after.ktru.final_code == BATTERY


def test_typo_in_name_is_not_an_unexplained_word() -> None:
    # «Бумаага» — та же «бумага» с опечаткой: подтверждать позицию из-за неё не нужно.
    out = _fill("Бумаага офисная белая А4 500 листов")
    assert out.ktru.final_code == WHITE


def test_typo_correction_keeps_the_first_letter() -> None:
    # Оценка: «колёсах» исправлялось в «солевая» (батарейка) — стул получал кандидата.
    out = _fill("Стул офисный на колёсах")
    assert out.ktru.candidates == []


# Т2.5: код не определить — сказать, что дополнить, и предложить конкретные шаги.
BAGS = "Пакеты для мусора полиэтиленовые фасовочные Avikomp Professional Extra Strong 60л 20шт"


def test_two_shared_words_ask_to_confirm_instead_of_not_found() -> None:
    # Ветка Nik-Dmit: бренд и чужие слова не обнуляют два общих слова (`BM25Index.max_score`) —
    # вместо «не найдено» вопрос «Позиция КТРУ», код без подтверждения не ставится.
    out = _fill(BAGS, _portal_deps(["Мешки для мусора полиэтиленовые", "Ноутбук"]))
    assert out.ktru.final_code is None
    q = out.ktru.question
    assert q is not None and q.characteristic == CONFIRM
    assert NOTHING_FITS in [o.value for o in q.options]
    assert "«фасовочные»" in " ".join(out.ktru.steps)


NEAR = NearPosition(code="К0", name="Мешки для мусора полиэтиленовые", matched=["мусора"])


def test_not_found_names_similar_positions_and_what_matched() -> None:
    steps = " ".join(not_found_steps([NEAR], rejected=False, head="пакеты"))
    assert "Похожие позиции справочника" in steps and "«мусора»" in steps
    assert "К0" in steps
    # После «Ни одна не подходит» отвергнутые позиции снова не предлагаются.
    assert "К0" not in " ".join(not_found_steps([NEAR], rejected=True, head="пакеты"))


def test_not_found_without_similar_positions_says_what_to_add() -> None:
    out = _fill("Услуги по уборке помещений")
    assert out.ktru.steps[0].startswith("Категория не найдена")
    assert any("По слову «услуги» подходящей позиции" in s for s in out.ktru.steps)


def test_not_found_issue_explains_all_steps() -> None:
    issue = not_found(not_found_steps([NEAR], rejected=False))
    assert "Похожие позиции справочника" in issue.why


@pytest.mark.parametrize(
    ("word", "shown"),
    [
        ("г", False),
        ("см", False),
        ("мм", False),
        ("штук", False),
        ("литров", False),
        ("ПНД", True),
        ("скрепка", True),
    ],
)
def test_similar_positions_ignore_units_and_short_words(word: str, shown: bool) -> None:
    from app.pipeline import shown_match

    assert shown_match(word) is shown


# подтверждение — только из-за слов о самом товаре, а не цвета или «артикул».
def test_colour_and_service_words_do_not_require_confirmation() -> None:
    gloves = "Перчатки смотровые нитриловые нестерильные неопудренные синие, размер M"
    assert _fill(gloves).ktru.final_code == NITRILE
    paper = "Бумага офисная белая А4 500 листов, цвет белый, артикул 123"
    assert _fill(paper).ktru.final_code == WHITE


def test_unknown_product_word_still_requires_confirmation() -> None:
    out = _fill("Батарейка AA щелочная 1,5 В")
    assert out.ktru.final_code is None and out.ktru.question is not None


def _portal_deps(names: list[str]) -> Deps:
    from app.catalog import CharacteristicSpec, Position

    spec = CharacteristicSpec(name="Цвет", kind="qualitative", values=["Серый"])
    positions = [
        Position(code=f"К{i}", name=n, okpd2="", unit=None, characteristics=[spec], source="t")
        for i, n in enumerate(names)
    ]
    catalog = Catalog(positions, "t")
    deps = _deps()
    deps.catalog, deps.bm25 = catalog, build_bm25(catalog)
    return deps


def test_weak_match_on_the_product_word_asks_to_confirm_instead_of_not_found() -> None:
    # Бренд и модель длиннее товара, оценка ниже порога, но первое слово — та же позиция: не
    # «категория не найдена» с этой же позицией в «похожих», а «подтвердите позицию».
    deps = _portal_deps(["Ноутбук", "Монитор, подключаемый к компьютеру", "Чай черный"])
    out = _fill("Ноутбук Apple MacBook Pro 16 Silver Space Black M3 Max 36GB 1TB SSD", deps)
    assert out.ktru.question is not None and out.ktru.question.characteristic == CONFIRM
    assert out.ktru.question.options[0].value == "К0"
    assert any("«ноутбук»" in s for s in out.ktru.steps)


def test_similar_positions_need_the_product_word_or_two_words() -> None:
    # «Фильтр масляный» ≠ «Чай черный» по одному слову «фильтр» (чайные пакетики-фильтры).
    deps = _portal_deps(["Чай черный в пакетиках-фильтрах", "Ноутбук"])
    out = _fill("Фильтр масляный MANN W 914/2 для двигателя", deps)
    assert out.ktru.final_code is None and out.ktru.question is None
    assert not any("Чай черный" in s for s in out.ktru.steps)
    assert any("«фильтр»" in s for s in out.ktru.steps)  # шаг называет главное слово


def _deps_with(positions_spec: list[tuple[str, list[str]]]) -> Deps:
    from app.catalog import CharacteristicSpec, Position

    positions = [
        Position(
            code=f"К{i}", name=n, okpd2="", unit=None, source="t",
            characteristics=[CharacteristicSpec(name="Вид тары", kind="qualitative", values=vals)],
        )
        for i, (n, vals) in enumerate(positions_spec)
    ]  # fmt: skip
    deps = _deps()
    deps.catalog = Catalog(positions, "t")
    deps.bm25 = build_bm25(deps.catalog)
    return deps


def test_product_word_only_in_values_needs_confirmation() -> None:
    # «Ведро» нашлось в перечне «Вид тары» у красок, а не в названии позиции — не код, а вопрос.
    deps = _deps_with([("Краски на основе полимеров", ["ведро", "банка"])])
    out = _fill("Ведро полимерное 10 л", deps)
    assert out.ktru.final_code is None
    assert out.ktru.question is not None and out.ktru.question.characteristic == CONFIRM


def test_spare_parts_position_for_the_device_itself_needs_confirmation() -> None:
    from app.pipeline import _doubtful_code

    deps = _deps_with([("Запасные части и оборудование к проекторам", ["коробка"])])
    position = deps.catalog.get("К0")
    assert position is not None
    why = _doubtful_code("Проектор Epson EB-L1755U", position, deps)
    assert why is not None and "запчастей и принадлежностей" in why
    assert _doubtful_code("Запчасть для проектора Epson, лампа", position, deps) is None
