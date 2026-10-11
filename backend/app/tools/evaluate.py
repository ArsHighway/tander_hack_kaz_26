"""Оценка конвейера на золотом наборе: `python -m app.tools.evaluate [--llm] [--seed 42]`.

Пишет `data/metrics/latest.json` (страница «Метрики», `GET /api/v1/metrics`) и подбирает порог
«вне справочника» (`SEARCH_MIN_SCORE`): печатает таблицу порогов и выбранное значение.
Без `--llm` конвейер работает на правилах: быстро и воспроизводимо; с `--llm` — как в проде.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from app.catalog import Catalog
from app.config import get_settings
from app.evaluation import MetricRow, build_golden, run_evaluation
from app.evaluation.near_twins import near_rows
from app.glossary import Glossary
from app.llm import LLMClient, LLMError
from app.pipeline import Deps
from app.search import build_bm25, find_candidates
from app.session import MemoryKV, SessionStore

THRESHOLDS = [0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5]


class _NoLLM:
    """Оценка без модели: извлечение только правилами, ответ помечается degraded."""

    async def chat(self, *_: Any, **__: Any) -> Any:
        raise LLMError("оценка без LLM")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise LLMError("оценка без эмбеддингов")


async def _calibrate(catalog: Catalog, deps: Deps, seed: int) -> list[tuple[float, float, float]]:
    """Для каждого порога: доля честных отказов и доля верной позиции среди пяти кандидатов."""
    cases = build_golden(catalog, seed=seed)
    known = [c for c in cases if c.group in ("clean", "typo")]
    outside = [c for c in cases if c.group == "out_of_catalog"]
    table = []
    for t in THRESHOLDS:
        hits = 0
        for c in known:
            found, _ = await find_candidates(
                f"{c.name}\n{c.description}",
                catalog,
                deps.bm25,
                None,
                None,
                min_score=t,
                gate=c.name,
            )
            hits += c.expected_code in [x.code for x in found[:5]]
        refusals = 0
        for c in outside:
            found, _ = await find_candidates(c.name, catalog, deps.bm25, None, None, min_score=t)
            refusals += not found
        table.append((t, refusals / len(outside), hits / len(known)))
    return table


async def run(use_llm: bool, seed: int) -> int:
    settings = get_settings()
    catalog = Catalog.load(settings.catalog_files())
    llm: Any = LLMClient.from_settings(settings) if use_llm else _NoLLM()
    deps = Deps(
        catalog=catalog,
        bm25=build_bm25(catalog),
        vectors=None,
        llm=llm,
        glossary=Glossary.load(),
        sessions=SessionStore(MemoryKV(), ttl_s=3600),  # type: ignore[arg-type]
        min_score=settings.search_min_score,
    )
    try:
        table = await _calibrate(catalog, deps, seed)
        print("порог  отказы-вне  верная-в-топ5")
        for t, refusal, acc5 in table:
            print(f"{t:5.2f}  {refusal:10.2f}  {acc5:13.2f}")
        # Порог, при котором хуже из двух долей максимальна; при равенстве — меньший.
        best = max(table, key=lambda r: (min(r[1], r[2]), -r[0]))
        deps.min_score = best[0]
        print(f"выбран порог {best[0]} (в .env: SEARCH_MIN_SCORE={best[0]})")
        model = settings.llm_chat_model if use_llm else "без LLM (только правила)"
        out = await run_evaluation(build_golden(catalog, seed=seed), deps, model=model)
        out.rows.append(
            MetricRow(
                key="min_score",
                title="Порог «вне справочника»",
                value=best[0],
                n=len(THRESHOLDS),
                how="порог нормированной оценки BM25 по наименованию; выбран так, чтобы худшая из "
                "долей «честный отказ» и «верная позиция в топ-5» была максимальной",
            )
        )
        out.rows += await near_rows(_NoLLM())  # 100 почти одинаковых товаров (near_twins)
        path = Path(settings.metrics_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(out.model_dump_json(indent=1), encoding="utf-8")
        for r in out.rows:
            print(f"{r.key:22} {r.value:8.3f}  n={r.n}")
        print(f"записано: {path}")
        return 0
    finally:
        if use_llm:
            await llm.aclose()


if __name__ == "__main__":
    # Деградация без LLM — штатный режим оценки: предупреждения конвейера не печатаем.
    logging.basicConfig(level=logging.ERROR)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--llm", action="store_true", help="извлечение с моделью из .env")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    sys.exit(asyncio.run(run(args.llm, args.seed)))
