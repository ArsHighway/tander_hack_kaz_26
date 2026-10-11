"""Цепочка «категория (КПГЗ) → позиция КТРУ»: КТРУ сужается внутри найденной категории."""

import asyncio

import pytest

from app.catalog import Catalog, CharacteristicSpec, Position
from app.extract import LLMItems
from app.glossary import Glossary
from app.narrowing import CONFIRM
from app.pipeline import AnswerIn, AutofillIn, BadAnswer, Deps, answer, autofill, check
from app.schemas import CardIn, CharIn, CheckOut, Issue
from app.search import build_bm25
from app.session import SessionStore
from tests.fakes import FakeLLM, FakeRedis

WHITE, COLOR = "17.12.14.129-00000031", "17.12.14.129-00000017"

CATEGORY = Position(
    code="01.15.02.01",
    name="Бумага для офисной техники",
    okpd2="17.12.14.129",
    kpgz="01.15.02.01",
    characteristics=[
        CharacteristicSpec(
            name="Формат", kind="qualitative", values=["A4", "A3"], ktru=[WHITE, COLOR],
            spgz=["1599802915"],
        ),
        CharacteristicSpec(name="Цветность", kind="qualitative", values=["Белая", "Цветная"]),
    ],
    source="test",
)  # fmt: skip


def ktru(code: str, name: str, color: str) -> Position:
    return Position(
        code=code,
        name=name,
        okpd2="17.12.14.129",
        characteristics=[
            CharacteristicSpec(name="Цветность", kind="qualitative", values=[color], required=True),
            CharacteristicSpec(name="Формат", kind="qualitative", values=["A4", "A3"]),
        ],
        source="test",
    )


KTRU = Catalog(
    [ktru(WHITE, "Бумага для офисной техники белая", "Белая"),
     ktru(COLOR, "Бумага для офисной техники цветная", "Цветная")],
    version="ktru-test",
)  # fmt: skip
CATALOG = Catalog([CATEGORY], version="test")


def _deps() -> Deps:
    return Deps(
        catalog=CATALOG,
        bm25=build_bm25(CATALOG),
        vectors=None,
        llm=FakeLLM([LLMItems(items=[])] * 10),  # type: ignore[arg-type]
        glossary=Glossary([]),
        sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.2,
        ktru=KTRU,
    )


def test_chain_shows_category_codes_and_narrows_ktru_from_text() -> None:
    out = asyncio.run(
        autofill(
            AutofillIn(name="Бумага для офисной техники", description="Цветность: белая"), _deps()
        )
    )
    assert out.ktru.final_code == CATEGORY.code
    chain = out.chain
    assert chain is not None
    assert (chain.category, chain.kpgz, chain.okpd2) == (
        CATEGORY.code,
        "01.15.02.01",
        "17.12.14.129",
    )
    assert chain.spgz == ["1599802915"]
    assert chain.ktru is not None
    assert chain.ktru.final_code == WHITE


def test_ktru_question_is_answered_in_its_own_stage() -> None:
    deps = _deps()
    out = asyncio.run(autofill(AutofillIn(name="Бумага для офисной техники"), deps))
    assert out.chain is not None and out.chain.ktru is not None
    question = out.chain.ktru.question
    assert question is not None and question.characteristic == "Цветность"
    after = asyncio.run(
        answer(
            out.session_id,
            AnswerIn(characteristic="Цветность", value="Цветная", stage="ktru"),
            deps,
        )
    )
    assert after.ktru.final_code == CATEGORY.code  # ответ про КТРУ не трогает категорию
    assert after.chain is not None and after.chain.ktru is not None
    assert after.chain.ktru.final_code == COLOR
    filled = {c.name: c.value for c in after.characteristics}
    assert filled["Цветность"] == "Цветная"  # ответ про КТРУ попал и в поле карточки


def test_ktru_answer_outside_ktru_positions_is_rejected() -> None:
    deps = _deps()
    out = asyncio.run(autofill(AutofillIn(name="Бумага для офисной техники"), deps))
    with pytest.raises(BadAnswer):
        asyncio.run(
            answer(
                out.session_id,
                AnswerIn(characteristic="Цветность", value="Синяя", stage="ktru"),
                deps,
            )
        )


