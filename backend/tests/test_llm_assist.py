"""Модель помогает коду там, где правила не видят смысла: отвечает по тексту на вопрос сужения до
позиции КТРУ и называет значения, которых у такого товара не бывает. Рядом — то, из-за чего
поставщик выбирал из двух одинаковых позиций (шаблон КТРУ) и пакет читал одно наименование."""

import asyncio
from typing import Any

from app.batch import parse_upload
from app.catalog import Catalog, CharacteristicSpec, Position
from app.catalog.ktru_meta import drop_wider
from app.extract import LLMItems
from app.extract.llm_fill import find_oddities, pick_option
from app.extract.model import ListMatch, Oddities, Oddity, OptionPick
from app.glossary import Glossary
from app.pipeline import AnswerIn, AutofillIn, Deps, answer, autofill
from app.search import build_bm25
from app.session import SessionStore
from tests.fakes import FakeLLM, FakeRedis

MAINS, BATTERY = "От сети", "От аккумуляторных батарей"
TEMPLATE, FROM_MAINS, FROM_BATTERY = (
    "28.24.11.000-00000032",
    "28.24.11.000-00000031",
    "28.24.11.000-00000030",
)


def _ktru(code: str, power: list[str]) -> Position:
    return Position(
        code=code,
        name="Шуруповерт",
        okpd2="28.24.11.000",
        characteristics=[
            CharacteristicSpec(name="Тип питания", kind="qualitative", values=power, required=True),
            CharacteristicSpec(name="Наличие функции удара", kind="qualitative", values=["Да"]),
        ],
        source="ktru-xml",
    )


# Как в выгрузке ЕИС: шаблон со всеми вариантами и две позиции из него, все actual и ACTIVE.
POSITIONS = [
    _ktru(TEMPLATE, [MAINS, BATTERY]),
    _ktru(FROM_MAINS, [MAINS]),
    _ktru(FROM_BATTERY, [BATTERY]),
]
TYPE = Position(
    code="01.11.02.01.03",
    name="Шуруповерт",
    okpd2="28.24.11.000",
    kpgz="01.11.02.01.03",
    characteristics=[
        CharacteristicSpec(
            name="Тип питания",
            kind="qualitative",
            values=[MAINS, BATTERY],
            ktru=[TEMPLATE, FROM_MAINS, FROM_BATTERY],
        )
    ],
    source="test",
)
NIGHTSTAND = Position(
    code="01.16.05.01.02",
    name="Тумба прикроватная",
    okpd2="31.09.12.124",
    characteristics=[CharacteristicSpec(name="Материал", kind="qualitative")],
    source="test",
)


def _deps(replies: list[Any], catalog: list[Position], **flags: bool) -> tuple[Deps, FakeLLM]:
    llm = FakeLLM(replies)
    cat = Catalog(catalog, version="test")
    deps = Deps(
        catalog=cat,
        bm25=build_bm25(cat),
        vectors=None,
        llm=llm,  # type: ignore[arg-type]
        glossary=Glossary([]),
        sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.2,
        ktru=Catalog(POSITIONS, version="ktru-test"),
        **flags,
    )
    return deps, llm


# --- шаблон КТРУ рядом с позицией из него ---


def test_template_is_hidden_while_an_exact_position_fits() -> None:
    kept, hidden = drop_wider(POSITIONS, lambda p: True)
    assert [p.code for p in kept] == [FROM_MAINS, FROM_BATTERY]
    assert hidden == {TEMPLATE: [FROM_MAINS, FROM_BATTERY]}


def test_template_stays_when_no_exact_position_fits() -> None:
    kept, hidden = drop_wider(POSITIONS, lambda p: p.code == TEMPLATE)
    assert [p.code for p in kept] == [TEMPLATE, FROM_MAINS, FROM_BATTERY] and hidden == {}


def test_positions_with_other_fields_are_not_a_template_pair() -> None:
    other = POSITIONS[2].model_copy(update={"name": "Гайковерт"})
    kept, hidden = drop_wider([POSITIONS[0], other], lambda p: True)
    assert len(kept) == 2 and hidden == {}


