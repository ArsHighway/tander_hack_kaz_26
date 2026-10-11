from app.catalog import CharacteristicSpec
from app.tools.find_synonyms import Stats, clean_pairs, units_fit, verdict


def q(name: str, unit: str | None = None) -> CharacteristicSpec:
    return CharacteristicSpec(name=name, kind="quantitative", unit=unit)


def t(name: str) -> CharacteristicSpec:
    return CharacteristicSpec(name=name, kind="qualitative")


def test_model_pairs_keep_only_names_of_the_category() -> None:
    names = {"Плотность", "Масса бумаги площадью 1м2", "Формат"}
    raw = [["Плотность", "Масса бумаги площадью 1м2"], ["Масса бумаги площадью 1м2", "Плотность"],
           ["Формат", "Формат"], ["Формат", "Размер"], ["Один"]]  # fmt: skip
    assert clean_pairs(raw, names) == [["Масса бумаги площадью 1м2", "Плотность"]]


def test_mass_of_square_metre_and_density_are_one_quantity() -> None:
    assert units_fit(q("Плотность", "г/м2"), q("Масса бумаги площадью 1м2", "г"))
    assert not units_fit(q("Плотность", "г/м2"), q("Масса", "г"))  # просто масса — другое
    assert not units_fit(q("Вес", "кг"), q("Объем", "л"))
    assert units_fit(q("Объем", "мл"), q("Вместимость", "л"))
    assert not units_fit(q("Объем", "л"), t("Объем"))


def _stats(rows: list[dict], pairs: set[tuple[str, str]], names: list[str]) -> Stats:
    s = Stats({1: {n.casefold(): n for n in names}}, {1: pairs})
    for r in rows:
        s.add(
            {"directory_id": 1, "characteristics": [{"name": k, "value": v} for k, v in r.items()]}
        )
    return s


def test_filled_together_with_different_values_means_different_characteristics() -> None:
    # «Цвет корпуса» и «Цвет ручки» в одной СТЕ — разные значения: не одна характеристика,
    # что бы ни предложила модель
    a, b = t("Цвет корпуса"), t("Цвет ручки")
    rows = [{"Цвет корпуса": "белый", "Цвет ручки": "черный"}] * 5
    s = _stats(rows, {("Цвет корпуса", "Цвет ручки")}, ["Цвет корпуса", "Цвет ручки"])
    assert verdict(s, 1, a, b)[0] == "reject"


def test_never_together_close_medians_do_not_object_far_medians_do() -> None:
    # близкие медианы — не довод «одна» (высота и глубина стола тоже близки), только «не против»
    a, b = q("Плотность", "г/м2"), q("Масса бумаги площадью 1м2", "г")
    rows = [{"Плотность": "80"}] * 6 + [{"Масса бумаги площадью 1м2": "80"}] * 4
    s = _stats(
        rows,
        {("Масса бумаги площадью 1м2", "Плотность")},
        ["Плотность", "Масса бумаги площадью 1м2"],
    )
    assert verdict(s, 1, a, b)[0] == "unsure"
    far = [{"Плотность": "80"}] * 6 + [{"Масса бумаги площадью 1м2": "500"}] * 4
    s2 = _stats(
        far,
        {("Масса бумаги площадью 1м2", "Плотность")},
        ["Плотность", "Масса бумаги площадью 1м2"],
    )
    assert verdict(s2, 1, a, b)[0] == "reject"


def test_together_with_values_in_other_words_agree() -> None:
    # «клетка» и «в клетку» в одной СТЕ — одно значение, записанное по-разному
    a, b = t("Вид линовки"), t("Тип линовки")
    rows = [{"Вид линовки": "клетка", "Тип линовки": "в клетку"}] * 5
    s = _stats(rows, {("Вид линовки", "Тип линовки")}, ["Вид линовки", "Тип линовки"])
    assert verdict(s, 1, a, b)[0] == "accept"


