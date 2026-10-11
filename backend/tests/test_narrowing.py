from pathlib import Path

from app.catalog import Catalog, CharacteristicSpec, Position
from app.narrowing import (
    CONFIRM,
    NOTHING_FITS,
    OTHER_VALUE,
    Known,
    best_question,
    build_state,
    compatible,
    narrow,
)
from app.search import Candidate

FAKE = Path(__file__).parent / "fixtures" / "fake"
CATALOG = Catalog.load([FAKE / "ktru.json"])
WHITE_CODE = "17.12.14.129-00000101"
COLOR_CODE = "17.12.14.129-00000102"
PAPER = [CATALOG.get(WHITE_CODE), CATALOG.get(COLOR_CODE)]


def _cands(positions: list) -> list[Candidate]:
    return [Candidate(code=p.code, name=p.name, score=1.0, why=[]) for p in positions]


def _answer(value: str, unit: str | None = None) -> Known:
    return Known(value=value, unit=unit, source="answer")


def _text(value: str, unit: str | None = None) -> Known:
    return Known(value=value, unit=unit, source="text")


def test_answer_on_characteristic_missing_in_position_excludes_it() -> None:
    # Ответ на вопрос — выбор человека: позиция без такой характеристики ему не подходит.
    assert not compatible(PAPER[0], {"Цветность": _answer("Цветная")})
    assert compatible(PAPER[1], {"Цветность": _answer("Цветная")})


def test_text_value_on_missing_characteristic_keeps_position() -> None:
    # Значение из текста могло относиться к характеристике, которой в позиции просто нет.
    assert compatible(PAPER[1], {"Белизна по CIE": _text("160", "%")})


def test_value_outside_list_excludes_position() -> None:
    assert not compatible(PAPER[1], {"Количество листов в пачке": _text("1000", "шт")})


def test_number_is_compared_with_buckets() -> None:
    assert compatible(PAPER[0], {"Масса бумаги площадью 1 м2": _text("80", "г/м2")})


def test_known_colour_gives_final_code() -> None:
    state = build_state(_cands(PAPER), PAPER, {"Цветность": _answer("Цветная")})
    assert (state.remaining, state.final_code, state.question) == (1, COLOR_CODE, None)


def test_question_options_report_remaining_and_cuts() -> None:
    q = best_question(PAPER, {})
    assert q is not None
    for o in q.options:
        assert o.cuts == 2 - o.remaining
        assert o.remaining == len(narrow(PAPER, {q.characteristic: _answer(o.value)}))


def test_ktru_question_options_name_the_codes_left() -> None:
    q = best_question(PAPER, {})
    assert q is not None
    for o in q.options:
        if o.value in (NOTHING_FITS, OTHER_VALUE):
            assert o.codes == []
            continue
        left = [p.code for p in narrow(PAPER, {q.characteristic: _answer(o.value)})]
        assert o.codes == left[:3]
        assert len(o.codes) == min(o.remaining, 3)
    assert any(len(o.codes) == 1 for o in q.options)


def test_answering_the_question_narrows_as_promised() -> None:
    q = best_question(PAPER, {})
    assert q is not None
    decisive = min(q.options, key=lambda o: o.remaining)
    state = build_state(_cands(PAPER), PAPER, {q.characteristic: _answer(decisive.value)})
    assert state.remaining == decisive.remaining


def test_question_prefers_characteristic_every_candidate_has() -> None:
    q = best_question(PAPER, {})
    assert q is not None
    spec_names = [{c.name for c in p.characteristics} for p in PAPER]
    assert all(q.characteristic in names for names in spec_names)


def test_dead_end_names_the_value_to_check() -> None:
    known = {"Формат": _answer("A5"), "Цветность": _answer("Цветная")}
    state = build_state(_cands(PAPER), PAPER, known)
    assert state.remaining == 0
    assert state.final_code is None
    assert state.steps[0].startswith("Нет позиции с такими значениями")


def test_no_candidates_says_category_not_found() -> None:
    state = build_state([], [], {})
    assert state.steps == [
        "Категория не найдена в справочнике: уточните наименование или опишите товар подробнее"
    ]