def test_answer_about_power_gives_one_position_not_two() -> None:
    """Кейс со скриншота: после «От аккумуляторных батарей» оставались шаблон и позиция из него."""
    deps, _ = _deps([LLMItems(items=[])] * 5, [TYPE])
    out = asyncio.run(autofill(AutofillIn(name="Шуруповерт"), deps))
    assert out.chain is not None and out.chain.ktru is not None
    question = out.chain.ktru.question
    assert question is not None and question.characteristic == "Тип питания"
    codes = {o.value: o.codes for o in question.options if o.codes}
    assert codes == {MAINS: [FROM_MAINS], BATTERY: [FROM_BATTERY]}  # шаблона в вариантах нет
    assert any("шаблон КТРУ" in t.label and TEMPLATE in t.label for t in out.chain.ktru.trail)
    after = asyncio.run(
        answer(
            out.session_id,
            AnswerIn(characteristic="Тип питания", value=BATTERY, stage="ktru"),
            deps,
        )
    )
    assert after.chain is not None and after.chain.ktru is not None
    assert after.chain.ktru.final_code == FROM_BATTERY


# --- модель отвечает на вопрос сужения по тексту ---

NAME, TEXT = "Шуруповерт", "Аккумуляторный, в кейсе. Поставляется без зарядного устройства"


def _pick(reply: OptionPick, options: list[str] | None = None) -> tuple[Any, FakeLLM]:
    llm = FakeLLM([reply])
    got = asyncio.run(pick_option(NAME, TEXT, "Тип питания", options or [MAINS, BATTERY], llm))  # type: ignore[arg-type]
    return got, llm


def test_option_named_by_the_text_is_accepted_with_its_quote() -> None:
    got, llm = _pick(OptionPick(value=BATTERY, quote="Аккумуляторный", reason="так в тексте"))
    assert got is not None and got.value == BATTERY
    assert (got.source.field, got.source.text) == ("description", "Аккумуляторный")
    assert BATTERY in llm.calls[0][1]["content"] and TEXT in llm.calls[0][1]["content"]


def test_value_outside_the_options_is_rejected() -> None:
    got, _ = _pick(OptionPick(value="От солнечной панели", quote="Аккумуляторный"))
    assert got is None


def test_quote_that_is_not_in_the_text_is_rejected() -> None:
    got, _ = _pick(OptionPick(value=BATTERY, quote="работает от аккумулятора 18 В"))
    assert got is None


def test_whole_text_is_not_a_quote() -> None:
    long_text = "Аккумуляторный, " + "в кейсе, " * 12
    llm = FakeLLM([OptionPick(value=BATTERY, quote=long_text.strip())])
    got = asyncio.run(pick_option(NAME, long_text, "Тип питания", [MAINS, BATTERY], llm))  # type: ignore[arg-type]
    assert got is None


def test_negated_quote_is_rejected() -> None:
    got, _ = _pick(
        OptionPick(value="Да", quote="зарядного устройства"), options=["Да", "Нет", "Иное"]
    )
    assert got is None  # «без зарядного устройства» — не «Да»


def test_null_answer_leaves_the_question_to_the_supplier() -> None:
    got, _ = _pick(OptionPick(value=None, quote=""))
    assert got is None


def test_numeric_options_are_not_sent_to_the_model() -> None:
    llm = FakeLLM()
    options = [">=500 ... <750", ">=750 ... <1000"]
    assert asyncio.run(pick_option(NAME, TEXT, "Мощность", options, llm)) is None  # type: ignore[arg-type]
    assert llm.calls == []  # числа читают и пересчитывают правила


def test_model_answer_chooses_the_ktru_position_and_says_so() -> None:
    """Кейс «Дрель-шуруповерт аккумуляторная»: позиция выбиралась вручную, хотя текст её называл."""
    pick = OptionPick(value=BATTERY, quote="Аккумуляторный", reason="в тексте «аккумуляторный»")
    deps, llm = _deps([LLMItems(items=[]), pick], [TYPE], pick_options=True)
    out = asyncio.run(autofill(AutofillIn(name=NAME, description=TEXT), deps))
    assert out.chain is not None and out.chain.ktru is not None
    assert out.chain.ktru.final_code == FROM_BATTERY
    assert out.chain.ktru.question is None
    assert any("модель по тексту «Аккумуляторный»" in t.label for t in out.chain.ktru.trail)
    assert any(
        "помогла выбрать модель" in s and "Аккумуляторный" in s for s in out.chain.ktru.steps
    )
    field = next(c for c in out.characteristics if c.name == "Тип питания")
    assert (field.value, field.method) == (BATTERY, "llm")
    assert field.note is not None and "проверьте" in field.note
    assert len(llm.calls) == 2  # извлечение и один вопрос о варианте


def test_without_the_flag_the_question_stays_with_the_supplier() -> None:
    deps, llm = _deps([LLMItems(items=[])], [TYPE])
    out = asyncio.run(autofill(AutofillIn(name=NAME, description=TEXT), deps))
    assert out.chain is not None and out.chain.ktru is not None
    assert out.chain.ktru.final_code is None and out.chain.ktru.question is not None
    assert len(llm.calls) == 1


