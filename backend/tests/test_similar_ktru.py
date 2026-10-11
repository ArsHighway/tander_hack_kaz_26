"""Регрессия на синтетическом каталоге из 30 похожих позиций.

Каталог в data/ не коммитится. Копия для тестов — fixtures/ktru_similar_products_30.json.
Коды в проверки не зашиты в прикладной код: здесь только ожидание на этой фикстуре.
"""

import asyncio
from pathlib import Path

import pytest

from app.catalog import Catalog
from app.extract import LLMItems
from app.glossary import Glossary
from app.llm import LLMError
from app.pipeline import AutofillIn, Deps, autofill
from app.search import build_bm25
from app.session import SessionStore
from tests.fakes import FakeLLM, FakeRedis

CATALOG_PATH = Path(__file__).parent / "fixtures" / "ktru_similar_products_30.json"
if not CATALOG_PATH.is_file():
    pytest.fail(
        "нет обязательной фикстуры backend/tests/fixtures/ktru_similar_products_30.json",
        pytrace=False,
    )

MASS = "Масса бумаги площадью 1 м2"
SHEETS = "Количество листов в пачке"
SURFACE = "Тип поверхности"
WHITENESS = "Белизна по CIE"

# Одна позиция на эту определяющую комбинацию. Остальные запросы оставляют несколько кодов.
EXACT = [
    ("Бумага А4, 90 г/м², 250 листов", "17.12.14.129-TEST0008"),
    ("Бумага А4 90 250", "17.12.14.129-TEST0008"),
    ("Бумага А4, 100 г/м², 500 листов, глянцевая", "17.12.14.129-TEST0028"),
    ("Бумага А4 100 500 глянцевая", "17.12.14.129-TEST0028"),
    ("Бумага А3 100 500 глянцевая", "17.12.14.129-TEST0030"),
]


def _deps(llm: FakeLLM | None = None) -> tuple[Deps, FakeLLM]:
    catalog = Catalog.load([CATALOG_PATH])
    model = llm or FakeLLM([LLMItems(items=[])] * 40)
    deps = Deps(
        catalog=catalog,
        bm25=build_bm25(catalog),
        vectors=None,
        llm=model,  # type: ignore[arg-type]
        glossary=Glossary([]),
        sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.2,
    )
    return deps, model


def _fill(name: str, description: str = "", llm: FakeLLM | None = None):
    deps, model = _deps(llm)
    out = asyncio.run(autofill(AutofillIn(name=name, description=description), deps))
    return out, model


def _value(out, name: str) -> str | None:
    for char in out.characteristics:
        if char.name == name:
            return char.value
    return None


def _codes(out) -> set[str]:
    return {c.code for c in out.ktru.candidates}


def _question(out) -> str | None:
    return out.ktru.question.characteristic if out.ktru.question else None


@pytest.mark.parametrize(("name", "code"), EXACT)
def test_unique_defining_signature_is_an_exact_code(name: str, code: str) -> None:
    out, _ = _fill(name)
    assert out.ktru.final_code == code
    assert _question(out) != WHITENESS


@pytest.mark.parametrize(
    "name",
    [
        "Бумага для принтера формата А4, плотность 80 г/м², пачка 500 листов",
        "Бумага А4 80 г/м2 500 листов",
        "Бумага А4 80 г/м² 500 листов",
        "Бумага А4 80 г / м2 500 листов",
        "Бумага А4 80 г / м² 500 листов",
        "Бумага А4 80 грамм/м² 500 листов",
        "Бумага А4 80 грамм на м2 500 листов",
        "Бумага А4 80 грамм на квадратный метр, пачка 500 штук",
        "Бумага А4 80 500",
        "Бумга А4 80 500",
        "Бумага А4 80 г/м² 500 лист",
        "Бумага А4 80 г/м² 500 листа",
        "Бумага А4 80 г/м² 500 штуки",
    ],
)
def test_a4_80_500_keeps_the_family_and_asks_what_still_differs(name: str) -> None:
    # TEST0001, 0002, 0004 и 0005 совпадают по формату, массе, листам и поверхности.
    # Различает их белизна. Код первой записи не выдаётся за точный.
    out, _ = _fill(name)
    assert out.ktru.final_code is None
    assert _question(out) == WHITENESS
    assert "17.12.14.129-TEST0001" in _codes(out)
    assert "17.12.14.129-TEST0003" not in _codes(out)
    assert "17.12.14.129-TEST0006" not in _codes(out)
    assert (_value(out, MASS), _value(out, SHEETS)) == ("80", "500")