def test_indistinguishable_positions_ask_for_manual_choice() -> None:
    twins = [
        Position(
            code=f"T-{i}",
            name=f"Позиция {i}",
            okpd2="00",
            source="test",
            characteristics=[CharacteristicSpec(name="Цвет", kind="qualitative", values=["Синий"])],
        )
        for i in (1, 2)
    ]
    # Различать нечем — модуль не перечисляет коды текстом, а спрашивает: какая позиция ваша.
    state = build_state(_cands(twins), twins, {})
    assert state.question is not None and state.question.characteristic == CONFIRM
    assert [o.value for o in state.question.options] == ["T-1", "T-2", NOTHING_FITS]
    assert [o.codes for o in state.question.options] == [["T-1"], ["T-2"], []]
    assert state.steps[0].startswith("Подошли позиции без различающих характеристик")


def test_question_on_partial_characteristic_offers_none_option() -> None:
    known = {
        "Формат": _text("A4"),
        "Количество листов в пачке": _text("500", "шт"),
        "Масса бумаги площадью 1 м2": _text("80", "г/м2"),
    }
    q = best_question(PAPER, known)
    assert q is not None
    values = [o.value for o in q.options]
    assert "" in values  # «нет такой характеристики» — у белой бумаги её нет
    assert len(values) >= 2


def test_required_characteristic_is_asked_when_it_still_splits_candidates() -> None:
    # Формат и масса совпали, но белизна у позиций разная: это не точный код.
    shared = [
        CharacteristicSpec(
            name="Формат", kind="qualitative", values=["A4"], required=True, level_define=True
        ),
        CharacteristicSpec(
            name="Масса бумаги площадью 1 м2",
            kind="quantitative",
            unit="г/м2",
            values=["80"],
            required=True,
            level_define=True,
        ),
    ]
    twins = [
        Position(
            code=f"T-{i}",
            name="Бумага",
            okpd2="17",
            source="test",
            characteristics=[
                *shared,
                CharacteristicSpec(
                    name="Белизна по CIE",
                    kind="quantitative",
                    unit="%",
                    values=[str(140 + i)],
                    required=True,
                ),
            ],
        )
        for i in (1, 2)
    ]
    known = {"Формат": _text("A4"), "Масса бумаги площадью 1 м2": _text("80", "г/м2")}
    state = build_state(_cands(twins), twins, known, text="Бумага А4 80")
    assert state.final_code is None
    assert state.question is not None
    assert state.question.characteristic == "Белизна по CIE"


def test_level_define_difference_is_still_asked() -> None:
    positions = [
        Position(
            code=code,
            name="Бумага",
            okpd2="17",
            source="test",
            characteristics=[
                CharacteristicSpec(
                    name="Количество листов в пачке",
                    kind="quantitative",
                    unit="шт",
                    values=[sheets],
                    required=True,
                    level_define=True,
                ),
                CharacteristicSpec(
                    name="Белизна по CIE",
                    kind="quantitative",
                    unit="%",
                    values=["146"],
                    required=True,
                ),
            ],
        )
        for code, sheets in (("T-500", "500"), ("T-250", "250"))
    ]
    state = build_state(_cands(positions), positions, {})
    assert state.final_code is None
    assert state.question is not None
    assert state.question.characteristic == "Количество листов в пачке"


def test_larger_sheet_group_is_not_chosen_when_the_count_is_missing() -> None:
    # Три позиции по 500 листов и одна на 250: большинство не подставляет 500.
    sheets = ["500", "500", "500", "250"]
    positions = [
        Position(
            code=f"T-{i}",
            name="Бумага",
            okpd2="17",
            source="test",
            characteristics=[
                CharacteristicSpec(
                    name="Формат",
                    kind="qualitative",
                    values=["A4"],
                    required=True,
                    level_define=True,
                ),
                CharacteristicSpec(
                    name="Количество листов в пачке",
                    kind="quantitative",
                    unit="шт",
                    values=[count],
                    required=True,
                    level_define=True,
                ),
            ],
        )
        for i, count in enumerate(sheets)
    ]
    state = build_state(
        _cands(positions),
        positions,
        {"Формат": _text("A4")},
        text="Бумага А4",
    )
    assert state.final_code is None
    assert state.remaining == 4
    assert state.question is not None
    assert state.question.characteristic == "Количество листов в пачке"


