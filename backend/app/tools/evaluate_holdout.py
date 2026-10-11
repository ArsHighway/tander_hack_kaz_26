"""Оценка модуля на отложенной выборке СТЕ (`build_holdout`) со справочником стандартов.

    uv run python -m app.tools.evaluate_holdout --limit 500 [--no-llm] [--no-vectors]

Справочник — из `CATALOG_PATHS` (должен быть `data/catalog/standards.json`, собранный без
отложенных СТЕ), векторы — `index_catalog` по нему. Вход случая — наименование и описание из части
характеристик; считаются категория (первая, в топ-5, итоговый код без вопросов, «не найдено»),
заполнение того, что было в тексте (`given_recall`), и того, чего не было (`target_*`). Пишет
`data/metrics/holdout.json` и разбор по случаям `holdout_cases.jsonl` рядом.

Вопрос о категории, где верная среди вариантов, получает ответ эталоном («поставщик выбрал»): время
шага ответа и заполнение после него — `after_answer` (модель и похожие СТЕ работают там). С
`--ktru` — второй этап «Позиция КТРУ», как в мастере: его вопрос — тоже действие человека. С
`--twins` — двойники на портале (`build_twins`): СТЕ выборки из них вычитаются, их двойники
остаются, как у новой карточки на портале. Точность значений по источнику — `by_method`.
"""

import argparse
import asyncio
import json
import logging
import random
import time
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import create_async_engine

from app.card_fill import HINT_IN_TEXT
from app.catalog import Catalog
from app.config import get_settings
from app.glossary import Glossary
from app.llm import LLMClient, LLMError
from app.narrowing import CONFIRM
from app.pipeline import (
    AnswerIn,
    AutofillIn,
    AutofillOut,
    Deps,
    answer,
    prepare_session,
    render,
)
from app.portal.holdout import same_value, score_case, summary
from app.search import build_bm25
from app.search.exemplar_vectors import ExemplarVectors
from app.search.exemplars import ExemplarIndex
from app.search.similar import SimilarValues, values_path
from app.search.twins import TwinIndex
from app.search.vectors import EmbeddingIndex
from app.session import MemoryKV, SessionStore
from app.values import fold

log = logging.getLogger(__name__)


class _Partial:
    """Подключённые части модели: без LLM — только правила, без векторов — только BM25."""

    def __init__(self, llm: LLMClient, chat: bool, embed: bool) -> None:
        self.llm, self.use_chat, self.use_embed = llm, chat, embed

    async def chat(self, *args: Any, **kwargs: Any) -> Any:
        if not self.use_chat:
            raise LLMError("оценка без LLM")
        return await self.llm.chat(*args, **kwargs)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.use_embed:
            raise LLMError("оценка без эмбеддингов")
        return await self.llm.embed(texts)


def load_cases(path: Path, limit: int | None, seed: int, skip: int = 0) -> list[dict[str, Any]]:
    """Случаи выборки в перемешанном порядке (`seed`): первые `limit` после `skip`. `skip` — срез,
    на котором решения не подбирались: на первых 2000 итерировали, `--skip 2000` — нетронутые."""
    with path.open(encoding="utf-8") as fh:
        cases = [json.loads(line) for line in fh if line.strip()]
    random.Random(seed).shuffle(cases)
    cases = cases[skip:]
    return cases[:limit] if limit else cases


