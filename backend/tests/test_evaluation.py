import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import get_deps
from app.catalog import Catalog, CharacteristicSpec
from app.evaluation import (
    MetricRow,
    MetricsOut,
    accuracy_at_k,
    build_golden,
    precision_recall,
    run_evaluation,
)
from app.evaluation.golden import (
    BRANDS,
    LABEL_SYNONYMS,
    REAL_GROUPS,
    build_real_formats,
    fits_spec,
)
from app.glossary import Glossary
from app.main import create_app
from app.pipeline import Deps
from app.search import build_bm25
from app.session import SessionStore
from app.values import convert, parse_bucket
from tests.fakes import FakeRedis

FAKE = Path(__file__).parent / "fixtures" / "fake"
CATALOG = Catalog.load([FAKE / "ktru.json"])
DEMO = Catalog.load([Path(__file__).parents[1] / "app" / "catalog" / "demo" / "demo.json"])


def test_golden_is_deterministic_for_a_seed() -> None:
    assert build_golden(CATALOG, seed=7) == build_golden(CATALOG, seed=7)


def test_golden_has_every_group() -> None:
    groups = {c.group for c in build_golden(CATALOG)}
    assert groups == {"clean", "typo", "out_of_catalog", "near_domain", "corrupted", *REAL_GROUPS}


def test_real_formats_do_not_change_template_groups() -> None:
    """Реальные форматы — своим генератором: шаблонные кейсы те же, что без них."""
    old = [c for c in build_golden(CATALOG, seed=3) if c.group not in REAL_GROUPS]
    real = build_real_formats(CATALOG, seed=3)
    assert build_golden(CATALOG, seed=3) == old + real
    assert real == build_real_formats(CATALOG, seed=3)
    assert real != build_real_formats(CATALOG, seed=4)


def _spec(code: str, name: str, catalog: Catalog = DEMO) -> CharacteristicSpec:
    position = catalog.get(code)
    assert position is not None
    return next(s for s in position.characteristics if s.name == name)


def test_fits_spec_rejects_values_outside_position() -> None:
    paper = "17.12.14.129-00000031"
    assert fits_spec(_spec(paper, "Формат"), "А4")  # кириллическая «А» — то же значение
    assert not fits_spec(_spec(paper, "Формат"), "A6")
    assert fits_spec(_spec(paper, "Белизна"), "146")
    assert not fits_spec(_spec(paper, "Белизна"), "200")
    assert fits_spec(_spec(paper, "Масса бумаги площадью 1м2"), ">=80 ... <90")
    assert not fits_spec(_spec(paper, "Масса бумаги площадью 1м2"), "80")


def test_real_format_references_fit_their_position() -> None:
    """Эталон и ответы поставщика — значения из перечня или диапазона своей позиции."""
    for catalog in (CATALOG, DEMO):
        for case in build_real_formats(catalog):
            code = case.expected_code or ""
            assert not set(case.expected) & set(case.answers), case.id
            for name, value in {**case.expected, **case.answers}.items():
                assert fits_spec(_spec(code, name, catalog), value), (case.id, name)


def test_real_formats_have_every_group_on_demo_catalog() -> None:
    groups = Counter(c.group for c in build_real_formats(DEMO))
    assert set(groups) == set(REAL_GROUPS)
    assert all(n >= 10 for n in groups.values()), groups


def test_prose_is_one_line_with_unit_after_number() -> None:
    prose = [c for c in build_real_formats(DEMO) if c.group == "prose"]
    assert all("\n" not in c.description and ", " in c.description for c in prose)
    # Метка строчными и единица после числа: «масса бумаги 83 г/м2», «белизна 120%».
    text = " | ".join(c.description for c in prose)
    assert re.search(r"масса бумаги:? \d+ г/м2", text)
    assert re.search(r"белизна:? \d+%", text)


