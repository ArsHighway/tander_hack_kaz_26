"""Модель против хаоса в описании: где её ставить и какую брать.

    uv run python -m app.tools.llm_chaos --cases 30 \
        --models gemma-4-31b-it-Q8_0 --out ../data/metrics/llm_chaos.json

Выборка — СТЕ хаккатона (`data/holdout_kazan`), у которых не меньше двух характеристик поставщика
есть в шаблоне категории. Описание портится как у живого поставщика (seed): опечатки, метки без
«Вид»/«Тип» и без двоеточия, сокращения («кол-во», «р-р»), всё одной строкой. Эталон — значения
поставщика до порчи.

Варианты на каждой модели (код проверяет каждый ответ, как в модуле):
- `rules` — только правила (без модели), на испорченном и на чистом тексте;
- `fill` — правила, затем модель дозаполняет пустые поля шаблона (как `_fill_missing`);
- `norm` — модель сначала исправляет опечатки и сокращения, потом правила;
- `norm+fill` — оба вызова.
Задержка — сумма вызовов модели на товар. Кэш ответов не используется.
"""

import argparse
import asyncio
import json
import random
import statistics
import time
from pathlib import Path
from typing import Any

import regex as re
from pydantic import BaseModel

from app.catalog import Catalog, CharacteristicSpec
from app.config import get_settings
from app.extract.llm_fill import extract_llm
from app.extract.model import Extracted
from app.extract.rules import extract_rules
from app.extract.title import derive_parts
from app.glossary import Glossary
from app.ingest.characteristics import to_number
from app.llm import LLMClient, LLMError, Message
from app.portal.cards import PortalChar
from app.portal.fill import same_value
from app.values import fit_value, fold

_WORD = re.compile(r"\p{L}{5,}")
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_GENERIC = {"вид", "тип", "наличие"}
_ABBR = {"количество": "кол-во", "размер": "р-р", "материал": "мат.", "диаметр": "диам."}
_SWAP = {"а": "о", "о": "а", "е": "и", "и": "е"}


def _typo(word: str, rnd: random.Random) -> str:
    i = rnd.randrange(1, len(word) - 1)
    op = rnd.choice(("swap", "drop", "double", "vowel"))
    if op == "swap":
        return word[:i] + word[i + 1] + word[i] + word[i + 2 :]
    if op == "drop":
        return word[:i] + word[i + 1 :]
    if op == "double":
        return word[:i] + word[i] + word[i:]
    j = next((k for k in range(1, len(word)) if word[k] in _SWAP), None)
    return word if j is None else word[:j] + _SWAP[word[j]] + word[j + 1 :]


def _mess(text: str, rnd: random.Random, rate: float = 0.3) -> str:
    return _WORD.sub(lambda m: _typo(m[0], rnd) if rnd.random() < rate else m[0], text)


def chaos(name: str, description: str, rnd: random.Random) -> str:
    """Описание, как его вставит торопливый поставщик: одной строкой, с опечатками."""
    parts = []
    for seg in description.split("; "):
        label, colon, value = seg.partition(": ")
        if not colon:
            parts.append(seg)
            continue
        words = label.split()
        if len(words) > 1 and fold(words[0]) in _GENERIC and rnd.random() < 0.6:
            label = " ".join(words[1:])
        label = label.lower()
        for full, short in _ABBR.items():
            if full in label and rnd.random() < 0.5:
                label = label.replace(full, short)
        sep = rnd.choice((": ", " ", " - ", " "))
        parts.append(f"{label}{sep}{value}")
    joiner = rnd.choice((", ", ". ", " "))
    return _mess(f"{name}{joiner}{joiner.join(parts)}", rnd)


class Fixed(BaseModel):
    text: str


NORM_SYSTEM = (
    "Ты исправляешь текст карточки товара для госзакупок. Исправь опечатки и раскрой сокращения "
    "(«кол-во» → «количество», «р-р» → «размер»). Числа, единицы, бренды, модели и артикулы не "
    "меняй. Ничего не добавляй и не удаляй, порядок слов сохрани. "
    'Ответ — JSON {"text": "<исправленный текст>"}.'
)


