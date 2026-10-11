"""Замер бюджета модели: время на товар с моделью и без, доля отказов и отклонённых кодом ответов.

    uv run python -m app.tools.measure_llm tests/fixtures/llm_cases.jsonl [ещё.jsonl ...] \
        [--budgets 4,10,20] [--out ../data/metrics/llm_budget.json]

Описания (jsonl: `id`, `name`, `description`) идут через /autofill и ответы на вопросы (первый
вариант модуля, как поставщик, который с ним соглашается) в процессе — Redis и Postgres не нужны.
Справочник и индексы — из `.env` корня, чат и эмбеддинги — настоящий сервер оттуда же (с хоста
llama.cpp server зовут по 127.0.0.1: `LLM_BASE_URL=http://127.0.0.1:8081/v1
EMBED_BASE_URL=http://127.0.0.1:8082/v1`). Кэш ответов модели выключен: меряется работа, а не
попадание в кэш.

Режимы на каждое описание:
- `off` — модель выключена (правила и справочник);
- `legacy` и `new` — модель без предела, прежним и нынешним промптом: долю ответов, отклонённых
  кодом, и время вызова считают отсюда;
- `budget-N` — модель с пределом шага N секунд: доля `degraded: ["llm"]` и время на товар.

Время на товар — весь путь поставщика: /autofill и ответы на вопросы. Перед каждым прогоном пауза
после отказа сервиса сбрасывается: описания не влияют друг на друга.
"""

import argparse
import asyncio
import json
import logging
import math
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.catalog import Catalog
from app.extract import llm_fill
from app.glossary import Glossary
from app.llm import ChatResult, LLMClient, Message
from app.narrowing import NONE_VALUE, NOTHING_FITS, OTHER_VALUE
from app.pipeline import AnswerIn, AutofillIn, AutofillOut, Deps, answer, autofill
from app.resources import load_exemplar_vectors, load_exemplars, load_ktru, load_similar, load_twins
from app.search import build_bm25
from app.session import MemoryKV, SessionStore
from app.tools.selfcheck import ROOT, settings_from_root

MAX_ANSWERS = 8
_NOT_AN_OPTION = {NONE_VALUE, OTHER_VALUE, NOTHING_FITS}
_REPLY = re.compile(r"ответ LLM: элементов=(\d+) принято=(\d+)")


