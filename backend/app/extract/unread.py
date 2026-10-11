"""Что из описания не легло ни в одно поле: поставщик видит, что информация не потеряна.

Описание режется на строки, «;» и предложения. Кусок, который пересекается с фрагментом хотя бы
одного поля или свойства вне шаблона (`extra`), прочитан. Реклама, цена, доставка и заголовок
таблицы «Параметр | Значение» — не о товаре, их не показываем.
"""

import regex as re

from app.extract.title import NOISE
from app.schemas import Span

MAX_UNREAD = 20
_PIECE = re.compile(r"[^\n;]+")
_SENTENCE = re.compile(r"(?<=[\p{L})\d%»\"])\.\s+(?=\S)")
_HEADER = re.compile(
    r"^\s*(?:параметр|характеристика|наименование)\s*\|\s*зн\p{L}*ение\s*$", re.IGNORECASE
)
_CONTENT = re.compile(r"[\p{L}\p{N}]{2,}")


def _parts(text: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for piece in _PIECE.finditer(text):
        start = piece.start()
        for cut in _SENTENCE.finditer(text, piece.start(), piece.end()):
            out.append((start, cut.start() + 1))
            start = cut.end()
        out.append((start, piece.end()))
    return out


def unread(description: str, spans: list[Span]) -> list[Span]:
    """Куски описания без единого фрагмента в полях — по порядку, не больше `MAX_UNREAD`."""
    read = [(s.start, s.end) for s in spans if s.field == "description"]
    out: list[Span] = []
    for start, end in _parts(description):
        raw = description[start:end]
        lead = len(raw) - len(raw.lstrip())
        text = raw.strip(" \t.,|")
        start += lead
        end = start + len(text)
        if not _CONTENT.search(text) or NOISE.search(text) or _HEADER.match(text):
            continue
        if any(a < end and start < b for a, b in read):
            continue
        out.append(Span(field="description", start=start, end=end, text=text))
        if len(out) >= MAX_UNREAD:
            break
    return out
