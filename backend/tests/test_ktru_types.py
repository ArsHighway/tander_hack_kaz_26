"""Типы товаров КТРУ как первый этап: сборка справочника типов и путь мастера по нему."""

import asyncio

from app.catalog import Catalog, CharacteristicSpec, Position
from app.catalog.ktru_types import TYPE_SOURCE, UNLINKED_PREFIX, build_types
from app.glossary import Glossary
from app.narrowing import CONFIRM
from app.pipeline import AnswerIn, AutofillIn, Deps, answer, autofill
from app.search import build_bm25
from app.session import SessionStore
from tests.fakes import FakeLLM, FakeRedis

BEDSIDE_1 = "31.09.12.124-00000001"
BEDSIDE_2 = "31.09.12.124-00000002"
OFFICE = "31.01.12.150-00000001"
CATEGORY = "01.16.05.01.02"


def _spec(
    name: str, values: list[str], *, required: bool = False, **kw: object
) -> CharacteristicSpec:
    return CharacteristicSpec(name=name, kind="qualitative", values=values, required=required, **kw)  # type: ignore[arg-type]


def _ktru() -> list[Position]:
    frame = ["Деревянный", "Металлический"]
    return [
        Position(
            code=BEDSIDE_1,
            name="Тумба прикроватная",
            okpd2="31.09.12.124",
            characteristics=[
                _spec("Тип каркаса", frame, required=True),
                _spec("Ширина тумбы", [">=40 ... <50"], required=True, unit="см"),
            ],
            source="ktru-xml",
        ),
        Position(
            code=BEDSIDE_2,
            name="Тумба прикроватная",
            okpd2="31.09.12.124",
            characteristics=[
                _spec("Тип каркаса", ["Деревянный", "Пластиковый"], required=True),
                _spec("Ширина тумбы", [">=50 ... <60"], required=True, unit="см"),
                _spec("Наличие колес", ["Да", "Нет"]),
            ],
            source="ktru-xml",
        ),
        Position(
            code=OFFICE,
            name="Тумба офисная деревянная",
            okpd2="31.01.12.150",
            characteristics=[_spec("Вид тумбы", ["Для документов"], required=True)],
            source="ktru-xml",
        ),
        Position(
            code="14.12.30.160-00000001",
            name="Спецодежда мужская",
            okpd2="14.12.30.160",
            characteristics=[],
            source="ktru-xml",
        ),
    ]


def _categories() -> list[Position]:
    return [
        Position(
            code=CATEGORY,
            name="Тумбы прикроватные",
            okpd2="31.09.12.124",
            kpgz=CATEGORY,
            portal_id=123,
            path=["Мебель", "Тумбы прикроватные"],
            characteristics=[
                _spec("Тип каркаса", ["Деревянный"], ktru=[BEDSIDE_1, BEDSIDE_2]),
                _spec("Цвет фасада", ["Белый"], ktru=["99.99.99.999-00000001"]),
                _spec("Производитель", [], group="general"),
            ],
            source="standards",
        )
    ]


def test_a_type_is_the_ktru_positions_of_one_name() -> None:
    types = {t.name: t for t in build_types(_ktru(), _categories())}
    assert set(types) == {
        "Тумба прикроватная",
        "Тумба офисная деревянная",
    }  # без характеристик — нет
    bedside = types["Тумба прикроватная"]
    assert bedside.source == TYPE_SOURCE
    specs = {c.name: c for c in bedside.characteristics}
    # Перечень общий, ссылки — все позиции типа (по ним второй этап их находит).
    assert specs["Тип каркаса"].values == ["Деревянный", "Металлический", "Пластиковый"]
    assert specs["Тип каркаса"].ktru == [BEDSIDE_1, BEDSIDE_2]
    # Обязательна, только если обязательна у всех позиций типа.
    assert specs["Тип каркаса"].required and specs["Ширина тумбы"].required
    assert not specs["Наличие колес"].required