def test_missing_sheet_count_is_asked_not_taken_from_the_larger_group() -> None:
    out, _ = _fill("Бумага А4 80")
    assert out.ktru.final_code is None
    assert _question(out) == SHEETS
    assert _value(out, MASS) == "80"
    assert _value(out, SHEETS) is None
    codes = _codes(out)
    assert "17.12.14.129-TEST0001" in codes
    assert "17.12.14.129-TEST0003" in codes


def test_typo_without_sheet_count_does_not_invent_five_hundred() -> None:
    out, _ = _fill("Бумга А4 80")
    assert out.ktru.final_code is None
    assert _question(out) == SHEETS
    assert _value(out, MASS) == "80"
    assert _value(out, SHEETS) is None


def test_a4_90_500_asks_surface_instead_of_the_matte_majority() -> None:
    out, _ = _fill("Бумага А4 90 500")
    assert out.ktru.final_code is None
    assert _question(out) == SURFACE
    codes = _codes(out)
    assert "17.12.14.129-TEST0006" in codes
    assert "17.12.14.129-TEST0027" in codes


def test_a4_100_500_and_a3_80_500_are_not_a_single_code() -> None:
    hundred, _ = _fill("Бумага А4 100 500")
    assert hundred.ktru.final_code is None
    assert "17.12.14.129-TEST0011" in _codes(hundred)
    assert "17.12.14.129-TEST0028" in _codes(hundred)
    a3, _ = _fill("Бумага А3 80 500")
    assert a3.ktru.final_code is None
    assert _question(a3) == WHITENESS
    assert "17.12.14.129-TEST0016" in _codes(a3)
    assert "17.12.14.129-TEST0017" not in _codes(a3)


def test_printer_typo_still_retrieves_the_paper() -> None:
    out, model = _fill("Бумага для прнтера А4 80 г/м² 500 листов")
    assert out.ktru.final_code is None
    assert "17.12.14.129-TEST0001" in _codes(out)
    # Модель читает текст только по определённой позиции (`pipeline._fill_missing`), не на вопросе.
    assert not model.calls


def test_bare_numbers_are_kept() -> None:
    out, _ = _fill("Бумага А4 80 500")
    assert (_value(out, MASS), _value(out, SHEETS)) == ("80", "500")
    assert not any("80" in i.message and "не подстав" in i.message for i in out.issues)


def test_density_range_is_not_an_exact_code() -> None:
    name = (
        "Нужна обычная белая бумага для офиса. Формат А4, пачка 500 листов. "
        "Плотность примерно 90–100 г/м², матовая, для лазерной и двусторонней печати."
    )
    out, _ = _fill(name)
    assert out.ktru.final_code is None
    assert _question(out) == MASS
    codes = _codes(out)
    assert "17.12.14.129-TEST0006" in codes
    assert "17.12.14.129-TEST0011" in codes


def test_llm_error_is_visible_and_rules_still_match() -> None:
    out, _ = _fill("Бумага А4 80 г/м² 500 листов", llm=FakeLLM([LLMError("нет связи")]))
    assert out.ktru.final_code is None
    assert "17.12.14.129-TEST0001" in _codes(out)
    # До определённой позиции модель не зовут: правила заполнили своё и без неё.
    assert (_value(out, MASS), _value(out, SHEETS)) == ("80", "500")


def test_natural_language_keeps_format_mass_and_sheets() -> None:
    queries = [
        (
            "Бумага для офиса A4, плотность 80 грамм на квадратный метр, "
            "упаковка 500 листов. Подходит для лазерной и струйной печати."
        ),
        (
            "Нужна офисная бумага формата А4, 90 г/м², 500 листов, "
            "подходит для лазерной и струйной печати."
        ),
        "Подберите бумагу A4 плотностью 100 г/м2, 500 листов, матовую.",
    ]
    expected = [("80", "500"), ("90", "500"), ("100", "500")]
    for name, (mass, sheets) in zip(queries, expected, strict=True):
        out, _ = _fill(name)
        assert out.ktru.candidates, name
        assert (_value(out, MASS), _value(out, SHEETS)) == (mass, sheets), name
        assert _question(out) != SHEETS