def test_closed_lists_without_common_values_object_free_text_does_not() -> None:
    a, b = t("Цвет"), t("Форма")
    closed = [{"Цвет": c} for c in ["белый", "черный"] * 5] + [
        {"Форма": f} for f in ["круглая", "квадратная"] * 5
    ]
    s = _stats(closed, {("Форма", "Цвет")}, ["Цвет", "Форма"])
    assert verdict(s, 1, a, b)[0] == "reject"
    # «Назначение» и «Предназначение» — свободный текст: у каждой СТЕ своя фраза
    c, d = t("Назначение"), t("Предназначение")
    free = [{"Назначение": f"для уборки помещения {i}"} for i in range(30)] + [
        {"Предназначение": f"для мытья полов тип {i}"} for i in range(30)
    ]
    s2 = _stats(free, {("Назначение", "Предназначение")}, ["Назначение", "Предназначение"])
    assert verdict(s2, 1, c, d)[0] == "unsure"


def test_yes_no_values_in_common_prove_nothing() -> None:
    a, b = t("Балансирование АКБ"), t("Измерение тока разряда АКБ")
    rows = [{"Балансирование АКБ": v} for v in ["да", "нет"] * 3] + [
        {"Измерение тока разряда АКБ": v} for v in ["да", "нет"] * 3
    ]
    s = _stats(rows, {("Балансирование АКБ", "Измерение тока разряда АКБ")}, [a.name, b.name])
    assert verdict(s, 1, a, b)[0] == "unsure"


def test_without_portal_data_the_pair_is_unsure() -> None:
    a, b = t("Обложка"), t("Материал обложки")
    s = _stats(
        [{"Обложка": "картон"}], {("Материал обложки", "Обложка")}, ["Обложка", "Материал обложки"]
    )
    assert verdict(s, 1, a, b)[0] == "unsure"


def test_pair_merges_when_a_model_said_yes_and_the_data_did_not_object(tmp_path) -> None:
    import json

    from app.catalog import Position
    from app.tools.find_synonyms import accepted_pairs, apply

    checked = tmp_path / "llm.checked.json"
    density = {"code": "P1", "a": "Масса бумаги площадью 1м2", "b": "Плотность"}
    results = [
        {**density, "verdict": "accept", "sources": ["embed"], "judge": True},
        {**density, "verdict": "unsure", "sources": ["llm"]},
        {"code": "P1", "a": "Цвет", "b": "Формат", "verdict": "reject", "sources": ["llm"]},
        # векторы близки, модель не спрашивали — не склеиваем
        {"code": "P1", "a": "Белизна", "b": "Яркость", "verdict": "unsure", "sources": ["embed"]},
        {"code": "P1", "a": "Вес", "b": "Масса", "verdict": "unsure", "sources": ["llm"]},
        {
            "code": "P1",
            "a": "Длина",
            "b": "Ширина",
            "verdict": "unsure",
            "sources": ["embed"],
            "judge": False,
        },
    ]
    checked.write_text(json.dumps({"results": results}, ensure_ascii=False), encoding="utf-8")
    assert accepted_pairs([checked]) == {
        "P1": [["Масса бумаги площадью 1м2", "Плотность"], ["Вес", "Масса"]]
    }

    names = ["Плотность", "Масса бумаги площадью 1м2", "Цвет", "Формат", "Белизна", "Яркость"]
    names += ["Вес", "Масса", "Длина", "Ширина"]
    position = Position(
        code="P1", name="Бумага", okpd2="", source="test", characteristics=[t(n) for n in names]
    )
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps({"positions": [position.model_dump()]}, ensure_ascii=False), encoding="utf-8"
    )
    out = tmp_path / "out.json"
    assert apply(catalog, [checked], out) == {"pairs": 2, "before": 10, "after": 8}
    merged = Position.model_validate(json.loads(out.read_text(encoding="utf-8"))["positions"][0])
    density = next(c for c in merged.characteristics if c.name == "Плотность")
    assert density.synonyms == ["Масса бумаги площадью 1м2"]


def test_pairs_of_one_method_can_be_taken_alone(tmp_path) -> None:
    import json

    from app.tools.find_synonyms import accepted_pairs

    checked = tmp_path / "all.checked.json"
    results = [
        {"code": "P1", "a": "Формат", "b": "Формат листа", "verdict": "accept", "sources": ["llm"]},
        {"code": "P1", "a": "Вес", "b": "Масса", "verdict": "accept", "sources": ["embed", "llm"]},
        {
            "code": "P1",
            "a": "Высота",
            "b": "Длина",
            "verdict": "accept",
            "sources": ["embed"],
            "judge": True,
        },
    ]
    checked.write_text(json.dumps({"results": results}, ensure_ascii=False), encoding="utf-8")
    assert accepted_pairs([checked], "embed") == {"P1": [["Вес", "Масса"], ["Высота", "Длина"]]}
    assert len(accepted_pairs([checked])["P1"]) == 3