def test_catalog_of_ktru_positions_is_its_own_ktru() -> None:
    # Справочник организаторов может быть самим КТРУ: найденная позиция и есть код КТРУ.
    deps = _deps()
    deps.catalog = KTRU
    deps.bm25 = build_bm25(KTRU)
    deps.ktru = None
    out = asyncio.run(
        autofill(
            AutofillIn(name="Бумага для офисной техники цветная", description="Цветность: цветная"),
            deps,
        )
    )
    assert out.ktru.final_code == COLOR
    assert out.chain is not None and out.chain.ktru is not None
    assert (out.chain.ktru.final_code, out.chain.ktru_missing) == (COLOR, None)


def test_five_digit_ktru_code_is_its_own_ktru_too() -> None:
    # В данных ЕИС бывают коды с пятизначным хвостом: «19.20.21.200-00001».
    short = ktru("19.20.21.200-00001", "Бумага для офисной техники белая", "Белая")
    deps = _deps()
    deps.catalog = Catalog([short], version="test")
    deps.bm25 = build_bm25(deps.catalog)
    deps.ktru = None
    out = asyncio.run(autofill(AutofillIn(name="Бумага для офисной техники белая"), deps))
    assert out.chain is not None and out.chain.ktru is not None
    assert (out.chain.ktru.final_code, out.chain.ktru_missing) == ("19.20.21.200-00001", None)


def test_ktru_catalog_without_linked_positions_says_not_loaded() -> None:
    deps = _deps()
    deps.ktru = Catalog([ktru("17.12.14.129-00000099", "Бумага другая", "Серая")], version="k")
    out = asyncio.run(autofill(AutofillIn(name="Бумага для офисной техники"), deps))
    assert out.chain is not None
    assert (out.chain.ktru, out.chain.ktru_missing) == (None, "not_loaded")


def test_partly_loaded_ktru_asks_to_confirm_instead_of_guessing() -> None:
    # Загружена только белая позиция из двух привязанных: текст про цветную — код не ставится сам.
    deps = _deps()
    deps.ktru = Catalog([ktru(WHITE, "Бумага для офисной техники белая", "Белая")], version="k")
    out = asyncio.run(
        autofill(
            AutofillIn(name="Бумага для офисной техники", description="Цветность: цветная"), deps
        )
    )
    assert out.chain is not None and out.chain.ktru is not None
    state = out.chain.ktru
    assert state.final_code is None and state.question is not None
    assert [o.value for o in state.question.options][:1] == [WHITE]
    after = asyncio.run(
        answer(
            out.session_id, AnswerIn(characteristic="Позиция КТРУ", value=WHITE, stage="ktru"), deps
        )
    )
    assert after.chain is not None and after.chain.ktru is not None
    assert after.chain.ktru.final_code == WHITE


def test_chain_carries_category_path_and_portal_id() -> None:
    category = CATEGORY.model_copy(
        update={"portal_id": 793371198, "path": ["Бумага", "Для офисной техники"]}
    )
    deps = _deps()
    deps.catalog = Catalog([category], version="test")
    deps.bm25 = build_bm25(deps.catalog)
    out = asyncio.run(autofill(AutofillIn(name="Бумага для офисной техники"), deps))
    assert out.chain is not None
    assert (out.chain.path, out.chain.portal_id) == (["Бумага", "Для офисной техники"], 793371198)


def test_category_without_ktru_links_says_no_links() -> None:
    plain = CATEGORY.model_copy(
        update={
            "characteristics": [c.model_copy(update={"ktru": []}) for c in CATEGORY.characteristics]
        }
    )
    deps = _deps()
    deps.catalog = Catalog([plain], version="test")
    deps.bm25 = build_bm25(deps.catalog)
    out = asyncio.run(autofill(AutofillIn(name="Бумага для офисной техники"), deps))
    assert out.chain is not None
    assert (out.chain.ktru_linked, out.chain.ktru, out.chain.ktru_missing) == (0, None, "no_links")


def test_linked_ktru_missing_from_catalog_says_not_loaded() -> None:
    deps = _deps()
    deps.ktru = None
    out = asyncio.run(autofill(AutofillIn(name="Бумага для офисной техники"), deps))
    assert out.chain is not None
    assert (out.chain.ktru_linked, out.chain.ktru, out.chain.ktru_missing) == (
        2,
        None,
        "not_loaded",
    )


