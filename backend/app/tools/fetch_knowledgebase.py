"""Выгрузка базы знаний Портала поставщиков в `data/knowledgebase/`.

    uv run python -m app.tools.fetch_knowledgebase --out ../data/knowledgebase

Четыре запроса — по одному на раздел (`SECTIONS`), с паузой; тот же публичный API, что у сайта
(`/newapi/...`, robots.txt закрывает только `/api` и `/admin`). Сохраняет сырые ответы (`raw/`),
статьи построчно (`articles.jsonl`), по статье в Markdown (`<раздел>/<id>-<slug>.md`) и оглавление
(`index.md`). Данные портала в git не идут: `data/` в `.gitignore`.
"""

import argparse
import json
import logging
import time
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from app.portal.knowledgebase import SECTIONS, index_markdown, parse_sections, path_of, to_markdown

log = logging.getLogger(__name__)

SECTION_URL = "https://zakupki.mos.ru/newapi/api/KnowledgeBase/GetArticlesBySectionType"
# Только ASCII: заголовки HTTP идут в latin-1.
USER_AGENT = "TenderHackKazanKB/1.0 (+https://zakupki.mos.ru) Mozilla/5.0 (compatible)"


def fetch(client: httpx.Client, section: str) -> list[dict[str, Any]]:
    r = client.get(SECTION_URL, params={"sectionType": section})
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise ValueError(f"раздел {section}: ждали список категорий, пришло {type(data).__name__}")
    return data


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("../data/knowledgebase"))
    ap.add_argument("--pause", type=float, default=1.0, help="пауза между запросами, с")
    ap.add_argument("--raw", type=Path, help="папка с уже скачанными raw/<раздел>.json — без сети")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    payloads: dict[str, list[dict[str, Any]]] = {}
    if args.raw:
        for section in SECTIONS:
            payloads[section] = json.loads((args.raw / f"{section}.json").read_text("utf-8"))
    else:
        (args.out / "raw").mkdir(parents=True, exist_ok=True)
        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60) as client:
            for i, section in enumerate(SECTIONS):
                if i:
                    time.sleep(args.pause)
                payloads[section] = fetch(client, section)
                raw = json.dumps(payloads[section], ensure_ascii=False)
                (args.out / "raw" / f"{section}.json").write_text(raw, encoding="utf-8")

    articles = parse_sections(payloads)
    per_section = {s: sum(len(sv["articles"]) for c in p for sv in c["articlesByService"])
                   for s, p in payloads.items()}  # fmt: skip
    if not articles:
        raise SystemExit(f"ни одной статьи: ответы разделов {per_section} — проверьте API портала")
    empty = [a.id for a in articles if not a.text]
    log.info("разделы: %s; статей без повторов: %d; без текста: %d", per_section, len(articles),
             len(empty))  # fmt: skip

    for section in SECTIONS:
        (args.out / section).mkdir(parents=True, exist_ok=True)
    with (args.out / "articles.jsonl").open("w", encoding="utf-8") as fh:
        for a in articles:
            fh.write(json.dumps(asdict(a), ensure_ascii=False) + "\n")
            (args.out / path_of(a)).write_text(to_markdown(a), encoding="utf-8")
    (args.out / "index.md").write_text(
        index_markdown(articles, date.today().isoformat()), encoding="utf-8"
    )
    log.info("готово: %s", args.out.resolve())


if __name__ == "__main__":
    main()
