"""Прогрев кэша картинок портала для демо: по каждому тексту — картинки вариантов категории.

    uv run python -m app.tools.warm_images --texts ../docs/demo-texts.txt

Для каждой строки файла — кандидаты категории (BM25 и похожие СТЕ, без LLM и векторов), как на
шаге 2 мастера, и в кэш (`--cache`, тот же каталог, что у api: `IMAGE_CACHE_DIR`) скачиваются
картинка каждой категории и фото её похожих СТЕ. В день показа портал может не отвечать: мастер
возьмёт картинки с диска.
"""

import argparse
import asyncio
import logging
from pathlib import Path

from app.catalog import Catalog
from app.portal.images import ImageCache, portal_client
from app.search import Candidate, build_bm25, find_candidates
from app.search.exemplars import ExemplarIndex

log = logging.getLogger(__name__)


def image_jobs(candidates: list[Candidate], catalog: Catalog) -> tuple[list[int], list[int]]:
    """Что скачать: id фото похожих СТЕ и id картинок категорий, без повторов, по порядку."""
    skus = dict.fromkeys(e.id for c in candidates for e in c.examples)
    images = dict.fromkeys(p.image_id for c in candidates if (p := catalog.get(c.code)) is not None)
    return list(skus), [i for i in images if i is not None]


async def warm(
    texts: list[str], catalog: Catalog, exemplars: ExemplarIndex, cache: ImageCache
) -> tuple[int, int]:
    bm25 = build_bm25(catalog)
    got = missing = 0
    for text in texts:
        found, _ = await find_candidates(text, catalog, bm25, None, None, exemplars=exemplars)
        skus, images = image_jobs(found, catalog)
        for sku in skus:
            ok = await cache.sku(sku) is not None
            got, missing = got + ok, missing + (not ok)
        for image in images:
            ok = await cache.category(image) is not None
            got, missing = got + ok, missing + (not ok)
        log.info("%s: категорий %d, похожих СТЕ %d", text, len(images), len(skus))
    return got, missing


async def _main(args: argparse.Namespace) -> None:
    texts = [t.strip() for t in args.texts.read_text(encoding="utf-8").splitlines() if t.strip()]
    if not texts:
        raise SystemExit(f"{args.texts}: ни одного текста")
    catalog = Catalog.load([args.catalog])
    exemplars = ExemplarIndex.load(args.exemplars)
    async with portal_client(args.timeout) as client:
        got, missing = await warm(
            texts, catalog, exemplars, ImageCache(args.cache, client, timeout_s=args.timeout)
        )
    log.info(
        "картинок в кэше: %d, нет на портале или не ответил: %d -> %s",
        got,
        missing,
        args.cache.resolve(),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--texts", type=Path, required=True, help="файл: текст товара в каждой строке")
    ap.add_argument("--catalog", type=Path, default=Path("../data/catalog/standards.json"))
    ap.add_argument("--exemplars", type=Path, default=Path("../data/catalog/exemplars.jsonl"))
    ap.add_argument("--cache", type=Path, default=Path("../data/cache/images"))
    ap.add_argument("--timeout", type=float, default=10.0, help="сколько ждать портал, с")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
