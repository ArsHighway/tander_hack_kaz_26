"""Служебные поля позиций КТРУ из XML ЕИС: шаблон, родитель, версия, даты.

    uv run python -m app.tools.build_ktru_meta "../data/organizers/Казань 2026/КТРУ/КТРУ xml.zip"

В КТРУ бывают позиции с одним наименованием и одинаковыми характеристиками: шаблон
(`isTemplate`) и позиция, созданная из него (`parentPositionInfo`), — «Тумба для уголка живой
природы» 31.09.13.190-00000012 и …-00000011. По тексту их не различить. Справочник `ktru_xml.json`
этих полей не хранит, а пересобирать его на стенде долго, поэтому они лежат рядом с кодом
(`app/catalog/ktru_meta.json`) — только для позиций, у которых в справочнике есть такая же
(наименование, единица и характеристики).
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from app.catalog import Catalog
from app.catalog import ktru_xml as kx
from app.catalog.ktru_meta import META_PATH, _signature

_POSITION = re.compile(rb"<oos:position>(.*?)</oos:position>", re.S)


def _tag(block: bytes, name: str) -> str | None:
    m = re.search(rb"<oos:" + name.encode() + rb">([^<]*)</oos:" + name.encode() + rb">", block)
    return m.group(1).decode() if m else None


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("xml", type=Path, help="выгрузка КТРУ организаторов (zip с вложенными zip)")
    p.add_argument("--catalog", type=Path, default=Path("../data/catalog/ktru_xml.json"))
    p.add_argument("--out", type=Path, default=META_PATH)
    args = p.parse_args()
    positions = Catalog.load([args.catalog]).all()
    same = Counter(_signature(pos) for pos in positions)
    wanted = {pos.code for pos in positions if same[_signature(pos)] > 1}
    meta: dict[str, dict[str, object]] = {}
    for _, data in kx.read_xml_files(args.xml, kx.Selection()):
        for m in _POSITION.finditer(data):
            block = m.group(1)
            code = _tag(block, "code")
            if code not in wanted:
                continue
            parent = re.search(rb"<oos:parentPositionInfo>\s*<oos:code>([^<]+)</oos:code>", block)
            meta[code] = {
                "template": _tag(block, "isTemplate") == "true",
                "parent": parent.group(1).decode() if parent else None,
                "version": int(_tag(block, "version") or 0),
                "included": _tag(block, "inclusionDate"),
                "updated": _tag(block, "updateDate"),
            }
    if not meta:
        raise SystemExit(f"в {args.xml} не нашлось ни одной позиции из {len(wanted)}")
    text = json.dumps(dict(sorted(meta.items())), ensure_ascii=False, separators=(",", ":"))
    args.out.write_text(text, encoding="utf-8")
    print(f"позиций с одинаковыми {len(wanted)}, записано {len(meta)} → {args.out}")


if __name__ == "__main__":
    main()
