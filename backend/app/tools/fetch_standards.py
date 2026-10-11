"""Выгрузка стандартов категорий товаров Портала поставщиков: шаблон характеристик и коды.

    uv run python -m app.tools.fetch_standards --out ../data/standards

По каждой конечной категории ветки «Товары» (то же дерево, что у `fetch_ste`) два открытых запроса:

- `ProductionDirectory/GetEntityByEntityId` — к чему привязана категория: позиция КПГЗ (код, имя,
  путь) и ОКПД2;
- `ProductCharacteristicValue/QueryDirectoryCharacteristicValues` — шаблон СТЕ категории: каждая
  характеристика со своим типом (справочник, текст, число), единицей, диапазоном, флагами
  `isObligatory` / `isUseInOfferName` / `isLevelDefine`, синонимами, базовой характеристикой и
  ссылками на СПГЗ и КТРУ (`spgzValues`, `ktruValues`). Строк на характеристику бывает несколько:
  у каждого допустимого значения своя. Значения справочников приходят id, их расшифровка закрыта
  (401) — видимые значения берутся из самих СТЕ (`fetch_ste`).

Пишет `standards.jsonl`: строка на категорию `{id, path, name, root, entity, items}`, поля без
значений отброшены (`False` и `0` остаются: «необязательна» — тоже ответ). Прерванная выгрузка
продолжается с места остановки. Данные портала в git не идут.

    uv run python -m app.tools.fetch_standards --entity-only

Только привязки уже выгруженных категорий (`entities.jsonl`: `{id, image_id}`) — картинка
категории для предпросмотра карточки; ~2 тыс. запросов, тоже продолжается с места остановки.
"""

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from app.tools.build_testset import USER_AGENT
from app.tools.fetch_ste import GOODS_ID, leaves, load_tree

log = logging.getLogger(__name__)

API = "https://zakupki.mos.ru/classifier/api/"
ENTITY_URL = API + "ProductionDirectory/GetEntityByEntityId"
TEMPLATE_URL = API + "ProductCharacteristicValue/QueryDirectoryCharacteristicValues"


