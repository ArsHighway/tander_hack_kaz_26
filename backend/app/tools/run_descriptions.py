"""Прогон набора описаний через весь конвейер с моделью — как поставщик в мастере.

    set -a; . ../.env; set +a
    uv run python -m app.tools.run_descriptions "<набор.csv>" --out ../data/qa/synth \
        --variants base,norm --shard 0/3 --keys LLM_API_KEY_RESERVE,LLM_API_KEY_RESERVE2

Набор — csv, где каждая колонка — тема (ИНСТРУМЕНТЫ, ОДЕЖДА, МЕБЕЛЬ), каждая ячейка — одна вставка
поставщика: первая строка — наименование, остальное — описание (как `splitPasted` во фронте).
Справочник, похожие СТЕ, двойники и КТРУ — из `.env` стенда, сессии — в памяти, кэша ответов
модели нет (время — честное).

Варианты:
- `base` — как на стенде: модель дозаполняет поля определённой позиции не дольше `LLM_BUDGET_S`;
- `norm` — сначала модель исправляет опечатки и сокращения во всём тексте (`llm_chaos`,
  правку проверяет `accept_fix`), потом тот же конвейер с бюджетом `--norm-budget`.

Вопрос мастера (тип, позиция КТРУ, характеристика) получает первый настоящий вариант — как
поставщик, который соглашается с первым предложением; каждый шаг пишется в разбор. Ключи модели —
по очереди из `--keys` (имена переменных окружения): кончилась квота — следующий ключ, в лог
`KEY_DEAD`. Итог — `<out>/runs.<shard>.jsonl`, строка на пару (описание, вариант).
"""

import argparse
import asyncio
import contextvars
import csv
import dataclasses
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings, get_settings
from app.glossary import Glossary
from app.llm import LLMClient, LLMError
from app.llm.base import LLMRateLimited
from app.narrowing import CONFIRM, NOTHING_FITS
from app.pipeline import AnswerIn, AutofillIn, AutofillOut, Deps, answer, prepare_session, render
from app.resources import (
    load_exemplar_vectors,
    load_exemplars,
    load_ktru,
    load_similar,
    load_twins,
)
from app.search import build_bm25
from app.search.vectors import EmbeddingIndex
from app.session import MemoryKV, SessionStore
from app.tools.llm_chaos import _normalize
from app.tools.score_gold import same
from app.values import fold

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[3]
_SKIP = {"", "__other__", "__nothing__"}
_calls: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "calls", default=None
)


class KeyPool:
    """Клиенты модели по ключам: квота кончилась — следующий ключ. Вызовы пишутся в разбор."""

    def __init__(self, settings: Settings, names: list[str]) -> None:
        self.names = [n for n in names if os.environ.get(n)]
        if not self.names:
            raise SystemExit(f"ни одного ключа из {names} нет в окружении")
        self.clients = [
            LLMClient.from_settings(settings.model_copy(update={"llm_api_key": os.environ[n]}))
            for n in self.names
        ]
        self.turn = 0
        self.dead: set[int] = set()

    def _next(self) -> int:
        alive = [j for j in range(len(self.clients)) if j not in self.dead]
        if not alive:
            raise LLMRateLimited("квота кончилась на всех ключах")
        self.turn += 1
        return alive[self.turn % len(alive)]

    async def chat(self, *args: Any, **kwargs: Any) -> Any:
        """Ключи по кругу: параллельные вызовы делят лимиты всех ключей."""
        t0 = time.perf_counter()
        ok, error, i = False, None, -1
        try:
            while True:
                i = self._next()
                try:
                    result = await self.clients[i].chat(*args, **kwargs)
                    ok = True
                    return result
                except LLMRateLimited as e:
                    if not any(m in str(e).casefold() for m in ("usage limit", "quota")):
                        raise
                    self.dead.add(i)
                    print(f"KEY_DEAD {self.names[i]}", file=sys.stderr, flush=True)
        except LLMError as e:
            error = type(e).__name__
            raise
        finally:
            calls = _calls.get()
            if calls is not None:
                calls.append(
                    {
                        "s": round(time.perf_counter() - t0, 2),
                        "ok": ok,
                        "error": error,
                        "key": self.names[i] if i >= 0 else None,
                    }
                )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self.clients[0].embed(texts)

    async def aclose(self) -> None:
        for c in self.clients:
            await c.aclose()