def test_loaded_ktru_positions_mean_nothing_missing() -> None:
    out = asyncio.run(autofill(AutofillIn(name="Бумага для офисной техники"), _deps()))
    assert out.chain is not None and out.chain.ktru is not None
    assert out.chain.ktru_missing is None


def test_fields_carry_unit_full_name_and_group() -> None:
    category = CATEGORY.model_copy(
        update={
            "characteristics": [
                *CATEGORY.characteristics,
                CharacteristicSpec(name="Масса бумаги", kind="quantitative", unit="г/м2"),
                CharacteristicSpec(name="Производитель", kind="qualitative", group="general"),
            ]
        }
    )
    deps = _deps()
    deps.catalog = Catalog([category], version="test")
    deps.bm25 = build_bm25(deps.catalog)
    out = asyncio.run(autofill(AutofillIn(name="Бумага для офисной техники"), deps))
    fields = {c.name: c for c in out.characteristics}
    assert fields["Масса бумаги"].unit_name == "Грамм на квадратный метр"
    assert fields["Производитель"].group == "general"
    assert fields["Формат"].group is None


def test_no_ktru_catalog_still_shows_category_codes() -> None:
    deps = _deps()
    deps.ktru = None
    out = asyncio.run(autofill(AutofillIn(name="Бумага для офисной техники"), deps))
    assert out.chain is not None
    assert (out.chain.okpd2, out.chain.ktru, out.chain.ktru_linked) == ("17.12.14.129", None, 2)


def test_value_of_any_ktru_position_narrows_without_a_label() -> None:
    # Перечень значений у каждой позиции КТРУ свой («Цветная» только у второй): значение в
    # наименовании без метки ищется по перечням всех позиций, а не только первой.
    out = asyncio.run(autofill(AutofillIn(name="Бумага для офисной техники цветная"), _deps()))
    assert out.chain is not None and out.chain.ktru is not None
    assert out.chain.ktru.final_code == COLOR


def test_long_catalog_names_and_values_do_not_break_the_card() -> None:
    # в стандартах портала имя характеристики до 400 знаков, значение перечня до 2 743:
    # карточка с ними собирается и проверяется, а не падает 500 на лимитах ввода
    from app.schemas import CardIn, CharIn

    long_name, long_value = "Набор реагентов " + "х" * 390, "Значение " + "ю" * 2740
    p = Position(
        code="01.99",
        name="Наборы реагентов",
        okpd2="21.20",
        characteristics=[
            CharacteristicSpec(name=long_name, kind="qualitative", values=[long_value, "Другое"])
        ],
        source="test",
    )
    cat = Catalog([p], version="t")
    deps = _deps()
    deps.catalog, deps.bm25, deps.ktru = cat, build_bm25(cat), None
    out = asyncio.run(
        autofill(
            AutofillIn(name="Наборы реагентов", description=f"{long_name}: {long_value}"), deps
        )
    )
    assert out.ktru.final_code == "01.99"
    CardIn(name="Наборы реагентов", characteristics=[CharIn(name=long_name, value=long_value)])


def test_single_category_below_70_percent_asks_to_confirm() -> None:
    # «Смеситель … с выдвижной лейкой» уходил в «Фурнитуру для смесителей» при
    # совпадении 0,43 без вопроса — единственный кандидат, потому что остальные отсеяны. Ниже порога
    # автовыбора код ставится только после подтверждения.
    from app.search.exemplars import ExemplarIndex

    mixers = Position(
        code="СМЕС", name="Смесители водоразборные", okpd2="28.14", characteristics=[
            CharacteristicSpec(name="Покрытие", kind="qualitative", values=["хром"])
        ], source="test",
    )  # fmt: skip
    cat = Catalog([mixers], version="t")
    index = ExemplarIndex([
        ("СМЕС", "Смеситель хром для кухни"), ("СМЕС", "Смеситель хром для ванны"),
        ("ЛЕЙ", "Лейка хром для смесителя"), ("ЛЕЙ", "Лейка хром душевая"),
    ])  # fmt: skip
    deps = _deps()
    deps.catalog, deps.bm25, deps.ktru, deps.exemplars = cat, build_bm25(cat), None, index
    share = dict(index.vote("Смеситель хром"))["СМЕС"]
    assert share < 0.7  # контроль: кандидат единственный, но голос ниже порога
    out = asyncio.run(autofill(AutofillIn(name="Смеситель хром"), deps))
    assert out.ktru.final_code is None
    assert out.ktru.question is not None and out.ktru.question.characteristic == "Позиция КТРУ"