def compact(value: Any) -> Any:
    """Без `None`, пустых строк, списков и словарей — на любой глубине; `False` и `0` остаются."""
    if isinstance(value, dict):
        out = {k: compact(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        return [v for v in (compact(x) for x in value) if v not in (None, "", [], {})]
    return value


def ste_leaves(
    ste: Path, known: set[str], branches: frozenset[str] | set[str] = frozenset()
) -> list[dict[str, Any]]:
    """Конечные категории, которые видны только по СТЕ: ветки, чьих детей портал не отдаёт,
    выгружены поиском по пути (`fetch_ste`), и у каждой СТЕ — путь своей категории. СТЕ на самом
    узле-ветке категорию не задаёт; оборванная последняя строка (выгрузка ещё пишет) —
    пропускается."""
    found: dict[str, dict[str, Any]] = {}
    with ste.open(encoding="utf-8") as fh:
        for line in fh:
            if '"category_path"' not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                log.warning("%s: оборванная строка — выгрузка СТЕ, возможно, не закончена", ste)
                continue
            path = row.get("category_path")
            if path and path not in known and path not in branches and path not in found:
                found[path] = {"path": path, "name": row["category"], "root": row["root"]}
    return list(found.values())


def in_shard(i: int, shard: str | None) -> bool:
    """Категория номер `i` (с 1) в доле «K/N»: процессы с долями 0/N…N-1/N делят выгрузку без
    пересечений и пишут каждый в свою папку."""
    if not shard:
        return True
    k, n = (int(x) for x in shard.split("/"))
    return (i - 1) % n == k


def leaf_id(path: str) -> int:
    """id категории — последний элемент пути дерева: «.1.72095647.793362891.» → 793362891."""
    return int(path.strip(".").split(".")[-1])


def entity_summary(raw: dict[str, Any]) -> dict[str, Any]:
    """Привязка категории: позиция КПГЗ и ОКПД2, разрешены ли наборы, варианты, СТЕ без модели."""
    production = raw.get("production") or {}
    okpd = production.get("okpd") or {}
    return {
        "kpgz": {
            "id": production.get("id"),
            "code": production.get("code"),
            "name": production.get("name"),
            "path": production.get("treePath"),
        }
        if production
        else None,
        "okpd2": {"id": okpd.get("id"), "code": okpd.get("code"), "name": okpd.get("name")}
        if okpd
        else None,
        "allow_sets": raw.get("allowSets"),
        "allow_variants": raw.get("allowSkuVariants"),
        "model_optional": raw.get("isModelOptional"),
        # Картинка категории: её портал показывает у СТЕ без своего фото (`Core/Thumbnail/<id>`).
        "image_id": (raw.get("fileStorage") or {}).get("id"),
    }


def _get(client: httpx.Client, url: str, params: dict[str, Any]) -> Any:
    for attempt in range(4):
        try:
            r = client.get(url, params=params)
            if r.status_code == httpx.codes.OK:
                return r.json()
            log.warning("%s %s: HTTP %s", url.rsplit("/", 1)[-1], params, r.status_code)
        except (httpx.HTTPError, ValueError) as e:
            log.warning("%s %s: %s", url.rsplit("/", 1)[-1], params, e)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{url} {params} не скачан за 4 попытки")


def _rows(path: Path) -> list[dict[str, Any]]:
    """Строки выгрузки; оборванная при остановке последняя строка — не выгружена."""
    if not path.is_file():
        return []
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and "id" in row:
                rows.append(row)
    return rows


def _done(path: Path) -> set[int]:
    return {r["id"] for r in _rows(path)}


def fetch_entities(client: httpx.Client, ids: list[int], path: Path, pause: float) -> int:
    """Привязки категорий заново (`--entity-only`): строка `{id, image_id}` на категорию в
    `entities.jsonl`. Уже выгруженные id пропускаются: прерванный проход продолжается."""
    done = _done(path)
    written = 0
    with path.open("a", encoding="utf-8") as out:
        for cid in ids:
            if cid in done:
                continue
            try:
                raw = _get(client, ENTITY_URL, {"entityId": cid})
            except RuntimeError as e:
                log.warning("пропуск %s: %s", cid, e)
                continue
            image = entity_summary(raw or {})["image_id"]
            out.write(json.dumps({"id": cid, "image_id": image}) + "\n")
            out.flush()
            written += 1
            if written % 100 == 0:
                log.info("привязок: %d", written)
            time.sleep(pause)
    return written


def _entity_only(args: argparse.Namespace) -> None:
    jsonl = args.out / "standards.jsonl"
    with jsonl.open(encoding="utf-8") as fh:
        ids = [json.loads(line)["id"] for line in fh if line.strip()][: args.limit]
    if not ids:
        raise SystemExit(f"{jsonl}: ни одного стандарта, сначала полная выгрузка")
    path = args.out / "entities.jsonl"
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=180) as client:
        written = fetch_entities(client, ids, path, args.pause)
    rows = _rows(path)
    done = {r["id"] for r in rows}
    with_image = sum(1 for r in rows if r.get("image_id"))
    log.info("привязки: +%d, всего %d из %d, с картинкой категории %d -> %s",
             written, len(done), len(ids), with_image, path.resolve())  # fmt: skip
    missing = [i for i in ids if i not in done]
    if missing:
        # Частичный отказ — громко: перезапуск докачает только эти id.
        raise SystemExit(f"не скачано привязок: {len(missing)} (первые: {missing[:10]})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("../data/standards"))
    ap.add_argument("--pause", type=float, default=0.6, help="пауза между запросами, с")
    ap.add_argument("--limit", type=int, help="только первые N категорий (проверка)")
    ap.add_argument(
        "--ste",
        type=Path,
        default=Path("../data/ste_full/ste.jsonl"),
        help="полная выгрузка СТЕ: категории веток, которых нет в дереве",
    )
    ap.add_argument("--shard", help="доля «K/N»: только категории с номером ≡ K по модулю N")
    ap.add_argument(
        "--also-done",
        type=Path,
        action="append",
        default=[],
        help="standards.jsonl другого процесса: его категории не качаются",
    )
    ap.add_argument(
        "--entity-only",
        action="store_true",
        help="только привязки уже выгруженных категорий (картинка) -> entities.jsonl",
    )
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.entity_only:
        _entity_only(args)
        return
    jsonl = args.out / "standards.jsonl"
    done = _done(jsonl)
    for other in args.also_done:
        done |= _done(other)

    failed: list[str] = []
    written = 0
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=180) as client:
        tree = load_tree(client, GOODS_ID)
        found = list(leaves(tree))
        cats = [c for c in found if not c.get("branch")]
        if not cats:
            raise SystemExit("дерево категорий пустое: проверьте адрес CategoryList")
        if args.ste.is_file():
            branches = {c["path"] for c in found if c.get("branch")}
            extra = ste_leaves(args.ste, {c["path"] for c in cats}, branches)
            log.info("категорий только по СТЕ (ветки без детей в дереве): %d", len(extra))
            cats += extra
        cats = cats[: args.limit]
        (args.out / "tree.json").write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
        log.info("категорий: %d, уже выгружено: %d", len(cats), len(done))
        with jsonl.open("a", encoding="utf-8") as out:
            for i, cat in enumerate(cats, 1):
                cid = leaf_id(cat["path"])
                if cid in done or not in_shard(i, args.shard):
                    continue
                try:
                    entity = _get(client, ENTITY_URL, {"entityId": cid})
                    time.sleep(args.pause)
                    template = _get(client, TEMPLATE_URL, {"directoryId": cid, "isDraft": "false"})
                except RuntimeError as e:
                    log.warning("пропуск %s: %s", cat["name"], e)
                    failed.append(cat["path"])
                    continue
                items = compact((template or {}).get("items") or [])
                rec = {"id": cid, **cat, "entity": entity_summary(entity or {}), "items": items}
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                written += 1
                if i % 50 == 0 or i == len(cats):
                    log.info("[%d/%d] %s: %d строк шаблона", i, len(cats), cat["name"], len(items))
                time.sleep(args.pause)

    total = len(_done(jsonl))
    log.info("готово: +%d категорий, всего %d из %d -> %s", written, total, len(cats),
             jsonl.resolve())  # fmt: skip
    if failed:
        log.warning("не скачано %d категорий (перезапуск докачает): %s", len(failed), failed[:20])
    if total == 0:
        raise SystemExit("ни одного стандарта: проверьте адреса API")


if __name__ == "__main__":
    main()