def test_a_linked_type_takes_the_portal_category_code_and_its_template() -> None:
    bedside = next(t for t in build_types(_ktru(), _categories()) if t.name == "Тумба прикроватная")
    assert (bedside.code, bedside.kpgz, bedside.portal_id) == (CATEGORY, CATEGORY, 123)
    specs = {c.name: c for c in bedside.characteristics}
    # Поле шаблона портала, которого нет в КТРУ, дописано — без чужих ссылок на КТРУ.
    assert specs["Цвет фасада"].ktru == []
    assert specs["Производитель"].group == "general"
    # Одноимённое поле шаблона не дублирует характеристику КТРУ.
    assert [c.name for c in bedside.characteristics].count("Тип каркаса") == 1


def test_an_unlinked_type_gets_its_own_code_and_the_card_fields() -> None:
    office = next(t for t in build_types(_ktru(), _categories()) if t.name.startswith("Тумба оф"))
    assert office.code == f"{UNLINKED_PREFIX}{OFFICE}"
    assert office.kpgz is None
    general = [c.name for c in office.characteristics if c.group == "general"]
    assert general == ["Производитель", "Модель", "Страна происхождения"]


def _deps() -> Deps:
    types = Catalog(build_types(_ktru(), _categories()), version="types")
    return Deps(
        catalog=types,
        bm25=build_bm25(types),
        vectors=None,
        llm=FakeLLM(),  # type: ignore[arg-type]
        glossary=Glossary([]),
        sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.2,
        ktru=Catalog(_ktru(), version="ktru"),
        llm_enabled=False,
    )


def test_the_wizard_chooses_the_ktru_type_then_narrows_to_the_code() -> None:
    deps = _deps()
    out = asyncio.run(autofill(AutofillIn(name="Тумба деревянная"), deps))
    question = out.ktru.question
    assert question is not None and question.characteristic == CONFIRM
    labels = {o.label for o in question.options if o.label}
    assert labels == {"Тумба прикроватная", "Тумба офисная деревянная"}
    # Пока тип не выбран, полей чужого товара нет.
    assert out.characteristics == []
    assert (
        out.ktru.trail[0].label == "Типы КТРУ и категории портала"
        and out.ktru.trail[0].remaining == 2
    )

    chosen = asyncio.run(
        answer(out.session_id, AnswerIn(characteristic=CONFIRM, value=CATEGORY), deps)
    )
    assert chosen.ktru.final_code == CATEGORY
    chain = chosen.chain
    assert chain is not None and chain.ktru is not None
    assert (chain.ktru_type, chain.category_via) == ("Тумба прикроватная", "ktru_link")
    assert chain.ktru.trail[0].remaining == 2  # позиции КТРУ типа
    fields = {c.name: c for c in chosen.characteristics}
    assert fields["Тип каркаса"].origin == "ktru" and fields["Тип каркаса"].required
    assert fields["Цвет фасада"].origin == "portal"

    narrowed = asyncio.run(
        answer(
            out.session_id,
            AnswerIn(characteristic="Ширина тумбы", value=">=40 ... <50", stage="ktru"),
            deps,
        )
    )
    assert narrowed.chain is not None and narrowed.chain.ktru is not None
    assert narrowed.chain.ktru.final_code == BEDSIDE_1
    assert narrowed.chain.ktru.trail[-1].remaining == 1


def test_a_type_named_in_full_is_chosen_without_a_question() -> None:
    # «Тумба прикроватная» целиком есть в наименовании — какой это товар, не спрашиваем.
    out = asyncio.run(autofill(AutofillIn(name="Тумба прикроватная деревянная"), _deps()))
    assert out.ktru.final_code == CATEGORY
    assert out.chain is not None and out.chain.ktru_type == "Тумба прикроватная"


def test_a_type_name_after_a_negation_does_not_count() -> None:
    out = asyncio.run(autofill(AutofillIn(name="Тумба деревянная, не прикроватная"), _deps()))
    assert out.ktru.final_code != CATEGORY


