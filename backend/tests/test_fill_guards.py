"""Одно слово — не в десяток полей; обязательное поле из подписи-синонима; ноль у величины."""

from app.card_fill import _size_issues
from app.catalog import CharacteristicSpec
from app.pipeline import _from_kin_fields, _one_span_few_fields
from app.schemas import CharacteristicOut, Span
from app.session import Session


def _char(name: str, value: str | None, at: int = 0, required: bool = False, unit=None):  # type: ignore[no-untyped-def]
    return CharacteristicOut(
        name=name,
        value=value,
        unit=unit,
        source=Span(field="description", start=at, end=at + 4, text="ЛДСП") if value else None,
        status="filled" if value else "not_found",
        required=required,
        method="rule" if value else None,
    )


def test_one_word_keeps_required_and_exact_label_only() -> None:
    session = Session.new("Тумба", "материал ЛДСП, цвет белый")
    at = session.description.index("ЛДСП")
    chars = [
        _char("Вид материала корпуса", "ЛДСП", at, required=True),
        _char("Вид материала полок", "ЛДСП", at),
        _char("Требования к фактуре материала", "ЛДСП", at),
        _char("Материал", "ЛДСП", at),
    ]
    kept = {c.name for c in _one_span_few_fields(chars, session) if c.value}
    assert kept == {"Вид материала корпуса", "Материал"}


def test_required_field_from_kin_label() -> None:
    spec = CharacteristicSpec(
        name="Максимальное количество оборотов", kind="quantitative", unit="об/мин", required=True
    )
    empty = _char("Максимальное количество оборотов", None, required=True)
    first = _char("Максимальные обороты на 1ой скорости", "350", 0, unit="об/мин")
    second = _char("Максимальные обороты на 2ой скорости", "1300", 10, unit="об/мин")
    [out] = _from_kin_fields([empty], [first, second], [spec])
    assert out.value == "1300" and out.note and "2ой скорости" in out.note


def test_zero_torque_is_flagged() -> None:
    [issue] = _size_issues([_char("Максимальный крутящий момент (Нм)", "0")])
    assert "ноль" in issue.message
