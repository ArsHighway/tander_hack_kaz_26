r"""Синтетические наименования СТЕ редких категорий: пакеты для генерации и проверка результата.

    uv run python -m app.tools.synthetic_exemplars batches \
        --categories "../data/organizers/Казань 2026"
    # пакеты ../data/synthetic/input/batch_NN.json → наименования (агенты по
    #   docs/synthetic-prompt.md) → ../data/synthetic/raw/<код>.jsonl
    uv run python -m app.tools.synthetic_exemplars merge \
        --base ../data/catalog/exemplars.jsonl --out ../data/catalog/exemplars_syn.jsonl

Редкая категория — меньше `--min` живых СТЕ в индексе похожих (`build_exemplars`): голос соседей
там слабый (верная категория первой у 52 % СТЕ категорий с < 10 примерами против 79 % у 50+). В
пакет идут название и ветка категории, её характеристики со значениями, до 8 живых наименований и
соседние категории той же ветки — их товары генерировать нельзя.

`merge` отбрасывает строку: не JSON, код не из пакетов, без кириллицы, короче 2 слов или длиннее 30,
равна названию категории, повтор внутри категории, повтор живого наименования или СТЕ отложенной
выборки (`--holdout`). Квота — 40 наименований в стиле портала и 10 запросов «как пишет менеджер» на
категорию. Синтетика дописывается в копию индекса похожих без `id`: голосует, но в примерах у
варианта категории не показывается.
"""

import argparse
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from python_calamine import CalamineWorkbook

KPGZ = re.compile(r"^\d{2}(\.\d{2})+$")
WORD = re.compile(r"[a-zа-яё0-9]+", re.I)
QUOTA = {"portal": 40, "query": 10}


def key(text: str) -> str:
    return " ".join(WORD.findall(text.lower().replace("ё", "е")))


