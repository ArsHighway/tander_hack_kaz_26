import asyncio
from pathlib import Path

import pytest

from app.catalog import Catalog, CharacteristicSpec
from app.extract import Extracted, LLMItem, LLMItems, extract_llm, extract_rules
from app.glossary import Glossary
from app.llm import LLMError, LLMOutputError
from app.values import fit_value
from tests.fakes import FakeLLM

FAKE = Path(__file__).parent / "fixtures" / "fake"
CATALOG = Catalog.load([FAKE / "ktru.json"])
WHITE = CATALOG.get("17.12.14.129-00000101").characteristics  # type: ignore[union-attr]
BAGS = CATALOG.get("22.22.11.000-00000201").characteristics  # type: ignore[union-attr]
GLOVES = CATALOG.get("22.19.60.119-00000301").characteristics  # type: ignore[union-attr]

# Карточка портала 47453345: описание поставщика построчно (docs/research/zakupki-portal.md, §2).
NAME = "Бумага офисная Туринский ЦБЗ А4 500л"
DESCRIPTION = "\n".join(
    [
        "Масса бумага площадью 1 м2, г 80",
        "Толщина, мкм 104",
        "Белизна с ООВ по ISO, % 98",
        "Непрозрачность, % 90",
        "Размер листа. мм А4 210*297",
        "Предельные отклонения по косине листа, мм 0,5",
        "Количество листов в пачке А4 500",
        "Количество пачек в коробке 5 шт.",
    ]
)


def _by_name(items: list[Extracted]) -> dict[str, Extracted]:
    return {e.name: e for e in items}


def _spans_point_to_text(items: list[Extracted], name: str, description: str) -> None:
    for e in items:
        text = name if e.source.field == "name" else description
        assert text[e.source.start : e.source.end] == e.source.text


def test_labelled_lines_of_description() -> None:
    got = _by_name(extract_rules(NAME, DESCRIPTION, WHITE))
    mass = got["Масса бумаги площадью 1 м2"]
    assert (mass.raw_value, mass.source.text, mass.method) == ("80", "80", "rule")
    assert got["Количество листов в пачке"].raw_value == "500"
    _spans_point_to_text(list(got.values()), NAME, DESCRIPTION)


def test_list_value_found_anywhere_in_text() -> None:
    got = _by_name(extract_rules(NAME, DESCRIPTION, WHITE))
    assert got["Формат"].raw_value == "A4"  # значение перечня (латиница)
    assert got["Формат"].source.text == "А4"  # как в тексте (кириллица)


def test_line_about_another_count_is_not_taken() -> None:
    # «Количество пачек в коробке 5 шт.» — не «Количество листов в пачке».
    got = _by_name(extract_rules("Бумага", "Количество пачек в коробке 5 шт.", WHITE))
    assert "Количество листов в пачке" not in got


def test_values_from_name_with_units() -> None:
    name = "Пакеты для мусора 60 л, 20 шт, ПНД"
    got = _by_name(extract_rules(name, "", BAGS))
    assert (got["Объем"].raw_value, got["Объем"].raw_unit) == ("60", "л")
    assert got["Количество в упаковке"].raw_value == "20"
    assert got["Материал"].raw_value == "ПНД"
    _spans_point_to_text(list(got.values()), name, "")


def test_word_forms_of_list_value() -> None:
    # Перечень «Нестерильные», в тексте «нестерильные» — то же значение.
    got = _by_name(extract_rules("Перчатки нитриловые нестерильные, размер M", "", GLOVES))
    assert got["Стерильность"].raw_value.lower() == "нестерильные"
    assert got["Размер"].raw_value == "M"


def test_short_size_value_needs_a_standalone_token() -> None:
    # «5 м» — метры, а не размер M.
    got = _by_name(extract_rules("Перчатки, рулон 5 м", "", GLOVES))
    assert "Размер" not in got
    # Наименование заглавными: «М» совпадает с размером по букве, отсекает число перед ним.
    got = _by_name(extract_rules("ПЕРЧАТКИ, РУЛОН 5 М", "", GLOVES))
    assert "Размер" not in got


def _llm(replies: list) -> FakeLLM:
    return FakeLLM(replies)


def test_llm_items_are_validated_by_code() -> None:
    reply = LLMItems(
        items=[
            LLMItem(name="Класс", value="A", unit=None, quote="класс A"),  # верный
            LLMItem(name="Класс", value="B", unit=None, quote="класс B"),  # цитаты нет в тексте
            LLMItem(name="Формат", value="A7", unit=None, quote="A7"),  # нет в перечне
            LLMItem(name="Цвет", value="Белый", unit=None, quote="белая"),  # чужое имя
        ]
    )
    llm = _llm([reply])
    got = asyncio.run(
        extract_llm("Бумага белая, класс A", "", WHITE, llm, Glossary([]))  # type: ignore[arg-type]
    )
    assert [(e.name, e.raw_value, e.method) for e in got] == [("Класс", "A", "llm")]
    assert got[0].source.text == "класс A"


def test_llm_bad_output_twice_gives_nothing() -> None:
    bad = LLMOutputError("не JSON", raw="...")
    got = asyncio.run(
        extract_llm("Бумага", "", WHITE, _llm([bad, bad]), Glossary([]))  # type: ignore[arg-type]
    )
    assert got == []


def test_llm_outage_is_raised_to_the_pipeline() -> None:
    with pytest.raises(LLMError):
        asyncio.run(
            extract_llm(
                "Бумага",
                "",
                WHITE,
                _llm([LLMError("нет связи")]),
                Glossary([]),  # type: ignore[arg-type]
            )
        )


def test_llm_not_called_without_specs() -> None:
    llm = _llm([])
    assert asyncio.run(extract_llm("Бумага", "", [], llm, Glossary([]))) == []  # type: ignore[arg-type]
    assert llm.calls == []


def test_word_form_is_returned_as_the_list_value() -> None:
    material = CharacteristicSpec(name="Материал", kind="qualitative", values=["Нитрил", "Латекс"])
    got = _by_name(extract_rules("Перчатки смотровые нитриловые", "", [material]))
    assert got["Материал"].raw_value == "Нитрил"
    assert got["Материал"].source.text == "нитриловые"