def test_none_answer_keeps_positions_without_the_characteristic() -> None:
    left = narrow(PAPER, {"Цветность": _answer("")})
    assert [p.code for p in left] == [WHITE_CODE]


# Кандидаты разных товаров (ни одного общего слова в названиях): вопрос по характеристике одного из
# них человек не поймёт («Категория шины» про аккумулятор) — выбор позиции.
def test_candidates_of_different_products_give_a_position_choice() -> None:
    from app.catalog import CharacteristicSpec, Position
    from app.narrowing import CONFIRM, NOTHING_FITS, build_state
    from app.search import Candidate

    def pos(code: str, name: str, char: str, values: list[str]) -> Position:
        spec = CharacteristicSpec(name=char, kind="qualitative", values=values)
        return Position(
            code=code, name=name, okpd2="", unit=None, characteristics=[spec], source="t"
        )

    positions = [
        pos("A", "Аккумуляторы свинцовые", "Напряжение", ["12"]),
        pos("B", "Шины пневматические", "Категория использования шины", ["Зимние", "Летние"]),
    ]
    cands = [Candidate(code=p.code, name=p.name, score=1.0, why=[]) for p in positions]
    state = build_state(cands, positions, {})
    assert state.question is not None and state.question.characteristic == CONFIRM
    assert [o.value for o in state.question.options] == ["A", "B", NOTHING_FITS]
    chosen = build_state(cands, positions, {}, confirmed="B")
    assert chosen.final_code == "B"


def test_options_that_cut_nothing_are_one_other_value() -> None:
    # Жюри: варианты, после которых остаются все позиции, — не выбор. Они сводятся в «другое».
    def pos(code: str, colours: list[str]) -> Position:
        return Position(
            code=code,
            name="Бумага",
            okpd2="17.12.14.129",
            source="test",
            characteristics=[
                CharacteristicSpec(name="Цвет", kind="qualitative", values=colours),
            ],
        )

    positions = [pos("A", ["Белый", "Серый"]), pos("B", ["Белый", "Желтый"])]
    q = best_question(positions, {})
    assert q is not None
    assert [(o.value, o.remaining) for o in q.options] == [
        ("Серый", 1),
        ("Желтый", 1),
        (OTHER_VALUE, 2),
        (NOTHING_FITS, 0),
    ]
    assert narrow(positions, {"Цвет": Known(value=OTHER_VALUE, source="answer")}) == positions


def _colours(order: list[str]) -> list[Position]:
    colour = {"A": "Белый", "B": "Белый", "C": "Белый", "D": "Белый", "E": "Красный"}
    return [
        Position(
            code=c,
            name=f"Краска {c}",
            okpd2="00",
            source="test",
            characteristics=[
                CharacteristicSpec(name="Цвет", kind="qualitative", values=[colour[c]])
            ],
        )
        for c in order
    ]


def test_option_codes_are_first_three_in_candidate_order() -> None:
    for order in (["A", "B", "C", "D", "E"], ["E", "D", "C", "B", "A"]):
        positions = _colours(order)
        q = best_question(positions, {})
        assert q is not None and q.characteristic == "Цвет"
        white = next(o for o in q.options if o.value == "Белый")
        assert white.remaining == 4
        assert white.codes == [c for c in order if c != "E"][:3]


def test_confirm_question_names_its_position() -> None:
    from app.narrowing import confirm_question

    [position] = _colours(["A"])
    q = confirm_question(position)
    assert [o.codes for o in q.options] == [["A"], []]


def test_spec_lookup_does_not_refold_every_name_on_every_call() -> None:
    # «Стол инструментальный» отвечал 26 с — 7 млн вызовов fold в _spec при
    # сужении 12 кандидатов. Поиск по имени — по готовому словарю позиции
    import time

    from app.catalog import CharacteristicSpec, Position
    from app.narrowing import _spec

    specs = [
        CharacteristicSpec(name=f"Характеристика номер {i}", kind="qualitative") for i in range(200)
    ]
    position = Position(code="P", name="x", okpd2="", source="t", characteristics=specs)
    started = time.perf_counter()
    for i in range(20000):
        assert _spec(position, f"характеристика номер {i % 200}") is specs[i % 200]
    assert time.perf_counter() - started < 1.0


