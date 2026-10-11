"""Такая СТЕ уже есть на портале: двойники по наименованию или модели.

Модератор каталога отклоняет заявку с причиной «Предложена другая СТЕ», когда во вкладке
«Похожие СТЕ» есть СТЕ с тем же наименованием и характеристиками (база знаний портала, статья
214102). Двойник — СТЕ портала с тем же наименованием (без регистра, кавычек и знаков) или с той
же моделью из наименования поставщика. Модуль показывает её поставщику, берёт из двойников
категорию и значения полей, которых нет в тексте.

Индекс — sqlite (`TWINS_PATH`, `python -m app.tools.build_twins`): 3,7 млн СТЕ в памяти API не
держим. Характеристики сжаты zlib: несжатые заняли бы ~3 ГБ.
"""

import json
import re
import sqlite3
import zlib
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.catalog import CharacteristicSpec
from app.ingest.units import normalize_unit
from app.search.similar import Similar, vote
from app.values import fold

# Двойников по наименованию читаем не больше: голосу хватит, а у «Бумага А4» их тысячи.
NAME_LIMIT = 50
# Ключ модели больше чем в стольких категориях — уже не модель, а код стандарта или признак
# («IP65», «USB3»); картридж CE285A — 215 СТЕ в двух категориях, это модель (лимит
# по числу СТЕ её терял).
MODEL_CATEGORIES = 2
# Сколько СТЕ одной модели поднимаем из индекса и сколько из них показываем.
MODEL_SCAN = 1000
MODEL_SHOWN = 20
# Модель короче — «A4», «85A»: такие склеили бы разные товары.
MIN_MODEL = 4
# Сколько id одного наименования поднимаем из индекса до вычета отложенной выборки.
_SCAN = 1000
# Значение двойников: доля согласных среди двойников, у которых поле заполнено.
TWIN_SHARE = 0.6

_CYR_TO_LAT = str.maketrans("авсенкмортху", "abcehkmoptxy")
_NOT_WORD = re.compile(r"[\W_]+")
_EDGE = ".,;:!?()[]{}«»\"'„“”/\\"
# «800х500х500», «210*297мм», резьба «М10х50» — размеры, а не модель; «205/55R16» — размер шины.
_SIZE = re.compile(r"^[mм]?\d+(?:[.,]\d+)?(?:[xх*×]\d+(?:[.,]\d+)?)+[^\d]*$")
_TYRE = re.compile(r"^\d{3}/\d{2}[rр]\d{2}")
# «80г/м2», «220в», «500мл» — число с единицей.
_QUANTITY = re.compile(r"^\d+(?:[.,]\d+)?([^\d].*)$")

Chars = dict[str, tuple[str, str | None]]
By = Literal["name", "model"]


@dataclass(frozen=True)
class Twin:
    id: int
    directory_id: int
    name: str
    chars: Chars
    by: By


class TwinRef(BaseModel):
    """Двойник в сессии: без характеристик (их читают из индекса по id, когда код определён)."""

    id: int
    name: str
    code: str | None
    by: By


def twin_category(twins: list[TwinRef], share: float) -> tuple[str, list[TwinRef]] | None:
    """Категория, которую двойники по наименованию называют почти единогласно (доля ≥ `share`).
    Двойник из категории не из справочника голосует «против».

    Двойники по модели категорию не решают: на отложенной выборке (2000 СТЕ) из 82
    решённых ими случаев без решения до того верных 23, неверных 59 — модель из наименования
    («KC-55713», «DC-60») бывает и у чужого товара. По наименованию — 145 верных из 175.
    """
    group = [t for t in twins if t.by == "name"]
    counts = Counter(t.code for t in group if t.code is not None)
    if not counts:
        return None
    code, votes = counts.most_common(1)[0]
    return (code, [t for t in group if t.code == code]) if votes / len(group) >= share else None