COLOR_PAPER = CATALOG.get("17.12.14.129-00000102").characteristics  # type: ignore[union-attr]


def test_labelled_line_with_colon_gives_list_value_as_written() -> None:
    # Значение пишется как в тексте: опечатку и чужое значение покажет подгонка к перечню.
    got = _by_name(extract_rules("Бумага цветная", "Интенсивность цвета: Медимум", COLOR_PAPER))
    e = got["Интенсивность цвета"]
    assert (e.raw_value, e.source.text) == ("Медимум", "Медимум")
    got = _by_name(extract_rules("Бумага цветная", "Интенсивность цвета: Белый", COLOR_PAPER))
    assert got["Интенсивность цвета"].raw_value == "Белый"


def test_name_value_goes_to_the_best_matching_characteristic() -> None:
    # «500 листов» — «Количество листов в пачке», а не «Количество цветов в пачке»: у второй совпала
    # только половина слов, у первой — все.
    sheets = CharacteristicSpec(
        name="Количество листов в пачке", kind="quantitative", unit="шт", values=["250", "500"]
    )
    colors = CharacteristicSpec(name="Количество цветов в пачке", kind="quantitative", unit="шт")
    got = _by_name(extract_rules("Бумага офисная А4 500 листов", "", [colors, sheets]))
    assert got["Количество листов в пачке"].raw_value == "500"
    assert "Количество цветов в пачке" not in got


DEMO = Catalog.load([Path(__file__).parent.parent / "app" / "catalog" / "demo" / "demo.json"])


def _demo_specs(code: str) -> list:
    position = DEMO.get(code)
    assert position is not None
    return position.characteristics


def test_one_line_description_with_commas() -> None:
    # «Толщина = 10» — бралось последнее число строки, а не число после метки.
    got = _by_name(
        extract_rules(
            "Мешки",
            "объем 120 л, толщина 30 мкм, в рулоне 10 шт",
            _demo_specs("22.22.11.000-00000201"),
        )
    )
    assert got["Толщина"].raw_value == "30"
    assert got["Объем"].raw_value == "120"


def test_sentences_with_periods_are_separate() -> None:
    got = _by_name(
        extract_rules(
            "Батарейка",
            "Типоразмер AAA. Напряжение 1,5 В. В упаковке 4 штуки",
            _demo_specs("27.20.11.000-00000501"),
        )
    )
    assert "Номинальное напряжение" not in got or got["Номинальное напряжение"].raw_value == "1,5"


def test_colon_value_stops_at_the_end_of_the_phrase() -> None:
    specs = _demo_specs("22.19.60.119-00000301")
    got = _by_name(
        extract_rules("Перчатки", "Опудренность: неопудренные. Стерильность: стерильные", specs)
    )
    assert got["Опудренность"].raw_value.lower() == "неопудренные"
    got = _by_name(extract_rules("Перчатки", "материал: латекс, размер M", specs))
    assert got["Материал"].raw_value.lower() == "латекс"


def test_one_shared_word_is_not_a_label_match() -> None:
    # «Масса бумаги площадью 1м2: 80 г» — не «Класс бумаги» (общее только «бумаги»).
    got = _by_name(
        extract_rules(
            "Бумага", "Масса бумаги площадью 1м2: 80 г", _demo_specs("17.12.14.129-00000031")
        )
    )
    assert "Класс бумаги" not in got


def test_llm_quote_must_contain_the_value() -> None:
    # цитата есть в тексте, но значения в ней нет — значение придумано моделью.
    reply = LLMItems(
        items=[
            LLMItem(name="Класс", value="B", unit=None, quote="Бумага белая"),
            LLMItem(name="Белизна по CIE", value="170", unit="%", quote="белизна 160"),
            LLMItem(name="Формат", value="A4", unit=None, quote="формата А4"),
        ]
    )
    got = asyncio.run(
        extract_llm(
            "Бумага белая формата А4, белизна 160",
            "",
            WHITE,
            _llm([reply]),
            Glossary([]),  # type: ignore[arg-type]
        )
    )
    assert [(e.name, e.raw_value) for e in got] == [("Формат", "A4")]


def test_llm_quote_with_word_form_of_the_value_is_accepted() -> None:
    # «неопудренных» в тексте — «Неопудренные» в перечне: форма слова, а не другое значение.
    powder = next(s for s in GLOVES if s.name == "Опудренность")
    reply = LLMItems(
        items=[LLMItem(name=powder.name, value="Неопудренные", unit=None, quote="неопудренных")]
    )
    got = asyncio.run(
        extract_llm(
            "Пара перчаток неопудренных",
            "",
            [powder],
            _llm([reply]),
            Glossary([]),  # type: ignore[arg-type]
        )
    )
    assert [(e.name, e.raw_value) for e in got] == [(powder.name, "Неопудренные")]


def test_llm_bucket_value_is_accepted_when_quote_number_is_inside() -> None:
    # Модель отвечает значением перечня «>=80 ... <90», в цитате — «80 г/м2».
    mass = next(s for s in WHITE if s.name.startswith("Масса"))
    ok = LLMItem(name=mass.name, value=">=80 ... <90", unit="г/м2", quote="80 г/м2")
    got = asyncio.run(
        extract_llm(
            "Бумага 80 г/м2",
            "",
            [mass],
            _llm([LLMItems(items=[ok])]),
            Glossary([]),  # type: ignore[arg-type]
        )
    )
    assert [e.raw_value for e in got] == [">=80 ... <90"]
    wrong = LLMItem(name=mass.name, value=">=90 ... <100", unit="г/м2", quote="80 г/м2")
    got = asyncio.run(
        extract_llm(
            "Бумага 80 г/м2",
            "",
            [mass],
            _llm([LLMItems(items=[wrong])]),
            Glossary([]),  # type: ignore[arg-type]
        )
    )
    assert got == []


