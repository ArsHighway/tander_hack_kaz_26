"""Наименование и описание из одной вставки, без обязательного переноса строки.

Наименование — текст до первой подписи характеристики или числа с единицей. «Модель: …» и
«высота 5 метров» уходят в описание, даже если поставщик написал всё одной строкой. Если описание
уже заполнено отдельно, деление поставщика не трогаем.
"""

from __future__ import annotations

import regex as re

from app.ingest.units import normalize_unit
from app.search.text import tokens

_CUE_WORDS = (
    "модель производитель бренд марка страна высота ширина глубина длина толщина диаметр "
    "материал мощность напряжение вес масса объем объём плотность цвет размер количество радиус "
    "формат комплектация артикул"
)
_GENERIC_WORDS = "вид тип наличие значение характеристика"

_WORD = re.compile(r"\p{L}+")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_UNIT_AFTER = re.compile(r"\s*([%‰]|[^\s,;:()0-9][^\s,;:()]*)")


def _stems(text: str) -> set[str]:
    return set(tokens(text))


CUES = _stems(_CUE_WORDS)
_CUES = CUES
_GENERIC = _stems(_GENERIC_WORDS)


# Конец предложения: точка, «;», «!» или «?», пробел и заглавная буква («нитрил. р-р M» — не конец).
_SENTENCE_END = re.compile(r"[.;!?]\s+(?=\p{Lu})")


def derive_parts(name: str, description: str) -> tuple[str, str]:
    """Короткое наименование для карточки и поиска и остаток — описание.

    Делим, только когда вставка явно похожа на описание: после первого предложения есть ещё
    текст, или в ней есть подпись «Метка: значение». Наименование одним куском («Бумага А4 80 г/м2
    500 листов», «Перчатки нитриловые, размер M») не трогаем: так пишут на портале, по нему ищутся
    двойники и голые числа «А4 80 500»."""
    if not description.strip() and (row := _table_row(name)) is not None:
        # Строка таблицы одной строкой: ячейки и свойства — это описание, а не наименование.
        name, description = row
    if description.strip():
        return _lead_noise(name, description)
    # Реклама и коды в начале одной строки ушли в описание — делим то, что осталось.
    name, noise = _lead_noise(name, "")
    sentence = _SENTENCE_END.search(name)
    if sentence is not None:
        cut: int | None = sentence.end()
    elif ":" in name:
        cut = _title_cut(name[: name.index(":")])
    else:
        cut = None
    title = name[:cut].strip(" \t,.;") if cut is not None else name
    rest = name[cut:].strip() if cut is not None else ""
    if len(title) < 2 or (cut is not None and not rest):
        title, rest = name, ""
    return title, "\n".join(x for x in (rest, noise) if x)


# Ячейка-номер (ID СТЕ, артикул, штрихкод) и номер в начале строки перед словами.
_ID_CELL = re.compile(r"\d{4,}")
_LEAD_ID = re.compile(r"\A\s*(\d{5,})\s+(?=\p{L})")
_CELL_BREAK = re.compile(r"\t+")
_PROPERTY_BREAK = " | "
_EMPTY_CELLS = {"", "-", "—", "–"}


def _table_row(text: str) -> tuple[str, str] | None:
    """Строка таблицы, вставленная одной строкой (копия строки Excel или выгрузки СТЕ).

    Ячейки разделены табуляцией, свойства — « | »: «1160330⇥Отвертка аккумуляторная Bort⇥Bort⇥
    КИТАЙ⇥Вес 1010 г | Высота упаковки 200 мм | …». Целиком в наименовании такая строка читалась
    правилами для наименования: номер СТЕ становился количеством, число из обозначения модели —
    оборотами, а описание оставалось пустым — без подписей, перечней и новых характеристик.
    Наименование — первая ячейка со словами (до первого свойства), остальное — описание по
    строкам; ячейки-номера уходят в его конец, повтор наименования («Модель») не дублируется."""
    line = text.strip()
    if "\n" in line or ("\t" not in line and _PROPERTY_BREAK not in line):
        return None
    cells = [c.strip() for c in _CELL_BREAK.split(line)]
    cells = [c for c in cells if c not in _EMPTY_CELLS]
    ids = [c for c in cells if _ID_CELL.fullmatch(c)]
    rest = [c for c in cells if not _ID_CELL.fullmatch(c)]
    if not rest:
        return None
    title, *others = rest
    lines: list[str] = []
    if _PROPERTY_BREAK in title:
        head, _, tail = title.partition(_PROPERTY_BREAK)
        if ":" in head:
            return (
                None  # «Артикул: … | Код товара: …» — подписи с двоеточием разбирает `_lead_noise`
            )
        cut = _title_cut(head)
        if cut:
            title, tail = head[:cut].strip(), f"{head[cut:].strip()}{_PROPERTY_BREAK}{tail}"
        else:
            title = head.strip()
        lines.append(tail)
    seen = {title.casefold()}
    for cell in others:
        if cell.casefold() not in seen:
            seen.add(cell.casefold())
            lines.append(cell)
    lines += ids
    if not title or not lines:
        return None
    return title, "\n".join(lines)


