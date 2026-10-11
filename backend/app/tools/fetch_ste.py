"""Выгрузка СТЕ с Портала поставщиков по всему дереву категорий товаров.

    uv run python -m app.tools.fetch_ste --out ../data/ste --per-category 100
    uv run python -m app.tools.fetch_ste --out ../data/ste_full --per-category 0 --exclude ""

`--per-category 0` — все СТЕ категории: страницы по 1000 через `skip` (глубина листания не
ограничена, проверено на 72 тыс. СТЕ) с `doNotCollapse`, иначе поиск склеивает варианты товара.
В записи полной выгрузки сверх формата `app.portal.cards` есть код КПГЗ (`kpgz`; старое поле
`okpd` на деле тоже КПГЗ), id категории и ОКПД2, модель, цена, страна и у каждой характеристики id
характеристики, значения и типа — по ним СТЕ связывается со стандартом категории
(`fetch_standards`).

Дерево — `GET /classifier/api/ProductionDirectory/CategoryList?parentId=1` (ветка «Товары», тот же
запрос, что у фильтра каталога), карточки — публичный поиск каталога `/newapi/api/Sku/QueryIndex` с
фильтром по пути категории: по одному запросу на конечную категорию, в выдаче уже есть описание и
характеристики. Так набор покрывает все ~2 тыс. категорий, а не только найденные по словам, как
`build_testset`. СТЕ, которые уже есть в `--exclude` (по умолчанию `data/testset/raw.jsonl`), не
пишутся: выгрузка — новые данные поверх старых.

Пишет `ste.jsonl` (формат `app.portal.cards`, как `data/testset/raw.jsonl`), `ste.csv` (колонки
как у пермской выгрузки СТЕ: характеристики строкой «имя:значение;…» — годится для `recon` и
массовой загрузки), `categories.json` (дерево с числом СТЕ в категории). Прерванная выгрузка
продолжается с места остановки (`done.txt`). Данные портала в git не идут.
"""

import argparse
import csv
import json
import logging
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx

from app.tools.build_testset import USER_AGENT, to_record

log = logging.getLogger(__name__)

TREE_URL = "https://zakupki.mos.ru/classifier/api/ProductionDirectory/CategoryList"
SEARCH_URL = "https://zakupki.mos.ru/newapi/api/Sku/QueryIndex"
GOODS_ID = 1
CSV_COLUMNS = [
    "Идентификатор СТЕ",
    "Наименование СТЕ",
    "Описание",
    "Категория",
    "Путь категории",
    "Производитель",
    "ОКПД2",
    "Единица измерения",
    "Характеристики",
]


def _children(client: httpx.Client, parent: int) -> list[dict[str, Any]]:
    for attempt in range(4):
        try:
            r = client.get(TREE_URL, params={"parentId": parent, "withSkuCount": "true"})
            if r.status_code == httpx.codes.OK and r.content:
                return r.json() or []
            log.warning("дерево %s: HTTP %s", parent, r.status_code)
        except (httpx.HTTPError, ValueError) as e:
            log.warning("дерево %s: %s", parent, e)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"ветка дерева {parent} не скачана за 4 попытки")


def load_tree(client: httpx.Client, root: int, pause: float = 0.3) -> list[dict[str, Any]]:
    """Дерево ветки целиком. CategoryList отдаёт его не на всю глубину: у части нелистовых узлов
    пустой `subCategories` (в «Товарах» 443 таких, за ними ~2 млн СТЕ — бумага для офисной
    техники, медицинские перчатки). Такие узлы догружаются своим запросом, пока не кончатся."""
    tree = _children(client, root)
    stack = list(tree)
    while stack:
        node = stack.pop()
        subs = node.get("subCategories") or []
        if not node.get("isLastLevel") and not subs:
            time.sleep(pause)
            subs = node["subCategories"] = _children(client, node["id"])
        stack.extend(subs)
    return tree


def leaves(tree: list[dict[str, Any]], root: str | None = None) -> Iterator[dict[str, Any]]:
    """Конечные категории дерева с корневым разделом: по ним и идёт выгрузка."""
    for node in tree or []:
        top = root or node["name"]
        if node.get("isLastLevel"):
            yield {"path": node["treePathId"], "name": node["name"], "root": top}
        elif not node.get("subCategories"):
            # Детей портал не отдал и на дозапрос («Бумага для канцелярских нужд»), но поиск по пути
            # ветки отдаёт её СТЕ, каждую со своей конечной категорией (`directory_id`).
            yield {"path": node["treePathId"], "name": node["name"], "root": top, "branch": True}
        yield from leaves(node.get("subCategories") or [], top)