def test_compound_unit_from_name_goes_to_the_characteristic_with_that_unit() -> None:
    # «80 г/м2» в наименовании пропускался, хотя у позиции есть характеристика в г/м2.
    mass = next(s for s in WHITE if s.name.startswith("Масса"))
    for name in ["Бумага офисная А4 80 г/м2 500 л", "Бумага офисная А4, 80г/м², 500 листов"]:
        e = _by_name(extract_rules(name, "", WHITE))[mass.name]
        assert (e.raw_value, e.raw_unit) == ("80", "г/м2")
        assert e.source.text.startswith("80")
    # «до 80 г/м2» — граница, а не значение.
    assert mass.name not in _by_name(extract_rules("Бумага плотность до 80 г/м2", "", WHITE))


def test_bare_number_list_value_is_not_taken_from_free_text() -> None:
    # Портал: «Велосипед … 20» — размер колеса, а не «Число зубьев: 20» из перечня.
    teeth = CharacteristicSpec(name="Число зубьев", kind="qualitative", values=["20", "32"])
    assert "Число зубьев" not in _by_name(extract_rules("Велосипед горный 20 дюймов", "", [teeth]))


def test_multi_word_list_value_is_found_as_a_phrase() -> None:
    # Портал: «Хризантема кустовая "Бакарди"» — значение из двух слов.
    kind = CharacteristicSpec(
        name="Название цветов и растений",
        kind="qualitative",
        values=["Хризантема кустовая", "Хризантема одноголовая", "Роза"],
    )
    got = _by_name(extract_rules('Хризантема кустовая "Бакарди"', "", [kind]))
    e = got["Название цветов и растений"]
    assert (e.raw_value, e.source.text) == ("Хризантема кустовая", "Хризантема кустовая")
    # Слова по отдельности и в другом порядке — не фраза: остаётся только «Роза».
    other = _by_name(extract_rules("Кустовая роза и хризантема", "", [kind]))
    assert other["Название цветов и растений"].raw_value == "Роза"


# Формы наименований карточек портала.
def _q(name: str, unit: str | None) -> CharacteristicSpec:
    return CharacteristicSpec(name=name, kind="quantitative", unit=unit)


def _values(name: str, specs: list[CharacteristicSpec]) -> dict[str, tuple[str, str | None]]:
    return {e.name: (e.raw_value, e.raw_unit) for e in extract_rules(name, "", specs)}


LUMBER = [_q("Толщина", "мм"), _q("Ширина", "мм"), _q("Длина", "мм")]


def test_three_dimensions_with_thickness_go_by_size() -> None:
    # Доска «25х150х6000мм» и плитка «300х300х8 мм»: порядок у поставщиков разный,
    # толщина — меньшее.
    assert _values("Доска обрезная 25х150х6000мм", LUMBER) == {
        "Толщина": ("25", "мм"), "Ширина": ("150", "мм"), "Длина": ("6000", "мм"),
    }  # fmt: skip
    assert _values("Плитка Estima Standard ST01, бежевый 300х300х8 мм", LUMBER) == {
        "Толщина": ("8", "мм"), "Ширина": ("300", "мм"), "Длина": ("300", "мм"),
    }  # fmt: skip


def test_dimension_span_points_to_its_own_number() -> None:
    name = "Доска обрезная 25х150х6000мм"
    got = _by_name(extract_rules(name, "", LUMBER))
    assert {k: name[e.source.start : e.source.end] for k, e in got.items()} == {
        "Толщина": "25", "Ширина": "150", "Длина": "6000",
    }  # fmt: skip


def test_two_dimensions_length_is_the_larger() -> None:
    specs = [_q("Длина", "см"), _q("Ширина", "см")]
    assert _values("Мешки для мусора 70х110 см", specs) == {
        "Длина": ("110", "см"), "Ширина": ("70", "см"),
    }  # fmt: skip


def test_furniture_dimensions_are_not_guessed() -> None:
    # «Ш×Г×В» или «В×Ш×Г» — у мебели порядок не угадать; решит LLM или человек.
    specs = [_q("Ширина", "мм"), _q("Глубина", "мм"), _q("Высота", "мм")]
    assert _values("Стол письменный 1600х510х750 мм", specs) == {}


def test_single_length_goes_to_the_only_length_characteristic() -> None:
    specs = [_q("Длина кабеля", "м"), _q("Количество розеток", "шт")]
    assert _values("Удлинитель Pilot 5м", specs)["Длина кабеля"] == ("5", "м")


def test_single_length_with_several_length_characteristics_needs_a_label() -> None:
    specs = [_q("Толщина линии письма", "мм"), _q("Длина", "мм")]
    assert _values("Ручка шариковая синяя, толщина линии 0,5 мм", specs) == {
        "Толщина линии письма": ("0,5", "мм")
    }
    assert _values("Салфетка из микрофибры 30 см", [_q("Длина", "см"), _q("Ширина", "см")]) == {}


def test_count_characteristic_without_unit_takes_pieces() -> None:
    spec = _q("Количество штук в упаковке", None)
    assert _values("Перчатки нитриловые 50 штук", [spec]) == {
        "Количество штук в упаковке": ("50", "шт")
    }


def test_counted_noun_goes_to_its_count_characteristic() -> None:
    specs = [_q("Количество розеток", "шт"), _q("Длина кабеля", "м")]
    assert _values("Сетевой фильтр 6 розеток 5м", specs)["Количество розеток"] == ("6", "шт")
    assert _values("Пульт 3 кнопки", [_q("Количество кнопок", None)]) == {
        "Количество кнопок": ("3", "шт")
    }


def test_weight_is_mass() -> None:
    assert _values("Молоток слесарный 500 г", [_q("Вес", "г")]) == {"Вес": ("500", "г")}


def test_unit_in_characteristic_name_is_used() -> None:
    assert _values("Мешки для мусора 30 мкм", [_q("Толщина, мкм", None)]) == {
        "Толщина, мкм": ("30", "мкм")
    }


def test_litres_written_as_a_word() -> None:
    assert _values("Ведро пластиковое 12 литров", [_q("Объем", "л")]) == {"Объем": ("12", "л")}


def test_feminine_colour_finds_the_masculine_list_value() -> None:
    colour = CharacteristicSpec(name="Цвет", kind="qualitative", values=["Белый", "Синий"])
    assert _values("Бумага белая А4", [colour]) == {"Цвет": ("Белый", None)}
    assert _values("Папка синяя", [colour]) == {"Цвет": ("Синий", None)}