def test_unit_cases_convert_back_to_reference() -> None:
    """В тексте другая единица той же величины; пересчёт обратно даёт эталон позиции."""
    units = [c for c in build_real_formats(DEMO) if c.group == "unit"]
    assert units
    for case in units:
        converted = []
        for line in case.description.splitlines():
            m = re.fullmatch(r"(.+?)(?:, (\S+) ([\d,]+)| ([\d,]+) (\S+))", line)
            if not m:
                continue
            name = m[1]
            unit, number = (m[2], m[3]) if m[2] else (m[5], m[4])
            spec = _spec(case.expected_code or "", name)
            if unit != spec.unit and spec.unit is not None:
                back = convert(float(number.replace(",", ".")), unit, spec.unit)
                assert back is not None, (case.id, line)
                converted.append((spec, back))
        assert len(converted) == 1, case.id
        spec, back = converted[0]
        expected = case.expected[spec.name]
        if spec.values and (b := parse_bucket(expected)) is not None and not b.is_point:
            assert b.contains(back), (case.id, expected, back)
        else:
            assert back == pytest.approx(float(expected.replace(",", "."))), (case.id, back)


def test_synonym_cases_rename_a_label() -> None:
    synonyms = [c for c in build_real_formats(DEMO) if c.group == "synonym"]
    words = {w for options in LABEL_SYNONYMS.values() for w in options}
    for case in synonyms:
        labels = {line.split(",")[0].split()[0] for line in case.description.splitlines()}
        assert labels & words, case.id


def test_ambiguous_names_fit_several_positions() -> None:
    """Неоднозначное наименование — общее начало имён двух и более позиций, без различающих."""
    for catalog in (CATALOG, DEMO):
        cases = [c for c in build_real_formats(catalog) if c.group == "ambiguous"]
        assert cases
        for case in cases:
            n = len(case.name.split())
            same = [p for p in catalog.all() if p.name.split()[:n] == case.name.split()]
            assert len(same) >= 2, case.id
            assert case.answers, case.id  # поставщик знает, что ответить на вопрос


def test_noisy_cases_drop_characteristics_and_add_noise() -> None:
    noisy = [c for c in build_real_formats(DEMO) if c.group == "noisy"]
    assert noisy
    for case in noisy:
        spoken = set(case.expected) | set(case.answers)
        assert set(case.expected) < spoken or len(spoken) <= 2, case.id  # часть не указана
    noise = ("Артикул", "Цена", "Страна происхождения", "Гарантия", "Штрихкод", "В упаковке")
    assert all(any(w in c.description for w in noise) for c in noisy)
    assert all(any(b in c.name for b in BRANDS) for c in noisy)


def test_clean_cases_point_to_existing_positions() -> None:
    for case in build_golden(CATALOG):
        if case.group in ("clean", "typo", "corrupted"):
            assert CATALOG.get(case.expected_code or "") is not None


def test_accuracy_at_k() -> None:
    ranked = [["a", "b"], ["c", "d"]]
    assert accuracy_at_k(ranked, ["b", "x"], 1) == 0.0
    assert accuracy_at_k(ranked, ["b", "x"], 2) == 0.5


def test_precision_recall() -> None:
    assert precision_recall({("a", "1"), ("b", "2")}, {("a", "1")}) == (0.5, 1.0)
    assert precision_recall(set(), set()) == (1.0, 1.0)


class NoLLM:
    async def chat(self, *_: object, **__: object) -> object:
        from app.llm import LLMError

        raise LLMError("оценка без LLM")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("не ожидался")


def _deps() -> Deps:
    return Deps(
        catalog=CATALOG,
        bm25=build_bm25(CATALOG),
        vectors=None,
        llm=NoLLM(),  # type: ignore[arg-type]
        glossary=Glossary([]),
        sessions=SessionStore(FakeRedis(), ttl_s=900),  # type: ignore[arg-type]
        min_score=0.2,
    )


def test_run_evaluation_reports_every_metric_with_sample_size() -> None:
    import asyncio

    out = asyncio.run(run_evaluation(build_golden(CATALOG), _deps(), model="без LLM"))
    keys = {r.key for r in out.rows}
    assert {
        "ktru_acc1",
        "ktru_acc5",
        "ktru_final",
        "questions",
        "fill_precision",
        "fill_recall",
    } <= keys
    assert {"refusal", "near_no_false_code", "latency_p50", "latency_p95"} <= keys
    # Те же ключевые метрики отдельно на реальных форматах.
    assert {
        "ktru_acc1_real",
        "ktru_final_real",
        "questions_real",
        "fill_precision_real",
        "fill_recall_real",
        "ktru_final_ambiguous",
    } <= keys
    rows = {r.key: r for r in out.rows}
    assert rows["questions_real"].value > 0  # неоднозначные наименования доходят до вопросов
    assert any(k.startswith("detector_") for k in keys)
    assert all(r.n > 0 for r in out.rows)
    assert all(r.how for r in out.rows)


