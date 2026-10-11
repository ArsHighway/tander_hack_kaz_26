"""Значения похожих СТЕ: чего нет в тексте, подсказывают СТЕ той же категории."""

import asyncio
import json
from pathlib import Path

from app.catalog import Catalog, CharacteristicSpec
from app.glossary import Glossary
from app.pipeline import AutofillIn, Deps, autofill
from app.schemas import Span
from app.search import build_bm25
from app.search.exemplars import ExemplarIndex
from app.search.similar import Similar, SimilarValues, build_values, vote
from app.session import SessionStore
from tests.fakes import FakeLLM, FakeRedis

CATALOG = Catalog.load([Path(__file__).parent.parent / "app" / "catalog" / "demo" / "demo.json"])


def q(name: str, unit: str, values: list[str] | None = None) -> CharacteristicSpec:
    return CharacteristicSpec(name=name, kind="quantitative", unit=unit, values=values or [])


def t(name: str, values: list[str], synonyms: list[str] | None = None) -> CharacteristicSpec:
    return CharacteristicSpec(name=name, kind="qualitative", values=values, synonyms=synonyms or [])


def test_value_most_neighbours_agree_on_is_suggested() -> None:
    colour = t("Цвет", ["Белый", "Черный"])
    # из 6 похожих поле заполнено у 4: «как у 3 из 4», а не «из 6» — доля среди заполнивших
    neighbours = [{"Цвет": ("белый", None)}] * 3 + [{"Цвет": ("черный", None)}] + [{}] * 2
    got = vote([colour], neighbours, skip=set())
    assert (got["Цвет"].value, got["Цвет"].votes, got["Цвет"].of) == ("Белый", 3, 4)


def test_split_or_lonely_vote_is_not_a_suggestion() -> None:
    colour = t("Цвет", ["Белый", "Черный"])
    split = [{"Цвет": ("белый", None)}] * 2 + [{"Цвет": ("черный", None)}] * 2  # 50 %
    lonely = [{"Цвет": ("белый", None)}, {}]  # один голос
    assert vote([colour], split, skip=set()) == {}
    assert vote([colour], lonely, skip=set()) == {}


def test_neighbour_under_a_synonym_votes_and_value_is_converted() -> None:
    # Похожая СТЕ пишет «Вес, кг», у позиции «Масса нетто, г» с синонимом «Вес»
    mass = CharacteristicSpec(name="Масса нетто", kind="quantitative", unit="г", synonyms=["Вес"])
    neighbours = [{"Вес": ("0,5", "кг")}, {"Вес": ("0.5", "кг")}, {"Масса нетто": ("500", "г")}]
    got = vote([mass], neighbours, skip=set())
    assert (got["Масса нетто"].value, got["Масса нетто"].unit) == ("500", "г")


def test_value_outside_the_list_and_fields_from_text_are_left_alone() -> None:
    colour = t("Цвет", ["Белый", "Черный"])
    size = t("Размер", ["S", "M"])
    neighbours = [{"Цвет": ("зелёный", None), "Размер": ("M", None)}] * 3
    assert vote([colour, size], neighbours, skip={"размер"}) == {}  # «зелёного» нет в перечне


def test_general_fields_are_not_copied_from_neighbours() -> None:
    maker = CharacteristicSpec(name="Производитель", kind="qualitative", group="general")
    neighbours = [{"Производитель": ("ООО Ромашка", None)}] * 5
    assert vote([maker], neighbours, skip=set()) == {}


def test_values_index_reads_neighbours_by_id(tmp_path: Path) -> None:
    ste = tmp_path / "ste.jsonl"
    rows = [
        {"id": 1, "name": "Перчатки", "characteristics": [{"name": "Размер", "value": "M"}]},
        {
            "id": 2,
            "name": "Перчатки",
            "characteristics": [{"name": "Вес", "value": "5", "unit": "г"}],
        },
        {"id": 3, "name": "Лишняя", "characteristics": [{"name": "Цвет", "value": "белый"}]},
    ]
    ste.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    out = tmp_path / "values.sqlite"
    assert build_values(ste, {1, 2}, out) == 2
    index = SimilarValues(out)
    assert index.get([2, 1, 3]) == [{"Вес": ("5", "г")}, {"Размер": ("M", None)}]


def test_decided_position_gets_values_of_similar_ste(tmp_path: Path) -> None:
    position = CATALOG.get("22.19.60.119-00000301")  # перчатки смотровые нитриловые
    assert position is not None
    spec = next(c for c in position.characteristics if len(c.values) >= 2 and c.group != "general")
    value = spec.values[-1]
    names = [f"Перчатки смотровые нитриловые вариант {i}" for i in range(4)]
    exemplars = ExemplarIndex([(position.code, n) for n in names], ids=[11, 12, 13, 14])
    ste = tmp_path / "ste.jsonl"
    rows = [
        {"id": i, "name": n, "characteristics": [{"name": spec.name, "value": value}]}
        for i, n in zip([11, 12, 13, 14], names, strict=True)
    ]
    ste.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    build_values(ste, {11, 12, 13, 14}, tmp_path / "values.sqlite")
    deps = Deps(
        catalog=CATALOG,
        bm25=build_bm25(CATALOG),
        vectors=None,
        llm=FakeLLM([]),  # type: ignore[arg-type]
        glossary=Glossary([]),
        sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.2,
        exemplars=exemplars,
        similar=SimilarValues(tmp_path / "values.sqlite"),
        llm_paused_until=float("inf"),  # модель на паузе: значение только от похожих
    )
    out = asyncio.run(autofill(AutofillIn(name="Перчатки смотровые нитриловые"), deps))
    field = next(c for c in out.characteristics if c.name == spec.name)
    assert (field.value, field.method, field.source) == (value, "similar", None)
    assert field.note == "как у 4 из 4 похожих СТЕ портала с этим полем"


