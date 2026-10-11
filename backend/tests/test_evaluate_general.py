import json
from pathlib import Path

from app.catalog import Catalog, CharacteristicSpec, Position
from app.tools.evaluate_general import portal_truth, score

MAKER = "Производитель"
COUNTRY = "Страна происхождения"


def _catalog() -> Catalog:
    p = Position(
        code="P",
        name="Блокнот",
        okpd2="17.23.13",
        characteristics=[
            CharacteristicSpec(
                name=MAKER, kind="qualitative", values=["BRAUBERG", "Staff"], group="general"
            ),
            CharacteristicSpec(
                name=COUNTRY, kind="qualitative", values=["Китай", "Россия"], group="general"
            ),
            CharacteristicSpec(name="Формат", kind="qualitative", values=["А5", "А6"]),
        ],
        source="t",
    )
    # своя характеристика категории с тем же именем — не поле «Общих сведений»: не считается
    own = Position(
        code="Q",
        name="Трактор",
        okpd2="28.30.2",
        characteristics=[CharacteristicSpec(name=COUNTRY, kind="qualitative", values=["Беларусь"])],
        source="t",
    )
    return Catalog([p, own], "t")


def test_score_counts_right_wrong_and_unverifiable_general_values() -> None:
    cases = [
        {"id": 1, "code": "P", "name": "Блокнот BRAUBERG А6"},
        {"id": 2, "code": "P", "name": "Блокнот Staff А5"},
        {"id": 3, "code": "P", "name": "Блокнот А5, Китай"},
        {"id": 4, "code": "P", "name": "Блокнот А5"},
        {"id": 5, "code": "нет такого", "name": "Блокнот BRAUBERG"},
        {"id": 6, "code": "P", "name": "Блокнот BRAUBERG А6"},  # карточки на портале нет
        {"id": 7, "code": "Q", "name": "Трактор Беларусь"},
    ]
    truth = {
        1: {MAKER: "Brauberg", COUNTRY: None},
        2: {MAKER: "САМСОН-ОПТ", COUNTRY: None},
        3: {MAKER: None, COUNTRY: None},
        4: {MAKER: "BRAUBERG", COUNTRY: "Китай"},
        5: {MAKER: "BRAUBERG", COUNTRY: None},
        7: {MAKER: None, COUNTRY: "Китай"},
    }
    out = score(cases, truth, _catalog())
    maker, country = out[MAKER], out[COUNTRY]
    # верно — без учёта регистра; «Staff» при юрлице САМСОН-ОПТ — неверно, с примером
    assert (maker.extracted, maker.right, maker.wrong, maker.unknown) == (2, 1, 1, 0)
    assert maker.precision == 0.5
    assert maker.examples == [
        {"name": "Блокнот Staff А5", "field": "name", "found": "Staff", "portal": "САМСОН-ОПТ"}
    ]
    # на портале страны нет — проверить нечем: ни верно, ни неверно
    assert (country.extracted, country.right, country.wrong, country.unknown) == (1, 0, 0, 1)
    assert country.precision is None


def test_description_label_is_counted_separately() -> None:
    cases = [{"id": 1, "code": "P", "name": "Блокнот А5", "description": "Производитель: Staff"}]
    out = score(cases, {1: {MAKER: "Staff", COUNTRY: None}}, _catalog())
    assert (out[MAKER].right, out[MAKER].from_description) == (1, 1)


def test_portal_truth_reads_only_cards_of_the_sample(tmp_path: Path) -> None:
    ste = tmp_path / "ste.jsonl"
    rows = [
        {"id": 1, "name": "a", "manufacturer": "ООО Ромашка", "country": "РОССИЯ"},
        {"id": 2, "name": "b", "manufacturer": "Staff", "country": None},
    ]
    ste.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    assert portal_truth(ste, {1}) == {1: {MAKER: "Ромашка", COUNTRY: "Россия"}}