def test_rejected_model_answer_keeps_the_question() -> None:
    invented = OptionPick(value=BATTERY, quote="питание от батареи")  # такой фразы в тексте нет
    deps, _ = _deps([LLMItems(items=[]), invented], [TYPE], pick_options=True)
    out = asyncio.run(autofill(AutofillIn(name=NAME, description=TEXT), deps))
    assert out.chain is not None and out.chain.ktru is not None
    assert out.chain.ktru.final_code is None
    assert out.chain.ktru.question is not None
    assert out.chain.ktru.question.characteristic == "Тип питания"


# --- модель о здравом смысле ---


def test_oddities_keep_only_fields_that_were_given() -> None:
    reply = Oddities(
        items=[
            Oddity(field="Материал", reason="Тумбы не делают из глины, обычно ЛДСП или дерево."),
            Oddity(field="Цвет", reason="поля не было"),
            Oddity(field="материал", reason="повтор"),
            Oddity(field="Глубина", reason=""),
        ]
    )
    llm = FakeLLM([reply])
    values = [("Материал", "глина"), ("Глубина", "100 м")]
    got = asyncio.run(find_oddities("Тумба прикроватная", values, llm))  # type: ignore[arg-type]
    assert [(o.field, o.reason[:5]) for o in got] == [("Материал", "Тумбы")]
    prompt = llm.calls[0][1]["content"]
    assert "Тумба прикроватная" in prompt and "Материал: глина" in prompt


def test_nothing_is_asked_without_values() -> None:
    llm = FakeLLM()
    assert asyncio.run(find_oddities("Тумба прикроватная", [], llm)) == []  # type: ignore[arg-type]
    assert llm.calls == []


def test_clay_nightstand_gets_a_warning_and_keeps_the_value() -> None:
    """Кейс «Тумбочка … Материал: глина»: правила видели только «слово не из перечня»."""
    odd = Oddities(items=[Oddity(field="Материал", reason="Тумбы из глины не делают.")])
    # «Материал» взяли правила, первой модели спрашивать нечего: единственный вызов — о смысле.
    deps, _ = _deps([odd], [NIGHTSTAND], sanity=True)
    out = asyncio.run(
        autofill(AutofillIn(name="Тумба прикроватная", description="Материал: глина"), deps)
    )
    field = next(c for c in out.characteristics if c.name == "Материал")
    assert field.value == "глина"  # значение поставщика не тронуто
    [issue] = [i for i in out.issues if i.field == "characteristics.Материал"]
    assert issue.severity == "warn"
    assert "необычно для товара «Тумба прикроватная»" in issue.message
    assert "тумбы из глины не делают" in issue.message


def test_sanity_is_off_without_the_flag() -> None:
    deps, llm = _deps([LLMItems(items=[])], [NIGHTSTAND])
    out = asyncio.run(
        autofill(AutofillIn(name="Тумба прикроватная", description="Материал: глина"), deps)
    )
    assert not [i for i in out.issues if i.field == "characteristics.Материал"]
    assert len(llm.calls) <= 1


# --- строка пакета читается целиком ---


def test_batch_row_gives_the_pipeline_all_columns_not_only_the_name() -> None:
    """Выгрузка организаторов: «имя значение | …» без двоеточий раньше не читалась вовсе."""
    data = (
        "ID СТЕ;Наименование СТЕ;Модель;Производитель;Страна происхождения;Характеристики\n"
        "1160207;Дрель-шуруповерт аккумуляторная Ставр;-;Ставр;КИТАЙ;"
        "Вес 2.03000 кг | Тип инструмента дрель-шуруповерт | Напряжение аккумулятора 10.8 В\n"
        "1160208;Шуруповерт Bosch PSR 14.4;Bosch PSR 14.4;Robert Bosch Gmbh;КИТАЙ;"
        "Вес 3.10000 кг | Режимы работы Завинчивание,Сверление\n"
    ).encode()
    first, second = parse_upload("tools.csv", data)
    assert first.name == "Дрель-шуруповерт аккумуляторная Ставр"
    assert first.description.splitlines() == [
        "Вес 2.03000 кг | Тип инструмента дрель-шуруповерт | Напряжение аккумулятора 10.8 В",
        "Производитель: Ставр",  # «Модель: -» — пустая ячейка, в текст не идёт
        "Страна происхождения: КИТАЙ",
    ]
    assert second.description.splitlines()[1:3] == [
        "Модель: Bosch PSR 14.4",
        "Производитель: Robert Bosch Gmbh",
    ]


# --- тот же механизм на первом этапе: вопрос о характеристике при выборе позиции ---