def twin_values(
    specs: list[CharacteristicSpec],
    chars: list[Chars],
    skip: set[str],
    ids: list[int] | None = None,
) -> dict[str, Similar]:
    """Значения двойников для полей, которых нет в тексте: хватает одного двойника, у нескольких —
    согласие не меньше `TWIN_SHARE` заполнивших поле. `ids` — чьи это характеристики.

    Только двойники по наименованию: на отложенной выборке их значения верны в 94 % (один
    двойник) и 91 % (несколько), а одного двойника по модели — в 59 %, ниже похожих СТЕ (80 %).
    """
    return vote(specs, chars, skip, min_votes=1, min_share=TWIN_SHARE, ids=ids)


def twin_maker(makers: list[tuple[int, str]]) -> Similar | None:
    """Производитель двойников по наименованию (id, как в выгрузке): тот, о ком согласны не
    меньше `TWIN_SHARE`, без формы собственности и кавычек. На 771 СТЕ выборки с
    двойником верен в 91,6 %; страна двойников — лишь в 63,5 %, её не подсказываем."""
    # Нормализация производителя живёт в справочнике портала; модуль app.portal импортирует
    # конвейер, поэтому импорт здесь, а не вверху: иначе кольцо при загрузке.
    from app.portal.standards import maker_name

    named = [(sid, maker_name(raw)) for sid, raw in makers if raw and raw.strip()]
    if not named:
        return None
    key, votes = Counter(fold(name) for _, name in named).most_common(1)[0]
    if votes / len(named) < TWIN_SHARE:
        return None
    agreed = [(sid, name) for sid, name in named if fold(name) == key]
    return Similar(
        value=agreed[0][1], unit=None, votes=votes, of=len(named), ids=[s for s, _ in agreed[:3]]
    )


def name_key(name: str) -> str:
    """Наименование для сравнения: `fold`, знаки препинания и кавычки — пробелы."""
    return " ".join(_NOT_WORD.sub(" ", fold(name)).split())


MIN_SPECIFIC_WORDS = 3


def specific_name(name: str) -> bool:
    """Наименование называет одну СТЕ, а не товар вообще: есть цифра (модель, размер, артикул) или
    не меньше трёх слов из трёх букв."""
    key = name_key(name)
    return (
        any(ch.isdigit() for ch in key)
        or sum(1 for w in key.split() if len(w) >= 3) >= MIN_SPECIFIC_WORDS
    )


def model_keys(text: str) -> set[str]:
    """Ключи моделей в тексте: слова с буквами и цифрами («AR-34.2» → «ar342»), не размеры и не
    числа с единицей. Кириллица-двойник — латиницей: модели пишут латиницей."""
    keys = set()
    for raw in text.split():
        token = raw.strip(_EDGE).casefold().replace("ё", "е")
        if _SIZE.match(token) or _TYRE.match(token):
            continue
        quantity = _QUANTITY.match(token)
        if quantity and normalize_unit(quantity[1]) is not None:
            continue
        key = _NOT_WORD.sub("", token.translate(_CYR_TO_LAT))
        if (
            len(key) >= MIN_MODEL
            and any(c.isdigit() for c in key)
            and any(c.isalpha() for c in key)
        ):
            keys.add(key)
    return keys


def _chars(row: dict) -> Chars:
    return {
        str(c["name"]): (str(c["value"]), c.get("unit") or None)
        for c in row.get("characteristics") or []
        if c.get("name") and c.get("value") not in (None, "")
    }


