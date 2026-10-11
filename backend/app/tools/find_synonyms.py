"""Синонимы характеристик по смыслу — эксперимент: кандидаты от LLM или векторов, проверка по СТЕ.

«Плотность» и «Масса бумаги площадью 1м2» у бумаги — одна характеристика, хотя слов общих нет;
склейка по словам (`app.portal.standards._merge_duplicates`) их не видит. Здесь кандидатов
предлагает модель (один запрос на категорию) или близость векторов имён внутри категории, а
решает проверка по живым СТЕ портала (`check`). Делается один раз при сборке справочника.

    uv run --env-file ../.env python -m app.tools.find_synonyms llm --out ../data/synonyms/llm.jsonl
    uv run --env-file ../.env python -m app.tools.find_synonyms embed --out <embed.jsonl>
    uv run python -m app.tools.find_synonyms check <llm.jsonl> --out <llm.checked.json>

Оба сбора продолжаются с места остановки (строка на категорию). LLM: не больше `--max-calls`
запросов и стоп после трёх отказов подряд — квота общая с модулем.
"""

import argparse
import asyncio
import json
import logging
import math
import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.catalog import Catalog, CharacteristicSpec, Position
from app.config import get_settings
from app.ingest.characteristics import to_number
from app.ingest.units import normalize_unit
from app.llm import LLMClient, LLMError, LLMInputError, LLMOutputError
from app.portal.standards import merge_pairs
from app.search.text import tokens
from app.values import convert, fold

log = logging.getLogger(__name__)

PROMPT = (
    "Ниже характеристики одной категории товаров с единицами и примерами значений. Найди пары, "
    "которые означают одно и то же — одна величина или одно свойство, только названы по-разному "
    "(например, «Плотность, г/м2» и «Масса бумаги площадью 1м2, г»). Не пара: разные части "
    "товара («Цвет корпуса» и «Цвет ручки»), разные величины. Не уверен — не включай. "
    'Отвечай только JSON {"pairs": [["имя1", "имя2"], ...]}, имена — точно как в списке.'
)


JUDGE_PROMPT = (
    "Ниже пронумерованные пары характеристик товаров: категория, затем две характеристики с "
    "единицами и примерами значений. Пара — одна характеристика, если это одно свойство или одна "
    "величина, только названы по-разному («Плотность, г/м2» и «Масса бумаги площадью 1м2, г»; "
    "«Страна производства» и «Страна происхождения»). Не одна: разные части товара («Цвет "
    "корпуса» и «Цвет ручки»), разные измерения («Высота» и «Глубина»), разное назначение "
    "(«ввод» и «вывод»). Не уверен — не включай. "
    'Отвечай только JSON {"same": [номера пар, которые одна характеристика]}.'
)


class Pairs(BaseModel):
    pairs: list[list[str]]


class Same(BaseModel):
    same: list[int]


def own_specs(p: Position) -> list[CharacteristicSpec]:
    return [s for s in p.characteristics if s.group != "general"]


def describe(s: CharacteristicSpec) -> str:
    unit = f" ({s.unit})" if s.unit else ""
    examples = "; ".join(s.values[:3])
    return f"{s.name}{unit}" + (f": {examples}" if examples else "")