def _abs(path: str) -> str:
    """Пути `.env` стенда — от корня репозитория, а запуск — из `backend/`."""
    parts = [p.strip() for p in path.split(",") if p.strip()]
    return ",".join(p if Path(p).is_absolute() else str(ROOT / p) for p in parts)


def load_items(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        head = fh.readline()
        fh.seek(0)
        # Выгрузка из Excel с русской локалью — через «;».
        delimiter = ";" if head.count(";") > head.count(",") else ","
        rows = list(csv.reader(fh, delimiter=delimiter))
    topics = [t.strip() for t in rows[0]]
    items = []
    for r, row in enumerate(rows[1:], 1):
        for c, topic in enumerate(topics):
            text = row[c].strip() if c < len(row) else ""
            if text:
                items.append({"id": f"{topic[:3]}-{r:03d}", "topic": topic, "text": text})
    return items


def split_pasted(text: str) -> tuple[str, str]:
    lines = [ln.strip() for ln in text.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    return (lines[0] if lines else ""), "\n".join(lines[1:]).strip()


def _pending(res: AutofillOut) -> tuple[Any, Literal["category", "ktru"]] | None:
    if res.ktru.question is not None:
        return res.ktru.question, "category"
    if res.chain and res.chain.ktru and res.chain.ktru.question is not None:
        return res.chain.ktru.question, "ktru"
    return None


def _snapshot(res: AutofillOut, deps: Deps) -> dict[str, Any]:
    chain = res.chain
    ktru_code = chain.ktru.final_code if chain and chain.ktru else None
    ktru_pos = deps.ktru.get(ktru_code) if deps.ktru and ktru_code else None
    filled = [
        {
            "name": c.name,
            "value": c.value,
            "unit": c.unit,
            "method": c.method,
            "status": c.status,
            "from": c.source.text if c.source else None,
            "note": c.note,
            "was": c.normalized_from,
            "checked": bool(c.checked),
        }
        for c in res.characteristics
        if c.value is not None
    ]
    return {
        "type_final": res.ktru.final_code,
        "candidates": [[c.code, c.name, round(c.score, 3)] for c in res.ktru.candidates[:3]],
        "category": [chain.category, chain.category_name] if chain else None,
        "ktru_missing": chain.ktru_missing if chain else None,
        "category_via": chain.category_via if chain else None,
        "ktru_type": chain.ktru_type if chain else None,
        "ktru_code": ktru_code,
        "ktru_name": ktru_pos.name if ktru_pos else None,
        "ktru_remaining": chain.ktru.remaining if chain and chain.ktru else None,
        "filled": filled,
        "empty_required": [c.name for c in res.characteristics if c.required and c.value is None],
        "extra": [[c.name, c.value] for c in res.extra],
        "issues": [[i.severity, i.field, i.message] for i in res.issues],
        "unread": [s.text for s in res.unread],
        "suggestions": [s.model_dump() for s in res.suggestions],
        "degraded": list(res.degraded),
        "steps": res.ktru.steps,
    }


def oracle_pick(q: Any, stage: str, options: list[Any], gold: dict[str, Any] | None) -> Any:
    """Ответ честного поставщика по эталону (`score_gold`): позиция — код эталона, характеристика —
    значение эталона. Эталон молчит — None (тогда первый вариант, `how: first`)."""
    if not gold:
        return None
    if q.characteristic == CONFIRM:
        if stage == "ktru":
            want = {gold.get("ktru_code"), *(gold.get("ktru_alt") or [])} - {None}
        else:
            want = {gold.get("type_code"), *(gold.get("type_alt") or [])} - {None}
        hit = [o for o in options if o.value in want or set(o.codes) & want]
        if hit:
            return hit[0]
        nothing = [o for o in q.options if o.value == NOTHING_FITS]
        return nothing[0] if want and nothing else None
    field = fold(q.characteristic)
    for v in gold.get("values") or []:
        if fold(v.get("field") or "") != field:
            continue
        for o in options:
            if same(o.value, v.get("value")) or same(o.label or "", v.get("value")):
                return o
    return None


async def run_item(
    item: dict[str, Any], variant: str, deps: Deps, pool: KeyPool, norm_budget: float
) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    _calls.set(calls)
    t0 = time.perf_counter()
    text: str = item["text"]
    norm: dict[str, Any] | None = None
    # Своя копия на случай: пауза модели после отказа (`llm_paused_until`) не гасит соседей.
    local = dataclasses.replace(deps)
    if variant == "norm":
        t1 = time.perf_counter()
        try:
            fixed, accepted = await asyncio.wait_for(_normalize(pool, text), timeout=20)  # type: ignore[arg-type]
        except (LLMError, TimeoutError) as e:
            fixed, accepted = text, False
            norm = {"error": type(e).__name__}
        norm = {**(norm or {}), "accepted": accepted, "s": round(time.perf_counter() - t1, 2)}
        if fixed != text:
            norm["fixed"] = fixed
        text = fixed if accepted else text
        local.llm_budget_s = norm_budget
    name, description = split_pasted(text)
    session = await prepare_session(AutofillIn(name=name, description=description), local)
    await local.sessions.save(session)
    res = render(session, local)
    first = _snapshot(res, local)
    asked: list[dict[str, Any]] = []
    for _ in range(8):
        pending = _pending(res)
        if pending is None:
            break
        q, stage = pending
        options = [o for o in q.options if o.value not in _SKIP]
        if not options:
            asked.append({"stage": stage, "q": q.characteristic, "pick": None})
            break
        pick, how = oracle_pick(q, stage, options, item.get("gold")), "gold"
        if pick is None:
            pick, how = options[0], "first"
        asked.append(
            {
                "stage": stage,
                "q": q.characteristic,
                "pick": pick.label or pick.value,
                "how": how,
                "first_ok": how == "gold" and pick is options[0],
                "options": [o.label or o.value for o in q.options[:8]],
            }
        )
        res = await answer(
            session.id, AnswerIn(characteristic=q.characteristic, value=pick.value, stage=stage),
            local,
        )  # fmt: skip
    return {
        "id": item["id"], "topic": item["topic"], "variant": variant, "text": item["text"],
        "norm": norm, "name": name, "description": description,
        "s_total": round(time.perf_counter() - t0, 2),
        "s_llm": round(sum(c["s"] for c in calls), 2), "llm_calls": calls,
        "first": {k: first[k] for k in ("type_final", "candidates")},
        "asked": asked, "final": _snapshot(res, local),
    }  # fmt: skip


async def run(args: argparse.Namespace) -> None:
    base = get_settings()
    settings = base.model_copy(
        update={
            "catalog_paths": _abs(base.catalog_paths),
            "exemplars_path": _abs(base.exemplars_path),
            "exemplar_vectors_path": _abs(base.exemplar_vectors_path),
            "twins_path": _abs(base.twins_path),
            "ktru_paths": _abs(base.ktru_paths),
            "embed_base_url": base.embed_base_url.replace("//embed:8080", "//127.0.0.1:8082"),
            "llm_timeout_s": 30.0,
            **({"llm_budget_s": args.budget} if args.budget is not None else {}),
            **(
                {
                    "llm_chat_model": args.model,
                    "llm_think": "low" if "gpt-oss" in args.model else False,
                }
                if args.model
                else {}
            ),
        }
    )
    items = load_items(args.csv)
    k, n = (int(x) for x in args.shard.split("/"))
    items = [it for i, it in enumerate(items) if i % n == k]
    if args.only:
        items = [it for it in items if it["id"] in set(args.only.split(","))]
    if args.limit:
        items = items[: args.limit]
    if args.gold:
        lines = args.gold.read_text(encoding="utf-8").splitlines()
        gold = {g["id"]: g for g in (json.loads(x) for x in lines if x.strip())}
        for it in items:
            it["gold"] = gold.get(it["id"])
    from app.catalog import Catalog

    catalog = Catalog.load(settings.catalog_files())
    engine = create_async_engine(settings.database_url)
    pool = KeyPool(settings, args.keys.split(","))
    review_pool = None
    if args.review_model:
        think = "low" if args.review_model.startswith("gpt-oss") else False
        review_settings = settings.model_copy(
            update={"llm_chat_model": args.review_model, "llm_think": think}
        )
        review_pool = KeyPool(review_settings, args.keys.split(","))
    exemplars_settings = settings
    deps = Deps(
        catalog=catalog,
        bm25=build_bm25(catalog),
        vectors=EmbeddingIndex(engine, settings.embed_model, catalog.version),
        llm=pool,  # type: ignore[arg-type]
        glossary=Glossary.load(),
        sessions=SessionStore(MemoryKV(), ttl_s=3600),  # type: ignore[arg-type]
        min_score=settings.search_min_score,
        llm_budget_s=settings.llm_budget_s,
        rerank_types=settings.llm_rerank_types,
        review_llm=review_pool,
        review_budget_s=settings.llm_review_budget_s,
        exemplars=load_exemplars(exemplars_settings),
        exemplar_vectors=load_exemplar_vectors(exemplars_settings),
        similar=load_similar(exemplars_settings),
        twins=load_twins(exemplars_settings),
        ktru=load_ktru(settings),
    )
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / f"runs.{k}.jsonl"
    done: set[tuple[str, str]] = set()
    if out.is_file():
        for line in out.open(encoding="utf-8"):
            r = json.loads(line)
            done.add((r["id"], r["variant"]))
    jobs = [(it, v) for it in items for v in args.variants.split(",") if (it["id"], v) not in done]
    sem = asyncio.Semaphore(args.concurrency)
    lock = asyncio.Lock()
    t_start = time.perf_counter()

    async def one(item: dict[str, Any], variant: str) -> None:
        async with sem:
            try:
                row = await run_item(item, variant, deps, pool, args.norm_budget)
            except Exception as e:  # сбой случая — в разбор, прогон идёт дальше
                log.exception("случай %s/%s", item["id"], variant)
                row = {"id": item["id"], "topic": item["topic"], "variant": variant,
                       "text": item["text"], "crash": f"{type(e).__name__}: {e}"}  # fmt: skip
            async with lock:
                with out.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(f"{item['id']} {variant} {row.get('s_total')} s", file=sys.stderr, flush=True)

    try:
        await asyncio.gather(*(one(it, v) for it, v in jobs))
    finally:
        await pool.aclose()
        if review_pool is not None:
            await review_pool.aclose()
        await engine.dispose()
    print(f"DONE {len(jobs)} за {time.perf_counter() - t_start:.0f} s", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("csv", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--variants", default="base,norm")
    p.add_argument("--shard", default="0/1")
    p.add_argument(
        "--keys", default="LLM_API_KEY_RESERVE,LLM_API_KEY_RESERVE2,LLM_API_KEY_RESERVE3"
    )
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--norm-budget", type=float, default=20.0)
    p.add_argument("--budget", type=float, default=None, help="LLM_BUDGET_S для base")
    p.add_argument("--model", default="", help="модель вместо LLM_CHAT_MODEL")
    p.add_argument("--gold", type=Path, help="эталон: вопросы получают ответ честного поставщика")
    p.add_argument("--review-model", default="", help="второй слой модели; пусто — без него")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--only", default="")
    args = p.parse_args()
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
