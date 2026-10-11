from pathlib import Path

from app.catalog import Catalog
from app.catalog.ktru_card import bucket, parse_card
from app.values import fit_value

FIXTURE = Path(__file__).parent / "fixtures" / "ktru" / "17.12.14.121-00000002.html"


def test_bucket_turns_eis_range_into_catalog_value() -> None:
    assert bucket("≥ 600 и < 800") == ">=600 ... <800"
    assert bucket("≥ 900 и ≤ 1000") == ">=900 ... <=1000"
    assert bucket("≥ 50") == ">=50"
    assert bucket("Линованная") == "Линованная"


def test_parse_card_reads_header_and_characteristics() -> None:
    pos = parse_card("17.12.14.121-00000002", FIXTURE.read_text(encoding="utf-8"))
    assert (pos.code, pos.name, pos.okpd2, pos.unit) == (
        "17.12.14.121-00000002", "Бумага для флипчарта", "17.12.14.121", "Упаковка",
    )  # fmt: skip
    specs = {s.name: s for s in pos.characteristics}
    assert set(specs) == {
        "Вид бумаги",
        "Длина листа",
        "Количество листов в упаковке",
        "Масса бумаги площадью 1м2",
        "Ширина листа",
    }
    assert specs["Вид бумаги"].kind == "qualitative"
    assert specs["Вид бумаги"].values == ["Линованная", "Нелинованная"]
    assert not specs["Вид бумаги"].required
    mass = specs["Масса бумаги площадью 1м2"]
    assert mass.required
    assert mass.kind == "quantitative"
    # «Масса бумаги площадью 1 м²» в граммах — это г/м²
    assert mass.unit == "г/м2"
    assert mass.values == [">=80 ... <90", ">=90"]
    assert specs["Длина листа"].unit == "мм"
    # перечень корзин понимает подгонка значений: 85 г → «>=80 ... <90»
    assert fit_value("85", "г", mass).value == ">=80 ... <90"
    Catalog([pos], version="test")
