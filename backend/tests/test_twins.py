"""Такая СТЕ уже есть на портале: двойник по наименованию или модели."""

import asyncio
import json
from pathlib import Path

from app.catalog import Catalog, CharacteristicSpec
from app.glossary import Glossary
from app.pipeline import RULE_DUPLICATE, AutofillIn, Deps, autofill
from app.search import build_bm25
from app.search.twins import (
    TwinIndex,
    TwinRef,
    build_twins,
    model_keys,
    name_key,
    twin_category,
    twin_values,
)
from app.session import SessionStore
from tests.fakes import FakeLLM, FakeRedis


def _ste(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "ste.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return path


def _row(i: int, name: str, model: str = "", directory: int = 7, **chars: str) -> dict:
    return {
        "id": i,
        "directory_id": directory,
        "name": name,
        "model": model,
        "manufacturer": "",
        "characteristics": [{"name": k, "value": v} for k, v in chars.items()],
    }


def test_name_key_ignores_case_spaces_and_punctuation() -> None:
    assert name_key("Бумага  «Снегурочка» А4, 500 л.") == name_key("бумага снегурочка а4 500 л")


def test_model_keys_are_codes_with_letters_and_digits_not_sizes() -> None:
    keys = model_keys("Стол инструментальный AR-34.2 800х500х500 мм, 80г/м2, А4, 500 л, 220В")
    assert keys == {"ar342"}  # размеры, числа с единицей («80г/м2», «220В»), «А4» — не модели


def test_twin_is_found_by_name_and_by_model(tmp_path: Path) -> None:
    rows = [
        _row(1, "Стол инструментальный AR-34.2 800х500х500 мм", "AR-34.2", Цвет="бело-зеленый"),
        _row(2, "Стол инструментальный AR-34.2 800х500х500 мм", "AR-34.2", Цвет="серый"),
        _row(3, "Тележка для мячей VBB900", "VBB900", directory=9),
    ]
    out = tmp_path / "twins.sqlite"
    assert build_twins(_ste(tmp_path, rows), out) == 3
    index = TwinIndex(out)
    by_name = index.find("Стол инструментальный AR-34.2 800х500х500 мм")
    assert sorted(t.id for t in by_name) == [1, 2] and {t.by for t in by_name} == {"name"}
    assert by_name[0].chars["Цвет"][0] in ("бело-зеленый", "серый")
    by_model = index.find("Тележка Allsix VBB900 для волейбольных мячей")
    assert [(t.id, t.by, t.directory_id) for t in by_model] == [(3, "model", 9)]
    assert index.find("Совсем другой товар XY") == []


def test_cards_of_the_evaluation_sample_are_not_their_own_twins(tmp_path: Path) -> None:
    rows = [_row(1, "Стол AR-34.2", "AR-34.2"), _row(2, "Стол AR-34.2", "AR-34.2")]
    out = tmp_path / "twins.sqlite"
    build_twins(_ste(tmp_path, rows), out)
    assert [t.id for t in TwinIndex(out, exclude={1}).find("Стол AR-34.2")] == [2]


def _ref(i: int, code: str | None, by: str = "name") -> TwinRef:
    return TwinRef(id=i, name=f"СТЕ {i}", code=code, by=by)  # type: ignore[arg-type]


def test_category_is_what_name_twins_say_almost_unanimously() -> None:
    twins = [_ref(1, "A"), _ref(2, "A"), _ref(3, "A"), _ref(4, "B"), _ref(5, "C", "model")]
    code, group = twin_category(twins, share=0.7) or ("", [])
    assert (code, [t.id for t in group]) == ("A", [1, 2, 3])  # 3 из 4 по наименованию
    split = [_ref(1, "A"), _ref(2, "B"), _ref(3, "C", "model"), _ref(4, "C", "model")]
    assert twin_category(split, share=0.7) is None  # спорят — модель не выручает


def test_model_twins_never_decide_and_foreign_category_votes_against() -> None:
    # модель из наименования бывает у чужого товара: на выборке 59 неверных решений из 82
    assert twin_category([_ref(7, "C", "model"), _ref(8, "C", "model")], 0.7) is None
    # двойник из категории не из справочника (code=None) — голос против: 1 из 2 < 70 %
    assert twin_category([_ref(1, "A"), _ref(2, None)], share=0.7) is None


def test_one_twin_is_enough_for_a_value_but_disagreeing_twins_are_not() -> None:
    colour = CharacteristicSpec(name="Цвет", kind="qualitative", values=["Серый", "Белый"])
    maker = CharacteristicSpec(name="Производитель", kind="qualitative", group="general")
    twin = {"Цвет": ("серый", None), "Производитель": ("ООО", None)}
    one = twin_values([colour, maker], [twin], set(), ids=[47536445])
    assert [(k, v.value, v.of, v.ids) for k, v in one.items()] == [("Цвет", "Серый", 1, [47536445])]
    split = [{"Цвет": ("серый", None)}, {"Цвет": ("белый", None)}]
    assert twin_values([colour], split, set()) == {}
    assert twin_values([colour], [{"Цвет": ("серый", None)}], {"цвет"}) == {}  # уже из текста


GLOVES = "22.19.60.119-00000301"  # перчатки смотровые нитриловые в демо-справочнике


def _deps(tmp_path: Path, rows: list[dict]) -> Deps:
    """Демо-справочник, где у перчаток есть категория портала 777, и индекс двойников из `rows`."""
    base = Catalog.load([Path(__file__).parent.parent / "app" / "catalog" / "demo" / "demo.json"])
    catalog = Catalog(
        [p.model_copy(update={"portal_id": 777}) if p.code == GLOVES else p for p in base.all()],
        version="twins",
    )
    build_twins(_ste(tmp_path, rows), tmp_path / "twins.sqlite")
    return Deps(
        catalog=catalog,
        bm25=build_bm25(catalog),
        vectors=None,
        llm=FakeLLM([]),  # type: ignore[arg-type]
        glossary=Glossary([]),
        sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.2,
        twins=TwinIndex(tmp_path / "twins.sqlite"),
        llm_paused_until=float("inf"),  # модель на паузе: значения только из текста и двойников
    )


def _spec() -> CharacteristicSpec:
    base = Catalog.load([Path(__file__).parent.parent / "app" / "catalog" / "demo" / "demo.json"])
    position = base.get(GLOVES)
    assert position is not None
    return next(c for c in position.characteristics if len(c.values) >= 2 and c.group != "general")


def test_twin_on_the_portal_decides_category_and_fills_what_text_lacks(tmp_path: Path) -> None:
    spec = _spec()
    name = "Benovy MultiColor Pro 100 шт"  # бренд и модель: по справочнику категорию не узнать
    deps = _deps(tmp_path, [_row(5001, name, directory=777, **{spec.name: spec.values[-1]})])
    out = asyncio.run(autofill(AutofillIn(name=name), deps))
    assert (out.ktru.final_code, out.ktru.question) == (GLOVES, None)
    field = next(c for c in out.characteristics if c.name == spec.name)
    assert (field.value, field.method) == (spec.values[-1], "twin")
    assert field.note == "как у такой же СТЕ №5001 на портале"
    assert [(t.id, t.url, t.by, t.same_category) for t in out.twins] == [
        (5001, "https://zakupki.mos.ru/sku/view/5001", "name", True)
    ]
    assert (out.issues[0].rule, out.issues[0].severity) == (RULE_DUPLICATE, "warn")


def test_supplier_text_beats_the_twin(tmp_path: Path) -> None:
    spec = _spec()
    name = "Benovy MultiColor Pro 100 шт"
    deps = _deps(tmp_path, [_row(5001, name, directory=777, **{spec.name: spec.values[-1]})])
    text = AutofillIn(name=name, description=f"{spec.name}: {spec.values[0]}")
    out = asyncio.run(autofill(text, deps))
    field = next(c for c in out.characteristics if c.name == spec.name)
    assert (field.value, field.method) == (spec.values[0], "rule")


def test_batch_row_says_such_ste_exists_and_counts_twin_values_as_hints(tmp_path: Path) -> None:
    from app.batch import BatchRow, process_row

    spec = _spec()
    name = "Benovy MultiColor Pro 100 шт"
    deps = _deps(tmp_path, [_row(5001, name, directory=777, **{spec.name: spec.values[-1]})])
    result = asyncio.run(process_row(BatchRow(row=1, name=name, raw={"Наименование": name}), deps))
    assert (result.ktru_code, result.hinted) == (GLOVES, 1)
    assert [i.rule for i in result.issues if i.rule == RULE_DUPLICATE] == [RULE_DUPLICATE]


def test_check_tells_the_moderator_such_ste_exists(tmp_path: Path) -> None:
    from app.pipeline import check
    from app.schemas import CardIn

    name = "Benovy MultiColor Pro 100 шт"
    deps = _deps(tmp_path, [_row(5001, name, directory=777)])
    got = check(CardIn(name=name, ktru_code=GLOVES, characteristics=[]), deps)
    assert (got.issues[0].rule, got.issues[0].message) == (
        RULE_DUPLICATE,
        "Такая СТЕ уже есть на портале: №5001",  # наименование то же, что в карточке
    )
    other = check(CardIn(name="Перчатки другие", ktru_code=GLOVES, characteristics=[]), deps)
    assert RULE_DUPLICATE not in [i.rule for i in other.issues]


def test_model_twin_is_listed_but_gives_no_values(tmp_path: Path) -> None:
    # Значение одного двойника по модели верно лишь в 59 % — ниже подсказок похожих СТЕ.
    spec = _spec()
    twin = _row(5002, "Перчатки Benovy другие", "XQ1234", 777, **{spec.name: spec.values[-1]})
    out = asyncio.run(
        autofill(AutofillIn(name="Перчатки смотровые нитриловые XQ1234"), _deps(tmp_path, [twin]))
    )
    assert out.ktru.final_code == GLOVES
    assert [(t.id, t.by, t.same_category) for t in out.twins] == [(5002, "model", True)]
    field = next(c for c in out.characteristics if c.name == spec.name)
    assert (field.value, field.method) == (None, None)
    assert RULE_DUPLICATE not in [i.rule for i in out.issues]  # дубль — по наименованию


def test_fields_the_twin_has_are_not_asked_to_the_model(tmp_path: Path) -> None:
    # Значение такой же СТЕ верно в 94 %: модель о нём не спрашивают — быстрее и без квоты.
    from app.extract import LLMItems

    spec = _spec()
    name = "Benovy MultiColor Pro 100 шт"
    deps = _deps(tmp_path, [_row(5001, name, directory=777, **{spec.name: spec.values[-1]})])
    llm = FakeLLM([LLMItems(items=[])])
    deps.llm, deps.llm_paused_until = llm, 0.0  # type: ignore[assignment]
    out = asyncio.run(autofill(AutofillIn(name=name), deps))
    asked = " ".join(m["content"] for m in llm.calls[0])
    assert spec.name not in asked and "Стерильность" in asked
    field = next(c for c in out.characteristics if c.name == spec.name)
    assert (field.value, field.method) == (spec.values[-1], "twin")


def test_maker_is_what_twins_agree_on_without_legal_form() -> None:
    from app.search.twins import twin_maker

    got = twin_maker([(1, 'ООО "Бенови"'), (2, "Бенови"), (3, "ООО «Бенови»")])
    assert got is not None and (got.value, got.votes, got.of, got.ids) == (
        "Бенови",
        3,
        3,
        [1, 2, 3],
    )
    assert twin_maker([(1, "Бенови"), (2, "Ромашка")]) is None  # спорят — не подсказываем
    assert twin_maker([]) is None


def _with_maker(tmp_path: Path, rows: list[dict]) -> Deps:
    """Как `_deps`, но у перчаток есть поле «Общих сведений» «Производитель»."""
    deps = _deps(tmp_path, rows)
    gloves = deps.catalog.get(GLOVES)
    assert gloves is not None
    maker = CharacteristicSpec(name="Производитель", kind="qualitative", group="general")
    positions = [
        p.model_copy(update={"characteristics": [*p.characteristics, maker]})
        if p.code == GLOVES
        else p
        for p in deps.catalog.all()
    ]
    deps.catalog = Catalog(positions, version="twins-maker")
    deps.bm25 = build_bm25(deps.catalog)
    return deps


def test_maker_of_the_twin_fills_general_field_but_text_wins(tmp_path: Path) -> None:
    # Производитель двойников верен в 91,6 % (страна — в 63,5 %, её не берём).
    name = "Benovy MultiColor Pro 100 шт"
    row = _row(5001, name, directory=777)
    row["manufacturer"] = 'ООО "Бенови"'
    deps = _with_maker(tmp_path, [row])
    out = asyncio.run(autofill(AutofillIn(name=name), deps))
    maker = next(c for c in out.characteristics if c.name == "Производитель")
    assert (maker.value, maker.method, maker.group) == ("Бенови", "twin", "general")
    own = asyncio.run(autofill(AutofillIn(name=name, description="Производитель: Ромашка"), deps))
    maker = next(c for c in own.characteristics if c.name == "Производитель")
    assert (maker.value, maker.method) == ("Ромашка", "rule")


# значение, которое в описании прочитала бы только модель, двойник подменять не должен.
PACK = "Количество пар в упаковке"
PACK_TEXT = "Перчатки Benovy, коробка на полсотни пар (50)"


def _with_llm_reading_pack(tmp_path: Path, rows: list[dict]) -> tuple[Deps, FakeLLM]:
    from app.extract import LLMItems
    from app.extract.model import LLMItem

    deps = _deps(tmp_path, rows)
    reply = LLMItems(items=[LLMItem(name=PACK, value="50", unit="пар", quote="полсотни пар (50)")])
    llm = FakeLLM([reply])
    deps.llm, deps.llm_paused_until = llm, 0.0  # type: ignore[assignment]
    return deps, llm


def test_with_a_description_the_model_reads_it_before_the_twin_is_used(tmp_path: Path) -> None:
    name = "Benovy MultiColor Pro 100 шт"
    deps, llm = _with_llm_reading_pack(tmp_path, [_row(5001, name, directory=777, **{PACK: "100"})])
    out = asyncio.run(autofill(AutofillIn(name=name, description=PACK_TEXT), deps))
    assert PACK in " ".join(m["content"] for call in llm.calls for m in call)
    field = next(c for c in out.characteristics if c.name == PACK)
    assert (field.value, field.method) == ("50", "llm")


def test_thread_and_tyre_sizes_are_not_models() -> None:
    # «М10х50» и «205/55R16» склеивали болты и шины разных производителей.
    assert "m10x50" not in model_keys("Болт М10х50 оцинкованный")
    assert model_keys("Шина 205/55R16 91V летняя") == set()


def test_model_shared_by_many_categories_is_a_feature_not_a_model(tmp_path: Path) -> None:
    rows = [_row(i, f"Товар {i}", "XQ1234", directory=i) for i in (1, 2, 3)]
    rows += [_row(10 + i, f"Картридж {i}", "CE285A", directory=50 + i % 2) for i in range(40)]
    out = tmp_path / "twins.sqlite"
    build_twins(_ste(tmp_path, rows), out)
    index = TwinIndex(out)
    assert index.find("Перчатки XQ1234") == []  # три категории — признак, а не модель
    # 40 СТЕ в двух категориях — модель (раньше отбрасывалась как «у больше 30 СТЕ»)
    assert {t.by for t in index.find("Картридж HP CE285A черный")} == {"model"}


def test_answered_field_gets_no_hint_even_if_answered_none(tmp_path: Path) -> None:
    # ответ «нет такой характеристики» оставлял в поле подсказку.
    from app.card_fill import _characteristics
    from app.narrowing import NONE_VALUE, Known
    from app.search.similar import Similar

    spec = _spec()
    base = Catalog.load([Path(__file__).parent.parent / "app" / "catalog" / "demo" / "demo.json"])
    position = base.get(GLOVES)
    assert position is not None
    hint = {spec.name: Similar(value=spec.values[-1], votes=1, of=1, ids=[5001])}
    known = {spec.name: Known(value=NONE_VALUE, source="answer")}
    field = next(
        f for f in _characteristics(position, [], known, hint, hint) if f.name == spec.name
    )
    assert (field.value, field.method) == (None, None)


def test_generic_name_is_not_one_ste() -> None:
    from app.search.twins import specific_name

    assert not specific_name("Стул")
    assert not specific_name("Кресло офисное")
    assert specific_name("Стул ИЗО черный")
    assert specific_name("Смеситель Grohe 32918000")