def test_code_with_digits_is_matched_exactly() -> None:
    size = CharacteristicSpec(name="Типоразмер", kind="qualitative", values=["CR123"])
    assert _values("Батарейка литиевая CR123A", [size]) == {}
    assert _values("Батарейка литиевая CR123", [size]) == {"Типоразмер": ("CR123", None)}


def test_negated_value_is_not_taken() -> None:
    mercury = CharacteristicSpec(name="Содержание ртути", kind="qualitative", values=["Ртуть"])
    assert _values("Лампа светодиодная без ртути", [mercury]) == {}


# Регрессии первого прогона правок на 5000 СТЕ.
def test_unit_after_the_last_size_is_not_split() -> None:
    specs = [_q("Длина", "мм"), _q("Ширина", "мм")]
    assert _values("Салфетка бумажная USMA, 100 шт, белый, 240*240 мм", specs) == {
        "Длина": ("240", "мм"), "Ширина": ("240", "мм"),
    }  # fmt: skip


def test_sizes_in_different_units_are_compared_in_one_unit() -> None:
    specs = [_q("Длина", "м"), _q("Ширина", "см")]
    got = _values("Бинт эластичный Балтик Медикал 5,0 м х 8 см.", specs)
    assert got == {"Длина": ("5,0", "м"), "Ширина": ("8", "см")}


def test_sizes_are_not_guessed_when_another_length_is_in_the_position() -> None:
    # Труба «Дн 89х5 мм»: диаметр × стенка, а не длина × толщина.
    specs = [_q("Наружный диаметр", "мм"), _q("Толщина стенки", "мм"), _q("Длина", "м")]
    got = _values("Труба стальная бесшовная, Дн 89х5 мм, 1 м", specs)
    assert "Толщина стенки" not in got and got.get("Длина") != ("89", "мм")


def test_count_word_alone_does_not_match_another_count() -> None:
    specs = [_q("Количество слоев", None), _q("Количество листов в рулоне", "шт")]
    got = _values("Полотенца бумажные, рулон 90 листов, 3-сл, белый", specs)
    assert got.get("Количество слоев") != ("90", "шт")
    zewa = _values("Полотенца бумажные Zewa 2-слойные белые 2 рулона по 14 метров", specs)
    assert "Количество листов в рулоне" not in zewa


def test_spelling_variants_of_one_list_value_are_not_ambiguous() -> None:
    colour = CharacteristicSpec(name="Цветность", kind="qualitative", values=["Белый", "Белая"])
    assert _values("Бумага Propapier A3 120 гр/м2 белая", [colour]) == {
        "Цветность": ("Белая", None)
    }


def test_pieces_go_to_any_single_count_characteristic() -> None:
    specs = [_q("Количество в рулоне", "шт")]
    assert _values("Мешок для мусора ПВД 360л рулон 10шт, Желтый", specs) == {
        "Количество в рулоне": ("10", "шт")
    }


# регрессии и находки.
def test_thousands_with_space_are_not_a_count() -> None:
    specs = [_q("Количество пользователей", None)]
    assert _values("Лицензия до 10 000 пользователей", specs) == {}


def test_colour_next_to_the_label_word_wins() -> None:
    body = CharacteristicSpec(
        name="Цвет корпуса", kind="qualitative", values=["Синий", "Оранжевый"]
    )
    got = _values("Ручка шариковая синяя, корпус оранжевый", [body])
    assert got == {"Цвет корпуса": ("Оранжевый", None)}


def test_single_length_without_label_goes_to_the_only_length() -> None:
    # Одно число длины и одна длина в позиции — её значение: «Насадка 27 см», «Шланг 2,5м».
    assert _values("Насадка на швабру York 27 см белый", [_q("Длина", "см")]) == {
        "Длина": ("27", "см")
    }
    assert _values("Шланг поливочный 2,5м", [_q("Длина шланга", "м")]) == {
        "Длина шланга": ("2,5", "м")
    }


def test_single_length_after_a_diameter_mark_is_not_a_length() -> None:
    assert _values("Шланг для пылесоса Ø 32 мм", [_q("Длина шланга", "м")]) == {}
    assert _values("Шланг всасывающий DN 32 мм", [_q("Длина шланга", "м")]) == {}


def test_single_length_does_not_fill_the_size_field() -> None:
    assert _values("Игрушка елочная, стекло 9 см", [_q("Габаритные размеры", "см")]) == {}


def test_sheets_go_to_count_in_package() -> None:
    specs = [_q("Количество в упаковке", "шт")]
    assert _values("Бумага для заметок 200 листов в упаковке", specs) == {
        "Количество в упаковке": ("200", "лист")
    }


def test_terabytes_go_to_memory() -> None:
    assert _values("Жесткий диск WD Purple 8ТБ", [_q("Объем накопителя", "ГБ")]) == {
        "Объем накопителя": ("8", "ТБ")
    }


def test_role_is_found_in_any_word_of_the_name() -> None:
    specs = [
        _q("Номинальная толщина пиломатериала", "мм"),
        _q("Номинальная ширина пиломатериала", "мм"),
        _q("Номинальная длина пиломатериала", "мм"),
    ]
    got = _values("Доска обрезная УЛК 150х40х6000 мм", specs)
    assert got == {
        "Номинальная толщина пиломатериала": ("40", "мм"),
        "Номинальная ширина пиломатериала": ("150", "мм"),
        "Номинальная длина пиломатериала": ("6000", "мм"),
    }


def test_film_thickness_in_microns_does_not_block_the_sizes() -> None:
    specs = [_q("Длина", "мм"), _q("Ширина", "мм"), _q("Толщина материала, мкм", None)]
    assert _values("Мешки для мусора 30 мкм 500*900 мм", specs) == {
        "Длина": ("900", "мм"), "Ширина": ("500", "мм"), "Толщина материала, мкм": ("30", "мкм"),
    }  # fmt: skip


def test_diameter_by_length() -> None:
    specs = [_q("Диаметр", "мм"), _q("Длина", "мм")]
    assert _values("Саморез по дереву 4,2х16 мм", specs) == {
        "Диаметр": ("4,2", "мм"), "Длина": ("16", "мм"),
    }  # fmt: skip


