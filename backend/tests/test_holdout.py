import random

from app.portal.holdout import Entry, entry, make_case, split
from app.values import fold


def test_entry_keys_ignore_case_and_short_models() -> None:
    e = entry({"id": 1, "directory_id": 7, "name": "Папка  СИНЯЯ", "model": "AB-1",
               "manufacturer": "Erich Krause"})  # fmt: skip
    assert (e.name_key, e.model_key) == ("папка синяя", fold("Erich Krause") + "|" + fold("AB-1"))
    assert entry({"id": 2, "directory_id": 7, "name": "x", "model": "1"}).model_key is None


def test_split_excludes_twins_of_held_out_skus() -> None:
    entries = [
        Entry(1, 7, "папка синяя", "m|a1"),
        Entry(2, 7, "папка синяя", None),  # то же наименование
        Entry(3, 7, "папка красная", "m|a1"),  # та же модель
        Entry(4, 7, "скоросшиватель", "m|b2"),
        Entry(5, 7, "файл", None),
        Entry(6, 7, "конверт", None),
        Entry(10, 8, "ручка", None),
        Entry(11, 8, "ручка", None),  # категория из двух СТЕ — не откладывается
    ]
    chosen, excluded = split(entries, per_category=10, seed=1)
    assert len(chosen) == 2  # 4 товара в категории — откладывается половина
    assert chosen <= excluded
    # из товара 1-2-3 (наименование + модель, транзитивно) — не больше одной СТЕ, а убран он
    # из обучения целиком
    assert len(chosen & {1, 2, 3}) <= 1
    assert excluded & {1, 2, 3} in (set(), {1, 2, 3})
    assert not {10, 11} & (chosen | excluded)


def test_split_respects_per_category_and_seed() -> None:
    entries = [Entry(i, 1, f"товар {i}", None) for i in range(50)]
    a, _ = split(entries, per_category=5, seed=3)
    b, _ = split(entries, per_category=5, seed=3)
    assert len(a) == 5
    assert a == b


def test_split_component_chosen_with_all_twins() -> None:
    entries = [Entry(1, 7, "a", "m|1"), Entry(2, 7, "a", None), Entry(3, 7, "b", "m|1")]
    entries += [Entry(10 + i, 7, f"x{i}", None) for i in range(5)]
    for seed in range(20):
        chosen, excluded = split(entries, per_category=10, seed=seed)
        if chosen & {1, 2, 3}:
            assert {1, 2, 3} <= excluded
            return
    raise AssertionError("ни при одном seed товар 1-2-3 не отложен")


def test_make_case_splits_characteristics_into_text_and_target() -> None:
    rec = {
        "id": 1,
        "directory_id": 7,
        "category": "Папки",
        "name": "Папка",
        "characteristics": [
            {"name": "Цвет", "value": "синий", "unit": None},
            {"name": "Ширина корешка", "value": 15.0, "unit": "мм"},
            {"name": "С кольцами", "value": True, "unit": None},
            {"name": "Пустая", "value": None, "unit": None},
        ],
    }
    case = make_case(rec, "01.15", random.Random(0))
    assert case["code"] == "01.15"
    assert len(case["given"]) == 1  # из трёх непустых в текст уходит половина с округлением вниз
    assert len(case["target"]) == 2
    given = case["given"][0]
    assert case["description"].startswith(f"{given['name']}: {given['value']}")
    names = {c["name"] for c in case["target"]} | {given["name"]}
    assert names == {"Цвет", "Ширина корешка", "С кольцами"}
    widths = [c for c in case["target"] + case["given"] if c["name"] == "Ширина корешка"]
    assert widths[0]["value"] == "15"


def test_same_value_folds_text_and_compares_numbers() -> None:
    from app.portal.holdout import same_value

    assert same_value("Синий.", "синий")
    assert same_value("15", "15,0")
    assert not same_value("15", "16")
    assert not same_value("синий", "красный")


def test_score_case_counts_category_and_fill() -> None:
    from app.portal.holdout import score_case, summary

    case = {
        "code": "A",
        "given": [{"name": "Цвет", "value": "синий"}],
        "target": [{"name": "Ширина", "value": "15"}, {"name": "Материал", "value": "картон"}],
    }
    chars = [
        ("Цвет", "Синий", "filled"),
        ("Ширина", "16", "filled"),
        ("Материал", None, "not_found"),
    ]
    c = score_case(case, ["A", "B"], None, "A", chars)
    s = summary(c)
    assert (s["category_top1"], s["category_final"], s["right_category"]) == (1.0, 0.0, 1.0)
    assert s["given_recall"] == 1.0
    assert (s["target_precision"], s["target_recall"], s["target_in_spec"]) == (0.0, 0.0, 1.0)
    wrong = score_case(case, ["B"], "B", "B", chars)
    assert (wrong["top5"], wrong["right_category"], wrong["target_filled"]) == (0, 0, 0)


