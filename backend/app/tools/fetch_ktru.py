"""Карточки позиций КТРУ с zakupki.gov.ru (ЕИС) — сырые страницы для разбора `app.ktru`.

    uv run python -m app.tools.fetch_ktru --out ../data/ktru/raw

Какие позиции: коды КТРУ, на которые ссылаются характеристики стандартов категорий портала
(`ktruValues`, `data/standards/standards.jsonl`), или список из `--codes` (по коду в строке).
Страница — «Описание товара» (`ktru-description.html?itemId=<код>`): характеристики, их
обязательность, допустимые значения и единицы. Сохраняется как есть (`<код>.html.gz`): разбор
отдельно, чтобы правка разбора не требовала новой выгрузки; уже скачанное пропускается.

Пауза между запросами по умолчанию 60 с — `Crawl-delay` из robots.txt ЕИС. Сертификат сайта
выдан российским корневым центром Минцифры, которого нет в certifi: он лежит рядом
(`certs/russian_trusted_root_ca.crt`, SHA-256 D2:6D:2D:02:31:B7:C3:9F:…:8E:CF:31).
ЕИС из-за границы не открывается: zakupki.gov.ru должен идти мимо VPN.
"""

import argparse
import gzip
import json
import logging
import ssl
import time
from pathlib import Path

import certifi
import httpx

from app.portal.standards import parse_standard

log = logging.getLogger(__name__)

CARD_URL = "https://zakupki.gov.ru/epz/ktru/ktruCard/ktru-description.html"
CA_FILE = Path(__file__).parent / "certs" / "russian_trusted_root_ca.crt"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) tenderhack-kazan-research"


def ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.load_verify_locations(CA_FILE)
    return ctx


def linked_codes(standards: Path) -> list[str]:
    """Коды КТРУ из ссылок характеристик стандартов категорий, по порядку."""
    codes: set[str] = set()
    with standards.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                for char in parse_standard(json.loads(line)).chars.values():
                    codes.update(char.ktru)
    return sorted(codes)


def fetch(client: httpx.Client, code: str) -> str | None:
    for attempt in range(3):
        try:
            r = client.get(CARD_URL, params={"itemId": code})
            if r.status_code == httpx.codes.OK and "Характеристики" in r.text:
                return r.text
            log.warning("%s: HTTP %s, %d байт", code, r.status_code, len(r.content))
            if r.status_code == httpx.codes.NOT_FOUND:
                return None
        except httpx.HTTPError as e:
            log.warning("%s: %s", code, e)
        time.sleep(30 * (attempt + 1))
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--standards", type=Path, default=Path("../data/standards/standards.jsonl"))
    ap.add_argument("--codes", type=Path, help="файл с кодами КТРУ вместо ссылок стандартов")
    ap.add_argument("--out", type=Path, default=Path("../data/ktru/raw"))
    ap.add_argument("--pause", type=float, default=60.0, help="пауза между запросами, с")
    ap.add_argument("--limit", type=int, help="только первые N кодов (проверка)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.codes:
        codes = [c.strip() for c in args.codes.read_text(encoding="utf-8").split() if c.strip()]
    else:
        codes = linked_codes(args.standards)
    codes = codes[: args.limit]
    args.out.mkdir(parents=True, exist_ok=True)
    todo = [c for c in codes if not (args.out / f"{c}.html.gz").exists()]
    log.info("кодов КТРУ: %d, уже скачано: %d, осталось: %d", len(codes), len(codes) - len(todo),
             len(todo))  # fmt: skip
    failed: list[str] = []
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(verify=ssl_context(), headers=headers, timeout=60) as client:
        for i, code in enumerate(todo, 1):
            page = fetch(client, code)
            if page is None:
                failed.append(code)
            else:
                (args.out / f"{code}.html.gz").write_bytes(gzip.compress(page.encode("utf-8")))
            if i % 20 == 0 or i == len(todo):
                log.info("[%d/%d] %s, не скачано: %d", i, len(todo), code, len(failed))
            if i < len(todo):
                time.sleep(args.pause)
    got = sum(1 for c in codes if (args.out / f"{c}.html.gz").exists())
    log.info("готово: %d из %d карточек -> %s", got, len(codes), args.out.resolve())
    if failed:
        log.warning("не скачано %d (перезапуск докачает): %s", len(failed), failed[:20])
    if not got:
        raise SystemExit("ни одной карточки: zakupki.gov.ru идёт мимо VPN?")


if __name__ == "__main__":
    main()