def percentile(values: list[float], q: float) -> float | None:
    """Перцентиль по ближайшему рангу; пустой список — None."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[rank - 1]


@dataclass
class Counters:
    """Что видела модель за прогон: вызовы чата и их длительность, ответы и принятые элементы."""

    chat_s: list[float] = field(default_factory=list)
    prompt_chars: list[int] = field(default_factory=list)
    items: int = 0
    accepted: int = 0
    unparsed: int = 0

    def reset(self) -> None:
        self.chat_s.clear()
        self.prompt_chars.clear()
        self.items = self.accepted = self.unparsed = 0


class Timed:
    """Клиент с подсчётом: длительность каждого вызова чата (в том числе прерванного пределом)."""

    def __init__(self, inner: Any, counters: Counters) -> None:
        self._inner = inner
        self.counters = counters
        self.chat_model = inner.chat_model
        self.embed_model = inner.embed_model

    async def chat(
        self, messages: list[Message], *, schema: Any = None, temperature: float = 0.0
    ) -> ChatResult:
        self.counters.prompt_chars.append(sum(len(m["content"]) for m in messages))
        started = time.perf_counter()
        try:
            result: ChatResult = await self._inner.chat(
                messages, schema=schema, temperature=temperature
            )
            return result
        finally:
            self.counters.chat_s.append(time.perf_counter() - started)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = await self._inner.embed(texts)
        return vectors


class _ReplyLog(logging.Handler):
    """Читает из журнала извлечения, сколько элементов вернула модель и сколько принял код."""

    def __init__(self, counters: Counters) -> None:
        super().__init__(level=logging.INFO)
        self.counters = counters

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if (m := _REPLY.search(text)) is not None:
            self.counters.items += int(m[1])
            self.counters.accepted += int(m[2])
        elif "извлечение LLM не удалось" in text:
            self.counters.unparsed += 1


# Промпт до правил раздела 4.3: нужен только для сравнения доли отклонённых ответов.
_LEGACY_SYSTEM = (
    "Ты извлекаешь характеристики товара из текста поставщика для каталога госзакупок. "
    'Отвечай только JSON по схеме {"items": [{"name", "value", "unit", "quote"}]}. '
    "name — строго из списка характеристик. "
    "Если у характеристики есть перечень, value — из перечня. "
    "quote — точный фрагмент текста, откуда взято значение. Чего нет в тексте — не придумывай и не "
    "включай."
)


def _legacy_spec_line(s: Any) -> str:
    parts = [f"- {s.name}"]
    if s.unit:
        parts.append(f"единица {s.unit}")
    if s.values and s.group != "general":
        parts.append("перечень: " + "; ".join(s.values[:30]) + (" …" if len(s.values) > 30 else ""))
    elif s.range is not None:
        parts.append(f"диапазон {s.range.min}–{s.range.max}")
    return ", ".join(parts)


def legacy_messages(
    name: str, description: str, specs: list[Any], glossary: Glossary
) -> list[Message]:
    text = f"Наименование: {name}\nОписание: {description or '(нет)'}"
    abbrs = glossary.prompt_block(f"{name}\n{description}")
    user = "Характеристики позиции:\n" + "\n".join(_legacy_spec_line(s) for s in specs)
    if abbrs:
        user += f"\n\nСокращения в тексте:\n{abbrs}"
    user += f"\n\nТекст поставщика:\n{text}"
    return [{"role": "system", "content": _LEGACY_SYSTEM}, {"role": "user", "content": user}]


_NEW_MESSAGES = llm_fill.build_messages


async def follow(out: AutofillOut, deps: Deps) -> tuple[AutofillOut, int]:
    """Вопросы категории и КТРУ — первым вариантом модуля. Второе — сколько ответов дано."""
    answered = 0
    for _ in range(MAX_ANSWERS):
        if out.ktru.question is not None:
            question, stage = out.ktru.question, "category"
        elif out.chain and out.chain.ktru and out.chain.ktru.question is not None:
            question, stage = out.chain.ktru.question, "ktru"
        else:
            break
        value = next((o.value for o in question.options if o.value not in _NOT_AN_OPTION), None)
        if value is None:
            break
        inp = AnswerIn(characteristic=question.characteristic, value=value, stage=stage)  # type: ignore[arg-type]
        out = await answer(out.session_id, inp, deps)
        answered += 1
    return out, answered


@dataclass
class Mode:
    label: str
    enabled: bool
    budget_s: float | None
    prompt: str  # "new" | "legacy"


def modes(budgets: list[float]) -> list[Mode]:
    return [
        Mode("off", False, None, "new"),
        Mode("legacy", True, None, "legacy"),
        Mode("new", True, None, "new"),
        *(Mode(f"budget-{b:g}", True, b, "new") for b in budgets),
    ]


async def run_one(
    case: dict[str, Any], mode: Mode, deps: Deps, counters: Counters
) -> dict[str, Any]:
    counters.reset()
    deps.llm_enabled = mode.enabled
    deps.llm_budget_s = mode.budget_s
    deps.llm_paused_until = 0.0
    llm_fill.build_messages = legacy_messages if mode.prompt == "legacy" else _NEW_MESSAGES
    started = time.perf_counter()
    first = await autofill(
        AutofillIn(name=case["name"], description=case.get("description", "")), deps
    )
    autofill_s = time.perf_counter() - started
    out, answered = await follow(first, deps)
    total_s = time.perf_counter() - started
    return {
        "id": case.get("id"),
        "mode": mode.label,
        "autofill_s": round(autofill_s, 3),
        "total_s": round(total_s, 3),
        "answers": answered,
        "llm": out.llm,
        "degraded_llm": "llm" in out.degraded,
        "chat_calls": len(counters.chat_s),
        "chat_s": [round(s, 3) for s in counters.chat_s],
        "prompt_chars": list(counters.prompt_chars),
        "items": counters.items,
        "accepted": counters.accepted,
        "unparsed": counters.unparsed,
        "code": out.ktru.final_code,
        "filled": sum(1 for c in out.characteristics if c.value is not None),
        "by_llm": sum(1 for c in out.characteristics if c.method == "llm"),
    }


def summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(runs)
    calls = [s for r in runs for s in r["chat_s"]]
    items = sum(r["items"] for r in runs)
    accepted = sum(r["accepted"] for r in runs)
    degraded = sum(r["degraded_llm"] for r in runs)
    return {
        "products": n,
        "total_p50_s": percentile([r["total_s"] for r in runs], 0.5),
        "total_p95_s": percentile([r["total_s"] for r in runs], 0.95),
        "total_max_s": max((r["total_s"] for r in runs), default=None),
        "autofill_p50_s": percentile([r["autofill_s"] for r in runs], 0.5),
        "autofill_p95_s": percentile([r["autofill_s"] for r in runs], 0.95),
        "asked_model": sum(1 for r in runs if r["chat_calls"] > 0),
        "chat_calls": len(calls),
        "chat_p50_s": percentile(calls, 0.5),
        "chat_p95_s": percentile(calls, 0.95),
        "chat_max_s": max(calls, default=None),
        "prompt_chars_p50": percentile([c for r in runs for c in r["prompt_chars"]], 0.5),
        "prompt_chars_max": max((c for r in runs for c in r["prompt_chars"]), default=None),
        "degraded_llm": degraded,
        "degraded_share": round(degraded / n, 3) if n else None,
        "reply_items": items,
        "reply_accepted": accepted,
        "rejected_share": round(1 - accepted / items, 3) if items else None,
        "unparsed_replies": sum(r["unparsed"] for r in runs),
        "filled": sum(r["filled"] for r in runs),
        "filled_by_llm": sum(r["by_llm"] for r in runs),
    }


def load_cases(paths: list[Path]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in paths:
        with path.open(encoding="utf-8") as fh:
            cases += [json.loads(line) for line in fh if line.strip()]
    return cases


def build_deps(client: Any) -> Deps:
    settings = settings_from_root()
    if settings.exemplar_vectors_path and not Path(settings.exemplar_vectors_path).is_absolute():
        settings = settings.model_copy(
            update={"exemplar_vectors_path": str(ROOT / settings.exemplar_vectors_path)}
        )
    catalog = Catalog.load(settings.catalog_files())
    return Deps(
        catalog=catalog,
        bm25=build_bm25(catalog),
        vectors=None,
        llm=client,
        glossary=Glossary.load(),
        sessions=SessionStore(MemoryKV(), ttl_s=600),  # type: ignore[arg-type]
        min_score=settings.search_min_score,
        exemplars=load_exemplars(settings),
        exemplar_vectors=load_exemplar_vectors(settings),
        similar=load_similar(settings),
        twins=load_twins(settings),
        ktru=load_ktru(settings),
    )


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def report(result: dict[str, Any]) -> str:
    meta = result["meta"]
    lines = [f"модель: {meta['chat_model']} ({meta['base_url']}), описаний: {meta['cases']}", ""]
    lines.append(
        f"{'режим':<12}{'p50':>7}{'p95':>7}{'макс':>7}{'af p95':>8}"
        f"{'спросили':>9}{'degr':>6}{'отклон':>8}{'вызов p50':>10}{'p95':>6}"
    )
    for label, s in result["modes"].items():
        rejected = "-" if s["rejected_share"] is None else f"{s['rejected_share']:.0%}"
        degraded = f"{s['degraded_share'] or 0:.0%}"
        lines.append(
            f"{label:<12}{_fmt(s['total_p50_s']):>7}{_fmt(s['total_p95_s']):>7}"
            f"{_fmt(s['total_max_s']):>7}{_fmt(s['autofill_p95_s']):>8}{s['asked_model']:>9}"
            f"{degraded:>6}{rejected:>8}{_fmt(s['chat_p50_s']):>10}{_fmt(s['chat_p95_s']):>6}"
        )
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_cases(args.cases)
    if not cases:
        raise SystemExit("в файлах нет описаний")
    settings = settings_from_root()
    client = LLMClient.from_settings(settings)
    counters = Counters()
    logger = logging.getLogger("app.extract.llm_fill")
    logger.addHandler(_ReplyLog(counters))
    logger.setLevel(logging.INFO)
    logger.propagate = False  # журнал читает счётчик, на экран он не нужен
    deps = build_deps(Timed(client, counters))
    plan = modes([float(b) for b in args.budgets.split(",") if b.strip()])
    if args.modes:
        wanted = {m.strip() for m in args.modes.split(",")}
        plan = [m for m in plan if m.label in wanted]
    runs: list[dict[str, Any]] = []
    try:
        # Загрузка модели в память не должна попасть в первый прогон.
        await client.chat([{"role": "user", "content": "ответь: ok"}])
        for i, case in enumerate(cases, 1):
            for mode in plan:
                runs.append(await run_one(case, mode, deps, counters))
            print(f"{i}/{len(cases)} {case.get('id') or case['name'][:40]}", file=sys.stderr)
    finally:
        await client.aclose()
    by_mode = {m.label: [r for r in runs if r["mode"] == m.label] for m in plan}
    return {
        "meta": {
            "chat_model": settings.llm_chat_model,
            "base_url": settings.llm_base_url,
            "think": str(settings.llm_think),
            "cases": len(cases),
            "budgets": args.budgets,
        },
        "modes": {label: summarize(rs) for label, rs in by_mode.items()},
        "runs": runs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cases", nargs="+", type=Path, help="jsonl: id, name, description")
    parser.add_argument("--budgets", default="4,10", help="пределы шага модели, с, через запятую")
    parser.add_argument("--modes", default="", help="только эти режимы (off,legacy,new,budget-N)")
    parser.add_argument("--out", type=Path, help="полный разбор в json")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    result = asyncio.run(run(args))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    print(report(result))


if __name__ == "__main__":
    main()
