"""Допустимые значения — из спецификации выбранной позиции, не из всего справочника."""

from app.card_fill import _characteristics
from app.catalog import CharacteristicSpec, NumRange, Position


def _position(*specs: CharacteristicSpec) -> Position:
    return Position(code="x", name="Бумага", okpd2="17", characteristics=list(specs), source="t")


def _by_name(position: Position) -> dict:
    return {c.name: c for c in _characteristics(position, [], {})}


def test_qualitative_characteristic_returns_its_allowed_values() -> None:
    spec = CharacteristicSpec(
        name="Формат", kind="qualitative", values=["A3", "A4", "A5"], required=True
    )
    got = _by_name(_position(spec))["Формат"]
    assert got.values == ["A3", "A4", "A5"]
    assert got.range is None
    assert got.value is None
    assert got.status == "not_found"
    assert got.required is True
    assert got.unit is None


def test_quantitative_characteristic_returns_its_range() -> None:
    spec = CharacteristicSpec(
        name="Белизна", kind="quantitative", unit="%", range=NumRange(min=90, max=175)
    )
    got = _by_name(_position(spec))["Белизна"]
    assert got.values == []
    assert got.range == NumRange(min=90, max=175)
    assert got.unit == "%"
    assert got.status == "not_found"
    assert got.value is None


def test_characteristic_without_constraints_stays_valid() -> None:
    spec = CharacteristicSpec(name="Комментарий", kind="qualitative")
    empty_range = CharacteristicSpec(
        name="Ширина", kind="quantitative", unit="мм", range=NumRange()
    )
    got = _by_name(_position(spec, empty_range))
    assert got["Комментарий"].values == []
    assert got["Комментарий"].range is None
    assert got["Комментарий"].status == "not_found"
    assert got["Ширина"].values == []
    assert got["Ширина"].range is None
    assert got["Ширина"].value is None
    assert got["Ширина"].unit == "мм"
    assert got["Ширина"].status == "not_found"


def test_duplicate_allowed_values_are_not_repeated_and_catalog_is_unchanged() -> None:
    spec = CharacteristicSpec(
        name="Формат", kind="qualitative", values=["A4", "A4", " ", "A3", "A5", "A3"]
    )
    original = list(spec.values)
    got = _by_name(_position(spec))["Формат"]
    assert got.values == ["A4", "A3", "A5"]
    assert spec.values == original


def test_constraints_belong_to_this_position_only() -> None:
    white = CharacteristicSpec(name="Формат", kind="qualitative", values=["A3", "A4", "A5"])
    color = CharacteristicSpec(name="Формат", kind="qualitative", values=["A3", "A4"])
    assert _by_name(_position(white))["Формат"].values == ["A3", "A4", "A5"]
    assert _by_name(_position(color))["Формат"].values == ["A3", "A4"]
