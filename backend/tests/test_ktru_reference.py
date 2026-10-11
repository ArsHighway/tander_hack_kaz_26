"""КТРУ — эталон в /check.

Значение из перечня итоговой позиции КТРУ карточки не блокируется ни шаблоном категории портала, ни
словом текста, которого в перечне КТРУ нет. Значение вне перечня позиции КТРУ — warn, с ближайшим
значением, если оно есть.
"""

import pytest

from app.catalog import Catalog, CharacteristicSpec, Position
from app.checks.detector import _refines, _same
from app.extract import LLMItems
from app.glossary import Glossary
from app.pipeline import Deps, check
from app.schemas import CardIn, CharIn, Issue
from app.search import build_bm25
from app.session import SessionStore
from tests.fakes import FakeLLM, FakeRedis

BOOTS_KTRU = "15.20.11.113-00000011"
TROUSERS_KTRU = "14.13.12.140-00000005"
SAW_KTRU = "25.73.20.110-00000008"
DESK_KTRU = "31.01.10.000-00000005"
GLOVES_KTRU = "22.19.60.113-00000002"


def _q(name: str, values: list[str], ktru: list[str] | None = None) -> CharacteristicSpec:
    return CharacteristicSpec(name=name, kind="qualitative", values=values, ktru=ktru or [])


def _position(code: str, name: str, specs: list[CharacteristicSpec]) -> Position:
    return Position(code=code, name=name, okpd2="", characteristics=specs, source="test")


# Категории портала: перечни так, как пишут на портале.
CATEGORIES = Catalog(
    [
        _position("01.19.04.06.10", "Сапоги резиновые", [
            _q("Половой признак", ["Муж", "Мужские", "Женские", "Жен"], [BOOTS_KTRU]),
        ]),
        _position("01.19.01.03.01", "Брюки рабочие", [
            _q("Материал изделия", ["Хлопок", "Полиэстер", "Смесовая"], [TROUSERS_KTRU]),
            _q("Половой признак", ["Мужские", "Женские"]),
        ]),
        _position("01.11.02.02.03.02", "Пилы ручные", [
            _q("Вид пилы", ["Ножовка", "Лучковая", "Классическая"], [SAW_KTRU]),
        ]),
        _position("01.16.03.01.01", "Столы письменные", [
            _q("Вид материала столешницы", ["ЛДСП", "МДФ", "Дерево"], [DESK_KTRU]),
            _q("Цвет", ["Белый", "Черный", "Бук"]),
        ]),
        _position("01.13.05.02", "Перчатки смотровые", [
            _q("Материал перчаток", ["Латекс", "Нитрил", "Винил"], [GLOVES_KTRU]),
        ]),
    ],
    version="test",
)  # fmt: skip

# Позиции КТРУ: перечни ЕИС — эталон.
KTRU = Catalog(
    [
        _position(BOOTS_KTRU, "Сапоги резиновые формовые", [
            _q("Половой признак", ["Мужские", "Женские"]),
            _q("Назначение сапог", ["Кислотощелочестойкие"]),
        ]),
        _position(TROUSERS_KTRU, "Брюки", [_q("Материал изделия", ["Ткань"])]),
        _position(SAW_KTRU, "Пила ручная", [_q("Вид пилы", ["Классическая"])]),
        _position(DESK_KTRU, "Стол письменный", [
            _q("Вид материала столешницы", ["ДСП", "ЛДСП", "МДФ", "Массив дерева"]),
            _q("Вид материала каркаса", ["Металл", "Металл хромированный"]),
        ]),
        _position(GLOVES_KTRU, "Перчатки смотровые", [
            _q("Материал перчаток", ["Нитриловые", "Латексные"]),
        ]),
    ],
    version="ktru-test",
)  # fmt: skip


def _deps() -> Deps:
    return Deps(
        catalog=CATEGORIES,
        bm25=build_bm25(CATEGORIES),
        vectors=None,
        llm=FakeLLM([LLMItems(items=[])] * 10),  # type: ignore[arg-type]
        glossary=Glossary([]),
        sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.2,
        ktru=KTRU,
    )