def test_size_field_takes_the_whole_size() -> None:
    size = CharacteristicSpec(name="Габаритные размеры", kind="qualitative")
    assert _values("Калькулятор настольный 175х90 мм", [size]) == {
        "Габаритные размеры": ("175х90 мм", None)
    }


def test_value_repeating_the_characteristic_name_is_not_taken() -> None:
    purpose = CharacteristicSpec(
        name="Назначение стола письменного", kind="qualitative",
        values=["Стол письменный", "Для персонала"],
    )  # fmt: skip
    assert _values("Стол письменный 1600х510х750 мм", [purpose]) == {}


def test_count_of_other_things_does_not_take_pieces_in_package() -> None:
    specs = [_q("Количество отделений", None)]
    assert _values("Пенал школьный (10 штук в упаковке)", specs) == {}


def test_load_in_kilograms_is_not_weight() -> None:
    assert _values("Стеллаж нагрузка на полку 100 кг", [_q("Вес", "кг")]) == {}


def test_volume_times_count() -> None:
    specs = [_q("Объем", "л"), _q("Количество бутылок в упаковке", "шт")]
    assert _values("Чай холодный Maho Tea, пэт 0,5л*12штук", specs) == {
        "Объем": ("0,5", "л"), "Количество бутылок в упаковке": ("12", "шт"),
    }  # fmt: skip


def test_decimal_size_is_one_word() -> None:
    size = CharacteristicSpec(name="Размер", kind="qualitative", values=["5x500 см", "2.5x500 см"])
    assert _values("Бинт гипсовый 2.5x500 см", [size]) == {"Размер": ("2.5x500 см", None)}


# регрессии.
def test_count_after_another_number_is_still_a_count() -> None:
    assert _values("Мышь NX-7005 31030017400 3 кнопки", [_q("Количество кнопок", None)]) == {
        "Количество кнопок": ("3", "шт")
    }


def test_bare_pieces_go_to_the_only_count() -> None:
    assert _values("Салфетки бумажные 200шт.", [_q("Количество салфеток", "шт")]) == {
        "Количество салфеток": ("200", "шт")
    }


def test_label_without_role_word_still_names_the_characteristic() -> None:
    specs = [_q("Толщина линии письма", "мм"), _q("Длина", "мм")]
    assert _values("Ручка гелевая синяя, линия письма 0,5 мм", specs) == {
        "Толщина линии письма": ("0,5", "мм")
    }


def test_product_words_are_not_a_label() -> None:
    specs = [_q("Ширина рулона бумажных полотенец", "см"), _q("Длина намотки", "м")]
    assert (
        _values("Полотенца бумажные в рулонах Veiro 160м", specs).get(
            "Ширина рулона бумажных полотенец"
        )
        is None
    )


def test_terabytes_skip_cache_memory() -> None:
    specs = [_q("Объем буферной памяти", "МБ"), _q("Объем накопителя", "ГБ")]
    assert _values("Жесткий диск WD 8TB", specs) == {"Объем накопителя": ("8", "ТБ")}


def test_size_field_is_the_size_itself_not_a_screw_slot() -> None:
    slot = CharacteristicSpec(name="Размер шлица", kind="qualitative", values=["PH2", "PH1"])
    assert _values("Саморез 3,5х25мм PH2", [slot]) == {"Размер шлица": ("PH2", None)}


def test_label_word_equal_to_the_product_word_does_not_pick() -> None:
    kind = CharacteristicSpec(
        name="Вид мяча", kind="qualitative", values=["Футбольный", "Волейбольный"]
    )
    assert _values("Мяч волейбольный, как футбольный мяч, размер 5", [kind]) == {}


def test_bare_count_takes_pieces() -> None:
    assert _values("Счетные палочки 30 штук, розовые", [_q("Количество", None)]) == {
        "Количество": ("30", "шт")
    }


def test_label_after_a_number_and_unit() -> None:
    specs = [_q("Ширина", "см"), _q("Длина", "м")]
    assert _values("Бинт марлевый шириной 10 см длиной 5 м", specs) == {
        "Ширина": ("10", "см"), "Длина": ("5", "м"),
    }  # fmt: skip


def test_single_length_with_a_diameter_spec_alongside() -> None:
    specs = [_q("Длина нити", "см"), _q("Диаметр иглы", "мм")]
    assert _values("Нить хирургическая Лавсан 150 см", specs) == {"Длина нити": ("150", "см")}


# Жюри-аудит: единица после числа в описании не читалась, число бралось не у той метки.
def _desc(description: str, specs: list[CharacteristicSpec]) -> dict[str, tuple[str, str | None]]:
    return {e.name: (e.raw_value, e.raw_unit) for e in extract_rules("Товар", description, specs)}


@pytest.mark.parametrize(
    ("description", "spec", "expected"),
    [
        ("Объем 1700 мл", _q("Объем", "л"), ("1700", "мл")),
        ("мощность 2,2 кВт", _q("Мощность", "Вт"), ("2,2", "кВт")),
        ("Объем 0,12 м3", _q("Объем", "л"), ("0,12", "м3")),
        ("толщина 0,03 мм", _q("Толщина", "мкм"), ("0,03", "мм")),
        ("белизна 146%", _q("Белизна", "%"), ("146", "%")),
        ("Плотность 80 г/м2", _q("Плотность", "г/м2"), ("80", "г/м2")),
    ],
)
def test_unit_after_the_number_is_read(
    description: str, spec: CharacteristicSpec, expected: tuple[str, str]
) -> None:
    assert _desc(description, [spec]) == {spec.name: expected}


def test_kettle_volume_and_power_are_converted() -> None:
    specs = [_q("Объем", "л"), _q("Мощность", "Вт")]
    got = _by_name(extract_rules("Чайник электрический", "Объем 1700 мл, мощность 2,2 кВт", specs))
    assert (got["Объем"].raw_value, got["Объем"].raw_unit) == ("1700", "мл")
    assert (got["Мощность"].raw_value, got["Мощность"].raw_unit) == ("2,2", "кВт")
    assert fit_value(got["Объем"].raw_value, got["Объем"].raw_unit, specs[0]).value == "1,7"
    assert fit_value(got["Мощность"].raw_value, got["Мощность"].raw_unit, specs[1]).value == "2200"