def _edit(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def accept_fix(original: str, fixed: str) -> bool:
    """Правка модели принимается, если числа те же и каждое новое слово — правка старого."""
    if _NUM.findall(original) != _NUM.findall(fixed):
        return False
    old = [fold(w) for w in re.findall(r"\p{L}+", original)]
    new = [fold(w) for w in re.findall(r"\p{L}+", fixed)]
    if abs(len(new) - len(old)) > max(2, len(old) // 5):
        return False
    pool = set(old)
    for w in new:
        if w in pool:
            continue
        if not any(_edit(w, o) <= 2 or (len(o) >= 2 and w.startswith(o[:2])) for o in pool):
            return False
    return True


def _truth(item: dict[str, str]) -> PortalChar:
    return PortalChar(
        name=item["name"], value=item["value"], numeric=to_number(item["value"]) is not None
    )


def _score(
    found: list[Extracted], given: list[dict[str, str]], specs: dict[str, CharacteristicSpec]
):
    by_name = {fold(e.name): e for e in found}
    right = wrong = 0
    for g in given:
        e = by_name.get(fold(g["name"]))
        if e is None:
            continue
        spec = specs[fold(g["name"])]
        value = fit_value(e.raw_value, e.raw_unit, spec).value or e.raw_value
        if same_value(value, _truth(g), e.raw_unit or spec.unit):
            right += 1
        else:
            wrong += 1
    return right, wrong


def _client(model: str) -> LLMClient:
    s = get_settings()
    update: dict[str, Any] = {
        "llm_chat_model": model,
        "llm_think": "low" if model.startswith("gpt-oss") else False,
        "llm_timeout_s": 60.0,
    }
    return LLMClient.from_settings(s.model_copy(update=update))


async def _normalize(llm: LLMClient, text: str) -> tuple[str, bool]:
    msgs: list[Message] = [
        {"role": "system", "content": NORM_SYSTEM},
        {"role": "user", "content": text},
    ]
    result = await llm.chat(msgs, schema=Fixed, temperature=0.0)
    fixed = result.parsed.text if isinstance(result.parsed, Fixed) else text
    return (fixed, True) if accept_fix(text, fixed) else (text, False)


def _split(text: str) -> tuple[str, str]:
    return derive_parts(text, "")


async def _case_with_model(
    case: dict[str, Any], llm: LLMClient, glossary: Glossary
) -> dict[str, Any]:
    specs = case["specs"]
    by = {fold(s.name): s for s in specs}
    out: dict[str, Any] = {}
    name, desc = _split(case["chaos"])
    base = extract_rules(name, desc, specs)
    t0 = time.perf_counter()
    norm_ok = False
    try:
        fixed, norm_ok = await _normalize(llm, case["chaos"])
    except LLMError:
        fixed = case["chaos"]
    t_norm = time.perf_counter() - t0
    nname, ndesc = _split(fixed)
    normed = extract_rules(nname, ndesc, specs)
    variants = {"fill": (name, desc, base, 0.0), "norm+fill": (nname, ndesc, normed, t_norm)}
    out["norm"] = (*_score(normed, case["given"], by), t_norm)
    out["norm_accepted"] = norm_ok
    out["fixed"] = fixed
    for key, (n, d, found, spent) in variants.items():
        have = {fold(e.name) for e in found}
        missing = [s for s in specs if fold(s.name) not in have and s.group != "general"]
        t1 = time.perf_counter()
        try:
            extra = await extract_llm(n, d, missing, llm, glossary)
        except LLMError:
            extra = []
        out[key] = (*_score([*found, *extra], case["given"], by), spent + time.perf_counter() - t1)
    return out


def build_cases(holdout: Path, catalog: Catalog, n: int, seed: int) -> list[dict[str, Any]]:
    rnd = random.Random(seed)
    rows = [json.loads(line) for line in holdout.open(encoding="utf-8")]
    rnd.shuffle(rows)
    cases = []
    for row in rows:
        position = catalog.get(row["code"])
        if position is None or not row.get("description"):
            continue
        names = {fold(s.name) for s in position.characteristics}
        given = [g for g in row["given"] if fold(g["name"]) in names]
        if len(given) < 2:
            continue
        cases.append(
            {
                "id": row["id"],
                "name": row["name"],
                "clean": f"{row['name']}\n{row['description']}",
                "chaos": chaos(row["name"], row["description"], rnd),
                "given": given,
                "specs": position.characteristics,
                "row": row,
            }
        )
        if len(cases) >= n:
            break
    return cases


def _summary(
    cases: list[dict[str, Any]], per_case: list[dict[str, Any]], key: str
) -> dict[str, Any]:
    total = sum(len(c["given"]) for c in cases)
    right = sum(r[key][0] for r in per_case)
    wrong = sum(r[key][1] for r in per_case)
    lat = sorted(r[key][2] for r in per_case)
    return {
        "recall": round(right / total, 3),
        "precision": round(right / (right + wrong), 3) if right + wrong else None,
        "wrong": wrong,
        "p50_s": round(statistics.median(lat), 2) if lat else 0.0,
        "p95_s": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 2) if lat else 0.0,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    catalog = Catalog.load([args.catalog])
    cases = build_cases(args.holdout, catalog, args.cases, args.seed)
    glossary = Glossary.load()
    report: dict[str, Any] = {"cases": len(cases), "givens": sum(len(c["given"]) for c in cases)}
    rules_rows = []
    for c in cases:
        by = {fold(s.name): s for s in c["specs"]}
        n, d = _split(c["chaos"])
        cn, cd = c["clean"].split("\n", 1)
        rules_rows.append(
            {
                "rules": (*_score(extract_rules(n, d, c["specs"]), c["given"], by), 0.0),
                "clean": (*_score(extract_rules(cn, cd, c["specs"]), c["given"], by), 0.0),
            }
        )
    report["rules"] = _summary(cases, rules_rows, "rules")
    report["rules_clean_text"] = _summary(cases, rules_rows, "clean")
    report["examples"] = [c["chaos"] for c in cases[:5]]
    for model in args.models:
        print(f"модель {model} …", flush=True)
        llm = _client(model)
        rows = []
        sem = asyncio.Semaphore(args.concurrency)

        async def one(
            c: dict[str, Any], llm: LLMClient = llm, sem: asyncio.Semaphore = sem
        ) -> dict[str, Any]:
            async with sem:
                return await _case_with_model(c, llm, glossary)

        rows = await asyncio.gather(*(one(c) for c in cases))
        report[model] = {
            key: _summary(cases, rows, key) for key in ("fill", "norm", "norm+fill")
        } | {"norm_accepted": sum(r["norm_accepted"] for r in rows)}
        print(json.dumps(report[model], ensure_ascii=False), flush=True)
        if args.dump:
            _dump(
                args.dump / f"norm_{model.replace(':', '_')}.jsonl",
                cases,
                [r["fixed"] for r in rows],
            )
    if args.dump:
        _dump(args.dump / "chaos.jsonl", cases, [c["chaos"] for c in cases])
        _dump(args.dump / "clean.jsonl", cases, [c["clean"] for c in cases])
    return report


def _dump(path: Path, cases: list[dict[str, Any]], texts: list[str]) -> None:
    """Те же СТЕ в формате отложенной выборки: на них `evaluate_holdout` меряет тип КТРУ."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for c, text in zip(cases, texts, strict=True):
            name, desc = text.split("\n", 1) if "\n" in text else _split(text)
            row = c["row"] | {"name": name, "description": desc}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--holdout", type=Path, default=Path("../data/holdout_kazan/holdout.jsonl"))
    ap.add_argument(
        "--catalog", type=Path, default=Path("../data/catalog/eval/ktru_types_fb2.json")
    )
    ap.add_argument("--models", nargs="+", default=[])
    ap.add_argument("--cases", type=int, default=30)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--dump", type=Path, default=None, help="папка для jsonl в формате выборки")
    args = ap.parse_args(argv)
    report = asyncio.run(run(args))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