def _on(issues: list[Issue], field: str) -> list[tuple[str, str]]:
    return [(i.kind, i.severity) for i in issues if i.field == f"characteristics.{field}"]


def _card(name: str, category: str, ktru: str | None, chars: dict[str, str],
          description: str = "") -> CardIn:  # fmt: skip
    return CardIn(
        name=name,
        description=description,
        category_code=category,
        ktru_code=ktru,
        characteristics=[CharIn(name=k, value=v) for k, v in chars.items()],
    )


def test_ktru_value_missing_from_portal_list_is_not_blocked() -> None:
    # c05: ответ на вопрос КТРУ «Материал изделия» → «Ткань»; в перечне категории портала его нет.
    card = _card("брюки рабочии мужскии", "01.19.01.03.01", TROUSERS_KTRU,
                 {"Материал изделия": "Ткань", "Половой признак": "Мужские"},
                 "хлопок 100%, размер 52, рост 182")  # fmt: skip
    out = check(card, _deps())
    assert _on(out.issues, "Материал изделия") == [("not_in_list", "info")]
    assert out.verdict == "ready", out.issues


def test_same_value_outside_portal_list_without_ktru_is_still_blocked() -> None:
    card = _card("брюки рабочие", "01.19.01.03.01", None, {"Материал изделия": "Ткань"})
    out = check(card, _deps())
    assert _on(out.issues, "Материал изделия") == [("not_in_list", "block")]


def test_ktru_value_is_not_refuted_by_a_portal_only_word_of_the_name() -> None:
    # c17: «Вид пилы: Классическая» — единственное значение позиции КТРУ; «ножовка» в наименовании —
    # значение перечня портала, а не КТРУ: исправление «Ножовка» КТРУ не пройдёт.
    card = _card("Ножовка по дереву 450мм", "01.11.02.02.03.02", SAW_KTRU,
                 {"Вид пилы": "Классическая"}, "закаленный зуб, шаг 7 TPI")  # fmt: skip
    out = check(card, _deps())
    found = [i for i in out.issues if i.field == "characteristics.Вид пилы"]
    assert [(i.kind, i.severity) for i in found] == [("mismatch", "info")]
    assert found[0].fix is None
    assert out.verdict == "ready"


def test_portal_word_against_value_without_ktru_is_still_a_conflict() -> None:
    card = _card("Ножовка по дереву 450мм", "01.11.02.02.03.02", None, {"Вид пилы": "Классическая"})
    found = _on(check(card, _deps()).issues, "Вид пилы")
    assert found == [("mismatch", "block")]


def test_text_naming_another_ktru_value_is_still_a_conflict() -> None:
    # Положительный контроль: текст называет другое значение перечня КТРУ — ошибка остаётся.
    card = _card("Сапоги резиновые женские", "01.19.04.06.10", BOOTS_KTRU,
                 {"Половой признак": "Мужские"})  # fmt: skip
    out = check(card, _deps())
    found = [i for i in out.issues if i.field == "characteristics.Половой признак"]
    assert [(i.kind, i.severity) for i in found] == [("mismatch", "block")]
    assert found[0].fix is not None and found[0].fix.value == "Женские"


@pytest.mark.parametrize("ktru", [BOOTS_KTRU, None])
def test_word_cut_short_is_the_same_value(ktru: str | None) -> None:
    # c19: «муж» в наименовании — сокращение «Мужские», не другое значение перечня.
    card = _card(
        "сапоги резиновые муж 43 р", "01.19.04.06.10", ktru, {"Половой признак": "Мужские"}
    )
    out = check(card, _deps())
    assert _on(out.issues, "Половой признак") == []
    assert out.verdict == "ready", out.issues


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("муж", "Мужские", True),
        ("Жен", "Женские", True),
        ("Женские", "жен", True),
        # Не сокращение: «нет» — ответ, слово короче трёх букв, разные начала, не одно слово.
        ("нет", "Нетканый", False),
        ("му", "Мужские", False),
        ("жен", "Мужские", False),
        ("муж", "Мужские и женские", False),
        ("Металл", "Металлик", False),
    ],
)
def test_prefix_abbreviation(a: str, b: str, same: bool) -> None:
    assert _same(a, b) is same


