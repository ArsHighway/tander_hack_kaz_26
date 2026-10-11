"""Прогрев кэша ответов модели перед показом: демо-тексты через работающий API с включённой моделью.

    uv run python -m app.tools.warm_llm --texts ../docs/demo-texts.txt [--api http://127.0.0.1:8000]

Кэш ответов (`CachedChat`) живёт в Redis стенда и привязан к тексту запроса к модели: тот же товар
и тот же набор оставшихся характеристик. Поэтому текст идёт в настоящий API стенда, а не в копию
конвейера: тот же справочник, индексы, векторы и ключ кэша. На каждый вопрос (категории, позиции
КТРУ) идут первые `--branch` вариантов — поставщик на показе выберет один из них, и его запрос к
модели уже будет в кэше. Запускать после последней правки промпта (`llm_fill.SYSTEM`): новый
промпт — другой ключ кэша.

Выключенную модератором модель команда не включает: она останавливается и просит включить.
"""

import argparse
import asyncio
import logging
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.narrowing import NONE_VALUE, NOTHING_FITS, OTHER_VALUE

log = logging.getLogger(__name__)

_NOT_AN_OPTION = {NONE_VALUE, OTHER_VALUE, NOTHING_FITS}
# Шаг пути: чей вопрос, характеристика, выбранное значение.
Step = tuple[str, str, str]


class ModelSwitchedOff(RuntimeError):
    """Модель выключена модератором: греть нечего, а включать её должен он."""


@dataclass
class TextReport:
    text: str
    paths: int = 0
    decided: int = 0
    llm: Counter[str] = field(default_factory=Counter)

    @property
    def unavailable(self) -> int:
        return self.llm["unavailable"]


def _open_question(out: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Вопрос, который ждёт ответа: сначала категории, затем позиции КТРУ."""
    if question := (out.get("ktru") or {}).get("question"):
        return "category", question
    chain_ktru = (out.get("chain") or {}).get("ktru") or {}
    if question := chain_ktru.get("question"):
        return "ktru", question
    return None


async def _post(client: httpx.AsyncClient, url: str, body: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(url, json=body)
    response.raise_for_status()
    result: dict[str, Any] = response.json()
    return result


async def _run_path(
    client: httpx.AsyncClient, name: str, description: str, path: list[Step]
) -> dict[str, Any]:
    out = await _post(client, "/api/v1/autofill", {"name": name, "description": description})
    for stage, characteristic, value in path:
        url = f"/api/v1/autofill/{out['session_id']}/answer"
        out = await _post(
            client, url, {"characteristic": characteristic, "value": value, "stage": stage}
        )
    return out


async def warm_text(
    client: httpx.AsyncClient, text: str, *, branch: int = 3, max_paths: int = 8
) -> TextReport:
    """Все пути ответов (в ширину, не больше `max_paths`), по первым `branch` вариантам вопроса."""
    report = TextReport(text)
    queue: list[list[Step]] = [[]]
    while queue and report.paths < max_paths:
        path = queue.pop(0)
        out = await _run_path(client, text, "", path)
        report.paths += 1
        report.llm[out.get("llm", "on")] += 1
        if (out.get("ktru") or {}).get("final_code"):
            report.decided += 1
        found = _open_question(out)
        if found is None:
            continue
        stage, question = found
        options = [o["value"] for o in question["options"] if o["value"] not in _NOT_AN_OPTION]
        queue += [[*path, (stage, question["characteristic"], v)] for v in options[:branch]]
    return report


async def warm(
    client: httpx.AsyncClient, texts: list[str], *, branch: int = 3, max_paths: int = 8
) -> list[TextReport]:
    settings = (await client.get("/api/v1/settings")).raise_for_status().json()
    if not settings.get("llm_enabled"):
        raise ModelSwitchedOff("модель выключена: включите её на странице «Модерация»")
    reports = []
    for text in texts:
        reports.append(await warm_text(client, text, branch=branch, max_paths=max_paths))
        r = reports[-1]
        log.info("%s: путей %d, с кодом %d, модель %s", text, r.paths, r.decided, dict(r.llm))
    return reports


def summary(reports: list[TextReport]) -> str:
    paths = sum(r.paths for r in reports)
    decided = sum(r.decided for r in reports)
    unavailable = sum(r.unavailable for r in reports)
    lines = [f"текстов {len(reports)}, путей ответов {paths}, с кодом {decided}"]
    if unavailable:
        lines.append(
            f"модель не ответила в {unavailable} шагах (квота, 429, сервис): кэш прогрет не "
            "полностью, см. `degraded` и логи api"
        )
    return "\n".join(lines)


async def _main(args: argparse.Namespace) -> int:
    texts = [t.strip() for t in args.texts.read_text(encoding="utf-8").splitlines() if t.strip()]
    if not texts:
        raise SystemExit(f"{args.texts}: ни одного текста")
    # Ответ модели ждём сколько нужно: прерванный запрос в кэш не попадает.
    async with httpx.AsyncClient(base_url=args.api, timeout=args.timeout) as client:
        try:
            reports = await warm(client, texts, branch=args.branch, max_paths=args.max_paths)
        except ModelSwitchedOff as exc:
            print(exc, file=sys.stderr)
            return 2
    print(summary(reports))
    return 1 if any(r.unavailable for r in reports) else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--texts", type=Path, required=True, help="файл: текст товара в каждой строке")
    ap.add_argument("--api", default="http://127.0.0.1:8000", help="адрес API стенда")
    ap.add_argument("--branch", type=int, default=3, help="сколько вариантов вопроса пройти")
    ap.add_argument("--max-paths", type=int, default=8, help="сколько путей ответов на текст")
    ap.add_argument("--timeout", type=float, default=120.0, help="сколько ждать один запрос, с")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