def build_twins(ste: Path, out: Path) -> int:
    """СТЕ выгрузки `fetch_ste` (jsonl) → индекс двойников; сколько СТЕ записано."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript(
        """
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        CREATE TABLE ste (
            id INTEGER PRIMARY KEY, directory_id INTEGER, name TEXT NOT NULL,
            maker TEXT, country TEXT, chars BLOB NOT NULL
        );
        CREATE TABLE names (key TEXT NOT NULL, id INTEGER NOT NULL);
        CREATE TABLE models (key TEXT NOT NULL, id INTEGER NOT NULL);
        """
    )
    written = 0
    with ste.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            name = (row.get("name") or "").strip()
            if not name or not isinstance(row.get("id"), int):
                continue
            packed = zlib.compress(json.dumps(_chars(row), ensure_ascii=False).encode())
            db.execute(
                "INSERT OR REPLACE INTO ste VALUES (?, ?, ?, ?, ?, ?)",
                (
                    row["id"], row.get("directory_id"), name,
                    row.get("manufacturer") or None, row.get("country") or None, packed,
                ),
            )  # fmt: skip
            db.execute("INSERT INTO names VALUES (?, ?)", (name_key(name), row["id"]))
            db.executemany(
                "INSERT INTO models VALUES (?, ?)",
                [(k, row["id"]) for k in model_keys(row.get("model") or "")],
            )
            written += 1
    db.execute("CREATE INDEX names_key ON names (key)")
    db.execute("CREATE INDEX models_key ON models (key)")
    db.commit()
    db.close()
    tmp.replace(out)
    return written


class TwinIndex:
    """Двойники по наименованию поставщика, только чтение. `exclude` — id, которые двойниками не
    считаются (СТЕ отложенной выборки при оценке: карточка — не двойник сама себе)."""

    def __init__(self, path: Path, exclude: Collection[int] = ()) -> None:
        self.path = path
        uri = f"{path.resolve().as_uri()}?mode=ro"
        self._db = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self.exclude = frozenset(exclude)

    def find(self, name: str) -> list[Twin]:
        """Двойники по наименованию (новые СТЕ первыми), затем по модели из наименования."""
        found: dict[int, By] = {}
        # «Стул», «Кресло офисное» — не одна СТЕ, а сотни разных: их производитель и размеры —
        # чужие. Отложенная выборка: двойников по наименованию 525 вместо 543, верных 94,9 % вместо
        # 94,1 %.
        key = name_key(name) if specific_name(name) else ""
        named = self._db.execute(
            "SELECT id FROM names WHERE key = ? ORDER BY id DESC LIMIT ?", (key, _SCAN)
        )
        for (sid,) in named:
            if sid not in self.exclude and len(found) < NAME_LIMIT:
                found[sid] = "name"
        shown = 0
        for key in sorted(model_keys(name)):
            hits = self._db.execute(
                "SELECT m.id, s.directory_id FROM models m JOIN ste s ON s.id = m.id "
                "WHERE m.key = ? ORDER BY m.id DESC LIMIT ?",
                (key, MODEL_SCAN + 1),
            ).fetchall()
            if len(hits) > MODEL_SCAN or len({d for _, d in hits}) > MODEL_CATEGORIES:
                continue  # не модель, а признак товаров разных категорий
            for sid, _ in hits:
                if sid not in self.exclude and sid not in found and shown < MODEL_SHOWN:
                    found[sid] = "model"
                    shown += 1
        rows = self._rows(list(found))
        return [
            Twin(id=sid, directory_id=directory, name=title, chars=chars, by=found[sid])
            for sid in found
            if (row := rows.get(sid)) is not None
            for directory, title, chars in [row]
        ]

    def chars(self, ids: list[int]) -> list[tuple[int, Chars]]:
        """(id, характеристики) в порядке `ids`; нет в индексе — пропуск."""
        rows = self._rows(ids)
        return [(i, rows[i][2]) for i in ids if i in rows]

    def makers(self, ids: list[int]) -> list[tuple[int, str]]:
        """(id, производитель как в выгрузке) в порядке `ids`; без производителя — пропуск."""
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = dict(
            self._db.execute(
                f"SELECT id, maker FROM ste WHERE id IN ({marks}) AND maker IS NOT NULL", ids
            )
        )
        return [(i, rows[i]) for i in ids if i in rows]

    def _rows(self, ids: list[int]) -> dict[int, tuple[int, str, Chars]]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = self._db.execute(
            f"SELECT id, directory_id, name, chars FROM ste WHERE id IN ({marks})", ids
        )
        return {sid: (directory, title, _unpack(packed)) for sid, directory, title, packed in rows}


def _unpack(packed: bytes) -> Chars:
    return {k: (v, u) for k, (v, u) in json.loads(zlib.decompress(packed)).items()}