def test_different_goods_get_a_choice_of_the_top_ranked_not_a_characteristic() -> None:
    from app.narrowing import CONFIRM, MAX_CHOICE, build_state

    def pos(code: str, name: str) -> Position:
        spec = CharacteristicSpec(
            name="Подкладка", kind="qualitative", values=[code], level_define=True
        )
        return Position(code=code, name=name, okpd2="14.13", characteristics=[spec], source="t")

    names = [
        "Куртки для взрослых из кожи",
        "Куртки для взрослых текстильные",
        "Шапки зимние для взрослых",
    ]
    positions = [pos(f"01.19.{i:02d}", names[i % 3]) for i in range(8)]
    candidates = [
        Candidate(code=p.code, name=p.name, score=1.0 - i / 10, why=[])
        for i, p in enumerate(positions)
    ]
    state = build_state(candidates, positions, {})
    assert state.question is not None and state.question.characteristic == CONFIRM
    shown = [o.value for o in state.question.options if o.codes]
    assert shown == [p.code for p in positions[:MAX_CHOICE]]


def _milk(code: str, unit: str) -> Position:
    fat = CharacteristicSpec(name="Вид молока", kind="qualitative", values=["Коровье", "Козье"])
    return Position(
        code=code, name="Молоко питьевое", okpd2="", unit=unit, source="t", characteristics=[fat]
    )


MILK = [_milk("M-L", "Литр; кубический дециметр"), _milk("M-K", "Килограмм")]


def test_same_positions_differing_by_unit_ask_the_unit() -> None:
    # две «Молоко питьевое» без различий давали выбор из голых кодов.
    from app.narrowing import UNIT_QUESTION

    state = build_state(_cands(MILK), MILK, {})
    assert state.question is not None and state.question.characteristic == UNIT_QUESTION
    assert [o.value for o in state.question.options] == ["Килограмм", "Литр", NOTHING_FITS]
    answered = build_state(_cands(MILK), MILK, {UNIT_QUESTION: _answer("Литр")})
    assert answered.final_code == "M-L"


def test_number_with_unit_in_text_picks_the_position_unit() -> None:
    state = build_state(_cands(MILK), MILK, {}, text="Молоко 3,2% 1 л")
    assert state.final_code == "M-L"
    # текст называет обе величины или ни одной — спрашиваем
    both = build_state(_cands(MILK), MILK, {}, text="Молоко 1 л, 1,03 кг")
    assert both.final_code is None


def test_counting_units_are_not_guessed_from_text() -> None:
    # «20 шт в рулоне» — не то, как считается позиция: «Штука» или «Упаковка» спрашиваем
    bags = [_milk("B-P", "Упаковка"), _milk("B-S", "Штука")]
    state = build_state(_cands(bags), bags, {}, text="Мешки 60 л, 20 шт")
    assert state.final_code is None
    assert state.question is not None and len(state.question.options) == 3


# вопросы сужения.
def _spec(name: str, values: list[str], level: bool = False) -> CharacteristicSpec:
    return CharacteristicSpec(name=name, kind="qualitative", values=values, level_define=level)


def _pos(code: str, name: str, specs: list[CharacteristicSpec]) -> Position:
    return Position(code=code, name=name, okpd2="", characteristics=specs, source="t")


def test_long_list_loses_to_a_short_question() -> None:
    # f01: «Цвет рабочей поверхности» на 72 варианта выигрывал по среднему остатку.
    colours = [f"Цвет {i}" for i in range(12)]
    positions = [
        _pos(
            f"S-{i}",
            "Стол письменный",
            [_spec("Цвет", [colours[i]]), _spec("Кромка", ["ПВХ" if i < 6 else "АБС"])],
        )
        for i in range(12)
    ]
    q = best_question(positions, {})
    assert q is not None and q.characteristic == "Кромка"


def test_long_defining_list_loses_to_a_short_other_question() -> None:
    colours = [f"Цвет {i}" for i in range(12)]
    positions = [
        _pos(
            f"S-{i}",
            "Стол письменный",
            [_spec("Цвет", [colours[i]], level=True), _spec("Кромка", ["ПВХ" if i < 6 else "АБС"])],
        )
        for i in range(12)
    ]
    state = build_state(_cands(positions), positions, {})
    assert state.question is not None and state.question.characteristic == "Кромка"


