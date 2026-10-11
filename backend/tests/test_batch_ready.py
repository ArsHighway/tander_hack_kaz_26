import asyncio
import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.api.v1 import get_deps
from app.batch import BatchRow, process_row, run_batch
from app.catalog import Catalog, CharacteristicSpec, Position
from app.main import create_app
from app.pipeline import AutofillIn, autofill
from app.schemas import Issue
from app.search import build_bm25
from tests.test_batch import _deps
from tests.test_ktru_types import CATEGORY
from tests.test_ktru_types import _deps as type_deps


def test_selected_type_without_final_ktru_requires_second_stage_answer() -> None:
    result = asyncio.run(
        process_row(BatchRow(row=1, name="Тумба прикроватная деревянная"), type_deps())
    )
    assert result.category_code == CATEGORY
    assert result.ktru_code is None
    assert result.status == "needs_answer"
    assert result.note is not None and "Ширина тумбы" in result.note
    assert "Уточните" in result.note


@pytest.mark.parametrize("linked", [False, True])
def test_category_without_available_ktru_is_not_ready(linked: bool) -> None:
    deps = _deps()
    specs = [
        CharacteristicSpec(
            name=name,
            kind="qualitative",
            values=[value],
            ktru=["31.01.12.160-00000001"] if linked else [],
        )
        for name, value in [
            ("Цвет", "Белый"),
            ("Материал", "Дерево"),
            ("Назначение", "Для дома"),
            ("Вид", "Напольный"),
        ]
    ]
    catalog = Catalog(
        [
            Position(
                code="01.16.99",
                name="Табуреты",
                okpd2="31.01.12.160",
                characteristics=specs,
                source="standards",
            ),
        ],
        "test",
    )
    deps.catalog, deps.bm25 = catalog, build_bm25(catalog)
    deps.llm_enabled = False
    row = BatchRow(
        row=1,
        name="Табуреты",
        description="Цвет: Белый; Материал: Дерево; Назначение: Для дома; Вид: Напольный",
    )
    result = asyncio.run(process_row(row, deps))
    assert result.category_code == "01.16.99"
    assert result.ktru_code is None and result.status == "needs_answer"
    assert result.note is not None
    assert ("не загружены" if linked else "связи нет") in result.note


def test_ready_row_has_final_ktru_not_only_category() -> None:
    result = asyncio.run(process_row(BatchRow(row=1, name="Батарейка AAA щелочная 1,5 В"), _deps()))
    assert result.status == "ready"
    assert result.ktru_code is not None


def test_final_ktru_does_not_hide_blocking_issues() -> None:
    row = BatchRow(
        row=1,
        name="Батарейка AAA щелочная 1,5 В",
        rejected=[
            Issue(
                field="characteristics",
                kind="moderation_rule",
                severity="block",
                message="Характеристика не принята",
                why="Недопустимое значение",
                rule="Проверить входную характеристику",
            )
        ],
    )
    result = asyncio.run(process_row(row, _deps()))
    assert result.ktru_code is not None and result.status == "needs_fix"
    assert result.issues[0].severity == "block"


def test_missing_chain_does_not_make_row_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    deps = _deps()
    out = asyncio.run(autofill(AutofillIn(name="Батарейка AAA щелочная 1,5 В"), deps))
    assert out.ktru.final_code is not None
    monkeypatch.setattr("app.pipeline.render", lambda *_: out.model_copy(update={"chain": None}))
    result = asyncio.run(process_row(BatchRow(row=1, name="Батарейка AAA щелочная 1,5 В"), deps))
    assert result.status == "needs_answer" and result.ktru_code is None
    assert result.note is not None and "Код КТРУ не определён" in result.note


def test_batch_counts_filter_and_export_keep_second_stage_answer() -> None:
    async def scenario() -> tuple[str, object]:
        deps = type_deps()
        deps.batches = _deps().batches
        assert deps.batches is not None
        job = await deps.batches.create(
            "f.csv", [BatchRow(row=1, name="Тумба прикроватная деревянная")]
        )
        await run_batch(job, deps)
        return job, deps

    job, deps = asyncio.run(scenario())
    app = create_app()
    app.dependency_overrides[get_deps] = lambda: deps
    client = TestClient(app)
    state = client.get(f"/api/v1/batch/{job}?limit=0").json()
    assert state["status"] == "done"  # Finished processing is distinct from a ready row.
    assert state["counts"] == {"needs_answer": 1}
    assert client.get(f"/api/v1/batch/{job}/results?status=ready").json()["matched"] == 0
    row = client.get(f"/api/v1/batch/{job}/results?status=needs_answer").json()["rows"][0]
    assert row["ktru_code"] is None and "Ширина тумбы" in row["note"]
    response = client.get(f"/api/v1/batch/{job}/result.xlsx")
    assert response.status_code == 200
    sheet = load_workbook(io.BytesIO(response.content)).active
    assert sheet is not None
    values = dict(zip([c.value for c in sheet[1]], [c.value for c in sheet[2]], strict=True))
    assert values["Код КТРУ"] is None
    assert values["Статус"] == "нужен ответ"
    assert "Ширина тумбы" in str(values["Замечания"])
