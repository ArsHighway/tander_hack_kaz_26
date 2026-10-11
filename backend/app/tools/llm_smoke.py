"""Ручная проверка модели.

`uv run python -m app.tools.llm_smoke [--chat-model M] [--embed-model E]`.

Работает против локальных llama.cpp server (чат gemma-4-31b-it-Q8_0, эмбеддинги bge-m3) — берёт
`LLM_*` / `EMBED_*` из окружения или `.env`. Печатает таблицу: случай, результат, задержка, начало
ответа. Код выхода 1, если хоть один случай провалился.
"""

import argparse
import asyncio
import math
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from app.config import get_settings
from app.glossary import Glossary
from app.llm import LLMClient, LLMError


class Item(BaseModel):
    # Модели часто отдают число числом (80, а не "80"); значение потом сверяется со справочником.
    model_config = ConfigDict(coerce_numbers_to_str=True)

    name: str
    value: str
    unit: str | None = None


class Extraction(BaseModel):
    items: list[Item]


@dataclass
class CaseResult:
    name: str
    ok: bool
    latency_ms: float
    detail: str


DESCRIPTION = "Бумага офисная А4, плотность 80 г/м2, белизна 146% CIE, 500 листов в пачке, класс C"
SYSTEM = (
    "Ты помощник по заполнению карточек товаров на Портале поставщиков. Отвечай по-русски, кратко, "
    "без рассуждений."
)


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


async def case_ping(llm: LLMClient) -> tuple[bool, str]:
    r = await llm.chat(
        [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": "Столица России? Одно слово."},
        ]
    )
    return "москв" in r.text.casefold(), r.text


async def case_extract(llm: LLMClient) -> tuple[bool, str]:
    prompt = (
        "Извлеки характеристики товара в JSON: items — список {name, value, unit}. Единицу пиши "
        f"отдельно от значения. Описание: {DESCRIPTION}"
    )
    r = await llm.chat(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        schema=Extraction,
    )
    items = r.parsed.items if isinstance(r.parsed, Extraction) else []
    values = " ".join(f"{i.name}={i.value}{i.unit or ''}" for i in items).casefold()
    ok = all(k in values for k in ("80", "500", "146")) and ("а4" in values or "a4" in values)
    return ok, values


async def case_abbreviation(llm: LLMClient, with_glossary: bool) -> tuple[bool, str]:
    text = "Мешки для мусора ПВД 35 л, 30 шт"
    glossary = Glossary.load().prompt_block(text) if with_glossary else ""
    prompt = (f"Словарь:\n{glossary}\n\n" if glossary else "") + (
        f"Товар: «{text}». Из какого материала мешки? Назови материал полностью, одной фразой."
    )
    r = await llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}])
    return "полиэтилен" in r.text.casefold(), r.text


async def case_embeddings(llm: LLMClient) -> tuple[bool, str]:
    a, b, c = await llm.embed(
        ["бумага офисная А4 белая", "бумага для принтера A4 500 листов", "шины летние R16"]
    )
    near, far = cosine(a, b), cosine(a, c)
    return near > far, f"похожие {near:.3f} > разные {far:.3f}, размерность {len(a)}"


async def run(llm: LLMClient) -> list[CaseResult]:
    cases: list[tuple[str, Callable[[], Awaitable[tuple[bool, str]]]]] = [
        ("русский ответ", lambda: case_ping(llm)),
        ("извлечение в JSON", lambda: case_extract(llm)),
        ("аббревиатура без словаря", lambda: case_abbreviation(llm, with_glossary=False)),
        ("аббревиатура со словарём", lambda: case_abbreviation(llm, with_glossary=True)),
        ("эмбеддинги", lambda: case_embeddings(llm)),
    ]
    results = []
    for name, fn in cases:
        started = time.perf_counter()
        try:
            ok, detail = await fn()
        except LLMError as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        latency = (time.perf_counter() - started) * 1000
        results.append(CaseResult(name, ok, round(latency), " ".join(detail.split())[:160]))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat-model")
    parser.add_argument("--embed-model")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # TextIO has no reconfigure
    settings = get_settings().model_copy(
        update={
            k: v
            for k, v in {"llm_chat_model": args.chat_model, "embed_model": args.embed_model}.items()
            if v
        }
    )
    llm = LLMClient.from_settings(settings)
    print(
        f"провайдер {settings.llm_provider} {settings.llm_base_url}; чат {llm.chat_model}; "
        f"эмбеддинги {llm.embed_model} ({settings.embed_provider or settings.llm_provider})"
    )
    results = asyncio.run(_run_and_close(llm))
    print("| Случай | Итог | мс | Ответ |\n|---|---|---|---|")
    for r in results:
        print(f"| {r.name} | {'ок' if r.ok else 'ПРОВАЛ'} | {r.latency_ms} | {r.detail} |")
    failed = [r.name for r in results if not r.ok]
    print(f"\nпрошло {len(results) - len(failed)} из {len(results)}")
    return 1 if failed else 0


async def _run_and_close(llm: LLMClient) -> list[CaseResult]:
    try:
        return await run(llm)
    finally:
        await llm.aclose()


if __name__ == "__main__":
    raise SystemExit(main())