def test_summary_counts_decisions_without_questions_and_confident_errors() -> None:
    # точность автовыбора и доля уверенных ошибок — то, чего «категория без вопроса» не говорит
    from collections import Counter

    from app.portal.holdout import score_case, summary

    case = {"code": "A", "given": [], "target": []}
    c: Counter[str] = Counter()
    c += score_case(case, ["A"], "A", "A", [])  # решено верно
    c += score_case(case, ["B"], "B", "B", [])  # решено неверно — уверенная ошибка
    c += score_case(case, ["A", "B"], None, "A", [])  # задан вопрос
    c += score_case(case, ["A"], "A", "A", [])
    s = summary(c)
    assert (s["decided"], s["decided_precision"], s["confident_errors"]) == (0.75, 0.6667, 0.25)


def test_value_under_a_merged_name_is_found_by_its_synonym() -> None:
    # СТЕ портала пишет «Формат листа», справочник слил его в «Формат» (имя КТРУ): найденное
    # значение засчитывается по синониму, а не теряется
    from app.portal.holdout import score_case

    case = {"code": "A", "given": [{"name": "Формат листа", "value": "А4"}], "target": []}
    chars = [("Формат", "А4", "filled")]
    plain = score_case(case, ["A"], "A", "A", chars)
    merged = score_case(case, ["A"], "A", "A", chars, aliases={"формат листа": "формат"})
    assert (plain["given_correct"], merged["given_correct"]) == (0, 1)


def test_summary_counts_what_the_supplier_still_has_to_do() -> None:
    # Вопрос о категории, поиск вручную (верной нет в пятёрке), обязательные поля без значения;
    # «без единого действия» — код верный без вопроса и все обязательные заполнены
    from collections import Counter

    from app.portal.holdout import score_case, summary

    case = {"code": "A", "given": [], "target": []}
    required = ["Ширина", "Материал"]
    half = [("Ширина", "15", "filled"), ("Материал", None, "not_found")]
    full = [("Ширина", "15", "filled"), ("Материал", "картон", "filled")]
    clash = [("Ширина", "15", "filled"), ("Материал", "картон", "conflict")]
    c: Counter[str] = Counter()
    c += score_case(case, ["A"], "A", "A", half, required=required)
    c += score_case(case, ["A"], "A", "A", full, required=required)  # без единого действия
    c += score_case(case, ["B", "C", "D", "E", "F", "A"], None, "B", full, required=required)
    c += score_case(case, ["A", "B"], None, "A", clash, required=required)  # вопрос и конфликт
    s = summary(c)
    assert (s["question_rate"], s["manual_search_rate"], s["zero_touch"]) == (0.5, 0.25, 0.25)
    # обязательные — только при верной категории: 2 + 2 + 2, заполнено 1 + 2 + 1
    assert (s["required_auto"], s["manual_required_per_card"]) == (0.6667, 0.6667)


def test_fields_left_to_the_supplier_are_targets_not_filled_correctly() -> None:
    # Обязательных в стандартах портала почти нет (24 на 1 272 СТЕ): мера ручной работы —
    # характеристики настоящей СТЕ, которые модуль не заполнил верно (ввести или исправить)
    from collections import Counter

    from app.portal.holdout import score_case, summary

    case = {
        "code": "A",
        "given": [],
        "target": [{"name": "Ширина", "value": "15"}, {"name": "Материал", "value": "картон"}],
    }
    chars = [("Ширина", "15", "filled"), ("Материал", "пластик", "filled")]
    c: Counter[str] = Counter()
    c += score_case(case, ["A"], "A", "A", chars)  # одно верно, одно исправить
    c += score_case(case, ["B"], "B", "B", chars)  # неверная категория — не в счёт
    assert summary(c)["manual_fields_per_card"] == 1.0


def test_ktru_question_is_an_action_too() -> None:
    # Мастер открывает шаг «Позиция КТРУ», если второй этап спрашивает: это не «без действий»
    # (без справочника КТРУ оценка этого не видела)
    from collections import Counter

    from app.portal.holdout import score_case, summary

    case = {"code": "A", "given": [], "target": []}
    c: Counter[str] = Counter()
    c += score_case(case, ["A"], "A", "A", [], ktru_asked=True)
    c += score_case(case, ["A"], "A", "A", [])
    s = summary(c)
    assert (s["zero_touch"], s["ktru_question_rate"]) == (0.5, 0.5)


def test_value_copied_from_a_twin_or_neighbours_is_not_the_suppliers_text() -> None:
    # «значения поставщика найдены» засчитывали значение такой же СТЕ.
    from app.portal.holdout import score_case, summary

    case = {"code": "A", "given": [{"name": "Цвет", "value": "синий"}], "target": []}
    chars = [("Цвет", "Синий", "filled")]
    assert summary(score_case(case, ["A"], "A", "A", chars))["given_recall"] == 1.0
    hinted = score_case(case, ["A"], "A", "A", chars, hinted={"цвет"})
    assert summary(hinted)["given_recall"] == 0.0


def test_summary_carries_sample_sizes_of_filling_rows() -> None:
    from app.portal.holdout import score_case, summary

    case = {"code": "A", "given": [], "target": [{"name": "Ширина", "value": "15"}]}
    s = summary(score_case(case, ["A"], "A", "A", [("Ширина", "15", "filled")]))
    assert (s["targets"], s["target_filled"]) == (1, 1)
