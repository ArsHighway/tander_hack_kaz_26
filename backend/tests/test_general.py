"""«Общие сведения» карточки (производитель, модель, страна): поля карточки портала, а не
характеристики. Второй производитель данных для старых потребителей: поиск, вопросы сужения,
счёт характеристик модерации и оценка отложенной выборки их не видят; новый производитель не
блокирует."""

from app.catalog import Catalog, CharacteristicSpec, Position
from app.checks.detector import detect
from app.extract.llm_fill import build_messages
from app.extract.rules import extract_rules
from app.glossary import Glossary
from app.narrowing import Known, best_question, narrow
from app.portal.holdout import score_case
from app.schemas import CardIn, CharIn
from app.search import build_bm25
from app.search.vectors import position_text


def paper(code: str, makers: list[str], color: str = "белый") -> Position:
    return Position(
        code=code,
        name="Бумага для офисной техники",
        okpd2="17.12.14.129",
        characteristics=[
            CharacteristicSpec(name="Формат", kind="qualitative", values=["A4"]),
            CharacteristicSpec(name="Цвет", kind="qualitative", values=[color]),
            CharacteristicSpec(
                name="Производитель", kind="qualitative", values=makers, group="general"
            ),
            CharacteristicSpec(name="Модель", kind="qualitative", group="general"),
            CharacteristicSpec(
                name="Страна происхождения", kind="qualitative", values=["Россия"], group="general"
            ),
        ],
        source="t",
    )


def test_general_does_not_enter_search_texts() -> None:
    p = paper("A", ["Туринский ЦБЗ"])
    assert "Туринский" not in position_text(p)
    assert "Производитель" not in position_text(p)
    tokens = build_bm25(Catalog([p], "t")).doc_tokens("A")
    assert not [t for t in tokens if t.startswith("турин") or t.startswith("производител")]


def test_question_is_never_about_general() -> None:
    # Позиции различаются только производителем: спрашивать нечего.
    assert best_question([paper("A", ["Туринский ЦБЗ"]), paper("B", ["Снегурочка"])], {}) is None


def test_maker_from_text_does_not_narrow_categories() -> None:
    known = {"Производитель": Known(value="Туринский ЦБЗ", source="text")}
    kept = narrow([paper("A", ["Туринский ЦБЗ"]), paper("B", ["Снегурочка"])], known)
    assert [p.code for p in kept] == ["A", "B"]


def _minimum(card: CardIn) -> list[str]:
    return [i.message for i in detect(card, None) if "не меньше" in i.message]


def test_moderation_minimum_counts_characteristics_without_general() -> None:
    three = [CharIn(name=n, value="x") for n in ("Формат", "Цвет", "Плотность")]
    general = [
        CharIn(name="Производитель", value="Туринский ЦБЗ"),
        CharIn(name="Модель", value="Офисная"),
        CharIn(name="Страна происхождения", value="Россия"),
    ]
    alone = _minimum(CardIn(name="Бумага А4", characteristics=three))
    assert alone
    assert _minimum(CardIn(name="Бумага А4", characteristics=three + general)) == alone


def test_new_maker_is_not_blocked() -> None:
    card = CardIn(
        name="Бумага А4",
        characteristics=[
            CharIn(name="Формат", value="A4"),
            CharIn(name="Цвет", value="белый"),
            CharIn(name="Производитель", value="Новый бумажный завод"),
            CharIn(name="Страна происхождения", value="Казахстан"),
        ],
    )
    issues = detect(card, paper("A", ["Туринский ЦБЗ"]))
    assert not [
        i
        for i in issues
        if i.field in ("characteristics.Производитель", "characteristics.Страна происхождения")
    ]


def test_holdout_score_does_not_count_general() -> None:
    case = {"code": "A", "given": [], "target": []}
    plain = score_case(case, ["A"], "A", "A", [("Цвет", "белый", "filled")])
    more = score_case(
        case,
        ["A"],
        "A",
        "A",
        [("Цвет", "белый", "filled"), ("Производитель", "Туринский ЦБЗ", "filled")],
        general={"Производитель"},
    )
    assert plain["filled_total"] == more["filled_total"] == 1