def test_hint_said_by_text_is_a_text_value_with_its_fragment() -> None:
    # Подсказка, которую называет текст («латунь» у «Материала»), — значение
    # из текста с фрагментом, а не предложение «Принять».
    from app.card_fill import HINT_IN_TEXT, _characteristics

    position = CATALOG.get("22.19.60.119-00000301")
    assert position is not None
    spec = next(c for c in position.characteristics if len(c.values) >= 2 and c.group != "general")
    other = next(
        c for c in position.characteristics if c.name != spec.name and c.group != "general"
    )
    span = Span(field="name", start=0, end=8, text="Перчатки")
    similar = {spec.name: Similar(value=spec.values[-1], votes=3, of=4, source=span)}
    twin = {other.name: Similar(value="1", votes=1, of=1, ids=[5001], source=span)}
    got = {c.name: c for c in _characteristics(position, [], {}, similar=similar, twin=twin)}
    for name, value in ((spec.name, spec.values[-1]), (other.name, "1")):
        field = got[name]
        assert (field.value, field.method, field.source) == (value, "rule", span)
        assert field.note is not None and field.note.startswith(HINT_IN_TEXT)
    assert got[spec.name].note == f"{HINT_IN_TEXT}; как у 3 из 4 похожих СТЕ портала"


def test_number_that_became_a_field_value_gets_no_not_taken_note() -> None:
    # «Молоток слесарный 500 г»: «Вес» из текста (подсказка, которую назвал текст) — заметки
    # «число 500 похоже на «Вес бойка», но не взято» быть не должно
    from app.card_fill import CharacteristicOut, _number_issues
    from app.catalog import NumRange, Position
    from app.session import Session

    head = CharacteristicSpec(
        name="Вес бойка", kind="quantitative", unit="г", range=NumRange(min=100, max=2000)
    )
    weight = CharacteristicSpec(name="Вес", kind="quantitative", unit="г")
    position = Position(
        code="01", name="Молотки", okpd2="", characteristics=[weight, head], source="t"
    )
    session = Session(id="s", name="Молоток слесарный 500 г", description="")
    assert [i.message for i in _number_issues(session, [position], [])] == [
        "Число «500» похоже на «Вес бойка», но не взято."
    ]
    said = CharacteristicOut(
        name="Вес", value="500", unit="г", status="filled", required=False, method="rule",
        source=Span(field="name", start=18, end=23, text="500 г"),
    )  # fmt: skip
    assert _number_issues(session, [position], [said]) == []


def test_batch_row_counts_hints_apart_and_says_so_in_the_notes(tmp_path: Path) -> None:
    # В пакете «Заполнено» не должно выдавать подсказки похожих СТЕ за значения из текста
    from app.batch import BatchRow, _append_rows, process_row

    position = CATALOG.get("22.19.60.119-00000301")
    assert position is not None
    names = [f"Перчатки смотровые нитриловые вариант {i}" for i in range(4)]
    ste = tmp_path / "ste.jsonl"
    rows = [
        {"id": i, "name": n, "characteristics": [{"name": "Размер", "value": "XL"}]}
        for i, n in zip([11, 12, 13, 14], names, strict=True)
    ]
    ste.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    build_values(ste, {11, 12, 13, 14}, tmp_path / "values.sqlite")
    deps = Deps(
        catalog=CATALOG,
        bm25=build_bm25(CATALOG),
        vectors=None,
        llm=FakeLLM([]),  # type: ignore[arg-type]
        glossary=Glossary([]),
        sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.2,
        exemplars=ExemplarIndex([(position.code, n) for n in names], ids=[11, 12, 13, 14]),
        similar=SimilarValues(tmp_path / "values.sqlite"),
        llm_paused_until=float("inf"),
    )
    row = BatchRow(row=1, name="Перчатки смотровые нитриловые", raw={"Наименование": "x"})
    result = asyncio.run(process_row(row, deps))
    assert (result.filled, result.hinted) == (2, 1)  # «Нитрил» из текста и «XL» от похожих

    class Sheet:
        def __init__(self) -> None:
            self.rows: list[list[object]] = []

        def append(self, values: list[object]) -> None:
            self.rows.append(values)

    sheet = Sheet()
    _append_rows(sheet, ["Наименование"], [row], {1: result})
    assert "подсказки СТЕ портала: 1" in str(sheet.rows[0])