def test_low_share_category_left_by_supplier_answer_is_not_asked_again() -> None:
    # «Мешки для мусора 120 л» → ответ «Материал» оставил одну категорию с 53 % —
    # модуль переспрашивал «Позиция КТРУ», хотя в варианте было «останется 1»
    from app.search.exemplars import ExemplarIndex

    def pos(code: str, name: str, mat: str) -> Position:
        return Position(code=code, name=name, okpd2="22.22", source="test", characteristics=[
            CharacteristicSpec(name="Материал", kind="qualitative", values=[mat]),
        ])  # fmt: skip

    cat = Catalog(
        [
            pos("П", "Мешки для мусора полимерные", "ПНД"),
            pos("Б", "Мешки для мусора бумажные", "Крафт"),
        ],
        "t",
    )
    index = ExemplarIndex([
        ("П", "Мешки для мусора ПНД"), ("П", "Мешки для мусора 120 л"),
        ("Б", "Мешки бумажные для мусора"), ("Б", "Мешки для мусора крафт"),
    ])  # fmt: skip
    deps = _deps()
    deps.catalog, deps.bm25, deps.ktru, deps.exemplars = cat, build_bm25(cat), None, index
    out = asyncio.run(autofill(AutofillIn(name="Мешки для мусора"), deps))
    # Названия различаются не значениями перечня («полимерные» — не «ПНД»): разные категории,
    # человек выбирает свою.
    assert out.ktru.question is not None and out.ktru.question.characteristic == CONFIRM
    after = asyncio.run(answer(out.session_id, AnswerIn(characteristic=CONFIRM, value="П"), deps))
    assert after.ktru.final_code == "П"


def test_answer_given_for_the_category_is_not_asked_again_for_ktru() -> None:
    # Поставщик уже ответил «Цветность: Белая», выбирая категорию; второй этап (КТРУ) тот же
    # вопрос не задаёт — ответ сужает и его (меньше действий человека)
    k1, k2, k3 = "17.12.14.129-00000101", "17.12.14.129-00000102", "17.12.14.129-00000103"

    def category(code: str, name: str, color: str) -> Position:
        return Position(
            code=code, name=name, okpd2="17.12.14.129", source="test",
            characteristics=[
                CharacteristicSpec(name="Цветность", kind="qualitative", values=[color],
                                   ktru=[k1, k2, k3]),
                CharacteristicSpec(name="Формат", kind="qualitative", values=["A4"]),
            ],
        )  # fmt: skip

    catalog = Catalog(
        [category("01.15.02.01", "Бумага офисная белая", "Белая"),
         category("01.15.02.02", "Бумага офисная цветная", "Цветная")],
        version="test",
    )  # fmt: skip
    ktru_catalog = Catalog(
        [ktru(k1, "Бумага белая", "Белая"), ktru(k2, "Бумага цветная", "Цветная"),
         ktru(k3, "Бумага цветная яркая", "Цветная")],
        version="ktru-test",
    )  # fmt: skip
    deps = Deps(
        catalog=catalog,
        bm25=build_bm25(catalog),
        vectors=None,
        llm=FakeLLM([LLMItems(items=[])] * 10),  # type: ignore[arg-type]
        glossary=Glossary([]),
        sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.0,
        ktru=ktru_catalog,
    )

    async def scenario() -> tuple[str | None, str | None, object]:
        out = await autofill(AutofillIn(name="Бумага офисная"), deps)
        assert out.ktru.question is not None and out.ktru.question.characteristic == "Цветность"
        done = await answer(
            out.session_id, AnswerIn(characteristic="Цветность", value="Белая"), deps
        )
        chain = done.chain
        assert chain is not None and chain.ktru is not None
        return done.ktru.final_code, chain.ktru.final_code, chain.ktru.question

    category_code, ktru_code, question = asyncio.run(scenario())
    assert (category_code, ktru_code, question) == ("01.15.02.01", k1, None)


# /check: категория (КПГЗ) и итоговый код КТРУ второго этапа — разные поля карточки.
OTHER = "17.12.14.129-00000099"
KTRU_WITH_OTHER = Catalog(
    [*KTRU.all(), ktru(OTHER, "Бумага для офисной техники неон", "Неон")], version="ktru-other"
)
PAPER_CHARS = [CharIn(name="Формат", value="A4"), CharIn(name="Цветность", value="Белая")]