def _num(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def chars_line(chars: list[dict[str, Any]]) -> str:
    """Характеристики строкой пермской выгрузки: «Формат:A4;Масса бумаги:80 г/м2»."""
    parts = []
    for c in chars:
        if not c.get("name") or c.get("value") in (None, ""):
            continue
        value = _num(c["value"]) + (f" {c['unit']}" if c.get("unit") else "")
        parts.append(f"{c['name']}:{value}".replace(";", ","))
    return ";".join(parts)


def csv_row(rec: dict[str, Any]) -> list[str]:
    return [
        str(rec["id"]),
        rec.get("name") or "",
        rec.get("description") or "",
        rec.get("category") or "",
        rec.get("category_path") or "",
        rec.get("manufacturer") or "",
        rec.get("okpd") or "",
        rec.get("unit") or "",
        chars_line(rec.get("characteristics") or []),
    ]


PAGE = 1000


def pages(count: int, limit: int | None, take: int = PAGE) -> list[tuple[int, int]]:
    """(skip, take) запросов, покрывающих категорию из `count` СТЕ, но не больше `limit`."""
    total = min(count, limit) if limit else count
    return [(skip, min(take, total - skip)) for skip in range(0, total, take)]


def fetch_page(
    client: httpx.Client, path: str, skip: int, take: int
) -> tuple[list[dict], int | None]:
    query = {
        "take": take,
        "skip": skip,
        "withCount": True,
        "filter": {"productionDirectoryPaths": {"values": [path]}},
    }
    for attempt in range(4):
        try:
            # Без doNotCollapse поиск склеивает варианты товара: в выдаче 5753 СТЕ из 9211.
            params = {"queryFilter": json.dumps(query), "doNotCollapse": "true"}
            r = client.get(SEARCH_URL, params=params)
            if r.status_code == httpx.codes.OK:
                data = r.json()
                return data.get("items") or [], data.get("count")
            log.warning("%s skip=%d: HTTP %s", path, skip, r.status_code)
        except (httpx.HTTPError, ValueError) as e:
            log.warning("%s skip=%d: %s", path, skip, e)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"категория {path} (skip={skip}) не скачана за 4 попытки")


def fetch_category(
    client: httpx.Client, path: str, limit: int | None, pause: float
) -> Iterator[tuple[list[dict], int | None]]:
    """Страницы категории: первая узнаёт число СТЕ, остальные идут по `pages`."""
    items, count = fetch_page(client, path, 0, min(limit or PAGE, PAGE))
    yield items, count
    for skip, take in pages(count or 0, limit)[1:]:
        time.sleep(pause)
        yield fetch_page(client, path, skip, take)[0], count


def full_record(item: dict, root: str) -> dict:
    """Запись полной выгрузки: `to_record` плюс коды, цена и id характеристик."""
    rec = to_record(item, root)
    for ch, row in zip(rec["characteristics"], item.get("skuCharacteristics") or [], strict=True):
        meta = row.get("productCharacteristicValue") or {}
        ch["char_id"] = meta.get("productCharacteristicId")
        ch["value_id"] = row.get("productCharacteristicValueId")
        ch["type"] = meta.get("productCharacteristicDescriptionTypeId")
    rec.update(
        kpgz=item.get("productionCode") or "",
        directory_id=item.get("productionDirectoryId"),
        okpd_id=item.get("okpdId"),
        model=item.get("modelName") or "",
        price=item.get("referencePrice") or item.get("medianPrice"),
        country=item.get("oksmName") or "",
    )
    return rec


def done_paths(files: list[Path]) -> set[str]:
    """Пути категорий, выгруженные другими процессами (`--also-done`): два процесса идут по дереву
    навстречу (`--reverse`) и перед каждой категорией читают свежий список соседа."""
    out: set[str] = set()
    for f in files:
        if f.is_file():
            out |= set(f.read_text(encoding="utf-8").split())
    return out


