"""Второй слой модели: решения проверяет код, подсказки — только из перечня или слов текста."""

from app.catalog import CharacteristicSpec
from app.extract.llm_review import Check, Place, Review, apply_review
from app.extract.model import Extracted
from app.schemas import Span

TEXT = "Тумба прикроватная. Ширина 40 мм. Материал корпуса: глина."
WIDTH = CharacteristicSpec(name="Ширина", kind="quantitative", unit="мм")
THICK = CharacteristicSpec(name="Толщина", kind="quantitative", unit="мм")
BODY = CharacteristicSpec(
    name="Вид материала корпуса", kind="qualitative", values=["Массив дерева", "МДФ", "ЛДСП"]
)
SPECS = [WIDTH, THICK, BODY]


def _llm(name: str, value: str, quote: str) -> Extracted:
    at = TEXT.index(quote)
    span = Span(field="description", start=at, end=at + len(quote), text=quote)
    return Extracted(name=name, raw_value=value, raw_unit="мм", source=span, method="llm")


def _unread(quote: str) -> Span:
    at = TEXT.index(quote)
    return Span(field="description", start=at, end=at + len(quote), text=quote)


def test_drop_removes_and_move_goes_to_a_free_field() -> None:
    doubt = _llm("Толщина", "40", "Ширина 40 мм")
    dropped, _ = apply_review(
        Review(checks=[Check(field="Толщина", verdict="drop")]), [doubt], SPECS, []
    )
    assert dropped == []
    moved, _ = apply_review(
        Review(checks=[Check(field="Толщина", verdict="move", to="Ширина", reason="подпись")]),
        [doubt],
        SPECS,
        [],
    )
    assert [e.name for e in moved] == ["Ширина"] and "перенесла" in (moved[0].note or "")


def test_keep_marks_checked() -> None:
    kept, _ = apply_review(
        Review(checks=[Check(field="Ширина", verdict="keep", reason="ok")]),
        [_llm("Ширина", "40", "Ширина 40 мм")],
        SPECS,
        [],
    )
    assert kept[0].checked


def test_place_suggests_field_and_shows_the_list_when_value_is_not_there() -> None:
    piece = _unread("Материал корпуса: глина")
    answer = Review(places=[Place(quote=piece.text, field="Вид материала корпуса", value="глина")])
    _, [s] = apply_review(answer, [], SPECS, [piece])
    assert s.field == "Вид материала корпуса" and s.value == "глина"
    assert not s.in_list and "МДФ" in s.allowed


def test_invented_place_value_is_rejected() -> None:
    piece = _unread("Материал корпуса: глина")
    answer = Review(places=[Place(quote=piece.text, field="Вид материала корпуса", value="МДФ")])
    [s] = apply_review(answer, [], SPECS, [piece])[1]
    assert s.value == "глина" and not s.in_list  # «МДФ» не о глине: вместо него слова текста
    answer = Review(places=[Place(quote=piece.text, field="Вид материала корпуса", value="бетон")])
    assert apply_review(answer, [], SPECS, [piece])[1] == []
