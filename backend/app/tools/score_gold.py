"""Прогон `run_descriptions` против эталона разметки — без людей и агентов.

    uv run python -m app.tools.score_gold ../data/qa/gold/hard_gold.jsonl \
        ../data/qa/runs/hard/runs.0.jsonl [--variant base] [--out score.json]

Эталон — строка на описание (`id`): тип (`type_code`, `type_alt`), позиция КТРУ (`ktru_code`,
`ktru_alt`, null — в КТРУ такого товара нет), значения из текста (`values`: field, value, unit,
quote), прочие факты (`facts_other`), что модуль должен пометить (`flags`). Значения эталона —
только то, что текст говорит прямо.

Счёт:
- `type_ok`, `ktru_ok` — доля описаний с верным типом и позицией (null в эталоне — кода нет);
- `value_recall` — доля значений эталона, которые модуль поставил в поле из текста (rule, llm)
  тем же значением; `kept` — доля значений и фактов эталона, которые не потерялись: значение в
  поле, в extra или фрагмент поля пересекает цитату;
- `unsupported` — значения модуля из текста (rule, llm), которых в эталоне нет ни значением, ни
  цитатой: кандидаты в «выдумано»; подсказки (similar, twin) — отдельно, `hints`;
- `flags_caught` — доля пометок эталона, на которые у модуля есть warn или block по тем же словам.
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from app.values import convert, fold, parse_bucket

_WORD = re.compile(r"[\w.,]+")
_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


def _norm(s: Any) -> str:
    return " ".join(_WORD.findall(fold(str(s or "")).replace("ё", "е")))


def _nums(s: Any) -> set[float]:
    return {float(x.replace(",", ".")) for x in _NUM.findall(str(s or ""))}


_COUNTRY = {"рф": "россия", "кнр": "китай", "prc": "китай", "russia": "россия", "china": "китай"}


def _canon(s: Any) -> str:
    n = _norm(s)
    return _COUNTRY.get(n, n)


def _number_in(a: Any, b: Any, unit_a: str | None, unit_b: str | None) -> bool:
    """Число одной стороны попадает в корзину КТРУ другой («270» и «>=250 ... <300») или равно
    ей после пересчёта единиц («42,4 см» и «424 мм»)."""
    for x, y, ux, uy in ((a, b, unit_a, unit_b), (b, a, unit_b, unit_a)):
        nums = _nums(x)
        if len(nums) != 1:
            continue
        n = next(iter(nums))
        if ux and uy and ux != uy and (c := convert(n, ux, uy)) is not None:
            n = c
        bucket = parse_bucket(str(y or ""))
        if bucket is not None and bucket.contains(n):
            return True
        other = _nums(y)
        if len(other) == 1 and abs(next(iter(other)) - n) < 1e-6 * max(1.0, abs(n)):
            return True
    return False


def same(a: Any, b: Any, unit_a: str | None = None, unit_b: str | None = None) -> bool:
    """Одно значение: та же запись после `fold`, синоним страны, то же слово в составе, число в
    корзине КТРУ или то же число после пересчёта единиц."""
    na, nb = _canon(a), _canon(b)
    if not na or not nb:
        return False
    if na == nb or na in nb.split(" ") or nb in na.split(" "):
        return True
    return _number_in(a, b, unit_a, unit_b)


def overlaps(fragment: str | None, quote: str) -> bool:
    f, q = _norm(fragment), _norm(quote)
    return bool(f) and bool(q) and (f in q or q in f)


def _key_words(text: str) -> set[str]:
    return {w[:5] for w in _norm(text).split() if len(w) >= 4 and not _NUM.fullmatch(w)}


def score_case(gold: dict[str, Any], run: dict[str, Any]) -> Counter[str]:
    c: Counter[str] = Counter()
    f = run["final"]
    c["cases"] += 1
    type_codes = {gold.get("type_code"), *(gold.get("type_alt") or [])} - {None}
    got_type = f["category"][0] if f["category"] else None
    if gold.get("is_goods", True):
        c["type_total"] += 1
        c["type_ok"] += bool(got_type and got_type in type_codes)
        if not gold.get("ktru_unknown"):  # позицию КТРУ разметчик не выбрал — не считаем
            c["ktru_total"] += 1
            want = {gold.get("ktru_code"), *(gold.get("ktru_alt") or [])} - {None}
            c["ktru_ok"] += (f["ktru_code"] in want) if want else (f["ktru_code"] is None)
    else:
        c["notgoods_total"] += 1
        c["notgoods_ok"] += got_type is None
    filled = [x for x in f["filled"] if x["method"] in ("rule", "llm")]
    hints = [x for x in f["filled"] if x["method"] in ("similar", "twin")]
    extra = [f"{a} {b}" for a, b in f["extra"]]
    used: set[int] = set()
    for v in gold.get("values") or []:
        c["values"] += 1
        hit = next(
            (
                i
                for i, x in enumerate(filled)
                if same(x["value"], v["value"], x.get("unit"), v.get("unit"))
                and (
                    overlaps(x["from"], v["quote"])
                    or _key_words(x["name"]) & _key_words(v["field"])
                )
            ),
            None,
        )
        if hit is not None:
            c["value_hit"] += 1
            used.add(hit)
        kept = (
            hit is not None
            or any(overlaps(x["from"], v["quote"]) for x in filled)
            or any(same(e, v["value"]) or overlaps(e, v["quote"]) for e in extra)
        )
        c["kept"] += kept
        c["kept_total"] += 1
    for fact in gold.get("facts_other") or []:
        c["kept_total"] += 1
        q = fact.get("quote") or fact.get("text") or ""
        c["kept"] += any(overlaps(x["from"], q) for x in filled) or any(
            overlaps(e, q) for e in extra
        )
    quotes = [v["quote"] for v in gold.get("values") or []] + [
        x.get("quote") or "" for x in gold.get("facts_other") or []
    ]
    gold_values = [v["value"] for v in gold.get("values") or []]
    for i, x in enumerate(filled):
        c["filled"] += 1
        if i in used:
            continue
        supported = any(same(x["value"], g) for g in gold_values) or any(
            overlaps(x["from"], q) for q in quotes
        )
        c["unsupported"] += not supported
    c["hints"] += len(hints)
    issues = [i for i in f["issues"] if i[0] in ("warn", "block")]
    for flag in gold.get("flags") or []:
        if flag.get("kind") in ("noise", "typo"):
            continue
        c["flags"] += 1
        words = _key_words(flag.get("quote") or "")
        c["flags_caught"] += any(words & _key_words(f"{i[1]} {i[2]}") for i in issues)
    c["over_60s"] += run["s_total"] > 60
    c["unread"] += len(f.get("unread") or [])
    return c


def summary(c: Counter[str], times: list[float]) -> dict[str, Any]:
    r = lambda a, b: round(c[a] / c[b], 4) if c[b] else None  # noqa: E731
    times = sorted(times)
    return {
        "cases": c["cases"],
        "type_ok": r("type_ok", "type_total"),
        "ktru_ok": r("ktru_ok", "ktru_total"),
        "notgoods_ok": r("notgoods_ok", "notgoods_total"),
        "value_recall": r("value_hit", "values"),
        "kept": r("kept", "kept_total"),
        "unsupported_share": r("unsupported", "filled"),
        "unsupported": c["unsupported"],
        "hints": c["hints"],
        "flags_caught": r("flags_caught", "flags"),
        "unread_per_case": round(c["unread"] / c["cases"], 2) if c["cases"] else None,
        "s_p50": times[len(times) // 2] if times else None,
        "s_p95": times[int(len(times) * 0.95)] if times else None,
        "over_60s": c["over_60s"],
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("gold", type=Path)
    p.add_argument("runs", type=Path, nargs="+")
    p.add_argument("--variant", default=None)
    p.add_argument("--out", type=Path)
    args = p.parse_args()
    gold = {
        g["id"]: g for g in map(json.loads, args.gold.read_text(encoding="utf-8").splitlines()) if g
    }
    runs = [
        json.loads(line) for f in args.runs for line in f.open(encoding="utf-8") if line.strip()
    ]
    by_variant: dict[str, tuple[Counter[str], list[float], dict[str, Counter[str]]]] = {}
    for run in runs:
        if "crash" in run or run["id"] not in gold:
            continue
        if args.variant and run["variant"] != args.variant:
            continue
        total, times, topics = by_variant.setdefault(run["variant"], (Counter(), [], {}))
        c = score_case(gold[run["id"]], run)
        total.update(c)
        topics.setdefault(run["topic"], Counter()).update(c)
        times.append(run["s_total"])
    report = {
        v: {"all": summary(t, times), **{k: summary(tc, []) for k, tc in topics.items()}}
        for v, (t, times, topics) in by_variant.items()
    }
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
