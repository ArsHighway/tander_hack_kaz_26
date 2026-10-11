"""Самопроверка: карточка, заполненная модулем, против /check (app.tools.selfcheck)."""

import asyncio
import json
from pathlib import Path

from app.catalog import Catalog
from app.config import DEMO_CATALOG
from app.glossary import Glossary
from app.pipeline import Deps
from app.schemas import CardIn, CharIn, Fix, Issue
from app.search import build_bm25
from app.session import MemoryKV, SessionStore
from app.tools.selfcheck import ROOT, _from_root, _NoModel, fix_loops, run, run_case

CATALOG = Catalog.load([DEMO_CATALOG])
WHITE = "17.12.14.129-00000031"


def _deps() -> Deps:
    return Deps(
        catalog=CATALOG,
        bm25=build_bm25(CATALOG),
        vectors=None,
        llm=_NoModel(),
        glossary=Glossary([]),
        sessions=SessionStore(MemoryKV(), ttl_s=600),  # type: ignore[arg-type]
        min_score=0.2,
        llm_paused_until=float("inf"),
        embed_paused_until=float("inf"),
    )


def test_relative_paths_are_taken_from_the_repository_root() -> None:
    absolute = str(Path("C:/data/x.json").resolve())
    assert _from_root("data/a.json, " + absolute) == f"{ROOT / 'data/a.json'},{absolute}"
    assert _from_root("") == ""


def test_fix_that_gets_blocked_again_is_a_loop() -> None:
    card = CardIn(
        name="Бумага для офисной техники белая А4",
        ktru_code=WHITE,
        characteristics=[CharIn(name="Цветность", value="Белая")],
    )
    field = "characteristics.Цветность"
    bad = Issue(field=field, kind="mismatch", severity="block", message="m", why="w", rule="r",
                fix=Fix(value="Фиолетовая"))  # fmt: skip
    good = bad.model_copy(update={"fix": Fix(value="Белая")})
    loops = fix_loops(card, [bad, good], _deps())
    assert [(lp["field"], lp["fix"]) for lp in loops] == [(field, "Фиолетовая")]


def test_run_counts_blocks_on_module_values(tmp_path: Path) -> None:
    path = tmp_path / "d.jsonl"
    rows = [
        {"id": "P1", "name": "Бумага для офисной техники белая А4", "description": "500 листов"},
        {"id": "X1", "name": "Услуги по уборке помещений", "description": ""},
    ]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    result = asyncio.run(run(path, _deps()))
    s = result["summary"]
    assert s["cards"] == 2
    assert [c["id"] for c in result["cases"]] == ["P1", "X1"]
    for case in result["cases"]:
        # Блок на своё значение — только на поле, которое модуль заполнил сам.
        assert all(b["field"].startswith("characteristics.") for b in case["own_blocks"])
    assert s["own_blocks"] == sum(len(c["own_blocks"]) for c in result["cases"])
    assert s["ready"] == sum(1 for c in result["cases"] if c["verdict"] == "ready")


def test_ktru_question_is_answered_with_the_first_option_and_checked() -> None:
    from tests.test_chain import CATEGORY, WHITE
    from tests.test_chain import _deps as chain_deps

    case = asyncio.run(run_case({"id": "K1", "name": "Бумага для офисной техники"}, chain_deps()))
    assert case["ktru_answers"] == 1
    assert (case["code"], case["ktru_code"]) == (CATEGORY.code, WHITE)
    # Ответ на вопрос КТРУ — значение модуля: block на него нет (block карточки — «мало полей»).
    assert case["own_blocks"] == [], case["blocks"]