def test_holdout_counts_own_country_characteristic() -> None:
    # у 46 категорий «Страна происхождения» — своя характеристика стандарта, а не поле карточки
    case = {"code": "A", "given": [], "target": []}
    own = score_case(case, ["A"], "A", "A", [("Страна происхождения", "Россия", "filled")])
    assert own["filled_total"] == 1


def test_maker_is_found_in_the_name_by_the_list() -> None:
    p = paper("A", ["Туринский ЦБЗ", "Снегурочка"])
    found = {
        e.name: e.raw_value for e in extract_rules("Бумага Туринский ЦБЗ А4", "", p.characteristics)
    }
    assert found.get("Производитель") == "Туринский ЦБЗ"


def test_llm_is_not_told_to_pick_the_maker_from_the_list() -> None:
    p = paper("A", ["Туринский ЦБЗ", "Снегурочка"])
    text = " ".join(
        m["content"]
        for m in build_messages("Бумага", "Производитель: Монди", p.characteristics, Glossary([]))
    )
    assert "Туринский ЦБЗ" not in text
    assert "Производитель" in text


# --- извлечение «Общих сведений» по перечню: обычные слова принимались за завод ---


def _paper_specs(
    makers: list[str],
    countries: list[str] | None = None,
    extra: list[CharacteristicSpec] | None = None,
) -> list[CharacteristicSpec]:
    return [
        CharacteristicSpec(name="Формат", kind="qualitative", values=["A4"]),
        *(extra or []),
        CharacteristicSpec(
            name="Производитель", kind="qualitative", values=makers, group="general"
        ),
        CharacteristicSpec(
            name="Страна происхождения",
            kind="qualitative",
            values=countries or ["Россия"],
            group="general",
        ),
    ]


def _found(name: str, description: str, specs: list[CharacteristicSpec]) -> dict[str, str]:
    return {e.name: e.raw_value for e in extract_rules(name, description, specs)}


def test_label_word_is_not_a_maker() -> None:
    assert "Производитель" not in _found("Бумага Формат: А4", "", _paper_specs(["Формат"]))
    # слово перед двоеточием — метка, даже если такой характеристики у позиции нет
    assert "Производитель" not in _found(
        "Бумага Плотность: 80 г/м2", "", _paper_specs(["Плотность"])
    )


def test_maker_is_a_whole_word_not_a_stem() -> None:
    specs = _paper_specs(["Цветы", "ТехНН", "пирамида"])
    for name in ("Бумага цветная, 5 цветов", "Насос технологический", "Пирамидка детская"):
        assert "Производитель" not in _found(name, "", specs), name


def test_country_is_not_taken_from_an_adjective() -> None:
    specs = _paper_specs([], ["Россия", "Франция"])
    for name in ("Футболка, размер российский 48", "Багет французский"):
        assert "Страна происхождения" not in _found(name, "", specs), name


def test_maker_that_is_also_a_value_of_a_characteristic_is_not_taken() -> None:
    material = CharacteristicSpec(name="Материал", kind="qualitative", values=["ЭВА", "ПВХ"])
    found = _found("Коврик ЭВА 50x80", "", _paper_specs(["ЭВА"], extra=[material]))
    assert "Производитель" not in found
    assert found.get("Материал") == "ЭВА"


def test_maker_by_the_list_is_looked_for_in_the_name_only() -> None:
    found = _found(
        "Бумага офисная", "Подходит для принтеров, Туринский ЦБЗ", _paper_specs(["Туринский ЦБЗ"])
    )
    assert "Производитель" not in found


def test_maker_keeps_the_supplier_spelling() -> None:
    assert (
        _found("Блокнот BRAUBERG А6", "", _paper_specs(["Brauberg"]))["Производитель"] == "BRAUBERG"
    )


