"""Справочник КТРУ из XML ЕИС: структура позиции как в выгрузке `nsiKTRUNew` (значения выдуманы)."""

import io
import zipfile
from pathlib import Path

import pytest

from app.catalog import Catalog
from app.catalog.ktru_xml import iter_positions, load_positions
from app.values import fit_value

HEAD = (
    '<export xmlns="http://zakupki.gov.ru/oos/export/1" '
    'xmlns:oos="http://zakupki.gov.ru/oos/types/1" '
    'xmlns:cmn="http://zakupki.gov.ru/oos/common/1"><nsiKTRUs>'
)
TAIL = "</nsiKTRUs></export>"


def position(code: str, *, status: str = "ACTIVE", version: int = 1, name: str = "Молоток") -> str:
    return f"""
<oos:position><oos:data>
  <oos:code>{code}</oos:code><oos:version>{version}</oos:version><oos:name>{name}</oos:name>
  <oos:OKPD2><oos:code>25.73.30.141</oos:code><oos:name>Молотки</oos:name></oos:OKPD2>
  <oos:status>{status}</oos:status>
  <oos:OKEIs><oos:OKEI><oos:code>796</oos:code><oos:name>Штука</oos:name></oos:OKEI></oos:OKEIs>
  <oos:characteristics>
    <oos:characteristic>
      <oos:code>a1</oos:code><oos:name>Безынерционный  молоток</oos:name>
      <oos:type>1</oos:type><oos:kind>1</oos:kind>
      <oos:actual>true</oos:actual><oos:isRequired>true</oos:isRequired><oos:choiceType>1</oos:choiceType>
      <oos:values>
        <oos:value><oos:valueCode>v1</oos:valueCode><oos:qualityDescription>Да</oos:qualityDescription></oos:value>
        <oos:value><oos:valueCode>v2</oos:valueCode><oos:qualityDescription>Нет</oos:qualityDescription></oos:value>
        <oos:value><oos:valueCode>v6</oos:valueCode>
          <oos:qualityDescription>≥ 3,5</oos:qualityDescription></oos:value>
      </oos:values>
    </oos:characteristic>
    <oos:characteristic>
      <oos:code>a2</oos:code><oos:name>Длина рукоятки</oos:name>
      <oos:type>2</oos:type><oos:kind>1</oos:kind>
      <oos:actual>true</oos:actual><oos:isRequired>false</oos:isRequired><oos:choiceType>1</oos:choiceType>
      <oos:values>
        <oos:value><oos:valueCode>v3</oos:valueCode>
          <oos:OKEI><oos:code>003</oos:code><oos:name>Миллиметр</oos:name></oos:OKEI>
          <oos:rangeSet><oos:valueRange><oos:maxMathNotation>less</oos:maxMathNotation><oos:max>100</oos:max>
          </oos:valueRange></oos:rangeSet>
        </oos:value>
        <oos:value><oos:valueCode>v4</oos:valueCode>
          <oos:OKEI><oos:code>003</oos:code><oos:name>Миллиметр</oos:name></oos:OKEI>
          <oos:rangeSet><oos:valueRange>
            <oos:minMathNotation>greaterOrEqual</oos:minMathNotation><oos:min>100</oos:min>
            <oos:maxMathNotation>less</oos:maxMathNotation><oos:max>150</oos:max>
          </oos:valueRange></oos:rangeSet>
        </oos:value>
        <oos:value><oos:valueCode>v5</oos:valueCode>
          <oos:OKEI><oos:code>003</oos:code><oos:name>Миллиметр</oos:name></oos:OKEI>
          <oos:valueFormat>N</oos:valueFormat><oos:valueSet><oos:concreteValue>900</oos:concreteValue></oos:valueSet>
        </oos:value>
      </oos:values>
    </oos:characteristic>
    <oos:characteristic>
      <oos:code>a3</oos:code><oos:name>Устаревшая</oos:name><oos:type>1</oos:type><oos:kind>1</oos:kind>
      <oos:actual>false</oos:actual><oos:isRequired>false</oos:isRequired><oos:choiceType>1</oos:choiceType>
    </oos:characteristic>
  </oos:characteristics>
</oos:data></oos:position>"""


def xml(*positions: str) -> bytes:
    return (HEAD + "".join(positions) + TAIL).encode("utf-8")


def test_position_fields_and_characteristics() -> None:
    [record] = list(iter_positions(io.BytesIO(xml(position("25.73.30.141-00000001")))))
    p = record.position
    assert (p.code, p.name, p.okpd2, p.unit, p.source) == (
        "25.73.30.141-00000001", "Молоток", "25.73.30.141", "Штука", "ktru-xml"
    )  # fmt: skip
    assert [c.name for c in p.characteristics] == ["Безынерционный молоток", "Длина рукоятки"]
    quality, length = p.characteristics
    assert (quality.kind, quality.values, quality.required) == (
        "qualitative",
        ["Да", "Нет", ">=3,5"],
        True,
    )
    assert (length.kind, length.unit, length.required) == ("quantitative", "мм", False)
    assert length.values == ["<100", ">=100 ... <150", "900"]


def test_bucket_is_understood_by_fit_value() -> None:
    [record] = list(iter_positions(io.BytesIO(xml(position("25.73.30.141-00000001")))))
    length = record.position.characteristics[1]
    got = fit_value("120", "мм", length)
    assert got.value == ">=100 ... <150"


def write_archive(tmp_path: Path) -> Path:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr(
            "nsiKTRUNew_all_actual_20261003_001.xml",
            xml(
                position("25.73.30.141-00000001", version=1, name="Молоток старый"),
                position("25.73.30.141-00000001", version=2, name="Молоток"),
                position("25.73.30.141-00000002", status="TERMINATED"),
            ),
        )
        z.writestr(
            "nsiKTRUNew_all_not-actual_20261003_001.xml", xml(position("25.73.30.141-00000009"))
        )
    outer = tmp_path / "ktru.zip"
    with zipfile.ZipFile(outer, "w") as z:
        z.writestr("КТРУ/", b"")
        z.writestr("КТРУ/161_nsiKTRUNew_all_2026-10-05.zip", inner.getvalue())
    return outer


def test_archive_takes_actual_active_latest_version(tmp_path: Path) -> None:
    positions, report = load_positions(write_archive(tmp_path))
    assert [(p.code, p.name) for p in positions] == [("25.73.30.141-00000001", "Молоток")]
    assert report["files_taken"] == 1 and report["files_dropped"] == 1
    assert report["statuses"] == {"ACTIVE": 2, "TERMINATED": 1}
    assert report["repeated_codes"] == 1
    Catalog(positions, version="test")


def test_archive_without_actual_files_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "empty.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("nsiKTRUNew_all_not-actual_1.xml", xml(position("25.73.30.141-00000009")))
    with pytest.raises(ValueError, match="нет XML actual"):
        load_positions(path)