def test_value_outside_ktru_list_is_a_warning_with_allowed_values() -> None:
    card = CardIn(
        name="Стол письменный", category_code="01.16.03.01.01", ktru_code=DESK_KTRU,
        characteristics=[CharIn(name="Вид материала столешницы", value="МДФ"),
                         CharIn(name="Вид материала каркаса", value="Дерево", origin="ktru")],
    )  # fmt: skip
    out = check(card, _deps())
    found = [i for i in out.issues if i.field == "characteristics.Вид материала каркаса"]
    assert [(i.kind, i.severity) for i in found] == [("not_in_list", "warn")]
    assert "Металл хромированный" in found[0].why and DESK_KTRU in found[0].message
    assert out.verdict == "ready"


def test_typo_against_ktru_list_is_a_warning_with_fix() -> None:
    # Поле только из позиции КТРУ (вне шаблона категории): опечатка — warn с исправлением.
    card = CardIn(
        name="Стол письменный", category_code="01.16.03.01.01", ktru_code=DESK_KTRU,
        characteristics=[CharIn(name="Вид материала каркаса", value="Металл хромированый",
                                origin="ktru")],
    )  # fmt: skip
    found = [i for i in check(card, _deps()).issues
             if i.field == "characteristics.Вид материала каркаса"]  # fmt: skip
    assert [(i.kind, i.severity) for i in found] == [("typo", "warn")]
    assert found[0].fix is not None and found[0].fix.value == "Металл хромированный"


def test_portal_typo_of_ktru_value_gets_the_ktru_fix() -> None:
    # Опечатка значения КТРУ, которого нет в перечне категории: исправление — по КТРУ.
    card = _card("Стол письменный", "01.16.03.01.01", DESK_KTRU,
                 {"Вид материала столешницы": "Масив дерева"})  # fmt: skip
    found = [i for i in check(card, _deps()).issues
             if i.field == "characteristics.Вид материала столешницы"]  # fmt: skip
    assert [(i.kind, i.severity) for i in found] == [("typo", "warn")]
    assert found[0].fix is not None and found[0].fix.value == "Массив дерева"


def test_ktru_list_written_another_way_is_not_flagged() -> None:
    # Перечни ЕИС и портала пишутся по-разному: «Нитрил» на портале — «Нитриловые» в КТРУ.
    card = _card("Перчатки смотровые нитриловые", "01.13.05.02", GLOVES_KTRU,
                 {"Материал перчаток": "Нитрил"})  # fmt: skip
    out = check(card, _deps())
    assert _on(out.issues, "Материал перчаток") == []


def test_other_word_form_of_ktru_value_is_not_a_typo() -> None:
    # c05: модуль поставил «Мужской» (так на портале), в КТРУ — «Мужские»: то же значение.
    card = _card("Сапоги резиновые мужские", "01.19.04.06.10", BOOTS_KTRU,
                 {"Половой признак": "Мужской"})  # fmt: skip
    found = _on(check(card, _deps()).issues, "Половой признак")
    # «Мужской» нет в перечне категории, но в КТРУ это «Мужские» — сведения, а не опечатка.
    assert found == [("not_in_list", "info")], found


