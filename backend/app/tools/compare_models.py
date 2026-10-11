"""Сравнение моделей LLM на работе, которую модель делает в модуле: дозаполнение характеристик,
которые правила не нашли, на карточках портала, где значение в тексте есть.

    uv run python -m app.tools.compare_models ../data/testset/testset.jsonl \
        --models gemma-4-31b-it-Q8_0 --cards 120 --out ../data/metrics/models.json

Выборка — карточки, где правила пропустили хотя бы одно значение, которое видно в тексте
(`evidenced`); у каждой модели те же карточки и те же характеристики. Эталон — значение поставщика
на портале, сравнение — `same_value` (с пересчётом единиц). Модель видит только то, что видит
в модуле: наименование, описание и спецификации, и её ответ проходит ту же проверку кодом
(`extract_llm`).
Кэш ответов не используется.
"""

import argparse
import asyncio
import json
import random
import statistics
import time
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.extract.llm_fill import extract_llm
from app.extract.model import LLMItems
from app.extract.rules import extract_rules
from app.glossary import Glossary
from app.llm import ChatResult, LLMClient, LLMError, LLMOutputError, Message
from app.portal.cards import PortalCard, load_cards
from app.portal.fill import evidenced, same_value
from app.portal.values import ValueIndex, card_specs
from app.values import fit_value, fold

# Модели, которые не выключают рассуждения, получают короткие (как в .env.example); gemma — без них.
THINK_LOW = ("gpt-oss",)


class _Counting:
    """Обёртка клиента: сколько элементов предложила модель и сколько раз ответ не прошёл схему."""

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm
        self.proposed = 0
        self.schema_errors = 0

    async def chat(
        self, messages: list[Message], *, schema: type[LLMItems], temperature: float = 0.0
    ) -> ChatResult:
        try:
            result = await self.llm.chat(messages, schema=schema, temperature=temperature)
        except LLMOutputError:
            self.schema_errors += 1
            raise
        items = getattr(result.parsed, "items", None)
        self.proposed += len(items or [])
        return result


def _gaps(card: PortalCard, index: ValueIndex) -> list[tuple[Any, Any]]:
    """(спецификация, значение поставщика) там, где правила не нашли видимое в тексте значение."""
    specs = {fold(s.name): s for s in card_specs(card, index)}
    found = {fold(e.name) for e in extract_rules(card.name, card.description, list(specs.values()))}
    return [
        (spec, ch)
        for ch in card.characteristics
        if (spec := specs.get(fold(ch.name))) is not None
        and fold(ch.name) not in found
        and evidenced(ch, card.text)
    ]


async def _run_model(
    model: str, sample: list[tuple[PortalCard, list[tuple[Any, Any]]]], glossary: Glossary
) -> dict[str, Any]:
    think = "low" if model.startswith(THINK_LOW) else False
    settings = get_settings().model_copy(update={"llm_chat_model": model, "llm_think": think})
    llm = _Counting(LLMClient.from_settings(settings))
    correct = wrong = total = calls = failed = 0
    latencies: list[float] = []
    for card, gaps in sample:
        specs = [spec for spec, _ in gaps]
        total += len(gaps)
        started = time.perf_counter()
        try:
            got = await extract_llm(card.name, card.description, specs, llm, glossary)
        except LLMError as exc:
            failed += 1
            if calls == 0 and any(code in str(exc) for code in ("401", "402", "403", "404")):
                return {"model": model, "error": str(exc)[:200]}  # платная или нет такой модели
            continue
        finally:
            calls += 1
        latencies.append((time.perf_counter() - started) * 1000)
        by_name = {e.name: e for e in got}
        for spec, truth in gaps:
            e = by_name.get(spec.name)
            if e is None:
                continue
            value = fit_value(e.raw_value, e.raw_unit, spec).value or e.raw_value
            if same_value(value, truth, spec.unit):
                correct += 1
            else:
                wrong += 1
    accepted = correct + wrong
    lat = sorted(latencies)
    return {
        "model": model,
        "cards": len(sample),
        "gaps": total,
        "correct": correct,
        "wrong": wrong,
        "recall": correct / total if total else 0.0,
        "precision": correct / accepted if accepted else 0.0,
        "proposed": llm.proposed,
        "rejected_by_code": llm.proposed - accepted,
        "schema_errors": llm.schema_errors,
        "failed_calls": failed,
        "latency_p50_ms": round(statistics.median(lat)) if lat else None,
        "latency_p95_ms": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))]) if lat else None,
    }


def _table(rows: list[dict[str, Any]]) -> str:
    head = (
        "| Модель | Найдено верно (полнота) | Точность | Предложено / отвергнуто кодом | "
        "Ошибок схемы | Сбоев | Задержка p50 / p95, мс |\n|---|---|---|---|---|---|---|"
    )
    lines = [head]
    for r in rows:
        if "error" in r:
            lines.append(f"| {r['model']} | недоступна: {r['error'][:80]} | | | | | |")
            continue
        lines.append(
            f"| {r['model']} | {r['correct']} из {r['gaps']} ({r['recall']:.1%}) | "
            f"{r['precision']:.1%} | {r['proposed']} / {r['rejected_by_code']} | "
            f"{r['schema_errors']} | {r['failed_calls']} | "
            f"{r['latency_p50_ms']} / {r['latency_p95_ms']} |"
        )
    return "\n".join(lines)


async def run(paths: list[Path], models: list[str], n_cards: int, seed: int) -> list[dict]:
    cards = load_cards(paths)
    index = ValueIndex(cards)
    candidates = [(c, g) for c in cards if (g := _gaps(c, index))]
    random.Random(seed).shuffle(candidates)
    sample = candidates[:n_cards]
    print(f"карточек с пропусками правил: {len(candidates)}, в выборке {len(sample)}")
    glossary = Glossary.load()
    rows = []
    for model in models:
        print(f"модель {model} …", flush=True)
        rows.append(await _run_model(model, sample, glossary))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("inputs", nargs="+", type=Path, help="карточки портала (jsonl)")
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--cards", type=int, default=120)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=None, help="куда писать результат (JSON)")
    args = ap.parse_args(argv)
    rows = asyncio.run(run(args.inputs, args.models, args.cards, args.seed))
    print(_table(rows))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