# Реклама, цена, доставка и коды учёта в начале вставки — не наименование товара: по «ХИТ ПРОДАЖ!»
# искался бы не тот товар. Они не выбрасываются, а уходят в конец описания.
NOISE = re.compile(
    r"хит\s+продаж|новинк|лучш\p{L}*\s+цен|в\s+наличии|отгрузк|доставк|рейтинг|отзыв|скидк"
    r"|\bакци[яи]\b|\bцена\b|\bруб\b|₽|фото\s+товара|право\s+менять|остались\s+вопросы|звоните|гарантия\s+качества",
    re.IGNORECASE,
)
_CODES = re.compile(
    r"\A\s*(?:(?:артикул|код\s+товара|штрих-?код|sku)\s*:\s*[\p{L}\p{N}./-]+\s*\|?\s*)+",
    re.IGNORECASE,
)
_LEAD_PIECE = re.compile(r".*?(?:[.;!?](?=\s)|\s\|(?=\s)|\Z)", re.DOTALL)


def _lead_noise(name: str, description: str) -> tuple[str, str]:
    """Перенос строки в наименовании (после HTML и JSON) — первая строка и есть наименование;
    рекламные фразы и коды учёта с начала уходят в конец описания."""
    if "\n" in name.strip():
        first, _, rest = name.strip().partition("\n")
        name, description = first, "\n".join(x for x in (rest.strip(), description) if x.strip())
    moved: list[str] = []
    codes = _CODES.match(name)
    if codes and codes.end() < len(name):
        moved.append(codes.group().strip(" |"))
        name = name[codes.end() :]
    # «1160330 Отвертка аккумуляторная …» — номер учёта перед наименованием, не часть товара.
    lead_id = _LEAD_ID.match(name)
    if lead_id:
        moved.append(lead_id.group(1))
        name = name[lead_id.end() :]
    elif _ID_CELL.fullmatch(name.strip()) and description.strip():
        # Первая строка — один номер: наименование — следующая строка.
        moved.append(name.strip())
        name = ""
    while True:
        piece = _LEAD_PIECE.match(name)
        if piece is None or not piece.group().strip() or not NOISE.search(piece.group()):
            break
        if piece.end() >= len(name.rstrip()) and not description.strip():
            break  # кроме рекламы ничего нет — наименование остаётся, каким его написали
        moved.append(piece.group().strip(" |"))
        name = name[piece.end() :].lstrip(" |")
    if not name.strip() and description.strip():
        first, _, rest = description.strip().partition("\n")
        name, description = first, rest
    if moved:
        description = "\n".join(x for x in (description.strip(), *moved) if x)
    return name.strip(), description


def _title_cut(text: str) -> int | None:
    seen_product = False
    for kind, start, stem, raw in _pieces(text):
        product = kind == "word" and stem not in _CUES and stem not in _GENERIC
        if product and normalize_unit(raw) is None:
            seen_product = True
            continue
        if not seen_product:
            continue
        if stem in _CUES or kind == "qty":
            return start
    return None


def _pieces(text: str) -> list[tuple[str, int, str, str]]:
    found: list[tuple[str, int, str, str]] = []
    for word in _WORD.finditer(text):
        raw = word.group()
        stems = tokens(raw)
        found.append(("word", word.start(), stems[0] if stems else "", raw))
    for number in _NUMBER.finditer(text):
        if _glued(text, number.start()):
            continue
        unit, _length = _unit_span(text, number.end())
        if unit is None:
            continue
        found.append(("qty", number.start(), "", number.group()))
    found.sort(key=lambda item: item[1])
    return found


def _glued(text: str, start: int) -> bool:
    """«Гигант-5» и «A4» — часть названия, а не величина."""
    return start > 0 and (text[start - 1].isalnum() or text[start - 1] in "-–—")


def _unit_span(text: str, end: int) -> tuple[str | None, int]:
    matched = _UNIT_AFTER.match(text, end)
    if matched is None:
        return None, 0
    raw = matched.group(1).rstrip(".")
    if not raw:
        return None, 0
    for cand in (raw, raw.split()[0]):
        unit = normalize_unit(cand)
        if unit is not None:
            return unit, matched.start(1) - end + len(cand)
    return None, 0
