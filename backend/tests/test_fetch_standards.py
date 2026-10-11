import json
from pathlib import Path

import httpx
import pytest

from app.tools import fetch_standards
from app.tools.fetch_standards import compact, entity_summary, fetch_entities, leaf_id


def test_compact_drops_empty_but_keeps_false_and_zero() -> None:
    raw = {"a": None, "b": False, "c": [], "d": {"e": ""}, "f": 0, "g": [{"h": None, "i": 1}]}
    assert compact(raw) == {"b": False, "f": 0, "g": [{"i": 1}]}


def test_leaf_id_is_last_element_of_tree_path() -> None:
    assert leaf_id(".1.72095647.793362869.793362891.") == 793362891


def test_entity_summary_links_category_to_kpgz_and_okpd2() -> None:
    raw = {
        "entityId": 793362891,
        "allowSets": False,
        "allowSkuVariants": False,
        "isModelOptional": None,
        "production": {
            "id": 12125290,
            "code": "01.11.01.04.05.01",
            "name": "СМЕСИТЕЛИ ВОДОРАЗБОРНЫЕ",
            "treePath": ".59347.66240.66241.66268.66273.",
            "okpd": {"id": 1128111, "code": "28.14.12.110", "name": "Краны, клапаны"},
        },
        "fileStorage": {"fileName": "смеситель.jpeg"},
    }
    assert entity_summary(raw) == {
        "kpgz": {
            "id": 12125290,
            "code": "01.11.01.04.05.01",
            "name": "СМЕСИТЕЛИ ВОДОРАЗБОРНЫЕ",
            "path": ".59347.66240.66241.66268.66273.",
        },
        "okpd2": {"id": 1128111, "code": "28.14.12.110", "name": "Краны, клапаны"},
        "allow_sets": False,
        "allow_variants": False,
        "model_optional": None,
        "image_id": None,
    }


def test_entity_summary_without_production() -> None:
    assert entity_summary({"entityId": 1})["kpgz"] is None


def test_entity_summary_reads_category_image() -> None:
    raw = {"entityId": 793362891, "fileStorage": {"id": 231277051, "fileName": "смеситель.jpeg"}}
    assert entity_summary(raw)["image_id"] == 231277051


def test_entity_summary_without_image() -> None:
    assert entity_summary({"entityId": 1, "fileStorage": None})["image_id"] is None


def _entities(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_fetch_entities_resumes_and_writes_image_ids(tmp_path: Path) -> None:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cid = request.url.params["entityId"]
        asked.append(cid)
        storage = {"id": 231277051} if cid == "2" else None
        return httpx.Response(200, json={"entityId": int(cid), "fileStorage": storage})

    path = tmp_path / "entities.jsonl"
    path.write_text(json.dumps({"id": 1, "image_id": None}) + chr(10), encoding="utf-8")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        written = fetch_entities(client, [1, 2, 3], path, pause=0)
    assert (written, asked) == (2, ["2", "3"])
    assert _entities(path) == [
        {"id": 1, "image_id": None},
        {"id": 2, "image_id": 231277051},
        {"id": 3, "image_id": None},
    ]


def test_failed_id_is_skipped_and_asked_again_next_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch_standards.time, "sleep", lambda s: None)
    down = {"2"}
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cid = request.url.params["entityId"]
        asked.append(cid)
        if cid in down:
            return httpx.Response(502)
        return httpx.Response(200, json={"entityId": int(cid), "fileStorage": None})

    path = tmp_path / "entities.jsonl"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert fetch_entities(client, [1, 2, 3], path, pause=0) == 2
        assert [r["id"] for r in _entities(path)] == [1, 3]
        down.clear()
        asked.clear()
        assert fetch_entities(client, [1, 2, 3], path, pause=0) == 1
    assert asked == ["2"]
    assert sorted(r["id"] for r in _entities(path)) == [1, 2, 3]


def test_cut_last_line_does_not_stop_resume(tmp_path: Path) -> None:
    path = tmp_path / "entities.jsonl"
    path.write_text(
        json.dumps({"id": 1, "image_id": None}) + chr(10) + '{"id": 5, "ima', encoding="utf-8"
    )
    assert fetch_standards._done(path) == {1}


def test_leaves_seen_only_in_ste_are_added(tmp_path: Path) -> None:
    from app.tools.fetch_standards import ste_leaves

    ste = tmp_path / "ste.jsonl"
    rows = [
        {"id": 1, "category": "Бумага для офисной техники", "root": "Канцелярия",
         "category_path": ".1.30.31.793410722.", "directory_id": 793410722},
        {"id": 2, "category": "Бумага для офисной техники", "root": "Канцелярия",
         "category_path": ".1.30.31.793410722.", "directory_id": 793410722},
        {"id": 3, "category": "Бланки", "root": "Бумажная продукция",
         "category_path": ".1.10.11.", "directory_id": 11},
        {"id": 4, "category": "Без пути", "root": "Канцелярия"},
    ]  # fmt: skip
    ste.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    assert ste_leaves(ste, known={".1.10.11."}) == [
        {"path": ".1.30.31.793410722.", "name": "Бумага для офисной техники", "root": "Канцелярия"}
    ]


def test_ste_leaves_skip_cut_line_and_branch_itself(tmp_path: Path) -> None:
    from app.tools.fetch_standards import ste_leaves

    ste = tmp_path / "ste.jsonl"
    good = {"id": 1, "category": "Бумага", "root": "К", "category_path": ".1.30.31.5."}
    on_branch = {"id": 2, "category": "Ветка", "root": "К", "category_path": ".1.30.31."}
    cut = '{"id": 3, "category": "Обрыв", "category_path": ".1.30.31.6.'
    lines = [json.dumps(good, ensure_ascii=False), json.dumps(on_branch, ensure_ascii=False), cut]
    ste.write_text("\n".join(lines), "utf-8")
    got = ste_leaves(ste, known=set(), branches={".1.30.31."})
    assert [c["path"] for c in got] == [".1.30.31.5."]


def test_shards_split_categories_without_overlap() -> None:
    from app.tools.fetch_standards import in_shard

    first = [i for i in range(1, 8) if in_shard(i, "0/2")]
    second = [i for i in range(1, 8) if in_shard(i, "1/2")]
    assert (first, second) == ([1, 3, 5, 7], [2, 4, 6])
    assert all(in_shard(i, None) for i in range(1, 8))