async def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    catalog = Catalog.load(settings.catalog_files())
    cases = load_cases(args.holdout, args.limit, args.seed, args.skip)
    if not cases:
        raise SystemExit(f"в {args.holdout} нет случаев: сначала build_holdout")
    unknown = sum(1 for c in cases if catalog.get(c["code"]) is None)
    if unknown == len(cases):
        raise SystemExit(
            "ни одного кода выборки нет в справочнике: CATALOG_PATHS не standards.json?"
        )
    engine = create_async_engine(settings.database_url)
    llm = LLMClient.from_settings(settings)
    deps = Deps(
        catalog=catalog,
        bm25=build_bm25(catalog),
        vectors=EmbeddingIndex(engine, settings.embed_model, catalog.version)
        if args.vectors
        else None,
        llm=_Partial(llm, chat=args.llm, embed=args.vectors or args.exemplar_vectors is not None),
        glossary=Glossary.load(),
        sessions=SessionStore(MemoryKV(), ttl_s=600),  # type: ignore[arg-type]
        min_score=settings.search_min_score,
        # Как в шаге мастера: модель ждём не дольше бюджета, иначе p95 — не то, что увидит человек.
        llm_budget_s=settings.llm_budget_s,
        exemplars=ExemplarIndex.load(args.exemplars) if args.exemplars else None,
        exemplar_vectors=ExemplarVectors.load(args.exemplar_vectors)
        if args.exemplar_vectors
        else None,
        # Подсказки похожих СТЕ — из sqlite рядом с индексом (без отложенной выборки, как он сам).
        similar=SimilarValues(values_path(args.exemplars))
        if args.exemplars and values_path(args.exemplars).is_file() and args.similar
        else None,
        ktru=Catalog.load([args.ktru]) if args.ktru else None,
        # Карточка выборки — не двойник сама себе: вычитаем всю выборку, а не только прогон.
        twins=TwinIndex(args.twins, exclude=_holdout_ids(args.holdout)) if args.twins else None,
    )
    sem = asyncio.Semaphore(args.concurrency)
    total: Counter[str] = Counter()
    after: Counter[str] = Counter()
    degraded: Counter[str] = Counter()
    latencies: list[float] = []
    answer_latencies: list[float] = []
    out_cases = args.out.with_name(args.out.stem + "_cases.jsonl")
    args.out.parent.mkdir(parents=True, exist_ok=True)

    def methods_of(case: dict[str, Any], res: AutofillOut) -> Counter[str]:
        """Заполнение того, чего нет в тексте, по источнику значения: модель, двойник, похожие."""
        target = res.ktru.final_code or (
            res.ktru.candidates[0].code if res.ktru.candidates else None
        )
        position = deps.catalog.get(case["code"]) if target == case["code"] else None
        if position is None:
            return Counter()
        aliases = {fold(syn): fold(spec.name) for spec in position.characteristics
                   for syn in spec.synonyms}  # fmt: skip
        gold = {aliases.get(fold(t["name"]), fold(t["name"])): t["value"] for t in case["target"]}
        c: Counter[str] = Counter()
        for ch in res.characteristics:
            if ch.method is None or ch.value is None or ch.status == "conflict":
                continue
            want = gold.get(fold(ch.name))
            if want is not None:
                # Подсказка, которую назвал текст, — своей строкой, не в «rule».
                said = ch.method == "rule" and (ch.note or "").startswith(HINT_IN_TEXT)
                method = "confirmed" if said else ch.method
                c[f"{method}_filled"] += 1
                c[f"{method}_correct"] += same_value(ch.value, want)
        return c

    def score_of(case: dict[str, Any], codes: list[str], res: AutofillOut) -> Counter[str]:
        target = res.ktru.final_code or (
            res.ktru.candidates[0].code if res.ktru.candidates else None
        )
        chars: list[tuple[str, str | None, str]] = [
            (c.name, c.value, c.status) for c in res.characteristics
        ]
        general = {c.name for c in res.characteristics if c.group == "general"}
        required = [c.name for c in res.characteristics if c.required and c.group != "general"]
        position = deps.catalog.get(target) if target else None
        aliases = {
            fold(syn): fold(spec.name)
            for spec in (position.characteristics if position else [])
            for syn in spec.synonyms
        }
        ktru_asked = bool(res.chain and res.chain.ktru and res.chain.ktru.question)
        hinted = [c.name for c in res.characteristics if c.method in ("twin", "similar")]
        return score_case(
            case, codes, res.ktru.final_code, target, chars, general, aliases, required, ktru_asked,
            hinted, [(c.name, c.value) for c in res.extra],
            [c.value for c in res.characteristics if c.value and c.source is not None],
        )  # fmt: skip

    async def one(case: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            t0 = time.perf_counter()
            session = await prepare_session(
                AutofillIn(name=case["name"], description=case["description"]), deps
            )
            res = render(session, deps)
            dt = time.perf_counter() - t0
            codes = [c.code for c in session.candidates]
            score = score_of(case, codes, res) + methods_of(case, res)
            final, answered_ms = score, None
            if res.ktru.final_code is None and case["code"] in codes:
                # Поставщик выбрал верную категорию из вариантов — шаг /answer со своей моделью.
                await deps.sessions.save(session)
                t1 = time.perf_counter()
                res2 = await answer(
                    session.id, AnswerIn(characteristic=CONFIRM, value=case["code"]), deps
                )
                answered_ms = round((time.perf_counter() - t1) * 1000)
                final = score_of(case, codes, res2) + methods_of(case, res2)
        return {"id": case["id"], "code": case["code"], "name": case["name"], "top": codes[:5],
                "final": res.ktru.final_code, "degraded": res.degraded, "ms": round(dt * 1000),
                "ms_answer": answered_ms,
                # Для порога автовыбора: доля голоса похожих СТЕ за первую категорию и сколько их.
                "share": session.candidates[0].similar if session.candidates else None,
                "n_candidates": len(session.candidates),
                "twins": [[t.id, t.by, t.code == case["code"]] for t in session.twins],
                "score": dict(score), "after_answer": dict(final)}  # fmt: skip

    try:
        results = await asyncio.gather(*(one(c) for c in cases))
    finally:
        await llm.aclose()
        await engine.dispose()
    with out_cases.open("w", encoding="utf-8") as fh:
        for r in results:
            total.update(r["score"])
            after.update(r["after_answer"])
            degraded.update(r["degraded"])
            latencies.append(r["ms"])
            if r["ms_answer"] is not None:
                answer_latencies.append(r["ms_answer"])
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    latencies.sort()
    answer_latencies.sort()
    answered = summary(after)
    report = {
        "catalog": catalog.version,
        "llm": settings.llm_chat_model if args.llm else None,
        "vectors": settings.embed_model if args.vectors else None,
        "exemplar_vectors": str(args.exemplar_vectors) if args.exemplar_vectors else None,
        "exemplars": str(args.exemplars) if args.exemplars else None,
        "similar": deps.similar is not None,
        "twins": str(args.twins) if args.twins else None,
        "skip": args.skip,
        **summary(total),
        "by_method": _by_method(total),
        "degraded": dict(degraded),
        "latency_ms_p50": latencies[len(latencies) // 2],
        "latency_ms_p95": latencies[int(len(latencies) * 0.95)],
        "latency_ms_max": latencies[-1],
        # Доля ответов дольше 7 с — бюджет шага.
        "over_7s": round(sum(ms > 7000 for ms in latencies) / len(latencies), 4),
        "codes_not_in_catalog": unknown,
        "ktru": str(args.ktru) if args.ktru else None,
        # После ответа поставщика на вопрос категории (если верная была среди вариантов).
        "after_answer": _after_answer(answered, answer_latencies),
    }
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def _holdout_ids(path: Path) -> set[int]:
    with path.open(encoding="utf-8") as fh:
        return {int(json.loads(line)["id"]) for line in fh if line.strip()}


def _by_method(c: Counter[str]) -> dict[str, dict[str, float | int]]:
    """Значения того, чего нет в тексте, по источнику: сколько заполнено и сколько верно."""
    out: dict[str, dict[str, float | int]] = {}
    for method in ("rule", "llm", "answer", "twin", "similar", "confirmed"):
        filled = c[f"{method}_filled"]
        if filled:
            correct = c[f"{method}_correct"]
            out[method] = {"filled": filled, "correct": correct,
                           "precision": round(correct / filled, 4)}  # fmt: skip
    return out


def _after_answer(answered: dict[str, float | int], ms: list[float]) -> dict[str, Any]:
    keys = ("right_category", "given_recall", "target_recall", "target_precision",
            "manual_fields_per_card", "ktru_question_rate")  # fmt: skip
    return {
        "answered": len(ms),
        **{k: answered[k] for k in keys},
        "latency_ms_p50": ms[len(ms) // 2] if ms else None,
        "latency_ms_p95": ms[int(len(ms) * 0.95)] if ms else None,
        "over_7s": round(sum(x > 7000 for x in ms) / len(ms), 4) if ms else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--holdout", type=Path, default=Path("../data/holdout/holdout.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("../data/metrics/holdout.json"))
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--skip", type=int, default=0, help="пропустить первые N перемешанных случаев")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--no-llm", dest="llm", action="store_false")
    ap.add_argument("--no-vectors", dest="vectors", action="store_false")
    ap.add_argument("--exemplars", type=Path, help="похожие СТЕ (build_exemplars); нет — без них")
    ap.add_argument("--ktru", type=Path, help="справочник КТРУ (второй этап, как KTRU_PATHS)")
    ap.add_argument(
        "--exemplar-vectors", type=Path, help="векторы похожих СТЕ (как EXEMPLAR_VECTORS_PATH)"
    )
    ap.add_argument(
        "--no-similar",
        dest="similar",
        action="store_false",
        help="без подсказок похожих СТЕ (<exemplars>.values.sqlite)",
    )
    ap.add_argument(
        "--twins", type=Path, help="индекс двойников (build_twins); выборка из него вычитается"
    )
    args = ap.parse_args()
    logging.basicConfig(level=logging.ERROR)
    report = asyncio.run(run(args))
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