def test_bag_thickness_is_not_taken_from_the_pieces() -> None:
    specs = _demo_specs("22.22.11.000-00000202")
    by_name = {s.name: s for s in specs}
    got = _by_name(
        extract_rules(
            "Мешки для мусора 120 л", "Объем 0,12 м3, толщина 0,03 мм, 50 шт в упаковке, ПНД", specs
        )
    )
    thick, volume = got["Толщина"], got["Объем"]
    assert (thick.raw_value, thick.raw_unit, thick.source.text) == ("0,03", "мм", "0,03")
    assert fit_value(thick.raw_value, thick.raw_unit, by_name["Толщина"]).value == "30"
    assert (volume.raw_value, volume.raw_unit) == ("0,12", "м3")
    assert fit_value(volume.raw_value, volume.raw_unit, by_name["Объем"]).value == "120"


def test_paper_whiteness_is_not_the_sheet_count() -> None:
    specs = _demo_specs("17.12.14.129-00000031")
    got = _by_name(
        extract_rules(
            "Бумага офисная А4",
            "Плотность 80 г/м2, белизна 146%, 500 листов в пачке, класс C",
            specs,
        )
    )
    assert (got["Белизна"].raw_value, got["Белизна"].raw_unit) == ("146", "%")
    assert got["Количество листов в пачке"].raw_value == "500"


@pytest.mark.parametrize(
    ("description", "spec"),
    [
        ("толщина 50 шт", _q("Толщина", "мкм")),  # счётная против физической
        ("Мощность 50 шт", _q("Мощность", "Вт")),
    ],
)
def test_number_with_an_incompatible_unit_is_not_taken(
    description: str, spec: CharacteristicSpec
) -> None:
    assert _desc(description, [spec]) == {}


def test_number_with_unit_of_other_dimension_comes_as_conflict() -> None:
    # другая размерность — не молчание, а значение с чужой единицей: в поле оно не встанет
    # (fit — unit_mismatch), карточка покажет конфликт
    spec = _q("Объем", "л")
    assert _desc("Объем 2 кг", [spec]) == {"Объем": ("2", "кг")}
    assert fit_value("2", "кг", spec).status == "unit_mismatch"


def test_number_without_any_unit_is_still_taken() -> None:
    assert _desc("Толщина 30", [_q("Толщина", "мкм")]) == {"Толщина": ("30", None)}
    # Единица из метки по-прежнему работает.
    assert _desc("Толщина, мкм 30", [_q("Толщина", "мкм")]) == {"Толщина": ("30", "мкм")}


def test_millilitres_in_the_name_go_to_the_volume_named_exactly() -> None:
    # Портал 46490218: с м3 в объёмах «250 мл» переводится и в «Объем освежаемого помещения».
    specs = [_q("Объем освежаемого помещения", "м3"), _q("Объем", "мл")]
    assert _values("Освежитель воздуха Дикий гранат 250 мл", specs) == {"Объем": ("250", "мл")}
    # Портал 46555530: обе в литрах — какая из них «2,5 л», не угадываем.
    same = [_q("Объем в упаковке", "л"), _q("Объем", "л")]
    assert _values("Пропитка-антисептик тиковое дерево 2,5 л", same) == {}


def test_grams_are_grams_per_square_metre_when_the_label_says_so() -> None:
    spec = _q("Масса бумаги площадью 1м2", "г/м2")
    assert _desc("Масса бумаги площадью 1м2: 80 г", [spec]) == {spec.name: ("80", "г/м2")}


def test_one_letter_preposition_after_the_number_is_not_a_unit() -> None:
    # «в» — и вольты, и предлог: у счётной характеристики это предлог.
    spec = _q("Количество в упаковке", "шт")
    assert _desc("Количество в упаковке 10 в коробке", [spec]) == {spec.name: ("10", None)}
    assert _desc("Напряжение 220 в", [_q("Напряжение", "В")]) == {"Напряжение": ("220", "В")}


def _ql(name: str, values: list[str]) -> CharacteristicSpec:
    return CharacteristicSpec(name=name, kind="qualitative", values=values)


@pytest.mark.parametrize(
    ("spec", "text", "value"),
    [
        (_ql("Цвет чернил", ["Синий", "Черный"]), "Цвет чернил Синий", "Синий"),
        (_ql("Оригинальность", ["Оригинальный", "Совместимый"]), "Оригинальность Совместимый",
         "Совместимый"),
        (_ql("Стерильность", ["Стерильные", "Нестерильные"]), "стерильность нестерильные",
         "Нестерильные"),
    ],
)  # fmt: skip
def test_word_of_the_label_is_not_a_list_value(
    spec: CharacteristicSpec, text: str, value: str
) -> None:
    # Золотой набор с реальными форматами: «чернил» принималось за «Черный», и находок было две.
    got = {e.name: e.raw_value for e in extract_rules("Ручка", text, [spec])}
    assert got == {spec.name: value}


@pytest.mark.parametrize(
    ("label", "spec_name", "unit"),
    [
        ("Вес бумаги, г/м2 93", "Масса бумаги площадью 1м2", "г/м2"),
        ("Вместимость, л 120", "Объем", "л"),
        ("Емкость: 120 л", "Объем", "л"),
        ("Кол-во в упаковке, шт 86", "Количество в упаковке", "шт"),
    ],
)
def test_label_synonyms_are_understood(label: str, spec_name: str, unit: str) -> None:
    got = {e.name: e.raw_value for e in extract_rules("Товар", label, [_q(spec_name, unit)])}
    assert got == {spec_name: label.split()[-1] if label[-1].isdigit() else "120"}