# --- «общие» — по группе позиции, а не по имени (у 46 категорий своя «Страна происхождения») ---


def _with_own_country() -> Position:
    return Position(
        code="B",
        name="Мебель",
        okpd2="31.01",
        characteristics=[
            CharacteristicSpec(name="Материал", kind="qualitative"),
            CharacteristicSpec(name="Цвет", kind="qualitative"),
            CharacteristicSpec(name="Высота", kind="qualitative"),
            CharacteristicSpec(
                name="Страна происхождения", kind="qualitative", values=["Россия", "Китай"]
            ),
        ],
        source="t",
    )


def test_own_country_characteristic_counts_for_moderation_minimum() -> None:
    chars = [CharIn(name=n, value="x") for n in ("Материал", "Цвет", "Высота")]
    card = CardIn(
        name="Стул", characteristics=[*chars, CharIn(name="Страна происхождения", value="Россия")]
    )
    assert not [i for i in detect(card, _with_own_country()) if "не меньше" in i.message]


def test_question_options_do_not_come_from_general_hints() -> None:
    # У A страна — своя характеристика категории, у B — подсказка «Общих сведений»: вариантов из
    # подсказок B в вопросе нет, B для вопроса — позиция без такой характеристики.
    own = Position(
        code="A",
        name="Стулья",
        okpd2="31.01",
        characteristics=[
            CharacteristicSpec(
                name="Страна происхождения", kind="qualitative", values=["Россия", "Китай"]
            )
        ],
        source="t",
    )
    hint = Position(
        code="B",
        name="Табуреты",
        okpd2="31.01",
        characteristics=[
            CharacteristicSpec(
                name="Страна происхождения",
                kind="qualitative",
                values=["Беларусь"],
                group="general",
            )
        ],
        source="t",
    )
    q = best_question([own, hint], {})
    assert q is not None and q.characteristic == "Страна происхождения"
    assert "Беларусь" not in [o.value for o in q.options]


# --- пробелы тестов, найденные порчей кода ---


def test_general_value_contradicting_the_name_is_not_blocked() -> None:
    p = paper("A", ["Brauberg", "Staff"])
    card = CardIn(
        name="Блокнот BRAUBERG А6",
        characteristics=[
            CharIn(name="Формат", value="A4"),
            CharIn(name="Цвет", value="белый"),
            CharIn(name="Производитель", value="САМСОН-ОПТ"),
        ],
    )
    assert not [i for i in detect(card, p) if i.field == "characteristics.Производитель"]


def test_general_value_named_in_description_is_not_a_conflict() -> None:
    p = paper("A", ["Brauberg", "Staff"])
    card = CardIn(
        name="Блокнот А6",
        description="Производитель: Brauberg",
        characteristics=[
            CharIn(name="Формат", value="A4"),
            CharIn(name="Цвет", value="белый"),
            CharIn(name="Производитель", value="Staff"),
        ],
    )
    assert not [i for i in detect(card, p) if i.field == "characteristics.Производитель"]


# --- нарицательные слова, «::», справочник без группы general ---


def test_lowercase_common_word_from_the_maker_list_is_not_a_maker() -> None:
    specs = _paper_specs(["марка", "щепа", "мулине", "комфорт"])
    for name in (
        "Стандартная почтовая марка номиналом 25 руб",
        "Декоративная щепа сосновая",
        "Нитки мулине, 8 м",
        "Матрас Джуно комфорт 90x200",
    ):
        assert "Производитель" not in _found(name, "", specs), name


def test_lowercase_latin_brand_is_still_a_maker() -> None:
    assert (
        _found("Ручка шариковая erich krause синяя", "", _paper_specs(["Erich Krause"]))[
            "Производитель"
        ]
        == "erich krause"
    )


def test_value_after_double_colon_has_no_leading_colon() -> None:
    found = _found("Бумага", "Страна происхождения:: Россия", _paper_specs([]))
    assert found["Страна происхождения"] == "Россия"