def test_metrics_endpoint_reads_last_run(tmp_path: Path) -> None:
    out = MetricsOut(
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
        catalog_version="ktru.json",
        catalog_source="synthetic",
        model="без LLM",
        rows=[MetricRow(key="ktru_acc1", title="Точность кода", value=0.9, n=10, how="тест")],
    )
    path = tmp_path / "latest.json"
    path.write_text(out.model_dump_json(), encoding="utf-8")
    deps = _deps()
    deps.metrics_path = path
    app = create_app()
    app.dependency_overrides[get_deps] = lambda: deps
    body = TestClient(app).get("/api/v1/metrics").json()
    assert body["rows"][0]["value"] == 0.9
    assert json.loads(path.read_text(encoding="utf-8"))["catalog_source"] == "synthetic"


def test_metrics_endpoint_404_before_first_run(tmp_path: Path) -> None:
    deps = _deps()
    deps.metrics_path = tmp_path / "none.json"
    app = create_app()
    app.dependency_overrides[get_deps] = lambda: deps
    r = TestClient(app).get("/api/v1/metrics")
    assert r.status_code == 404
    assert r.json()["detail"] == "оценка ещё не запускалась"


def test_portal_report_endpoint_reads_last_run(tmp_path: Path) -> None:
    from app.portal import build_report, parse_card

    card = parse_card(
        {"id": 5, "name": "Мешки для мусора 60л", "category_path": ".1.2.",
         "characteristics": [{"name": "Объем", "value": 60, "unit": "л", "type_id": 1}]}
    )  # fmt: skip
    assert card is not None
    path = tmp_path / "portal.json"
    path.write_text(build_report([card], source="тест").model_dump_json(), encoding="utf-8")
    deps = _deps()
    deps.portal_report_path = path
    app = create_app()
    app.dependency_overrides[get_deps] = lambda: deps
    body = TestClient(app).get("/api/v1/metrics/portal").json()
    assert (body["cards"], body["source"]) == (1, "тест")
    assert body["audit"]["rules"][0]["examples"][0]["url"] == "https://zakupki.mos.ru/sku/view/5"


def test_portal_report_endpoint_404_before_first_run(tmp_path: Path) -> None:
    deps = _deps()
    deps.portal_report_path = tmp_path / "none.json"
    app = create_app()
    app.dependency_overrides[get_deps] = lambda: deps
    r = TestClient(app).get("/api/v1/metrics/portal")
    assert r.status_code == 404
    assert r.json()["detail"] == "отчёт по карточкам портала ещё не собран"


def test_golden_does_not_corrupt_or_expect_general_fields() -> None:
    """Порча «не из перечня», «опечатка», «противоречие» и эталон заполнения — только по
    характеристикам: поля «Общих сведений» детектор намеренно не проверяет (перечни — подсказки)."""
    from app.catalog import Catalog, CharacteristicSpec, Position

    general = CharacteristicSpec(
        name="Производитель",
        kind="qualitative",
        values=["Туринский ЦБЗ", "Снегурочка"],
        group="general",
    )
    p = Position(
        code="P",
        name="Бумага для офисной техники",
        okpd2="17.12.14",
        characteristics=[
            general,
            CharacteristicSpec(name="Плотность", kind="quantitative", unit="г/м2"),
        ],
        source="t",
    )
    cases = build_golden(Catalog([p], "t"), seed=3)
    assert not [c for c in cases if c.corrupted_field == "Производитель"]
    assert not [c for c in cases if "Производитель" in c.expected]


def _general_paper() -> Catalog:
    from app.catalog import CharacteristicSpec, Position

    maker = CharacteristicSpec(
        name="Производитель", kind="qualitative", values=["Снегурочка"], group="general"
    )
    density = CharacteristicSpec(name="Плотность", kind="quantitative", unit="г/м2")
    p = Position(
        code="P",
        name="Бумага для офисной техники",
        okpd2="17.12.14",
        characteristics=[maker, density],
        source="t",
    )
    return Catalog([p], "t")


