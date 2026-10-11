"""Векторы наименований похожих СТЕ для голоса категорий (`EXEMPLAR_VECTORS_PATH`).

    uv run python -m app.tools.build_exemplar_vectors --exemplars ../data/catalog/exemplars.jsonl \
        --out ../data/catalog/exemplars.vectors.npz

Из каждой категории берётся до `--per-category` наименований (случайно, seed 7 на категорию): на
отложенной выборке 10 на категорию дали тот же прирост, что все 623 тыс., а памяти и времени — в
десять раз меньше.
Модель — `EMBED_MODEL` из `.env` (bge-m3); `--reuse` — прошлый файл, его векторы берутся по
наименованию без запроса к модели. Порцию с NaN модель отдаёт ошибкой: порция делится
пополам, наименование, на котором модель падает, получает нулевой вектор (не голосует), их число
печатается; если векторов не получил никто — ошибка.
"""

import argparse
import asyncio
import json
import logging
import random
from collections import defaultdict
from pathlib import Path

import numpy as np

from app.config import Settings
from app.llm import LLMClient, LLMInputError
from app.search.exemplar_vectors import ExemplarVectors

log = logging.getLogger(__name__)
BATCH = 128


def sample(rows: list[dict], per_category: int, seed: int = 7) -> list[dict]:
    by_code: defaultdict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_code[r["code"]].append(r)
    out: list[dict] = []
    for code in sorted(by_code):
        items = by_code[code]
        # Генератор на категорию: добавленные в одну категорию наименования не меняют выборку
        # остальных, и `--reuse` берёт их векторы из прошлого файла.
        rng = random.Random(f"{seed}:{code}")
        out.extend(items if len(items) <= per_category else rng.sample(items, per_category))
    return out


async def embed_all(
    llm: LLMClient, names: list[str], dim: int | None
) -> tuple[list[list[float] | None], int]:
    """Векторы по порядку; `None` — модель не справилась с наименованием."""
    out: list[list[float] | None] = [None] * len(names)
    failed = 0

    async def run(lo: int, hi: int) -> None:
        nonlocal failed
        try:
            vecs = await llm.embed(names[lo:hi])
        except LLMInputError:
            if hi - lo == 1:
                failed += 1
                return
            mid = (lo + hi) // 2
            await run(lo, mid)
            await run(mid, hi)
            return
        out[lo:hi] = vecs

    for i, lo in enumerate(range(0, len(names), BATCH)):
        await run(lo, min(lo + BATCH, len(names)))
        if i % 50 == 0:
            log.info("векторы: %d из %d", min(lo + BATCH, len(names)), len(names))
    return out, failed


async def main_async(args: argparse.Namespace) -> None:
    rows = [json.loads(x) for x in args.exemplars.open(encoding="utf-8") if x.strip()]
    chosen = sample(rows, args.per_category)
    names = [r["name"] for r in chosen]
    known: dict[str, np.ndarray] = {}
    if args.reuse and args.reuse.is_file():
        with np.load(args.reuse, allow_pickle=False) as data:
            if "names" in data:
                known = dict(zip((str(n) for n in data["names"]), data["vectors"], strict=True))
    todo = [n for n in dict.fromkeys(names) if n not in known]
    codes = len({r["code"] for r in chosen})
    log.info("наименований %d (категорий %d), из прошлого файла %d, к модели %d",
             len(names), codes, len(names) - len(todo), len(todo))  # fmt: skip
    if todo:
        llm = LLMClient.from_settings(Settings())
        vecs, failed = await embed_all(llm, todo, None)
        got = {
            n: np.asarray(v, dtype=np.float32)
            for n, v in zip(todo, vecs, strict=True)
            if v is not None
        }
        if not got and not known:
            raise SystemExit("модель не вернула ни одного вектора: EMBED_* в .env?")
        known.update(got)
        log.info("без вектора (NaN у модели): %d", failed)
    dim = next(iter(known.values())).shape[0]
    matrix = np.stack([known.get(n, np.zeros(dim, dtype=np.float32)) for n in names])
    index = ExemplarVectors([r["code"] for r in chosen], matrix)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(args.out, codes=np.array(index.codes), vectors=index.matrix, names=np.array(names))
    log.info("векторов %d × %d -> %s", *matrix.shape, args.out.resolve())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exemplars", type=Path, default=Path("../data/catalog/exemplars.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("../data/catalog/exemplars.vectors.npz"))
    ap.add_argument("--per-category", type=int, default=10)
    ap.add_argument("--reuse", type=Path, help="прошлый файл векторов: векторы по наименованию")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