def _done(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.add(json.loads(line)["code"])
        except (ValueError, KeyError):
            continue
    return out


def clean_pairs(raw: list[list[str]], names: set[str]) -> list[list[str]]:
    """Пары модели — только из имён категории, без повторов и пар «имя с самим собой»."""
    seen: set[tuple[str, str]] = set()
    out = []
    for pair in raw:
        if len(pair) != 2 or pair[0] == pair[1] or not {pair[0], pair[1]} <= names:
            continue
        key = (min(pair), max(pair))
        if key not in seen:
            seen.add(key)
            out.append([key[0], key[1]])
    return out


async def collect_llm(catalog: Catalog, out: Path, max_calls: int, concurrency: int) -> int:
    client = LLMClient.from_settings(get_settings())
    done = _done(out)
    todo = [p for p in catalog.all() if p.code not in done and len(own_specs(p)) >= 2][:max_calls]
    log.info("категорий к модели: %d (уже готово %d)", len(todo), len(done))
    refusals = 0
    stop = asyncio.Event()
    slots = asyncio.Semaphore(concurrency)
    written = 0

    async def one(p: Position, fh: Any) -> None:
        nonlocal refusals, written
        if stop.is_set():
            return
        specs = own_specs(p)
        text = f"{PROMPT}\nКатегория: {p.name}\n" + "\n".join(f"- {describe(s)}" for s in specs)
        async with slots:
            if stop.is_set():
                return
            try:
                res = await client.chat([{"role": "user", "content": text}], schema=Pairs)
            except LLMOutputError as e:
                log.warning("%s: %s", p.code, str(e)[:200])  # ответ не по схеме — не отказ сервиса
                return
            except LLMError as e:
                refusals += 1
                log.warning("%s: %s", p.code, str(e)[:200])
                if refusals >= 3:
                    stop.set()
                    log.error("три отказа сервиса подряд — стоп, квоту бережём")
                return
        refusals = 0
        parsed = res.parsed if isinstance(res.parsed, Pairs) else Pairs(pairs=[])
        pairs = clean_pairs(parsed.pairs, {s.name for s in specs})
        fh.write(json.dumps({"code": p.code, "pairs": pairs}, ensure_ascii=False) + "\n")
        fh.flush()
        written += 1
        if written % 100 == 0:
            log.info("готово %d из %d", written, len(todo))

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as fh:
        await asyncio.gather(*(one(p, fh) for p in todo))
    return written


async def _embed_safely(client: LLMClient, texts: list[str]) -> list[list[float] | None]:
    """Векторы пачкой; на тексте, где модель отдаёт NaN (500 «unsupported value: NaN»), пачка
    делится пополам — такой текст остаётся без вектора, а не роняет сбор."""
    try:
        return list(await client.embed(texts))
    except LLMInputError:
        if len(texts) == 1:
            return [None]
        mid = len(texts) // 2
        return [
            *await _embed_safely(client, texts[:mid]),
            *await _embed_safely(client, texts[mid:]),
        ]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def collect_embed(
    catalog: Catalog, out: Path, threshold: float, batch: int, limit: int | None = None
) -> int:
    client = LLMClient.from_settings(get_settings())
    done = _done(out)
    todo = [p for p in catalog.all() if p.code not in done and len(own_specs(p)) >= 2][:limit]
    log.info("категорий к векторам: %d (уже готово %d)", len(todo), len(done))
    out.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with out.open("a", encoding="utf-8") as fh:
        group: list[Position] = []
        size = 0
        for p in [*todo, None]:
            if p is not None:
                group.append(p)
                size += len(own_specs(p))
                if size < batch:
                    continue
            if not group:
                break
            texts = [describe(s) for q in group for s in own_specs(q)]
            vectors = await _embed_safely(client, texts)
            at = 0
            for q in group:
                specs = own_specs(q)
                vecs = vectors[at : at + len(specs)]
                at += len(specs)
                pairs = []
                for i, a in enumerate(specs):
                    for j in range(i + 1, len(specs)):
                        b = specs[j]
                        va, vb = vecs[i], vecs[j]
                        if a.kind != b.kind or va is None or vb is None:
                            continue
                        cos = _cosine(va, vb)
                        if cos >= threshold:
                            pairs.append([a.name, b.name, round(cos, 4)])
                fh.write(json.dumps({"code": q.code, "pairs": pairs}, ensure_ascii=False) + "\n")
                written += 1
            fh.flush()
            if written % 500 < len(group):
                log.info("векторы: готово %d из %d", written, len(todo))
            group, size = [], 0
    return written


# --- проверка по СТЕ портала ---

_PER_AREA = re.compile(r"м2|м²|кв\.?\s*м|площад")


def units_fit(a: CharacteristicSpec, b: CharacteristicSpec) -> bool:
    """Одна величина: единицы пересчитываются или одной нет; «г/м2» и «г» у массы площади 1 м2 —
    тоже одна («Масса бумаги площадью 1м2, г»)."""
    if a.kind != b.kind:
        return False
    if a.kind == "qualitative":
        return True
    ua, ub = normalize_unit(a.unit), normalize_unit(b.unit)
    if ua is None or ub is None or ua == ub or convert(1.0, ua, ub) is not None:
        return True
    for y, ux, uy in ((b, ua, ub), (a, ub, ua)):
        if ux == "г/м2" and uy == "г" and _PER_AREA.search(fold(y.name)):
            return True
    return False


class Stats:
    """Заполнение характеристик в СТЕ категорий с кандидатами: сколько, какими значениями, сколько
    раз пары заполнены в одной СТЕ и с равными ли значениями."""

    def __init__(self, wanted: dict[int, dict[str, str]], pairs: dict[int, set[tuple[str, str]]]):
        self.wanted = wanted  # id категории → вариант имени (fold) → имя спецификации
        self.pairs = pairs
        self.fill: Counter[tuple[int, str]] = Counter()
        self.values: defaultdict[tuple[int, str], Counter[str]] = defaultdict(Counter)
        self.together: Counter[tuple[int, str, str]] = Counter()
        self.same: Counter[tuple[int, str, str]] = Counter()

    def add(self, row: dict[str, Any]) -> None:
        try:
            cat = int(row.get("directory_id"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        names = self.wanted.get(cat)
        if not names:
            return
        got: dict[str, str] = {}
        for ch in row.get("characteristics") or []:
            spec = names.get(fold(str(ch.get("name") or "")))
            value = ch.get("value")
            if spec is None or value in (None, ""):
                continue
            got[spec] = fold(str(value))
        for spec, value in got.items():
            self.fill[(cat, spec)] += 1
            self.values[(cat, spec)][value] += 1
        for a, b in self.pairs.get(cat, ()):
            if a in got and b in got:
                self.together[(cat, a, b)] += 1
                self.same[(cat, a, b)] += agree(got[a], got[b])


@lru_cache(maxsize=200_000)
def _stems(value: str) -> frozenset[str]:
    return frozenset(tokens(value))


def agree(x: str, y: str) -> bool:
    """Одно значение, записанное по-разному: «клетка» и «в клетку», «пластик» и «пластиковый»."""
    if x == y:
        return True
    nx, ny = to_number(x), to_number(y)
    if nx is not None and ny is not None:
        return nx == ny
    return any(
        len(min(u, v, key=len)) >= 4 and (u.startswith(v) or v.startswith(u))
        for u in _stems(x)
        for v in _stems(y)
    )


def _closed(counter: Counter[str]) -> bool:
    """Значения из перечня: двадцать самых частых покрывают 80 % заполнений."""
    total = sum(counter.values())
    return total > 0 and sum(k for _, k in counter.most_common(20)) >= 0.8 * total


def _median(counter: Counter[str]) -> float | None:
    numbers = sorted(n for v, k in counter.items() for n in [to_number(v)] * k if n is not None)
    return numbers[len(numbers) // 2] if numbers else None


def verdict(
    stats: Stats, cat: int, a: CharacteristicSpec, b: CharacteristicSpec
) -> tuple[str, str]:
    """(«accept» | «reject» | «unsure», причина) для пары «одна характеристика».

    Данные — вето, а не судья: «accept» только если в одних СТЕ пару заполняют одним значением;
    близкие медианы и общие «да/нет» ничего не доказывают («Высота» и «Глубина» стола), такие
    пары «unsure» — решает модель (`accepted_pairs`)."""
    if not units_fit(a, b):
        return "reject", "единицы разных величин"
    fa, fb = stats.fill[(cat, a.name)], stats.fill[(cat, b.name)]
    first, second = sorted((a.name, b.name))
    key = (cat, first, second)
    together, same = stats.together[key], stats.same[key]
    if together >= 3 and together / max(1, min(fa, fb)) >= 0.2:
        if same / together >= 0.8:
            return "accept", f"заполнены вместе {together} раз, совпадают {same}"
        return "reject", f"заполнены вместе {together} раз, различаются {together - same}"
    if not fa or not fb:
        return "unsure", "одну из характеристик СТЕ не заполняют"
    va, vb = stats.values[(cat, a.name)], stats.values[(cat, b.name)]
    if a.kind == "quantitative":
        ma, mb = _median(va), _median(vb)
        if ma is None or mb is None or ma <= 0 or mb <= 0:
            return "unsure", "нет чисел для сравнения"
        ratio = max(ma, mb) / min(ma, mb)
        return ("reject" if ratio > 2 else "unsure"), f"медианы {ma:g} и {mb:g}"
    top_a = [v for v, _ in va.most_common(20)]
    top_b = [v for v, _ in vb.most_common(20)]
    common = sum(any(agree(x, y) for y in top_b) for x in top_a)
    overlap = common / max(1, min(len(top_a), len(top_b)))
    if overlap == 0 and _closed(va) and _closed(vb):
        return "reject", "перечни значений без общих"
    return "unsure", f"общих частых значений {overlap:.0%}"


def check(
    catalog: Catalog, inputs: list[Path], ste: Path, out: Path, exclude: set[int] | None = None
) -> dict[str, Any]:
    pairs_by_code: dict[str, set[tuple[str, str]]] = defaultdict(set)
    origin: defaultdict[tuple[str, str, str], set[str]] = defaultdict(set)  # llm, embed
    for path in inputs:
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            for pair in row["pairs"]:
                first, second = sorted(pair[:2])
                pairs_by_code[row["code"]].add((first, second))
                origin[(row["code"], first, second)].add(path.name.split(".")[0])
    wanted: dict[int, dict[str, str]] = {}
    pairs_by_cat: dict[int, set[tuple[str, str]]] = {}
    for code, pairs in pairs_by_code.items():
        p = catalog.get(code)
        if p is None or p.portal_id is None or not pairs:
            continue
        names: dict[str, str] = {}
        in_pairs = {n for pair in pairs for n in pair}
        for s in own_specs(p):
            if s.name in in_pairs:
                for variant in (s.name, *s.synonyms):
                    names.setdefault(fold(variant), s.name)
        wanted[p.portal_id] = names
        pairs_by_cat[p.portal_id] = pairs
    stats = Stats(wanted, pairs_by_cat)
    with ste.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if exclude and row.get("id") in exclude:
                continue  # отложенная выборка: проверка пар её не видит, замер честный
            stats.add(row)
    results = []
    counts: Counter[str] = Counter()
    for code, pairs in sorted(pairs_by_code.items()):
        p = catalog.get(code)
        if p is None or p.portal_id is None:
            continue
        by_name = {s.name: s for s in own_specs(p)}
        for a, b in sorted(pairs):
            if a not in by_name or b not in by_name:
                continue
            v, why = verdict(stats, p.portal_id, by_name[a], by_name[b])
            counts[v] += 1
            results.append(
                {
                    "code": code,
                    "category": p.name,
                    "a": a,
                    "b": b,
                    "verdict": v,
                    "why": why,
                    "sources": sorted(origin[(code, a, b)]),
                }
            )
    report = {"pairs": len(results), "verdicts": dict(counts), "results": results}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


# --- решение модели о парах от векторов ---


def _key(r: dict[str, Any]) -> tuple[str, str, str]:
    return r["code"], r["a"], r["b"]


def _needs_judge(r: dict[str, Any]) -> bool:
    return r["verdict"] != "reject" and "llm" not in r.get("sources", [])


async def judge(
    results: list[dict[str, Any]],
    catalog: Catalog,
    client: Any,
    progress: Path,
    size: int = 30,
    max_calls: int = 2000,
    concurrency: int = 4,
) -> int:
    """Пары от векторов, против которых данные не возражают, — модели пачками по `size`: «одна ли
    это характеристика». Ответ — поле `judge` у пары; `progress` (jsonl) — продолжение с места
    остановки. Пары от модели (`llm`) и отвергнутые данными не спрашиваются."""
    known: dict[tuple[str, str, str], bool] = {}
    if progress.is_file():
        for line in progress.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            known[(row["code"], row["a"], row["b"])] = row["same"]
    for r in results:
        if _needs_judge(r) and _key(r) in known:
            r["judge"] = known[_key(r)]
    todo = [r for r in results if _needs_judge(r) and "judge" not in r]
    batches = [todo[i : i + size] for i in range(0, len(todo), size)][:max_calls]
    log.info("пар к модели: %d, пачек %d (уже решено %d)", len(todo), len(batches), len(known))
    slots = asyncio.Semaphore(concurrency)
    refusals = 0
    calls = 0

    def ask(i: int, r: dict[str, Any]) -> str:
        p = catalog.get(r["code"])
        specs = {s.name: s for s in own_specs(p)} if p else {}
        a, b = specs.get(r["a"]), specs.get(r["b"])
        left = describe(a) if a else r["a"]
        right = describe(b) if b else r["b"]
        return f"{i}. {p.name if p else r['code']}: {left} | {right}"

    async def one(batch: list[dict[str, Any]], fh: Any) -> None:
        nonlocal refusals, calls
        text = JUDGE_PROMPT + "\n" + "\n".join(ask(i, r) for i, r in enumerate(batch, 1))
        async with slots:
            if refusals >= 3:
                return
            try:
                res = await client.chat([{"role": "user", "content": text}], schema=Same)
            except LLMOutputError as e:
                log.warning("пачка: %s", str(e)[:200])
                return
            except LLMError as e:
                refusals += 1
                log.warning("пачка: %s", str(e)[:200])
                if refusals >= 3:
                    log.error("три отказа сервиса подряд — стоп, квоту бережём")
                return
        refusals = 0
        calls += 1
        same = set(res.parsed.same) if isinstance(res.parsed, Same) else set()
        for i, r in enumerate(batch, 1):
            r["judge"] = i in same
            fh.write(
                json.dumps(
                    {**dict(zip(("code", "a", "b"), _key(r), strict=True)), "same": r["judge"]},
                    ensure_ascii=False,
                )
                + "\n"
            )
        fh.flush()
        if calls % 50 == 0:
            log.info("пачек готово %d из %d", calls, len(batches))

    progress.parent.mkdir(parents=True, exist_ok=True)
    with progress.open("a", encoding="utf-8") as fh:
        await asyncio.gather(*(one(b, fh) for b in batches))
    return calls


_GENERIC = {"тип", "вид", "товар", "продукц", "продукт", "издел", "издели"}


def _generic(name: str) -> bool:
    """Имя без предмета: «Тип», «Вид товара» — у разных СТЕ значат разное (у стусла «Вид товара» —
    «стусло», «Тип» — «поворотное»), склейка путает значения."""
    words = set(tokens(name))
    return not words or words <= _GENERIC


def chosen(r: dict[str, Any]) -> bool:
    """Склеиваем, если модель сказала «одна» (сама предложила пару или подтвердила пару от
    векторов), а данные портала не возразили; имена без предмета не склеиваем."""
    if _generic(r["a"]) or _generic(r["b"]):
        return False
    return r["verdict"] != "reject" and ("llm" in r.get("sources", []) or r.get("judge") is True)


def accepted_pairs(checked: list[Path], source: str | None = None) -> dict[str, list[list[str]]]:
    """Выбранные пары (`chosen`) по коду позиции: вход `apply` и
    `build_standard_catalog --synonyms`."""
    out: dict[str, list[list[str]]] = defaultdict(list)
    for path in checked:
        for r in json.loads(path.read_text(encoding="utf-8"))["results"]:
            if source and source not in r.get("sources", [source]):
                continue
            if chosen(r) and [r["a"], r["b"]] not in out[r["code"]]:
                out[r["code"]].append([r["a"], r["b"]])
    return dict(out)


def apply(
    catalog_path: Path, checked: list[Path], out: Path, source: str | None = None
) -> dict[str, int]:
    """Справочник с принятыми синонимами — без пересборки из СТЕ (для замера на выборке)."""
    pairs = accepted_pairs(checked, source)
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    before = after = 0
    positions = []
    for raw in data["positions"]:
        p = Position.model_validate(raw)
        before += len(p.characteristics)
        if p.code in pairs:
            p = p.model_copy(
                update={"characteristics": merge_pairs(p.characteristics, pairs[p.code])}
            )
        after += len(p.characteristics)
        positions.append(p.model_dump(exclude_defaults=True))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({**data, "positions": positions}, ensure_ascii=False), encoding="utf-8"
    )
    return {"pairs": sum(len(v) for v in pairs.values()), "before": before, "after": after}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=["llm", "embed", "check", "judge", "apply"])
    ap.add_argument(
        "inputs", nargs="*", type=Path, help="check: файлы пар (llm/embed); apply: *.checked.json"
    )
    ap.add_argument("--catalog", type=Path, default=Path("../data/catalog/standards.json"))
    ap.add_argument("--ste", type=Path, default=Path("../data/ste_full/ste.jsonl"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--max-calls", type=int, default=9000, help="llm: потолок запросов")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--threshold", type=float, default=0.8, help="embed: косинус пары")
    ap.add_argument("--batch", type=int, default=256, help="embed: строк на запрос")
    ap.add_argument("--limit", type=int, help="embed: только первые N категорий (проверка)")
    ap.add_argument(
        "--exclude", type=Path, help="check: id СТЕ отложенной выборки (build_holdout exclude.txt)"
    )
    ap.add_argument("--source", choices=["llm", "embed"], help="apply: пары только одного способа")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    catalog = Catalog.load([args.catalog])
    if args.mode == "llm":
        n = asyncio.run(collect_llm(catalog, args.out, args.max_calls, args.concurrency))
        log.info("модель: записано категорий %d -> %s", n, args.out)
    elif args.mode == "embed":
        n = asyncio.run(collect_embed(catalog, args.out, args.threshold, args.batch, args.limit))
        log.info("векторы: записано категорий %d -> %s", n, args.out)
    elif args.mode == "judge":
        report = json.loads(args.inputs[0].read_text(encoding="utf-8"))
        client = LLMClient.from_settings(get_settings())
        progress = args.out.with_suffix(".jsonl")
        n = asyncio.run(
            judge(
                report["results"],
                catalog,
                client,
                progress,
                max_calls=args.max_calls,
                concurrency=args.concurrency,
            )
        )
        report["judged"] = Counter(str(r.get("judge")) for r in report["results"])
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("модель: пачек %d, решения %s -> %s", n, report["judged"], args.out)
    elif args.mode == "apply":
        done = apply(args.catalog, args.inputs, args.out, args.source)
        log.info("синонимы: %s -> %s", done, args.out)
    else:
        exclude = set()
        if args.exclude and args.exclude.is_file():
            exclude = {int(x) for x in args.exclude.read_text(encoding="utf-8").split()}
        report = check(catalog, args.inputs, args.ste, args.out, exclude)
        log.info("пар %d, вердикты %s -> %s", report["pairs"], report["verdicts"], args.out)


if __name__ == "__main__":
    main()