def category_codes(path: Path) -> set[str]:
    """Коды КПГЗ: лист 2 выгрузок организаторов (папка с xlsx) или список (код в строке)."""
    if path.is_dir():
        codes: set[str] = set()
        for f in sorted(path.rglob("*.xlsx")):
            sheet = CalamineWorkbook.from_path(str(f)).get_sheet_by_index(1).to_python()
            head = [str(h) for h in sheet[0]]
            if "Код КПГЗ" not in head:
                continue
            col = head.index("Код КПГЗ")
            codes |= {str(r[col]).strip() for r in sheet[1:] if KPGZ.match(str(r[col]).strip())}
        return codes
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def batches(args: argparse.Namespace) -> None:
    positions = json.loads(args.catalog.read_text(encoding="utf-8"))["positions"]
    std = {p.get("kpgz") or p["code"]: p for p in positions}
    names: defaultdict[str, list[str]] = defaultdict(list)
    with args.exemplars.open(encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            names[r["code"]].append(r["name"])
    by_parent: defaultdict[tuple[str, ...], list[str]] = defaultdict(list)
    for p in std.values():
        by_parent[tuple(p.get("path", []))].append(p["name"])
    wanted = category_codes(args.categories) if args.categories else set(std)
    rare = sorted(k for k in wanted if k in std and len(names[k]) < args.min)
    rng = random.Random(11)
    items = []
    for k in rare:
        p = std[k]
        chars = [
            {"name": c["name"], "unit": c.get("unit"), "values": c.get("values", [])[:8]}
            for c in p["characteristics"]
            if c.get("group") != "general"
        ]
        examples = names[k][:]
        rng.shuffle(examples)
        path = tuple(p.get("path", []))
        items.append({
            "code": k, "category": p["name"], "path": list(path), "characteristics": chars[:14],
            "real_examples": examples[:8],
            "siblings": [n for n in by_parent[path] if n != p["name"]][:15],
            "have": len(names[k]),
        })  # fmt: skip
    (args.root / "input").mkdir(parents=True, exist_ok=True)
    (args.root / "raw").mkdir(parents=True, exist_ok=True)
    for i in range(0, len(items), args.size):
        out = args.root / "input" / f"batch_{i // args.size:02d}.json"
        out.write_text(json.dumps(items[i : i + args.size], ensure_ascii=False, indent=1), "utf-8")
    print(
        f"категорий {len(wanted)}, редких (< {args.min}) {len(items)}, "
        f"пакетов {-(-len(items) // args.size)}"
    )


def merge(args: argparse.Namespace) -> None:
    wanted = {
        it["code"]: it["category"]
        for f in sorted((args.root / "input").glob("*.json"))
        for it in json.loads(f.read_text(encoding="utf-8"))
    }
    if not wanted:
        raise SystemExit(f"{args.root / 'input'}: нет пакетов — сначала batches")
    holdout = set()
    if args.holdout and args.holdout.is_file():
        with args.holdout.open(encoding="utf-8") as fh:
            holdout = {key(json.loads(x)["name"]) for x in fh}
    base = args.base.read_text(encoding="utf-8")
    live = {key(json.loads(x)["name"]) for x in base.splitlines() if x.strip()}
    drop: Counter[str] = Counter()
    kept: list[dict] = []
    files = sorted((args.root / "raw").glob("*.jsonl"))
    for f in files:
        seen: set[str] = set()
        per: Counter[str] = Counter()
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                drop["не JSON"] += 1
                continue
            code, name = r.get("code"), str(r.get("name", "")).strip()
            kind = "query" if r.get("kind") == "query" else "portal"
            k = key(name)
            reason = (
                "код не из пакетов" if code not in wanted
                else "без кириллицы" if not re.search(r"[а-яё]", name, re.I)
                else "длина" if not 2 <= len(k.split()) <= 30
                else "= название категории" if k == key(wanted[code])
                else "повтор" if k in seen
                else "есть в отложенной выборке" if k in holdout
                else "есть среди живых" if k in live
                else "сверх квоты" if per[kind] >= QUOTA[kind]
                else None
            )  # fmt: skip
            if reason:
                drop[reason] += 1
                continue
            seen.add(k)
            per[kind] += 1
            kept.append({"code": code, "name": name, "kind": kind})
    if not kept:
        raise SystemExit(f"{args.root / 'raw'}: ни одной годной строки из {len(files)} файлов")
    (args.root / "synthetic.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept), encoding="utf-8"
    )
    rows = "".join(
        json.dumps({"code": r["code"], "name": r["name"]}, ensure_ascii=False) + "\n" for r in kept
    )
    args.out.write_text(base + ("" if base.endswith("\n") else "\n") + rows, encoding="utf-8")
    codes = Counter(r["code"] for r in kept)
    print(f"файлов {len(files)}, взято {len(kept)} строк в {len(codes)} категориях из "
          f"{len(wanted)}; меньше всего в категории {min(codes.values())}; отброшено "
          f"{dict(drop)} -> {args.out}")  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path("../data/synthetic"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("batches", help="пакеты редких категорий для генерации")
    b.add_argument("--catalog", type=Path, default=Path("../data/catalog/standards.json"))
    b.add_argument(
        "--exemplars", type=Path, default=Path("../data/catalog/eval/exemplars_new.jsonl")
    )
    b.add_argument(
        "--categories", type=Path, help="папка выгрузок организаторов (лист 2) или список кодов"
    )
    b.add_argument("--min", type=int, default=50)
    b.add_argument("--size", type=int, default=10)
    m = sub.add_parser("merge", help="проверка синтетики и индекс похожих с ней")
    m.add_argument("--base", type=Path, default=Path("../data/catalog/exemplars.jsonl"))
    m.add_argument("--out", type=Path, default=Path("../data/catalog/exemplars_syn.jsonl"))
    m.add_argument("--holdout", type=Path, default=Path("../data/holdout_full/holdout.jsonl"))
    args = ap.parse_args()
    batches(args) if args.cmd == "batches" else merge(args)


if __name__ == "__main__":
    main()