def test_two_different_makers_in_the_name_give_none() -> None:
    assert "Производитель" not in _found(
        "Набор BRAUBERG и Staff", "", _paper_specs(["BRAUBERG", "Staff"])
    )


def test_short_maker_inside_a_longer_one_is_the_same_find() -> None:
    specs = _paper_specs(["Attache", "Attache Selection"])
    assert _found("Ежедневник Attache Selection А5", "", specs)["Производитель"] == (
        "Attache Selection"
    )


def test_word_of_a_characteristic_name_is_not_a_maker() -> None:
    # без двоеточия и с заглавной: отсекает именно фильтр по именам характеристик
    assert "Производитель" not in _found("Бумага Формат А4", "", _paper_specs(["Формат"]))


def test_maker_keeps_number_sign_and_paired_quotes() -> None:
    specs = _paper_specs(["1 School", 'ФГБУ "ВГНКИ'])
    assert _found("Тетрадь №1 School 48 л", "", specs)["Производитель"] == "№1 School"
    assert _found('Реагент ФГБУ "ВГНКИ" 10 мл', "", specs)["Производитель"] == 'ФГБУ "ВГНКИ"'


def test_card_fields_do_not_count_on_a_catalog_without_general_group() -> None:
    # demo.json (справочник по умолчанию) групп не знает: «Производитель» и «Страна» — всё равно
    # поля карточки, а не характеристики, и минимум 4 из них не набирается
    p = Position(
        code="D",
        name="Стулья",
        okpd2="31.01",
        characteristics=[
            CharacteristicSpec(name="Материал", kind="qualitative"),
            CharacteristicSpec(name="Цвет", kind="qualitative"),
            CharacteristicSpec(name="Высота", kind="quantitative", unit="см"),
            CharacteristicSpec(name="Ширина", kind="quantitative", unit="см"),
        ],
        source="t",
    )
    card = CardIn(
        name="Стул",
        characteristics=[
            CharIn(name="Материал", value="дерево"),
            CharIn(name="Цвет", value="белый"),
            CharIn(name="Производитель", value="Ромашка"),
            CharIn(name="Страна происхождения", value="Россия"),
        ],
    )
    assert [i for i in detect(card, p) if "не меньше" in i.message]


def test_maker_equal_to_a_word_of_the_category_name_is_not_taken() -> None:
    # «Мешки для мусора 120 л» заполняли «Производитель: Мешки» — в перечне заводов
    # категории «Мешки полимерные» есть и такое значение; слово названия категории — товар, не завод
    import asyncio

    from app.extract import LLMItems
    from app.glossary import Glossary
    from app.pipeline import AutofillIn, Deps, autofill
    from app.session import SessionStore
    from tests.fakes import FakeLLM, FakeRedis

    bags = Position(
        code="М", name="Мешки полимерные", okpd2="22.22", source="t", characteristics=[
            CharacteristicSpec(name="Объем", kind="quantitative", unit="л"),
            CharacteristicSpec(name="Производитель", kind="qualitative", values=["Мешки", "Paclan"],
                               group="general"),
        ],
    )  # fmt: skip
    cat = Catalog([bags], "t")
    deps = Deps(
        catalog=cat, bm25=build_bm25(cat), vectors=None,
        llm=FakeLLM([LLMItems(items=[])] * 5),  # type: ignore[arg-type]
        glossary=Glossary([]), sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.2,
    )  # fmt: skip
    out = asyncio.run(autofill(AutofillIn(name="Мешки полимерные 120 л"), deps))
    maker = next(c for c in out.characteristics if c.name == "Производитель")
    assert maker.value is None
    paclan = asyncio.run(autofill(AutofillIn(name="Мешки полимерные Paclan 120 л"), deps))
    assert next(c for c in paclan.characteristics if c.name == "Производитель").value == "Paclan"
