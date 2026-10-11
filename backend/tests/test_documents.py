"""Текст из документа поставщика: Word, PDF, таблица — для шага 1 мастера и пакета."""

import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ingest.documents import (
    DocumentError,
    UnsupportedDocument,
    docx_text,
    extract_document,
    goods_table,
    row_text,
)
from app.ingest.readers import read_tables
from app.main import create_app

DOCS = Path(__file__).parent / "fixtures" / "docs"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _p(text: str) -> str:
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def _tbl(rows: list[list[str]]) -> str:
    cells = "".join(
        "<w:tr>" + "".join(f"<w:tc>{_p(c)}</w:tc>" for c in row) + "</w:tr>" for row in rows
    )
    return f"<w:tbl>{cells}</w:tbl>"


def make_docx(*parts: str) -> bytes:
    body = f'<w:document xmlns:w="{W}"><w:body>{"".join(parts)}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", body)
    return buf.getvalue()


SPEC = make_docx(_p("Бумага офисная А4"), _tbl([["Плотность", "80 г/м2"], ["Листов", "500"]]))
GOODS_ROWS = [["Наименование", "Количество"]] + [[f"Бумага А4 марки {i}", "10"] for i in range(6)]
GOODS = make_docx(_p("Спецификация к договору"), _tbl(GOODS_ROWS))
# Описание одного товара по КТРУ: таблица характеристик с «Наименованием показателя» —
# не список товаров.
KTRU_SPEC = make_docx(
    _p("Бумага офисная А4 для принтера"),
    _tbl(
        [
            ["Наименование показателя", "Значение показателя", "Единица измерения"],
            ["Плотность", "80", "г/м2"],
            ["Белизна", "146", "%"],
            ["Количество листов в пачке", "500", "шт"],
        ]
    ),
)


def test_docx_paragraphs_and_tables_become_text() -> None:
    text, tables = docx_text(SPEC)
    assert text.splitlines() == ["Бумага офисная А4", "Плотность: 80 г/м2", "Листов: 500"]
    assert tables == [[["Плотность", "80 г/м2"], ["Листов", "500"]]]


def test_goods_table_is_found_by_its_name_column() -> None:
    _, tables = docx_text(GOODS)
    assert goods_table(tables) == GOODS_ROWS
    _, spec_tables = docx_text(SPEC)
    assert goods_table(spec_tables) is None


def test_extract_docx_with_one_product() -> None:
    out = extract_document("spec.docx", SPEC)
    assert (out.kind, out.items) == ("docx", 1)
    assert "Бумага офисная А4" in out.text and "Плотность: 80 г/м2" in out.text


def test_extract_docx_with_a_goods_table_counts_products() -> None:
    out = extract_document("goods.docx", GOODS)
    assert (out.kind, out.items) == ("docx", 6)
    assert out.text.startswith("Бумага А4 марки 0")


def test_extract_pdf_with_text_layer() -> None:
    out = extract_document("spec.pdf", (DOCS / "spec.pdf").read_bytes())
    assert (out.kind, out.items) == ("pdf", 1)
    assert "Бумага офисная" in out.text and "80" in out.text


def test_pdf_without_text_layer_is_an_error() -> None:
    with pytest.raises(DocumentError, match="нет текстового слоя"):
        extract_document("scan.pdf", (DOCS / "scan.pdf").read_bytes())


def test_table_with_one_row_becomes_card_text() -> None:
    csv = "Наименование;Характеристики\nБумага офисная А4;Плотность:80 г/м2;\n".encode()
    out = extract_document("one.csv", csv)
    assert (out.kind, out.items) == ("table", 1)
    assert out.text == "Бумага офисная А4\nПлотность: 80 г/м2"


def test_table_with_three_rows_counts_them() -> None:
    rows = "".join(f"Бумага марки {i};Плотность:80 г/м2;\n" for i in range(3))
    out = extract_document("three.csv", ("Наименование;Характеристики\n" + rows).encode())
    assert (out.kind, out.items) == ("table", 3)


def test_unknown_type_is_unsupported() -> None:
    with pytest.raises(UnsupportedDocument):
        extract_document("tool.exe", b"MZ binary")


def test_row_text_lists_characteristics_with_units() -> None:
    assert row_text("Бумага", "", [("Плотность", "80", "г/м2"), ("Формат", "A4", None)]) == (
        "Бумага\nПлотность: 80 г/м2\nФормат: A4"
    )
    assert row_text("Бумага", "Для принтера", []) == "Бумага\nДля принтера"