def test_llm_value_with_a_foreign_unit_in_the_quote_is_rejected() -> None:
    # Стенд: «100 шт в упаковке» модель записала в «Количество пар в упаковке» как 100 пар.
    pairs = [_q("Количество пар в упаковке", "пар")]
    reply = LLMItems(
        items=[
            LLMItem(
                name="Количество пар в упаковке", value="100", unit="пар",
                quote="100 шт в упаковке",
            )
        ]
    )  # fmt: skip
    got = asyncio.run(
        extract_llm(
            "Перчатки смотровые",
            "100 шт в упаковке",
            pairs,
            _llm([reply]),
            Glossary([]),  # type: ignore[arg-type]
        )
    )
    assert got == []
    ok = LLMItems(
        items=[LLMItem(name="Количество пар в упаковке", value="50", unit="пар", quote="50 пар")]
    )
    got = asyncio.run(
        extract_llm("Перчатки смотровые", "50 пар", pairs, _llm([ok]), Glossary([]))  # type: ignore[arg-type]
    )
    assert [(e.name, e.raw_value) for e in got] == [("Количество пар в упаковке", "50")]


@pytest.mark.parametrize(
    ("spec_name", "name", "text", "value"),
    [
        ("Наличие завязок", "Мешки для мусора ПНД 120 л", "толщина 30 мкм, с завязками", "Да"),
        ("Наличие завязок", "Мешки для мусора с завязками 60 л", "", "Да"),
        ("Наличие спирта", "Средство для стекол", "спрей, без спирта", "Нет"),
        ("Наличие хлора", "Средство для полов", "хлор: нет", "Нет"),
    ],
)
def test_yes_no_from_the_subject_word(spec_name: str, name: str, text: str, value: str) -> None:
    # Стенд: «с завязками» не заполняло «Наличие завязок» ни правилами, ни моделью.
    spec = _ql(spec_name, ["Да", "Нет"])
    got = {e.name: e.raw_value.lower() for e in extract_rules(name, text, [spec])}
    assert got == {spec_name: value.lower()}  # «хлор: нет» — значение как в тексте, подгонка позже


def test_yes_and_no_together_are_ambiguous() -> None:
    spec = _ql("Наличие завязок", ["Да", "Нет"])
    assert extract_rules("Мешки", "с завязками или без завязок", [spec]) == []


def test_yes_no_asks_about_the_feature_not_the_product() -> None:
    # 5000 карточек портала: «Ручка автоматическая» заполнялось «Да» от слова «Ручка».
    spec = _ql("Ручка автоматическая", ["Да", "Нет"])
    assert extract_rules("Ручка шариковая синяя 0,5 мм", "", [spec]) == []
    got = extract_rules("Ручка шариковая неавтоматическая синяя", "", [spec])
    assert [(e.name, e.raw_value) for e in got] == [("Ручка автоматическая", "Нет")]


def _control(values: list[str]) -> list[CharacteristicSpec]:
    return [CharacteristicSpec(name="Тип управления", kind="qualitative", values=values)]


def test_count_prefix_names_the_same_kind() -> None:
    # «однорычажный» — рычажный, «двухвентильный» — вентильный: позиции КТРУ смесителей
    # различаются «Типом управления» без приставки числа
    specs = _control(["Рычажный", "Вентильный", "Сенсорный"])
    for name, value in (
        ("Смеситель для кухни однорычажный, хром", "Рычажный"),
        ("Смеситель двухвентильный для ванны", "Вентильный"),
        ("Смеситель трёхвентильный", "Вентильный"),
    ):
        found = {e.name: e.raw_value for e in extract_rules(name, "", specs)}
        assert found.get("Тип управления") == value, name


def test_count_prefix_is_not_any_prefix() -> None:
    # «полуавтоматический» — не «Автоматический», «многоразовый» — не «Разовый»
    specs = [
        CharacteristicSpec(name="Режим", kind="qualitative", values=["Автоматический"]),
        CharacteristicSpec(name="Применение", kind="qualitative", values=["Разовый"]),
    ]
    found = extract_rules("Станок полуавтоматический, многоразовый", "", specs)
    assert found == []


def test_exact_value_with_prefix_wins_over_the_bare_one() -> None:
    found = extract_rules("Смеситель однорычажный", "", _control(["Однорычажный", "Рычажный"]))
    assert [(e.name, e.raw_value) for e in found] == [("Тип управления", "Однорычажный")]


def test_count_prefix_does_not_flip_meaning() -> None:
    # «одноцветная печать» — монохром, а не «Цветная»; «одновременная» — не «Временная»;
    # «однолетняя» — не «Летняя» (на перечнях стандартов)
    for name, spec in (
        ("Бланк строгой отчетности, одноцветная печать, А4", "Цветная"),
        ("Бланк, двухцветная печать", "Цветная"),
        ("Лицензия одновременная на 5 пользователей", "Временная"),
        ("Жидкость однолетняя", "Летняя"),
    ):
        specs = [CharacteristicSpec(name="Вид", kind="qualitative", values=[spec])]
        assert extract_rules(name, "", specs) == [], name


def test_count_prefix_works_for_any_adjective_form() -> None:
    found = extract_rules("Смесители однорычажные для кухни", "", _control(["Рычажный"]))
    assert [(e.name, e.raw_value) for e in found] == [("Тип управления", "Рычажный")]


def test_counted_noun_after_number_is_not_a_list_value() -> None:
    # «500 листов» — число листов, а не «Формат: Листовая» (ложное «противоречит
    # описанию» на главном товаре); прилагательное после числа остаётся значением («0,7 синяя»)
    fmt = CharacteristicSpec(name="Формат", kind="qualitative", values=["А4", "Листовая"])
    color = CharacteristicSpec(name="Цвет чернил", kind="qualitative", values=["Синий"])
    found = {e.name: e.raw_value for e in extract_rules("Бумага А4, 500 листов", "", [fmt])}
    assert found == {"Формат": "А4"}
    assert "Формат" not in {e.name for e in extract_rules("", "В пачке 500 листов", [fmt])}
    got = {e.name: e.raw_value for e in extract_rules("Ручка шариковая 0,7 синяя", "", [color])}
    assert got == {"Цвет чернил": "Синий"}


