"""Проверка синтетики: что `merge` отбрасывает и что дописывает в индекс похожих."""

import argparse
import json
from pathlib import Path

import pytest

from app.tools.synthetic_exemplars import merge


def run(tmp_path: Path, lines: list[dict | str]) -> tuple[list[dict], list[str]]:
    (tmp_path / "input").mkdir()
    (tmp_path / "raw").mkdir()
    (tmp_path / "input" / "batch_00.json").write_text(
        json.dumps([{"code": "01.01", "category": "Пальто для взрослых из кожи зимние"}]), "utf-8"
    )
    raw = "\n".join(x if isinstance(x, str) else json.dumps(x, ensure_ascii=False) for x in lines)
    (tmp_path / "raw" / "01.01.jsonl").write_text(raw, "utf-8")
    base = tmp_path / "exemplars.jsonl"
    base.write_text(
        json.dumps({"code": "01.02", "name": "Куртка мужская зимняя Арата"}) + "\n", "utf-8"
    )
    holdout = tmp_path / "holdout.jsonl"
    holdout.write_text(json.dumps({"name": "Пальто кожаное зимнее женское, р. 48"}) + "\n", "utf-8")
    out = tmp_path / "out.jsonl"
    merge(argparse.Namespace(root=tmp_path, base=base, out=out, holdout=holdout))
    kept = [json.loads(x) for x in (tmp_path / "synthetic.jsonl").read_text("utf-8").splitlines()]
    return kept, out.read_text("utf-8").splitlines()


def test_bad_rows_are_dropped_and_good_ones_appended(tmp_path: Path) -> None:
    good = {"code": "01.01", "name": "Пальто женское зимнее из натуральной кожи, р. 50, чёрное"}
    kept, index = run(tmp_path, [
        good,
        {**good, "name": good["name"].upper()},  # повтор с точностью до регистра
        {"code": "01.01", "name": "Пальто для взрослых из кожи зимние"},  # = название категории
        {"code": "01.01", "name": "Пальто кожаное зимнее женское, р. 48"},  # из отложенной выборки
        {"code": "01.01", "name": "Куртка мужская зимняя Арата"},  # уже есть среди живых
        {"code": "01.01", "name": "VALBERG ASM-30"},  # без кириллицы
        {"code": "01.99", "name": "Пальто кожаное зимнее мужское"},  # код не из пакетов
        {"code": "01.01", "name": "пальто кожаное зимнее", "kind": "query"},
        "не json",
    ])  # fmt: skip
    assert [(r["name"], r["kind"]) for r in kept] == [
        (good["name"], "portal"), ("пальто кожаное зимнее", "query")
    ]  # fmt: skip
    assert len(index) == 3 and json.loads(index[1]) == {"code": "01.01", "name": good["name"]}


def test_quota_per_kind(tmp_path: Path) -> None:
    rows = [{"code": "01.01", "name": f"Пальто кожаное зимнее модель {i}"} for i in range(45)]
    kept, _ = run(tmp_path, rows)
    assert len(kept) == 40


def test_nothing_usable_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="ни одной годной строки"):
        run(tmp_path, ["не json"])
