"""Проверка правдоподобия: цитата модели должна быть в тексте, поле — по фрагменту."""

from app.extract.llm_sanity import Odd, Sanity, sanity_issues
from app.extract.model import Extracted
from app.schemas import Span

TEXT = "Тумбочка. Высота: 3 метра Материал: глина."


def _material() -> Extracted:
    at = TEXT.index("глина")
    return Extracted(
        name="Материал",
        raw_value="глина",
        raw_unit=None,
        source=Span(field="description", start=at, end=at + 5, text="глина"),
        method="rule",
    )


def test_quote_in_text_becomes_warn_on_its_field() -> None:
    answer = Sanity(odd=[Odd(quote="Материал: глина", reason="тумбы из глины не делают")])
    [issue] = sanity_issues(answer, TEXT, [_material()])
    assert issue.severity == "warn"
    assert issue.field == "characteristics.Материал"
    assert "глина" in issue.message


def test_invented_quote_is_dropped() -> None:
    answer = Sanity(odd=[Odd(quote="Цвет: вчерашний", reason="нет такого цвета")])
    assert sanity_issues(answer, TEXT, []) == []
