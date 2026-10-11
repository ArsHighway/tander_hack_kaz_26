"""Карточка позиции КТРУ из ЕИС («Описание товара», `fetch_ktru`) → `Position`.

Шапка: наименование и единица измерения позиции. Таблица «Характеристики товара, работы, услуги»:
строка с именем характеристики (ячейка с `rowspan` на все её значения) и пометкой «(характеристика
является / не является обязательной для применения)», дальше строки значение + единица. Числовое
значение ЕИС пишет порогами («≥ 80 и < 90») — в справочнике это корзина «>=80 ... <90», её
понимает `app.values.fit_value`. Код ОКПД2 — часть кода КТРУ до дефиса.
"""

import html
import re
from html.parser import HTMLParser
from typing import Literal

from app.catalog.model import CharacteristicSpec, Position
from app.ingest.characteristics import to_number
from app.portal.standards import canon_unit

_SIGNS = {"≥": ">=", "≤": "<=", ">": ">", "<": "<"}
_BOUND = re.compile(r"([≥≤<>])\s*(-?\d+(?:[.,]\d+)?)")
_NAME = re.compile(r'class="sectionMainInfo__body">.*?class="cardMainInfo__content">([^<]+)<', re.S)
_UNIT = re.compile(r"Единица измерения:\s*([^<]+)<")


def bucket(text: str) -> str:
    """«≥ 600 и < 800» → «>=600 ... <800»; не порог — как есть."""
    bounds = _BOUND.findall(text)
    if not bounds or _BOUND.sub("", text).replace("и", "").strip():
        return text.strip()
    return " ... ".join(f"{_SIGNS[s]}{n}" for s, n in bounds)


class _Rows(HTMLParser):
    """Строки таблицы характеристик: [(первая ли ячейка с именем, [тексты ячеек])]."""

    def __init__(self) -> None:
        super().__init__()
        self.inside = False
        self.depth = 0
        self.rows: list[tuple[bool, list[str]]] = []
        self._cells: list[str] | None = None
        self._first = False
        self._text: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "div" and a.get("id") == "ktruCharacteristicContent":
            self.inside, self.depth = True, 0
        if not self.inside:
            return
        if tag == "div":
            self.depth += 1
        if tag == "tr":
            self._cells, self._first = [], False
        elif tag == "td" and self._cells is not None:
            self._text = []
            # У ячейки имени атрибут class дважды («… col_first» и «characteristicName»): dict
            # оставил бы только последний.
            classes = " ".join(v or "" for k, v in attrs if k == "class")
            if "tableBlock__col_first" in classes:
                self._first = True

    def handle_endtag(self, tag: str) -> None:
        if not self.inside:
            return
        if tag == "td" and self._cells is not None and self._text is not None:
            self._cells.append(re.sub(r"\s+", " ", "".join(self._text)).strip())
            self._text = None
        elif tag == "tr" and self._cells is not None:
            if self._cells:
                self.rows.append((self._first, self._cells))
            self._cells = None
        elif tag == "div":
            self.depth -= 1
            if self.depth <= 0:
                self.inside = False

    def handle_data(self, data: str) -> None:
        if self._text is not None:
            self._text.append(data)


def _numeric(values: list[str]) -> bool:
    return bool(values) and all(
        v.startswith((">", "<")) or to_number(v) is not None for v in values
    )


def parse_card(code: str, page: str) -> Position:
    name = html.unescape(m[1]).strip() if (m := _NAME.search(page)) else code
    unit = html.unescape(m[1]).strip() if (m := _UNIT.search(page)) else None
    parser = _Rows()
    parser.feed(page)
    specs: list[dict] = []
    for first, cells in parser.rows:
        if first:
            label = cells[0]
            title, _, flag = label.partition("(характеристика")
            specs.append({
                "name": title.strip(),
                "required": "не является" not in flag and "является обязательной" in flag,
                "values": [], "units": [],
            })  # fmt: skip
            cells = cells[1:]
        if not specs or not cells:
            continue
        value = bucket(cells[0])
        if value and value not in specs[-1]["values"]:
            specs[-1]["values"].append(value)
        if len(cells) > 1 and cells[1]:
            specs[-1]["units"].append(cells[1])
    chars = []
    for s in specs:
        unit_of = canon_unit(s["units"][0], s["name"]) if s["units"] else None
        kind: Literal["qualitative", "quantitative"] = (
            "quantitative" if unit_of or _numeric(s["values"]) else "qualitative"
        )
        chars.append(
            CharacteristicSpec(
                name=s["name"], kind=kind, unit=unit_of, values=s["values"], required=s["required"]
            )
        )
    return Position(
        code=code,
        name=name,
        okpd2=code.split("-")[0],
        unit=unit,
        characteristics=chars,
        source="ktru-eis",
    )