def test_portal_value_written_otherwise_in_ktru_is_info_with_ktru_values() -> None:
    # Значение перечня портала, которое КТРУ пишет иначе («Термостатический» и «Рычажный»): info.
    mixer_ktru = "28.14.12.110-00000019"
    deps = _deps()
    deps.catalog = Catalog([_position("01.11.01.04.05.01", "Смесители водоразборные", [
        _q("Тип управления", ["Однорычажный", "Двухвентильный", "Термостатический"], [mixer_ktru]),
    ])], version="t")  # fmt: skip
    deps.ktru = Catalog([_position(mixer_ktru, "Смеситель водоразборный", [
        _q("Тип управления", ["Рычажный", "Вентильный"]),
    ])], version="k")  # fmt: skip
    card = _card("Смеситель термостатический", "01.11.01.04.05.01", mixer_ktru,
                 {"Тип управления": "Термостатический"})  # fmt: skip
    found = [i for i in check(card, deps).issues if i.field == "characteristics.Тип управления"]
    assert [(i.kind, i.severity) for i in found] == [("not_in_list", "info")]
    assert "записано иначе" in found[0].message and "«Рычажный»" in found[0].message
    # Поле шаблона, имя которого есть и в позиции КТРУ (`origin: ktru` в ответе модуля), — так же.
    card = card.model_copy(update={"characteristics": [
        CharIn(name="Тип управления", value="Термостатический", origin="ktru")]})  # fmt: skip
    found = [i for i in check(card, deps).issues if i.field == "characteristics.Тип управления"]
    assert [(i.kind, i.severity) for i in found] == [("not_in_list", "info")]
    # Уточнение значения КТРУ числом («Однорычажный» при «Рычажный», «Двухвентильный» при
    # «Вентильный») — то же значение: «однорычажный» и выбрал позицию КТРУ, замечания нет.
    for value in ("Однорычажный", "Двухвентильный"):
        card = _card("Смеситель для кухни", "01.11.01.04.05.01", mixer_ktru,
                     {"Тип управления": value})  # fmt: skip
        assert not [
            i for i in check(card, deps).issues if i.field == "characteristics.Тип управления"
        ]
    # Приставка-противоположность («Нерычажный» при «Рычажный») — другое значение: info остаётся.
    deps.catalog.get("01.11.01.04.05.01").characteristics[0].values.append("Нерычажный")
    card = _card("Смеситель", "01.11.01.04.05.01", mixer_ktru, {"Тип управления": "Нерычажный"})
    found = [i for i in check(card, deps).issues if i.field == "characteristics.Тип управления"]
    assert [(i.kind, i.severity) for i in found] == [("not_in_list", "info")]
    # Опечатка значения КТРУ — warn с исправлением, даже если перечень портала её хранит.
    deps.catalog.get("01.11.01.04.05.01").characteristics[0].values.append("Рчыажный")
    card = _card("Смеситель", "01.11.01.04.05.01", mixer_ktru, {"Тип управления": "Рчыажный"})
    found = [i for i in check(card, deps).issues if i.field == "characteristics.Тип управления"]
    assert [(i.kind, i.severity, i.fix.value if i.fix else None) for i in found] == [
        ("typo", "warn", "Рычажный")
    ]
    # Значения нет ни в КТРУ, ни в перечне категории — предупреждение.
    card = _card("Смеситель для кухни", "01.11.01.04.05.01", mixer_ktru,
                 {"Тип управления": "Сенсорный"})  # fmt: skip
    found = [i for i in check(card, deps).issues if i.field == "characteristics.Тип управления"]
    assert [i.severity for i in found] == ["block"]  # не из перечня категории — как и было


def test_only_a_counting_prefix_refines_a_ktru_value() -> None:
    # «одно-/двух-…» уточняют значение КТРУ; «не-», «без-», «полу-» дают противоположное или другое
    for value, ktru_value in (("Однорычажный", "Рычажный"), ("Двухвентильный", "Вентильный")):
        assert _refines(value, ktru_value), value
    for value, ktru_value in (
        ("Неразборный", "Разборный"),
        ("Бескамерная", "Камерная"),
        ("Полуботинки", "Ботинки"),
        ("Неутепленная", "Утепленная"),
        ("Беспружинный", "Пружинный"),
        ("Бесколлекторный", "Коллекторный"),
        ("Нестерильный", "Стерильный"),
        ("Неревизионная", "Ревизионная"),
    ):
        assert not _refines(value, ktru_value), value