def test_check_does_not_see_holdout_cards(tmp_path) -> None:
    import json

    from app.catalog import Catalog, Position
    from app.tools.find_synonyms import check

    position = Position(
        code="P1",
        name="Ручка",
        okpd2="",
        source="test",
        portal_id=7,
        characteristics=[t("Цвет корпуса"), t("Цвет ручки")],
    )
    pairs = tmp_path / "llm.jsonl"
    pairs.write_text(
        json.dumps({"code": "P1", "pairs": [["Цвет корпуса", "Цвет ручки"]]}, ensure_ascii=False),
        encoding="utf-8",
    )
    ste = tmp_path / "ste.jsonl"
    chars = [{"name": "Цвет корпуса", "value": "белый"}, {"name": "Цвет ручки", "value": "черный"}]
    rows = [{"id": i, "directory_id": 7, "characteristics": chars} for i in range(5)]
    ste.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    catalog = Catalog([position], version="test")
    seen = check(catalog, [pairs], ste, tmp_path / "a.json")
    assert seen["results"][0]["verdict"] == "reject"
    assert seen["results"][0]["sources"] == ["llm"]
    hidden = check(catalog, [pairs], ste, tmp_path / "b.json", exclude=set(range(5)))
    assert hidden["results"][0]["verdict"] == "unsure"


class _FakeModel:
    """Отвечает «одна характеристика» на первую пару каждой пачки."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def chat(self, messages, schema=None):
        from types import SimpleNamespace

        self.calls.append(messages[-1]["content"])
        return SimpleNamespace(parsed=schema(same=[1]))


def test_model_judges_only_embedding_pairs_the_data_allows(tmp_path) -> None:
    import asyncio

    from app.catalog import Catalog, Position
    from app.tools.find_synonyms import judge

    names = ["Вид", "Тип", "Цвет", "Формат", "Высота", "Глубина", "Вес", "Масса"]
    position = Position(
        code="P1", name="Стол", okpd2="", source="test", characteristics=[t(n) for n in names]
    )
    catalog = Catalog([position], version="test")
    results = [
        {"code": "P1", "a": "Вид", "b": "Тип", "verdict": "unsure", "sources": ["embed"]},
        {"code": "P1", "a": "Цвет", "b": "Формат", "verdict": "reject", "sources": ["embed"]},
        {"code": "P1", "a": "Вес", "b": "Масса", "verdict": "unsure", "sources": ["embed", "llm"]},
        {"code": "P1", "a": "Высота", "b": "Глубина", "verdict": "accept", "sources": ["embed"]},
    ]
    model = _FakeModel()
    progress = tmp_path / "judge.jsonl"
    assert asyncio.run(judge(results, catalog, model, progress, size=10)) == 1
    assert model.calls[0].splitlines()[1:] == ["1. Стол: Вид | Тип", "2. Стол: Высота | Глубина"]
    assert [r.get("judge") for r in results] == [True, None, None, False]

    again = [{k: v for k, v in r.items() if k != "judge"} for r in results]
    assert asyncio.run(judge(again, catalog, model, progress, size=10)) == 0  # с места остановки
    assert [r.get("judge") for r in again] == [True, None, None, False]


def test_bare_type_or_kind_is_never_glued() -> None:
    # «Вид товара» у стусла — «стусло», «Тип» — «поворотное»: модель зовёт их одним, а это разное
    from app.tools.find_synonyms import chosen

    def pair(a: str, b: str) -> dict:
        return {"code": "P1", "a": a, "b": b, "verdict": "unsure", "sources": ["llm"]}

    assert not chosen(pair("Вид товара", "Тип"))
    assert not chosen(pair("Тип", "Тип кофеварки"))
    assert chosen(pair("Тип кофеварки", "Вид кофеварки"))