def _code_issues(out: CheckOut) -> list[Issue]:
    return [i for i in out.issues if i.field in ("ktru_code", "category_code")]


def test_check_accepts_ktru_code_of_second_stage() -> None:
    card = CardIn(
        name="Бумага для офисной техники", category_code=CATEGORY.code, ktru_code=WHITE,
        characteristics=PAPER_CHARS,
    )  # fmt: skip
    out = check(card, _deps())
    assert _code_issues(out) == []
    assert not any("нет в справочнике" in i.message for i in out.issues)


def test_check_with_category_code_in_ktru_code_works_as_before() -> None:
    card = CardIn(
        name="Бумага для офисной техники", ktru_code=CATEGORY.code, characteristics=PAPER_CHARS
    )
    assert _code_issues(check(card, _deps())) == []


def test_check_with_only_ktru_code_finds_its_category() -> None:
    card = CardIn(name="Бумага для офисной техники", ktru_code=COLOR, characteristics=PAPER_CHARS)
    out = check(card, _deps())
    assert _code_issues(out) == []
    # Проверка шла по категории и позиции КТРУ: «Белая» не из перечня цветной бумаги (но из
    # перечня категории — сведения «записано иначе», не предупреждение).
    assert any(COLOR in i.message and "записано иначе" in i.message for i in out.issues)


def test_ktru_code_not_linked_to_category_is_a_warning() -> None:
    deps = _deps()
    deps.ktru = KTRU_WITH_OTHER
    card = CardIn(
        name="Бумага для офисной техники", category_code=CATEGORY.code, ktru_code=OTHER,
        characteristics=PAPER_CHARS,
    )  # fmt: skip
    found = _code_issues(check(card, deps))
    assert [(i.kind, i.severity) for i in found] == [("mismatch", "warn")]
    assert "не привязана к категории" in found[0].message


def test_value_outside_ktru_position_list_is_reported() -> None:
    card = CardIn(
        name="Бумага для офисной техники", category_code=CATEGORY.code, ktru_code=WHITE,
        characteristics=[CharIn(name="Формат", value="A4"),
                         CharIn(name="Цветность", value="Цветная")],
    )  # fmt: skip
    out = check(card, _deps())
    found = [i for i in out.issues if i.field == "characteristics.Цветность"]
    # Вне перечня позиции КТРУ, но в перечне категории портала — info со значениями КТРУ:
    # перечни ЕИС и портала пишутся по-разному (на честных карточках warn почти все ложные).
    assert [(i.kind, i.severity) for i in found] == [("not_in_list", "info")]
    assert out.verdict == "ready"
    assert WHITE in found[0].message and "«Белая»" in found[0].message
    # Поле, которого в категории нет, но и в КТРУ нет, — без «нет в позиции КТРУ».
    assert not any("позиции КТРУ" in i.message for i in out.issues if i.field != found[0].field)


def test_unknown_ktru_code_with_known_category_is_blocked() -> None:
    card = CardIn(
        name="Бумага для офисной техники", category_code=CATEGORY.code,
        ktru_code="00.00.00.000-00000000", characteristics=PAPER_CHARS,
    )  # fmt: skip
    found = _code_issues(check(card, _deps()))
    assert [(i.field, i.severity) for i in found] == [("ktru_code", "block")]
    assert "КТРУ" in found[0].message


def test_category_without_chosen_ktru_is_info_not_block() -> None:
    card = CardIn(
        name="Бумага для офисной техники", category_code=CATEGORY.code, characteristics=PAPER_CHARS
    )
    out = check(card, _deps())
    assert [(i.message, i.severity) for i in _code_issues(out)] == [
        ("Позиция КТРУ не выбрана.", "info")
    ]


def test_unknown_category_code_does_not_call_it_ktru() -> None:
    card = CardIn(name="Бумага", category_code="99.99", ktru_code=WHITE, characteristics=[])
    found = _code_issues(check(card, _deps()))
    assert [(i.field, i.severity) for i in found] == [("category_code", "block")]
    assert "КТРУ" not in found[0].message