def test_fill_score_ignores_general_fields_taken_from_name() -> None:
    """Точность заполнения — по характеристикам: «Производитель» из наименования — поле
    карточки, а не лишнее значение (в эталоне его нет)."""
    import asyncio

    from app.evaluation.golden import GoldenCase
    from app.evaluation.metrics import _resolve
    from app.pipeline import AutofillIn, autofill

    cat = _general_paper()
    deps = _deps()
    deps.catalog, deps.bm25 = cat, build_bm25(cat)
    case = GoldenCase(
        id="c",
        group="clean",
        name="Бумага для офисной техники Снегурочка",
        description="Плотность: 80 г/м2",
        expected_code="P",
        expected={"Плотность": "80"},
    )
    out = asyncio.run(autofill(AutofillIn(name=case.name, description=case.description), deps))
    # контроль: модуль действительно заполнил поле «Общих сведений» — иначе тест ничего не мерит
    assert [(c.name, c.value) for c in out.characteristics if c.group == "general"] == [
        ("Производитель", "Снегурочка")
    ]
    _, _, _, filled, _ = asyncio.run(_resolve(case, deps))
    assert {name for name, _ in filled} == {"плотность"}


def test_real_formats_do_not_expect_general_fields() -> None:
    from app.evaluation.golden import build_real_formats

    cases = build_real_formats(_general_paper(), seed=3)
    assert cases
    assert not [c for c in cases if "Производитель" in c.expected]


def test_holdout_endpoint_shows_portal_holdout_rows(tmp_path: Path) -> None:
    report = {
        "catalog": "standards.json", "cases": 2000, "llm": None, "category_top1": 0.643,
        "category_top5": 0.786, "category_final": 0.363, "decided": 0.4285,
        "decided_precision": 0.846, "confident_errors": 0.066, "not_found": 0.0055,
        "given_recall": 0.389, "latency_ms_p50": 97, "latency_ms_p95": 1495,
        "decided_cases": 857, "givens": 5120, "zero_touch": 0.12, "required_auto": 0.3,
        "required_fields": 4000, "manual_required_per_card": 2.1, "right_category_cases": 1270,
        "over_7s": 0.0, "manual_fields_per_card": 3.4,
    }  # fmt: skip
    path = tmp_path / "holdout.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    deps = _deps()
    deps.holdout_report_path = path
    app = create_app()
    app.dependency_overrides[get_deps] = lambda: deps
    body = TestClient(app).get("/api/v1/metrics/holdout").json()
    rows = {r["key"]: r for r in body["rows"]}
    assert body["cases"] == 2000 and body["catalog"] == "standards.json"
    assert rows["category_top1"]["value"] == 0.643 and rows["category_top1"]["n"] == 2000
    assert rows["decided_precision"]["value"] == 0.846
    # выборка у каждой строки своя: точность автовыбора — от решённых без вопроса, значения — от
    # числа значений поставщика (у всех стояло 2 000)
    assert rows["decided_precision"]["n"] == 857 and rows["given_recall"]["n"] == 5120
    assert rows["latency_p50"]["value"] == 97
    # что остаётся человеку: без единого действия — от всех, обязательные — от их числа,
    # «вручную на карточку» — от СТЕ с верной категорией
    assert rows["zero_touch"]["value"] == 0.12 and rows["zero_touch"]["n"] == 2000
    assert rows["required_auto"]["n"] == 4000 and rows["manual_required"]["n"] == 1270
    assert rows["over_7s"]["value"] == 0.0
    assert rows["manual_fields"]["value"] == 3.4 and rows["manual_fields"]["n"] == 1270
    deps.holdout_report_path = tmp_path / "нет.json"
    assert TestClient(app).get("/api/v1/metrics/holdout").status_code == 404


def test_holdout_endpoint_shows_filling_beyond_text_and_twin_precision(tmp_path: Path) -> None:
    # Двойники: точность их значений — отдельной строкой, выборка — сколько они заполнили.
    report = {
        "catalog": "standards.json", "cases": 2000, "target_recall": 0.1826,
        "target_precision": 0.7681,
        "by_method": {"twin": {"filled": 479, "correct": 450, "precision": 0.9395}},
    }  # fmt: skip
    path = tmp_path / "holdout.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    deps = _deps()
    deps.holdout_report_path = path
    app = create_app()
    app.dependency_overrides[get_deps] = lambda: deps
    rows = {r["key"]: r for r in TestClient(app).get("/api/v1/metrics/holdout").json()["rows"]}
    assert (rows["target_recall"]["value"], rows["target_precision"]["value"]) == (0.1826, 0.7681)
    assert (rows["twin_precision"]["value"], rows["twin_precision"]["n"]) == (0.9395, 479)