def test_candidates_explain_what_fits_and_what_is_missing() -> None:
    out = asyncio.run(autofill(AutofillIn(name="Тумба деревянная"), _deps()))
    by_name = {c.name: c for c in out.ktru.candidates}
    bedside = by_name["Тумба прикроватная"]
    # Обязательные характеристики типа, которых в тексте нет, названы — человек видит, что дописать.
    assert "Ширина тумбы" in bedside.missing
    assert "Вид тумбы" in by_name["Тумба офисная деревянная"].missing


def test_a_category_without_ktru_says_so() -> None:
    stool = Position(
        code="01.16.99",
        name="Табуреты",
        okpd2="31.01.12.160",
        kpgz="01.16.99",
        characteristics=[_spec("Материал сиденья", ["Дерево"])],
        source="standards",
    )
    catalog = Catalog([*build_types(_ktru(), _categories()), stool], version="types")
    deps = _deps()
    deps.catalog, deps.bm25 = catalog, build_bm25(catalog)
    out = asyncio.run(autofill(AutofillIn(name="Табуреты"), deps))
    assert out.ktru.final_code == "01.16.99"
    assert out.chain is not None and out.chain.ktru is None
    [issue] = [i for i in out.issues if i.message.startswith("Кода КТРУ нет")]
    assert (issue.field, issue.severity) == ("ktru_code", "info")


def test_another_type_drops_the_answers_about_the_previous_one() -> None:
    deps = _deps()
    out = asyncio.run(autofill(AutofillIn(name="Тумба деревянная"), deps))
    sid = out.session_id
    asyncio.run(answer(sid, AnswerIn(characteristic=CONFIRM, value=CATEGORY), deps))
    width = AnswerIn(characteristic="Ширина тумбы", value=">=40 ... <50", stage="ktru")
    asyncio.run(answer(sid, width, deps))
    office = f"{UNLINKED_PREFIX}{OFFICE}"
    switched = asyncio.run(answer(sid, AnswerIn(characteristic=CONFIRM, value=office), deps))
    assert switched.ktru.final_code == office
    session = asyncio.run(deps.sessions.load(sid))
    assert session is not None and session.ktru_known == {}
    assert switched.chain is not None and switched.chain.ktru is not None
    assert switched.chain.ktru.final_code == OFFICE  # одна позиция типа — без чужих ответов


def test_a_ktru_field_takes_the_usual_range_of_the_portal_category() -> None:
    from app.catalog import NumRange

    category = _categories()[0]
    width = _spec("Ширина тумбы", [])
    width.plausible = NumRange(min=30, max=80)
    category.characteristics.append(width)
    bedside = next(t for t in build_types(_ktru(), [category]) if t.name == "Тумба прикроватная")
    spec = next(c for c in bedside.characteristics if c.name == "Ширина тумбы")
    assert spec.plausible == NumRange(min=30, max=80) and spec.ktru  # поле КТРУ, пределы портала


def test_a_typo_in_the_name_is_fixed_by_ktru_names_and_said() -> None:
    out = asyncio.run(autofill(AutofillIn(name="Тумба прикраватная"), _deps()))
    assert out.ktru.final_code == CATEGORY  # «прикраватная» — «прикроватная»
    [issue] = [i for i in out.issues if i.kind == "typo" and i.field == "name"]
    assert "«прикраватная» — «прикроватная»" in issue.message
    assert issue.fix is not None and issue.fix.value == "Тумба прикроватная"


def test_same_name_positions_are_labelled_by_what_differs() -> None:
    from app.narrowing import _choice_question

    bedside = [p for p in _ktru() if p.name == "Тумба прикроватная"]
    labels = {o.value: o.label for o in _choice_question(bedside).options if o.label}
    assert labels[BEDSIDE_1] != labels[BEDSIDE_2]
    assert "Ширина тумбы: >=40 ... <50" in (labels[BEDSIDE_1] or "")
