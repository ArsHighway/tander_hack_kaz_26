"""Значения похожих СТЕ портала: чего нет в тексте поставщика, подсказывают СТЕ той же категории
с похожим наименованием.

Проба (500 случаев отложенной выборки, верная категория, похожие — без неё и её
двойников): 10 похожих, значение при ≥ 2 голосах и согласии ≥ 60 % — полнота значений, которых
нет в тексте, 22,6 % против 8,3 %, точность 68,6 % против 60,6 %. Это подсказка: в карточке она
помечена (`method: "similar"`) и меньше значения из текста или ответа поставщика.

Характеристики похожих лежат в sqlite рядом с индексом похожих (`<exemplars>.values.sqlite`,
`python -m app.tools.build_exemplar_values`): 650 тыс. СТЕ в памяти API не держим.
"""

import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

from app.catalog import CharacteristicSpec
from app.schemas import Span
from app.values import fit_value, fold

NEIGHBOURS = 10
MIN_VOTES = 2
MIN_SHARE = 0.6

Neighbour = dict[str, tuple[str, str | None]]


class Similar(BaseModel):
    """Подсказка поля: значение, подогнанное к позиции, и сколько похожих СТЕ за него (`votes`) из
    тех, у кого это поле заполнено (`of`)."""

    value: str
    unit: str | None = None
    votes: int
    of: int
    # СТЕ портала, давшие это значение (до трёх): у двойников — ссылка «как у СТЕ №…».
    ids: list[int] = []
    # Фрагмент текста поставщика с этим значением (`against_text.said_same`): тогда это значение
    # из текста, а не подсказка.
    source: Span | None = None


def values_path(exemplars: Path) -> Path:
    """Файл характеристик к индексу похожих: `exemplars.jsonl` → `exemplars.values.sqlite`."""
    return exemplars.with_name(f"{exemplars.stem}.values.sqlite")


class SimilarValues:
    """id СТЕ → её характеристики {имя: (значение, единица)}, только чтение."""

    def __init__(self, path: Path) -> None:
        self.path = path
        uri = f"{path.resolve().as_uri()}?mode=ro"
        self._db = sqlite3.connect(uri, uri=True, check_same_thread=False)

    def get(self, ids: list[int]) -> list[Neighbour]:
        """Характеристики в порядке `ids`; СТЕ без записи пропускается."""
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = dict(self._db.execute(f"SELECT id, chars FROM ste WHERE id IN ({marks})", ids))
        return [
            {name: (value, unit) for name, (value, unit) in json.loads(rows[i]).items()}
            for i in ids
            if i in rows
        ]


_ID = re.compile(r'^\{"id": (\d+)')


def build_values(ste: Path, ids: set[int], out: Path) -> int:
    """Характеристики СТЕ из `ids` (выгрузка `fetch_ste`, jsonl) → sqlite; сколько записано."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.execute("CREATE TABLE ste (id INTEGER PRIMARY KEY, chars TEXT NOT NULL)")
    written = 0
    with ste.open(encoding="utf-8") as fh:
        for line in fh:
            m = _ID.match(line)
            if m is None or int(m[1]) not in ids:
                continue
            row = json.loads(line)
            chars = {
                str(c["name"]): [str(c["value"]), c.get("unit") or None]
                for c in row.get("characteristics") or []
                if c.get("name") and c.get("value") not in (None, "")
            }
            db.execute(
                "INSERT OR REPLACE INTO ste VALUES (?, ?)",
                (row["id"], json.dumps(chars, ensure_ascii=False)),
            )
            written += 1
    db.commit()
    db.close()
    tmp.replace(out)
    return written


def vote(
    specs: list[CharacteristicSpec],
    neighbours: list[Neighbour],
    skip: set[str],
    *,
    min_votes: int = MIN_VOTES,
    min_share: float = MIN_SHARE,
    ids: list[int] | None = None,
) -> dict[str, Similar]:
    """Подсказки по характеристикам `specs`: значение, за которое ≥ `min_votes` похожих и
    ≥ `min_share` из заполнивших, и которое подходит позиции (перечень, единица). Двойникам
    (`app.search.twins`) хватает и одного голоса.

    `skip` — свёрнутые имена полей, уже заполненных из текста: текст поставщика важнее. Поля
    «Общих сведений» (производитель, страна) у похожих СТЕ свои — их не подсказываем.
    `ids` — id СТЕ в порядке `neighbours`: подсказка запомнит, чьё это значение.
    """
    out: dict[str, Similar] = {}
    for spec in specs:
        if spec.group == "general" or fold(spec.name) in skip:
            continue
        names = {fold(n) for n in (spec.name, *spec.synonyms)}
        found: list[tuple[int | None, str, str | None]] = []
        for i, n in enumerate(neighbours):
            hit = next((v for k, v in n.items() if fold(k) in names), None)
            if hit is not None:
                found.append((ids[i] if ids else None, *hit))
        if not found:
            continue
        keys = [
            (sid, (f.value, f.unit))
            for sid, value, unit in found
            if (f := fit_value(value, unit, spec)).status in ("exact", "normalized", "bucket")
            and f.value is not None
        ]
        if not keys:
            continue
        (value, unit), votes = Counter(k for _, k in keys).most_common(1)[0]
        if votes < min_votes or votes / len(found) < min_share:
            continue
        # «как у 3 из 4»: доля среди похожих, у которых поле заполнено, — по ней и решаем.
        agreed = [sid for sid, k in keys if k == (value, unit) and sid is not None][:3]
        out[spec.name] = Similar(value=value, unit=unit, votes=votes, of=len(found), ids=agreed)
    return out