def _ids(path: Path) -> set[int]:
    if not path.is_file():
        return set()
    with path.open(encoding="utf-8") as fh:
        return {json.loads(line)["id"] for line in fh if line.strip()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("../data/ste"))
    ap.add_argument("--per-category", type=int, default=100, help="СТЕ на категорию, 0 — все")
    ap.add_argument("--pause", type=float, default=0.6, help="пауза между запросами, с")
    ap.add_argument("--exclude", type=Path, default=Path("../data/testset/raw.jsonl"))
    ap.add_argument("--limit", type=int, help="только первые N категорий (проверка)")
    ap.add_argument(
        "--root", type=int, default=GOODS_ID, help="ветка дерева: 1 товары, 2 работы, 3 услуги"
    )
    ap.add_argument("--reverse", action="store_true", help="категории с конца: второй процесс")
    ap.add_argument(
        "--also-done",
        type=Path,
        action="append",
        default=[],
        help="done.txt параллельного процесса: его категории пропускаются",
    )
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args.out.mkdir(parents=True, exist_ok=True)
    jsonl, done_path = args.out / "ste.jsonl", args.out / "done.txt"

    excluded = _ids(args.exclude)
    seen = _ids(jsonl) | excluded
    done = set(done_path.read_text(encoding="utf-8").split()) if done_path.exists() else set()
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120) as client:
        tree = load_tree(client, args.root)
        cats = list(leaves(tree))[: args.limit]
        if not cats:
            raise SystemExit("дерево категорий пустое: проверьте адрес CategoryList")
        log.info("категорий: %d, уже выгружено: %d, исключено СТЕ: %d", len(cats), len(done),
                 len(excluded))  # fmt: skip
        counts: dict[str, int | None] = {}
        short: list[tuple[str, int, int]] = []
        full = args.per_category == 0
        convert = full_record if full else to_record
        with jsonl.open("a", encoding="utf-8") as out, done_path.open("a", encoding="utf-8") as dn:
            order = list(enumerate(cats, 1))
            for i, cat in reversed(order) if args.reverse else order:
                if cat["path"] in done or cat["path"] in done_paths(args.also_done):
                    continue
                new = got = 0
                total = None
                for items, count in fetch_category(client, cat["path"], args.per_category or None,
                                                   args.pause):  # fmt: skip
                    total = count
                    got += len(items)
                    for item in items:
                        if item["id"] in seen:
                            continue
                        seen.add(item["id"])
                        rec = convert(item, cat["root"])
                        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        new += 1
                counts[cat["path"]] = total
                if full and total and got < total:
                    short.append((cat["path"], got, total))
                dn.write(cat["path"] + "\n")
                out.flush()
                dn.flush()
                if i % 50 == 0 or i == len(cats) or got >= PAGE:
                    log.info("[%d/%d] %s: +%d (в категории %s)", i, len(cats), cat["name"], new,
                             total)  # fmt: skip
                time.sleep(args.pause)

    if short:
        # Не сдвиг листания: выдача стабильна при take 500 и 1000, а `count` считает и СТЕ, которых
        # поиск не отдаёт (4041 из 4122 в «.1.72092338.793371200.»).
        lost = sum(t - g for _, g, t in short)
        log.warning("поиск отдал меньше, чем count, в %d категориях, без %d СТЕ: %s", len(short),
                    lost, short[:20])  # fmt: skip
    for cat in cats:
        cat["sku_count"] = counts.get(cat["path"])
    (args.out / "categories.json").write_text(
        json.dumps({"tree": tree, "leaves": cats}, ensure_ascii=False), encoding="utf-8"
    )
    n = with_chars = 0
    paths: set[str] = set()
    with (
        jsonl.open(encoding="utf-8") as src,
        (args.out / "ste.csv").open("w", encoding="utf-8-sig", newline="") as fh,
    ):
        w = csv.writer(fh, delimiter=";")
        w.writerow(CSV_COLUMNS)
        for line in src:
            if not line.strip():
                continue
            rec = json.loads(line)
            w.writerow(csv_row(rec))
            n += 1
            with_chars += bool(rec["characteristics"])
            paths.add(rec["category_path"])
    if not n:
        raise SystemExit("ни одной новой СТЕ: проверьте адрес поиска и --exclude")
    log.info("готово: %d новых СТЕ (%d с характеристиками) в %d категориях -> %s", n,
             with_chars, len(paths), args.out.resolve())  # fmt: skip


if __name__ == "__main__":
    main()