def test_demo_catalog_of_ktru_positions_without_ktru_paths_works_as_before() -> None:
    demo = Catalog([ktru(WHITE, "Бумага для офисной техники белая", "Белая")], version="demo")
    deps = _deps()
    deps.catalog, deps.bm25, deps.ktru = demo, build_bm25(demo), None
    for card in (
        CardIn(name="Бумага белая", ktru_code=WHITE, characteristics=PAPER_CHARS),
        CardIn(name="Бумага белая", category_code=WHITE, ktru_code=WHITE,
               characteristics=PAPER_CHARS),
    ):  # fmt: skip
        out = check(card, deps)
        assert _code_issues(out) == [] and out.verdict == "ready", out.issues


def test_batch_row_reports_category_and_ktru_codes_separately() -> None:
    from app.batch import BatchRow, process_row

    row = BatchRow(row=2, name="Бумага для офисной техники", description="Цветность: белая")
    result = asyncio.run(process_row(row, _deps()))
    assert (result.category_code, result.ktru_code) == (CATEGORY.code, WHITE)


def test_nothing_fits_on_ktru_stage_says_ktru_not_category() -> None:
    from app.narrowing import NOTHING_FITS

    deps = _deps()
    out = asyncio.run(autofill(AutofillIn(name="Бумага для офисной техники"), deps))
    assert out.chain is not None and out.chain.ktru is not None
    question = out.chain.ktru.question
    assert question is not None
    after = asyncio.run(
        answer(
            out.session_id,
            AnswerIn(characteristic=question.characteristic, value=NOTHING_FITS, stage="ktru"),
            deps,
        )
    )
    assert after.ktru.final_code == CATEGORY.code
    assert after.chain is not None and after.chain.ktru is not None
    assert after.chain.ktru.steps[0].startswith("Позиция КТРУ не подобрана")


def test_ktru_answer_replaces_the_text_value_of_the_same_field() -> None:
    a, b = "28.24.11.000-00000001", "28.24.11.000-00000002"
    category = Position(
        code="01.11.02.01.08.03", name="Шлифмашины", okpd2="28.24.11.000", kpgz="01.11.02.01.08.03",
        characteristics=[CharacteristicSpec(name="Вид", kind="qualitative",
                                            values=["Угловая", "Ленточная"], ktru=[a, b])],
        source="test",
    )  # fmt: skip

    def kp(code: str, kind: str) -> Position:
        spec = CharacteristicSpec(name="Вид", kind="qualitative", values=[kind], required=True)
        return Position(code=code, name="Машина шлифовальная", okpd2="28.24.11.000",
                        characteristics=[spec], source="test")  # fmt: skip

    catalog = Catalog([category], version="t")
    deps = Deps(
        catalog=catalog, bm25=build_bm25(catalog), vectors=None,
        llm=FakeLLM([LLMItems(items=[])] * 10),  # type: ignore[arg-type]
        glossary=Glossary([]), sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.2,
        ktru=Catalog([kp(a, "УШМ"),
                      kp(b, "ЛШМ")], version="k"),
    )  # fmt: skip
    out = asyncio.run(autofill(AutofillIn(name="Шлифмашины угловая"), deps))
    assert {c.name: c.value for c in out.characteristics}["Вид"] == "Угловая"
    assert out.chain is not None and out.chain.ktru is not None
    # Варианты КТРУ — сокращения, которых в тексте нет: «угловая» на вопрос не отвечает.
    q = out.chain.ktru.question
    assert q is not None and q.characteristic == "Вид"
    after = asyncio.run(
        answer(
            out.session_id,
            AnswerIn(characteristic="Вид", value="ЛШМ", stage="ktru"),
            deps,
        )
    )
    assert {c.name: c.value for c in after.characteristics}["Вид"] == "ЛШМ"


def test_required_ktru_characteristic_is_required_in_card_and_check() -> None:
    out = asyncio.run(
        autofill(
            AutofillIn(name="Бумага для офисной техники", description="Цветность: белая"), _deps()
        )
    )
    assert out.chain is not None and out.chain.ktru is not None
    assert out.chain.ktru.final_code == WHITE
    assert {c.name: c.required for c in out.characteristics}["Цветность"] is True
    card = CardIn(
        name="Бумага для офисной техники",
        category_code=CATEGORY.code,
        ktru_code=WHITE,
        characteristics=[CharIn(name="Формат", value="A4")],
    )
    issues = [i for i in check(card, _deps()).issues if i.field == "characteristics.Цветность"]
    assert [i.severity for i in issues] == ["warn"]