def test_batch_reads_goods_table_from_docx(tmp_path: Path) -> None:
    (tmp_path / "goods.docx").write_bytes(GOODS)
    tables, skipped = read_tables(tmp_path)
    assert skipped == []
    [table] = tables
    rows = list(table.rows())
    assert len(rows) == 6
    assert rows[0]["Наименование"] == "Бумага А4 марки 0"


def _client() -> TestClient:
    return TestClient(create_app())


def test_extract_route() -> None:
    client = _client()
    ok = client.post(
        "/api/v1/extract", files={"file": ("spec.docx", SPEC, "application/octet-stream")}
    )
    assert ok.status_code == 200
    assert ok.json()["kind"] == "docx" and ok.json()["items"] == 1
    scan = client.post(
        "/api/v1/extract",
        files={"file": ("scan.pdf", (DOCS / "scan.pdf").read_bytes(), "application/pdf")},
    )
    assert scan.status_code == 422 and "текстового слоя" in scan.json()["detail"]
    assert (
        client.post(
            "/api/v1/extract", files={"file": ("a.exe", b"MZ", "application/octet-stream")}
        ).status_code
        == 415
    )
    big = b"0" * (10 * 1024 * 1024 + 1)
    assert (
        client.post("/api/v1/extract", files={"file": ("big.csv", big, "text/csv")}).status_code
        == 413
    )


def test_characteristics_table_is_not_a_list_of_products() -> None:
    out = extract_document("ktru.docx", KTRU_SPEC)
    assert (out.kind, out.items) == ("docx", 1)
    assert out.text.startswith("Бумага офисная А4 для принтера")
    assert "Плотность" in out.text


def test_text_inside_content_controls_is_kept() -> None:
    sdt = f"<w:sdt><w:sdtContent>{_p('Бумага офисная А4')}</w:sdtContent></w:sdt>"
    out = extract_document("form.docx", make_docx(sdt, _p("Плотность 80 г/м2")))
    assert out.text.splitlines() == ["Бумага офисная А4", "Плотность 80 г/м2"]


def _pdf_with(**encrypt: str) -> bytes:
    import io

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for page in PdfReader(DOCS / "spec.pdf").pages:
        writer.add_page(page)
    writer.encrypt(**encrypt)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_pdf_with_only_an_owner_password_is_read() -> None:
    # Так выглядят PDF «с запретом редактирования»: открываются без пароля, шифрование AES.
    data = _pdf_with(user_password="", owner_password="owner", algorithm="AES-128")
    assert "Бумага офисная" in extract_document("locked.pdf", data).text


def test_pdf_with_a_password_says_so() -> None:
    data = _pdf_with(user_password="secret", owner_password="owner", algorithm="RC4-128")
    with pytest.raises(DocumentError, match="паролем"):
        extract_document("secret.pdf", data)


def test_any_broken_pdf_is_a_document_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.ingest.documents as documents

    class Broken:
        def __init__(self, *_: object, **__: object) -> None:
            raise AttributeError("'NoneType' object has no attribute 'get_object'")

    monkeypatch.setattr(documents, "PdfReader", Broken)
    with pytest.raises(DocumentError):
        extract_document("broken.pdf", (DOCS / "spec.pdf").read_bytes())


def test_old_word_format_names_the_fix() -> None:
    with pytest.raises(UnsupportedDocument, match=r"\.docx"):
        extract_document("old.doc", b"\xd0\xcf\x11\xe0 old word")


def test_huge_document_xml_is_refused() -> None:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        body = f'<w:document xmlns:w="{W}"><w:body>{_p("x")}</w:body>' + " " * (60 * 1024 * 1024)
        zf.writestr("word/document.xml", body + "</w:document>")
    with pytest.raises(DocumentError, match="слишком большой"):
        extract_document("bomb.docx", buf.getvalue())


def test_name_and_value_columns_are_one_product() -> None:
    # «Наименование | Значение | Единица» — тоже характеристики одного товара, а не список товаров.
    table = [
        ["Наименование", "Значение", "Единица"],
        ["Плотность", "80", "г/м2"],
        ["Белизна", "146", "%"],
    ]
    out = extract_document("spec2.docx", make_docx(_p("Бумага офисная А4"), _tbl(table)))
    assert (out.kind, out.items) == ("docx", 1)


def test_long_table_is_counted_only_up_to_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    # ради «одна карточка или пакет» весь файл не читается: дальше предела — «больше N»
    import app.ingest.documents as documents

    monkeypatch.setattr(documents, "MAX_TABLE_ROWS", 2)
    rows = "".join(f"Бумага марки {i};Плотность:80 г/м2;\n" for i in range(5))
    out = extract_document("five.csv", ("Наименование;Характеристики\n" + rows).encode())
    assert (out.kind, out.items) == ("table", 3)
