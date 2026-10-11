import pytest

from app.catalog.model import Position
from app.tools.build_ktru_catalog import in_hack_scope


def _position(code: str, name: str) -> Position:
    return Position(code=code, name=name, okpd2=code.split("-")[0], characteristics=[], source="x")


def test_hack_scope_keeps_the_three_categories_without_services() -> None:
    positions = [
        _position("31.01.12.160-00000001", "Стул на деревянном каркасе"),
        _position("14.13.10.000-00000002", "Куртка зимняя из текстильных материалов"),
        _position("28.24.11.000-00000003", "Шуруповерт"),
        _position("31.09.91.000-00000004", "Услуги по отделке новой мебели"),
        _position("32.50.50.190-00001562", "Тумбочка прикроватная"),  # медицинская мебель
        _position("17.12.14.129-00000017", "Бумага для офисной техники белая"),
    ]
    kept = [p.code for p in in_hack_scope(positions)]
    assert kept == ["31.01.12.160-00000001", "14.13.10.000-00000002", "28.24.11.000-00000003"]


def test_hack_scope_without_hackathon_positions_fails_loudly() -> None:
    with pytest.raises(SystemExit):
        in_hack_scope([_position("17.12.14.129-00000017", "Бумага для офисной техники белая")])
