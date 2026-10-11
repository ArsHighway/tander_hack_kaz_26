"""Характеристики, вписанные в наименование, а не вынесенные в описание."""

from app.catalog import CharacteristicSpec
from app.extract import extract_rules

RAM = CharacteristicSpec(
    name="Объем оперативной памяти",
    kind="quantitative",
    unit="ГБ",
    values=["8", "16", "32"],
)
DISK = CharacteristicSpec(
    name="Объем накопителя",
    kind="quantitative",
    unit="ГБ",
    values=["256", "512", "1024"],
)
THICK = CharacteristicSpec(
    name="Толщина", kind="quantitative", unit="мкм", range={"min": 8, "max": 120}
)


def _by_name(items: list) -> dict:
    return {item.name: item for item in items}


def test_name_without_characteristics_stays_whole() -> None:
    assert extract_rules("Ноутбук Lenovo ThinkPad T14", "", [RAM, DISK]) == []


def test_one_characteristic_inside_the_name() -> None:
    got = _by_name(extract_rules("Ноутбук Lenovo ThinkPad T14, ОЗУ 16 ГБ", "", [RAM, DISK]))
    assert got["Объем оперативной памяти"].raw_value == "16"
    assert "Объем накопителя" not in got


def test_several_characteristics_inside_the_name() -> None:
    got = _by_name(
        extract_rules("Ноутбук Lenovo, ОЗУ 16 ГБ, SSD 512 ГБ, Intel Core i5", "", [RAM, DISK])
    )
    assert got["Объем оперативной памяти"].raw_value == "16"
    assert got["Объем накопителя"].raw_value == "512"


def test_separators_do_not_hide_a_characteristic() -> None:
    names = [
        "Ноутбук; оперативная память: 16 ГБ",
        "Ноутбук (оперативной памяти — 16гб)",
        "Ноутбук — Оперативная память 16 ГБ",
        "Ноутбук, оперативная память - 16 ГБ",
    ]
    for name in names:
        got = _by_name(extract_rules(name, "", [RAM, DISK]))
        assert got.get("Объем оперативной памяти") is not None, name
        assert got["Объем оперативной памяти"].raw_value == "16"


def test_case_and_whitespace() -> None:
    got = _by_name(extract_rules("  НОУТБУК,   озу    16   гб  ", "", [RAM, DISK]))
    assert got["Объем оперативной памяти"].raw_value == "16"
    assert got["Объем оперативной памяти"].raw_unit == "ГБ"


def test_minor_typo_in_the_label() -> None:
    got = _by_name(extract_rules("Ноутбук, оперативнй памяти 16 гб", "", [RAM, DISK]))
    assert got["Объем оперативной памяти"].raw_value == "16"


def test_abbreviation_and_equivalent_wording() -> None:
    for name in ("Ноутбук ОЗУ 16ГБ", "Ноутбук RAM 16 GB", "Ноутбук, оперативка 16 гб"):
        got = _by_name(extract_rules(name, "", [RAM, DISK]))
        assert got.get("Объем оперативной памяти") is not None, name
        assert got["Объем оперативной памяти"].raw_value == "16"


def test_ambiguous_number_is_not_extracted() -> None:
    # Две характеристики одинаково близки к «16 ГБ» — число остаётся в наименовании.
    other = CharacteristicSpec(
        name="Объем памяти", kind="quantitative", unit="ГБ", values=["8", "16", "32"]
    )
    assert extract_rules("Ноутбук Lenovo 16 ГБ", "", [RAM, other]) == []


def test_unlisted_value_is_not_assigned_to_another_characteristic() -> None:
    # 64 ГБ нет в перечне памяти и не объём накопителя: вторая характеристика не трогается.
    got = _by_name(extract_rules("Ноутбук, ОЗУ 64 ГБ", "", [RAM, DISK]))
    assert got["Объем оперативной памяти"].raw_value == "64"
    assert "Объем накопителя" not in got


def test_labelled_thickness_in_the_name_still_works() -> None:
    got = _by_name(extract_rules("Мешки для мусора, толщина 30 мкм", "", [THICK, RAM]))
    assert got["Толщина"].raw_value == "30"
