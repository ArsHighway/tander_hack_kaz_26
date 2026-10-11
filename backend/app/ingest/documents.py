"""Текст из документа поставщика: Word (.docx), PDF с текстовым слоем, таблица.

Шаг 1 мастера принимает файл вместо вставленного текста: из одного товара — текст в поле для
проверки, из нескольких — предложение открыть массовую загрузку. Word читается без зависимостей
(docx — zip с `word/document.xml`), PDF — `pypdf`, таблицы — теми же читателями, что и пакет.
Сканы (PDF без текстового слоя) не распознаются: так и пишется.
"""

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

from pypdf import PdfReader

from app.ingest.readers import TABLE_SUFFIXES, detect_encoding

Kind = Literal["docx", "pdf", "table", "text"]

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
# Распакованный document.xml больше этого — не документ поставщика, а бомба.
MAX_XML_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 50
# Сколько строк таблицы читать ради счёта товаров: дальше — «больше» (`items` = предел + 1).
MAX_TABLE_ROWS = 10_000


@dataclass
class Extracted:
    text: str
    items: int
    kind: Kind


class DocumentError(ValueError):
    """Документ прочитан, но текста в нём нет (скан PDF) или он повреждён: 422."""


class UnsupportedDocument(ValueError):
    """Не Word, не PDF и не таблица: 415."""


def _text(el: ElementTree.Element) -> str:
    parts: list[str] = []
    for node in el.iter():
        if node.tag == f"{W}t" and node.text:
            parts.append(node.text)
        elif node.tag == f"{W}tab":
            parts.append(" ")
        elif node.tag in (f"{W}br", f"{W}cr"):
            parts.append("\n")
    return "".join(parts).strip()


def _line(row: list[str]) -> str:
    cells = [c for c in row if c]
    # Строка «имя — значение» читается как характеристика: «Плотность: 80 г/м2».
    return f"{cells[0]}: {cells[1]}" if len(cells) == 2 else "; ".join(cells)


def docx_text(data: bytes) -> tuple[str, list[list[list[str]]]]:
    """Текст документа по порядку (абзацы и строки таблиц) и сами таблицы (строки ячеек)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            if zf.getinfo("word/document.xml").file_size > MAX_XML_BYTES:
                raise DocumentError("документ Word слишком большой")
            xml = zf.read("word/document.xml")
        body = ElementTree.fromstring(xml).find(f"{W}body")
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as e:
        raise DocumentError("файл Word повреждён: сохраните его заново") from e
    lines: list[str] = []
    tables: list[list[list[str]]] = []
    for el in _blocks(body):
        if el.tag == f"{W}p":
            if text := _text(el):
                lines.append(text)
        elif el.tag == f"{W}tbl":
            rows = [[_text(tc) for tc in tr.findall(f"{W}tc")] for tr in el.findall(f"{W}tr")]
            rows = [r for r in rows if any(r)]
            tables.append(rows)
            lines.extend(_line(r) for r in rows)
    return "\n".join(lines), tables


def _blocks(body: ElementTree.Element | None) -> list[ElementTree.Element]:
    """Абзацы и таблицы тела по порядку; поля шаблонов (`w:sdt`) раскрываются в своё содержимое."""
    out: list[ElementTree.Element] = []
    for el in body if body is not None else []:
        if el.tag == f"{W}sdt":
            out.extend(_blocks(el.find(f"{W}sdtContent")))
        else:
            out.append(el)
    return out


# Таблица характеристик одного товара («Наименование показателя | Значение показателя») — не список
# товаров: так оформлены описания по КТРУ.
_NOT_GOODS = ("показател", "характеристик", "параметр")


def _name_column(header: list[str]) -> int | None:
    cells = [c.casefold() for c in header]
    if any(c.startswith("значени") for c in cells):
        return None
    return next(
        (
            i
            for i, c in enumerate(cells)
            if "наименован" in c and not any(w in c for w in _NOT_GOODS)
        ),
        None,
    )


def goods_table(tables: list[list[list[str]]]) -> list[list[str]] | None:
    """Таблица товаров: в заголовке колонка «Наименование» (товара), а не «Наименование
    показателя» и без колонки значений — так её поймёт и пакет."""
    for rows in tables:
        if rows and _name_column(rows[0]) is not None:
            return rows
    return None


def pdf_text(data: bytes) -> str:
    from pypdf.errors import FileNotDecryptedError

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = reader.pages[:MAX_PDF_PAGES]
        return "\n".join((p.extract_text() or "").strip() for p in pages).strip()
    except FileNotDecryptedError as e:
        raise DocumentError("PDF защищён паролем: снимите пароль или вставьте текст вручную") from e
    except Exception as e:
        raise DocumentError("файл PDF повреждён: сохраните его заново или вставьте текст") from e


def row_text(
    name: str, description: str, characteristics: list[tuple[str, str, str | None]]
) -> str:
    """Товар строки таблицы как текст поставщика: наименование, описание, «Имя: значение ед»."""
    lines = [name, *([description] if description else [])]
    lines += [f"{n}: {v} {u}" if u else f"{n}: {v}" for n, v, u in characteristics]
    return "\n".join(lines)


def _goods_text(header: list[str], row: list[str]) -> str:
    # Колонка наименования — первой строкой, остальные — «Колонка: значение».
    at = _name_column(header) or 0
    rest = [f"{h}: {v}" for i, (h, v) in enumerate(zip(header, row, strict=False)) if i != at and v]
    return "\n".join([row[at] if at < len(row) else "", *rest])


def _table(filename: str, data: bytes) -> Extracted:
    from app.batch import BatchFormatError, peek_upload

    try:
        rows = peek_upload(filename, data, MAX_TABLE_ROWS)
    except BatchFormatError as e:
        raise DocumentError(str(e)) from e
    if not rows:
        raise DocumentError("в таблице нет ни одной строки с товаром")
    first = rows[0]
    chars = [(c.name, c.value, c.unit) for c in first.characteristics]
    return Extracted(row_text(first.name, first.description, chars), len(rows), "table")


def extract_document(filename: str, data: bytes) -> Extracted:
    """Текст для шага 1 и сколько в файле товаров (больше одного — мастер предложит пакет)."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".docx":
        text, tables = docx_text(data)
        goods = goods_table(tables)
        if goods and len(goods) > 2:
            return Extracted(_goods_text(goods[0], goods[1]), len(goods) - 1, "docx")
        if not text:
            raise DocumentError("в документе Word нет текста")
        return Extracted(text, 1, "docx")
    if suffix == ".pdf":
        text = pdf_text(data)
        if not text:
            raise DocumentError("В PDF нет текстового слоя: это скан, вставьте текст вручную")
        return Extracted(text, 1, "pdf")
    if suffix == ".txt":
        return Extracted(
            data.decode(detect_encoding(data[:65536]), errors="replace").strip(), 1, "text"
        )
    if suffix in TABLE_SUFFIXES:
        return _table(filename, data)
    if suffix == ".doc":
        raise UnsupportedDocument("старый формат Word (.doc): сохраните файл как .docx")
    raise UnsupportedDocument(f"файл {suffix or 'без расширения'}: подойдёт Word, PDF или таблица")
