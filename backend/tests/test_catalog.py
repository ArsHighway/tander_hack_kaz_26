import re
from pathlib import Path

import pytest

from app.catalog import Catalog, CatalogError, load_catalog_csv, load_catalog_json

FAKE = Path(__file__).parent / "fixtures" / "fake"
WHITE_PAPER = "17.12.14.129-00000101"


def test_json_fixture_loads_all_positions() -> None:
    assert len(load_catalog_json(FAKE / "ktru.json")) == 6


def test_position_keeps_characteristic_specs() -> None:
    catalog = Catalog.load([FAKE / "ktru.json"])
    paper = catalog.get(WHITE_PAPER)
    assert paper is not None
    assert len(paper.characteristics) == 5
    mass = next(c for c in paper.characteristics if c.name == "Масса бумаги площадью 1 м2")
    assert mass.values == [">=80 ... <90", ">=90 ... <100", ">=100"]
    assert mass.unit == "г/м2"
    assert mass.kind == "quantitative"
    assert mass.required is True


def test_range_spec_from_json() -> None:
    paper = Catalog.load([FAKE / "ktru.json"]).get(WHITE_PAPER)
    assert paper is not None
    whiteness = next(c for c in paper.characteristics if c.name == "Белизна по CIE")
    assert whiteness.range is not None
    assert (whiteness.range.min, whiteness.range.max) == (146, 175)
    assert whiteness.values == []


def _shape(positions: list) -> dict:
    return {
        p.code: (
            p.name,
            p.okpd2,
            [(c.name, c.kind, c.unit, c.values, c.range, c.required) for c in p.characteristics],
        )
        for p in positions
    }


def test_csv_and_json_fixtures_describe_the_same_catalog() -> None:
    assert _shape(load_catalog_csv(FAKE / "ktru_characteristics.csv")) == _shape(
        load_catalog_json(FAKE / "ktru.json")
    )


def test_missing_file_is_a_loud_error() -> None:
    with pytest.raises(CatalogError, match=r"nope\.json"):
        Catalog.load([FAKE / "nope.json"])


def test_no_paths_is_an_error() -> None:
    with pytest.raises(CatalogError):
        Catalog.load([])


def test_duplicate_code_across_files_is_an_error() -> None:
    with pytest.raises(CatalogError, match=re.escape(WHITE_PAPER)):
        Catalog.load([FAKE / "ktru.json", FAKE / "ktru_characteristics.csv"])


def test_unknown_code_is_none() -> None:
    assert Catalog.load([FAKE / "ktru.json"]).get("00.00.00.000-00000000") is None


def test_version_names_the_sources() -> None:
    catalog = Catalog.load([FAKE / "ktru.json"])
    assert "ktru.json" in catalog.version
    assert len(catalog) == 6


def test_clean_checkout_has_a_catalog_by_default() -> None:
    # Без CATALOG_PATHS модуль берёт демо-справочник из репозитория: чистый клон стартует.
    from app.config import Settings

    files = Settings(_env_file=None, catalog_paths="").catalog_files()  # type: ignore[call-arg]
    assert files and all(f.is_file() for f in files)
    assert len(Catalog.load(files)) >= 17