def test_answer_to_unit_question_of_ktru_stage_is_accepted() -> None:
    # Одноимённые позиции КТРУ различаются только единицей: ответ модуля на этот вопрос — не 400.
    import asyncio

    from app.narrowing import UNIT_QUESTION
    from app.pipeline import AnswerIn, AutofillIn, answer, autofill

    piece, pack = "22.29.29.190-00000101", "22.29.29.190-00000102"
    category = _position("01.20.03.07", "Губка для мытья посуды", [
        _q("Материал губки", ["Поролон", "Целлюлоза"], [piece, pack]),
    ])  # fmt: skip
    ktru = [
        Position(code=code, name="Губка для мытья посуды", okpd2="", unit=unit, source="test",
                 characteristics=[_q("Материал губки", ["Поролон"])])
        for code, unit in ((piece, "Штука"), (pack, "Упаковка"))
    ]  # fmt: skip
    deps = _deps()
    deps.catalog = Catalog([category], version="t")
    deps.bm25, deps.ktru = build_bm25(deps.catalog), Catalog(ktru, version="k")
    out = asyncio.run(autofill(AutofillIn(name="Губка для мытья посуды поролон"), deps))
    assert out.chain is not None and out.chain.ktru is not None
    question = out.chain.ktru.question
    assert question is not None and question.characteristic == UNIT_QUESTION
    inp = AnswerIn(characteristic=UNIT_QUESTION, value=question.options[0].value, stage="ktru")
    after = asyncio.run(answer(out.session_id, inp, deps))
    assert after.chain is not None and after.chain.ktru is not None
    assert after.chain.ktru.final_code in (piece, pack)


def _sponges(units: tuple[str, str]) -> list[Position]:
    return [
        Position(code=f"01.20.03.0{i}", name="Губка для мытья посуды", okpd2="", unit=unit,
                 source="test", characteristics=[_q("Материал губки", ["Поролон"])])
        for i, unit in enumerate(units, 7)
    ]  # fmt: skip


def test_unit_question_of_category_stage_accepts_a_unit_and_rejects_others() -> None:
    import asyncio

    from app.narrowing import UNIT_QUESTION
    from app.pipeline import AnswerIn, AutofillIn, BadAnswer, answer, autofill

    deps = _deps()
    deps.catalog = Catalog(_sponges(("Штука", "Упаковка")), version="t")
    deps.bm25, deps.ktru = build_bm25(deps.catalog), None
    out = asyncio.run(autofill(AutofillIn(name="Губка для мытья посуды поролон"), deps))
    question = out.ktru.question
    assert question is not None and question.characteristic == UNIT_QUESTION
    with pytest.raises(BadAnswer):
        asyncio.run(answer(out.session_id, AnswerIn(characteristic=UNIT_QUESTION,
                                                    value="Килограмм"), deps))  # fmt: skip
    inp = AnswerIn(characteristic=UNIT_QUESTION, value=question.options[0].value)
    after = asyncio.run(answer(out.session_id, inp, deps))
    assert after.ktru.final_code in {p.code for p in deps.catalog.all()}


def test_portal_value_two_edits_from_ktru_value_is_not_a_typo() -> None:
    # «желтый» из перечня портала при КТРУ «Белый», «Серый» — другой цвет (две правки),
    # а не опечатка с исправлением «Белый».
    eraser_ktru = "22.29.25.000-00000001"
    deps = _deps()
    deps.catalog = Catalog([_position("01.15.03.01", "Ластики", [
        _q("Цвет", ["Желтый", "Белый", "Серый"], [eraser_ktru]),
    ])], version="t")  # fmt: skip
    deps.ktru = Catalog([_position(eraser_ktru, "Ластик", [
        _q("Цвет", ["Белый", "Серый"]),
    ])], version="k")  # fmt: skip
    card = _card("Ластик желтый", "01.15.03.01", eraser_ktru, {"Цвет": "Желтый"})
    found = [i for i in check(card, deps).issues if i.field == "characteristics.Цвет"]
    assert [(i.kind, i.severity) for i in found] == [("not_in_list", "info")]
    assert all(i.fix is None for i in found)