def test_labelled_number_with_unit_of_other_quantity_is_a_conflict_not_silence() -> None:
    # «мощность 2200 кг» молча выбрасывалась: теперь значение с чужой единицей
    # доходит до карточки конфликтом — человек видит, что единица не та.
    power = CharacteristicSpec(name="Мощность", kind="quantitative", unit="Вт")
    [e] = extract_rules("Чайник электрический", "мощность 2200 кг", [power])
    assert (e.raw_value, e.raw_unit) == ("2200", "кг")
    assert fit_value(e.raw_value, e.raw_unit, power).status == "unit_mismatch"
    # счётная единица — значение другой характеристики, а не ошибка: «толщина 50 шт» молчит
    thickness = CharacteristicSpec(name="Толщина", kind="quantitative", unit="мкм")
    assert extract_rules("Мешки", "толщина 50 шт", [thickness]) == []
    # из нескольких чисел берётся подходящее
    [ok] = extract_rules("Чайник", "мощность 2200 Вт, 220 В", [power])
    assert (ok.raw_value, ok.raw_unit) == ("2200", "Вт")


def test_llm_unit_that_the_quote_contradicts_is_rejected() -> None:
    # «4 шт» модель отдала как «Напряжение. = 4 В» — у характеристики портала нет
    # единицы, проверка чужой единицы не срабатывала; единица модели против единицы цитаты
    volt = CharacteristicSpec(name="Напряжение.", kind="quantitative")
    items = LLMItems(items=[LLMItem(name="Напряжение.", value="4", unit="В", quote="4 шт")])
    got = asyncio.run(
        extract_llm("Батарейки АА", "4 шт, щелочные", [volt], _llm([items]), Glossary([]))  # type: ignore[arg-type]
    )
    assert got == []
    ok = LLMItems(items=[LLMItem(name="Напряжение.", value="1,5", unit="В", quote="1,5 В")])
    got = asyncio.run(
        extract_llm("Батарейки АА 1,5 В", "", [volt], _llm([ok]), Glossary([]))  # type: ignore[arg-type]
    )
    assert [(e.raw_value, e.raw_unit) for e in got] == [("1,5", "В")]


def test_unit_outside_known_quantities_stays_silent() -> None:
    # «3 года» при «мес», «50 Гц» при «кГц», «500 л.» у количества листов — единицы
    # вне таблицы величин или счётные: раньше молчание, конфликт стал бы ложной блокировкой
    for desc, spec in (("Количество листов: 500 л.", _q("Количество листов", "шт")),):
        assert _desc(desc, [spec]) == {}, desc


def test_years_are_taken_for_months_once_term_is_a_known_quantity() -> None:
    # срок в таблице величин: «3 года» при «мес» — 36 мес
    spec = _q("Минимальный остаточный срок годности", "мес")
    found = _desc("Минимальный остаточный срок годности 3 года", [spec])
    assert found == {spec.name: ("3", "лет")}
    assert fit_value("3", "лет", spec).value == "36"


def test_hertz_are_converted_once_frequency_is_a_known_quantity() -> None:
    # частота в таблице величин: «50 Гц» при «кГц» — то же значение, 0,05 кГц, а не молчание
    assert _desc("Частота 50 Гц", [_q("Частота", "кГц")]) == {"Частота": ("50", "Гц")}


def test_noun_value_after_number_still_matches() -> None:
    # запрет после числа отсекал и законные существительные — «4 рулона» → «Рулон»,
    # «30 капсул» → «Капсулы»; запрещено только прилагательное по основе («500 листов» ≠ «Листовая»)
    form = CharacteristicSpec(name="Форма выпуска", kind="qualitative", values=["Рулон", "Капсулы"])
    assert (
        _by_name(extract_rules("Бумага туалетная, 4 рулона", "", [form]))["Форма выпуска"].raw_value
        == "Рулон"
    )
    assert (
        _by_name(extract_rules("Витамины, 30 капсул", "", [form]))["Форма выпуска"].raw_value
        == "Капсулы"
    )


def test_label_matches_a_synonym_of_the_characteristic() -> None:
    # «Предназначение» склеено в «Назначение» (find_synonyms): метка поставщика — второе имя
    purpose = CharacteristicSpec(name="Назначение", kind="qualitative", synonyms=["Предназначение"])
    weight = CharacteristicSpec(name="Масса нетто", kind="quantitative", unit="г", synonyms=["Вес"])
    text = "Предназначение: для уроков химии; Вес: 50 г"
    got = _by_name(extract_rules("Набор посуды", text, [purpose, weight]))
    assert got["Назначение"].raw_value == "для уроков химии"
    assert (got["Масса нетто"].raw_value, got["Масса нетто"].raw_unit) == ("50", "г")


def test_label_equal_to_a_name_beats_the_same_word_as_a_synonym() -> None:
    # Синоним «Цвет» у «Цвета корпуса» (склейка дублей) не должен уводить метку «Цвет» от
    # характеристики с этим именем: отложенная выборка потеряла 0,5 п. п. на таких ничьих
    colour = CharacteristicSpec(name="Цвет", kind="qualitative")
    body = CharacteristicSpec(name="Цвет корпуса", kind="qualitative", synonyms=["Цвет"])
    got = _by_name(extract_rules("Чайник", "Цвет: белый", [colour, body]))
    assert got["Цвет"].raw_value == "белый" and "Цвет корпуса" not in got


def test_label_and_number_inside_the_name_fill_their_field() -> None:
    # Демо-текст: «…, белизна 146%» в наименовании; у позиции две белизны (обычная и CIE) — правило
    # числа по единице не решало, метка в наименовании решает
    white = CharacteristicSpec(name="Белизна", kind="quantitative", unit="%")
    cie = CharacteristicSpec(name="Cie белизна", kind="quantitative", unit="%")
    name = "Бумага для офисной техники Снегурочка А4, плотность 80 г/м2, 500 листов, белизна 146%"
    got = _by_name(extract_rules(name, "", [white, cie]))
    assert got["Белизна"].raw_value == "146" and "Cie белизна" not in got


def test_material_adjective_is_not_a_colour_without_label() -> None:
    # «Шкаф для одежды металлический … цвет серый»: «Металлический» — материал, а не цвет
    colour = CharacteristicSpec(
        name="Требования к цветовому решению",
        kind="qualitative",
        values=["Металлический", "Серый", "Белый"],
    )
    found = extract_rules("Шкаф для одежды металлический двухсекционный", "", [colour])
    assert all(e.raw_value != "Металлический" for e in found)