WHITE = Position(
    code="17.12.14.129-00000031",
    name="Бумага для офисной техники",
    okpd2="17.12.14.129",
    characteristics=[CharacteristicSpec(name="Цветность", kind="qualitative", values=["Белая"])],
    source="test",
)
COLOURED = WHITE.model_copy(
    update={
        "code": "17.12.14.129-00000017",
        "characteristics": [
            CharacteristicSpec(name="Цветность", kind="qualitative", values=["Цветная"])
        ],
    }
)


def test_first_stage_question_is_answered_from_the_text_too() -> None:
    pick = OptionPick(value="Цветная", quote="тонированная", reason="тонированная — цветная")
    deps, _ = _deps([pick, LLMItems(items=[])], [WHITE, COLOURED], pick_options=True)
    text = AutofillIn(name="Бумага для офисной техники", description="Пачка, тонированная")
    out = asyncio.run(autofill(text, deps))
    assert out.ktru.final_code == COLOURED.code and out.ktru.question is None
    field = next(c for c in out.characteristics if c.name == "Цветность")
    assert (field.value, field.method) == ("Цветная", "llm")


def test_first_stage_question_stays_without_the_flag() -> None:
    deps, llm = _deps([], [WHITE, COLOURED])
    text = AutofillIn(name="Бумага для офисной техники", description="Пачка, тонированная")
    out = asyncio.run(autofill(text, deps))
    assert out.ktru.final_code is None
    assert out.ktru.question is not None and out.ktru.question.characteristic == "Цветность"
    assert llm.calls == []


def test_conclusion_from_a_negation_is_rejected() -> None:
    """«Без аккумулятора» — не довод ни за один вариант: вывод от противного не принимается."""
    got, _ = _pick(OptionPick(value=MAINS, quote="без зарядного устройства"))
    assert got is None


def test_model_does_not_dispute_a_value_from_the_position_list() -> None:
    listed = NIGHTSTAND.model_copy(
        update={
            "characteristics": [
                CharacteristicSpec(name="Материал", kind="qualitative", values=["ЛДСП", "Дерево"])
            ]
        }
    )
    odd = Oddities(items=[Oddity(field="Материал", reason="ЛДСП — странно.")])
    deps, _ = _deps([odd], [listed], sanity=True)
    out = asyncio.run(
        autofill(AutofillIn(name="Тумба прикроватная", description="Материал: ЛДСП"), deps)
    )
    assert not [i for i in out.issues if i.field == "characteristics.Материал"]


def test_value_outside_the_list_still_gets_the_model_remark() -> None:
    listed = NIGHTSTAND.model_copy(
        update={
            "characteristics": [
                CharacteristicSpec(name="Материал", kind="qualitative", values=["ЛДСП", "Дерево"])
            ]
        }
    )
    odd = Oddities(items=[Oddity(field="Материал", reason="Из глины мебель не делают.")])
    # Слово не из перечня: сначала вопрос о приведении к перечню (нет такого), затем о смысле.
    deps, _ = _deps([ListMatch(value=None, quote=""), odd], [listed], sanity=True)
    out = asyncio.run(
        autofill(AutofillIn(name="Тумба прикроватная", description="Материал: глина"), deps)
    )
    remarks = [i for i in out.issues if i.field == "characteristics.Материал"]
    assert any(i.severity == "warn" and "необычно для товара" in i.message for i in remarks)


def test_model_cannot_cancel_a_catalog_field_but_can_reject_a_new_one() -> None:
    """Справочник важнее модели: о его характеристике принимается только «значение невозможно»,
    о новой — только «характеристика к товару не относится»."""
    reply = Oddities(
        items=[
            Oddity(field="Количество скоростей", kind="field", reason="У отвёрток нет скоростей."),
            Oddity(field="Вес", kind="value", reason="85 тонн отвёртка не весит."),
            Oddity(field="Жирность", kind="field", reason="Жирность бывает у молока."),
            Oddity(field="Длина жала", kind="value", reason="Многовато."),
        ]
    )
    values = [
        ("Количество скоростей", "2 шт"),
        ("Вес", "85 т"),
        ("Жирность", "3 %"),
        ("Длина жала", "90 мм"),
    ]
    llm = FakeLLM([reply])
    new = frozenset({"Жирность", "Длина жала"})
    got = asyncio.run(find_oddities("Отвертка аккумуляторная", values, llm, new))  # type: ignore[arg-type]
    assert [(o.field, o.kind) for o in got] == [("Вес", "value"), ("Жирность", "field")]
    assert "Жирность: 3 % [новая]" in llm.calls[0][1]["content"]