def test_long_list_is_asked_when_nothing_shorter() -> None:
    colours = [f"Цвет {i}" for i in range(12)]
    positions = [
        _pos(f"S-{i}", "Стол письменный", [_spec("Цвет", [colours[i]])]) for i in range(12)
    ]
    q = best_question(positions, {})
    assert q is not None and q.characteristic == "Цвет"


def test_categories_with_one_head_word_get_a_choice() -> None:
    # f01/f03: «Столы письменные» и «Столы … под станки» — разные категории, а не один товар.
    kromka = _spec("Вид материала отделочной кромки", ["ПВХ"])
    positions = [
        _pos("01.25.01", "Столы письменные", [kromka]),
        _pos("01.25.02", "Столы металлические под станки", []),
        _pos("01.25.03", "Столы раскройные", []),
    ]
    state = build_state(_cands(positions), positions, {})
    assert state.question is not None and state.question.characteristic == CONFIRM
    assert [o.value for o in state.question.options if o.codes] == [p.code for p in positions]


def test_same_named_positions_still_get_a_characteristic() -> None:
    positions = [
        _pos("K-1", "Стол письменный", [_spec("Кромка", ["ПВХ"])]),
        _pos("K-2", "Стол письменный", [_spec("Кромка", ["АБС"])]),
    ]
    state = build_state(_cands(positions), positions, {})
    assert state.question is not None and state.question.characteristic == "Кромка"


def test_abbreviation_in_text_does_not_answer_the_question() -> None:
    # Вывод значения из сокращения выключен: «нет» отвечало на любой вопрос Да/Нет,
    # «серия» — «Серый», а значение в карточку не попадало. Вопрос задаётся.
    drills = [
        _pos("D-1", "Шуруповерт", [_spec("Тип питания", ["От аккумуляторных батарей"])]),
        _pos("D-2", "Шуруповерт", [_spec("Тип питания", ["От сети"])]),
    ]
    state = build_state(_cands(drills), drills, {}, text="шуруповерт аккум 18в 2 акб")
    assert state.question is not None and state.question.characteristic == "Тип питания"


def test_question_with_one_real_option_is_not_asked() -> None:
    # «ПВХ или нет такой характеристики» — выбирать не из чего.
    positions = [
        _pos("K-1", "Стол письменный", [_spec("Кромка", ["ПВХ"]), _spec("Опоры", ["4"])]),
        _pos("K-2", "Стол письменный", [_spec("Опоры", ["2"])]),
        _pos("K-3", "Стол письменный", [_spec("Опоры", ["3"])]),
    ]
    q = best_question(positions, {})
    assert q is not None and q.characteristic == "Опоры"
    only_one = positions[:2]
    q = best_question([only_one[0], _pos("K-4", "Стол письменный", [])], {})
    assert q is None


def test_options_differing_by_gender_or_number_are_one() -> None:
    # «Смесовое/Смесовая», «Натуральное/Натуральная» — один вариант, ответ сужает по обоим.
    positions = [
        _pos("T-1", "Ткань", [_spec("Вид ткани", ["Смесовое"])]),
        _pos("T-2", "Ткань", [_spec("Вид ткани", ["Смесовая"])]),
        _pos("T-3", "Ткань", [_spec("Вид ткани", ["Натуральная"])]),
    ]
    q = best_question(positions, {})
    assert q is not None and q.characteristic == "Вид ткани"
    values = [o.value for o in q.options if o.value != NOTHING_FITS]
    assert values == ["Смесовое", "Натуральная"]
    kept = narrow(positions, {"Вид ткани": _answer("Смесовое")})
    assert [p.code for p in kept] == ["T-1", "T-2"]


def test_merging_keeps_negation_and_comparison_apart() -> None:
    from app.narrowing import form_key

    assert form_key("Не горючий") != form_key("Горючий")
    assert form_key(">=100") != form_key("<100")
    assert form_key("Мужские") == form_key("Мужской")


def test_only_endings_are_merged() -> None:
    from app.narrowing import same_value_form

    assert same_value_form("Смесовое", "Смесовая")
    assert same_value_form("Мужские", "Мужской")
    assert not same_value_form("Комплект", "Комплектующие")
    assert not same_value_form("Агрос", "Агросила")
    assert not same_value_form("Ткань", "Ткань смесовая")
