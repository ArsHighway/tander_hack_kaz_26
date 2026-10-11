"""Самопроверка: карточка, которую модуль заполнил сам, должна проходить /check.

    uv run python -m app.tools.selfcheck ../data/qa/2026-10-09-audit/gen/descriptions.jsonl \
        [--out ../data/metrics/selfcheck.json]

По каждому описанию (jsonl: `name`, `description`) — /autofill в процессе, без модели; на вопросы
второго этапа (позиция КТРУ) — первый вариант модуля, как поставщик, который с ним соглашается,
вопрос категории — без ответа. Из ответа — карточка: наименование, описание, код категории этапа 1
(или пусто, если модуль задал вопрос), код КТРУ и значения, которые модуль заполнил сам (и ответы).
Её — в /check. Считает:

- block на собственные значения модуля — по виду замечания и правилу, со списком замечаний;
- карточки с verdict ready;
- круги исправления: у замечания на поле есть fix, после его подстановки то же поле снова
  получает block (карточку не пройти без удаления поля).

Справочник и индексы — из `.env` корня репозитория (`CATALOG_PATHS`, `KTRU_PATHS`, `TWINS_PATH`,
`EXEMPLARS_PATH`), относительные пути — от корня. Сессии — в памяти, модели и эмбеддингов нет:
Redis, Postgres и сервер модели не нужны.
"""

import argparse
import asyncio
import json
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.config import Settings
from app.glossary import Glossary
from app.llm import LLMError
from app.narrowing import NONE_VALUE, NOTHING_FITS, OTHER_VALUE
from app.pipeline import AnswerIn, AutofillIn, AutofillOut, Deps, answer, autofill, check
from app.resources import load_exemplars, load_ktru, load_similar, load_twins
from app.schemas import CardIn, CharIn, Issue
from app.search import build_bm25
from app.session import MemoryKV, SessionStore

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
_PATH_FIELDS = ("catalog_paths", "ktru_paths", "twins_path", "exemplars_path")


class _NoModel:
    """Модели нет: шаг идёт только правилами, как после отказа сервиса."""

    async def chat(self, *args: Any, **kwargs: Any) -> Any:
        raise LLMError("самопроверка без модели")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise LLMError("самопроверка без эмбеддингов")


def _from_root(value: str) -> str:
    """Пути через запятую — от корня репозитория, если не абсолютные."""
    parts = [p.strip() for p in value.split(",") if p.strip()]
    return ",".join(str(p if Path(p).is_absolute() else ROOT / p) for p in parts)


def settings_from_root(env_file: Path = ROOT / ".env") -> Settings:
    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
    return settings.model_copy(update={f: _from_root(getattr(settings, f)) for f in _PATH_FIELDS})


def build_deps(settings: Settings) -> Deps:
    from app.catalog import Catalog

    catalog = Catalog.load(settings.catalog_files())
    deps = Deps(
        catalog=catalog,
        bm25=build_bm25(catalog),
        vectors=None,
        llm=_NoModel(),
        glossary=Glossary.load(),
        sessions=SessionStore(MemoryKV(), ttl_s=600),  # type: ignore[arg-type]
        min_score=settings.search_min_score,
        exemplars=load_exemplars(settings),
        similar=load_similar(settings),
        twins=load_twins(settings),
        ktru=load_ktru(settings),
    )
    deps.llm_paused_until = float("inf")
    deps.embed_paused_until = float("inf")
    return deps


def card_from(name: str, description: str, out: AutofillOut) -> tuple[CardIn, dict[str, str]]:
    """Карточка из ответа модуля: что он заполнил сам. Второе — источник значения по полю."""
    chars: list[CharIn] = []
    methods: dict[str, str] = {}
    for c in out.characteristics:
        if c.value is None or c.status == "not_found":
            continue
        try:
            chars.append(CharIn(name=c.name, value=c.value, unit=c.unit))
        except ValidationError:
            continue  # длинное значение карточки не пройдёт и в мастере: не о детекторе
        methods[c.name] = c.method or "?"
    card = CardIn(
        name=name,
        description=description,
        category_code=out.ktru.final_code,
        # Код КТРУ — как в мастере: итог второго этапа, если текст его определил.
        ktru_code=out.chain.ktru.final_code if out.chain and out.chain.ktru else None,
        characteristics=chars,
    )
    return card, methods


def _char_name(issue: Issue) -> str | None:
    prefix = "characteristics."
    return issue.field[len(prefix) :] if issue.field.startswith(prefix) else None


def _with_fix(card: CardIn, char: str, value: str) -> CardIn:
    chars = [c.model_copy(update={"value": value}) if c.name == char else c
             for c in card.characteristics]  # fmt: skip
    return card.model_copy(update={"characteristics": chars})


def fix_loops(card: CardIn, issues: list[Issue], deps: Deps) -> list[dict[str, Any]]:
    """Замечания с fix значения поля, после подстановки которого поле снова получает block."""
    loops = []
    for issue in issues:
        char = _char_name(issue)
        if char is None or issue.fix is None or not issue.fix.value:
            continue
        after = check(_with_fix(card, char, issue.fix.value), deps).issues
        again = [i for i in after if i.field == issue.field and i.severity == "block"]
        if again:
            loops.append(
                {
                    "field": issue.field,
                    "first": issue.message,
                    "fix": issue.fix.value,
                    "then": again[0].message,
                }
            )
    return loops


# Вопросов КТРУ больше не бывает: каждый ответ отсекает хотя бы одну позицию.
MAX_KTRU_ANSWERS = 10
_NOT_AN_OPTION = {NONE_VALUE, OTHER_VALUE, NOTHING_FITS}


async def answer_ktru(out: AutofillOut, deps: Deps) -> tuple[AutofillOut, int]:
    """Вопросы второго этапа — первым вариантом модуля. Второе — сколько ответов дано."""
    answered = 0
    for _ in range(MAX_KTRU_ANSWERS):
        question = out.chain.ktru.question if out.chain and out.chain.ktru else None
        value = next(
            (o.value for o in question.options if o.value not in _NOT_AN_OPTION), None
        ) if question else None  # fmt: skip
        if question is None or value is None:
            break
        inp = AnswerIn(characteristic=question.characteristic, value=value, stage="ktru")
        out = await answer(out.session_id, inp, deps)
        answered += 1
    return out, answered


async def run_case(case: dict[str, Any], deps: Deps) -> dict[str, Any]:
    name, description = case["name"], case.get("description", "")
    out = await autofill(AutofillIn(name=name, description=description), deps)
    out, answered = await answer_ktru(out, deps)
    card, methods = card_from(name, description, out)
    result = check(card, deps)
    own = []
    for issue in result.issues:
        char = _char_name(issue)
        if issue.severity == "block" and char in methods:
            own.append(
                {
                    "field": issue.field,
                    "kind": issue.kind,
                    "rule": issue.rule,
                    "method": methods[char],
                    "value": next(c.value for c in card.characteristics if c.name == char),
                    "message": issue.message,
                    "why": issue.why,
                    "fix": issue.fix.value if issue.fix else None,
                }
            )
    return {
        "id": case.get("id"),
        "name": name,
        "code": card.category_code,
        "ktru_code": card.ktru_code,
        "ktru_answers": answered,
        "filled": len(card.characteristics),
        "verdict": result.verdict,
        "blocks": [i.message for i in result.issues if i.severity == "block"],
        # Замечания к кодам (категория, КТРУ) любой строгости, кроме info.
        "code_issues": [
            i.message
            for i in result.issues
            if i.field in ("ktru_code", "category_code") and i.severity != "info"
        ],
        "own_blocks": own,
        "fix_loops": fix_loops(card, result.issues, deps),
    }


def summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    by_rule: Counter[str] = Counter()
    by_kind: Counter[str] = Counter()
    for c in cases:
        for b in c["own_blocks"]:
            by_rule[b["rule"]] += 1
            by_kind[b["kind"]] += 1
    return {
        "cards": len(cases),
        "with_code": sum(1 for c in cases if c["code"]),
        "with_ktru": sum(1 for c in cases if c["ktru_code"]),
        "ktru_answers": sum(c["ktru_answers"] for c in cases),
        "ktru_ready": sum(1 for c in cases if c["ktru_code"] and c["verdict"] == "ready"),
        "code_issues": sum(len(c["code_issues"]) for c in cases),
        "ready": sum(1 for c in cases if c["verdict"] == "ready"),
        "cards_with_own_block": sum(1 for c in cases if c["own_blocks"]),
        "own_blocks": sum(len(c["own_blocks"]) for c in cases),
        "own_blocks_by_rule": dict(by_rule.most_common()),
        "own_blocks_by_kind": dict(by_kind.most_common()),
        "fix_loops": sum(len(c["fix_loops"]) for c in cases),
    }


async def run(path: Path, deps: Deps) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        cases = [json.loads(line) for line in fh if line.strip()]
    if not cases:
        raise SystemExit(f"в {path} нет описаний")
    done = [await run_case(c, deps) for c in cases]
    return {"summary": summarize(done), "cases": done}


def report(result: dict[str, Any]) -> str:
    s = result["summary"]
    lines = [
        f"карточек: {s['cards']}, с кодом категории: {s['with_code']}, ready: {s['ready']}",
        f"с кодом КТРУ: {s['with_ktru']}, из них ready: {s['ktru_ready']}; "
        f"ответов на вопросы КТРУ: {s['ktru_answers']}; замечаний к кодам: {s['code_issues']}",
        f"block на свои значения: {s['own_blocks']} в {s['cards_with_own_block']} карточках",
        "  по правилу: " + "; ".join(f"{k} — {v}" for k, v in s["own_blocks_by_rule"].items()),
        "  по виду: " + "; ".join(f"{k} — {v}" for k, v in s["own_blocks_by_kind"].items()),
        f"круги исправления (fix снова даёт block): {s['fix_loops']}",
    ]
    for c in result["cases"]:
        for b in c["own_blocks"]:
            lines.append(f"{c['id']} [{b['method']}] {b['message']} | {b['why']} | fix={b['fix']}")
        for loop in c["fix_loops"]:
            lines.append(f"{c['id']} КРУГ {loop['first']} → fix «{loop['fix']}» → {loop['then']}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("descriptions", type=Path, help="jsonl: name, description")
    parser.add_argument("--out", type=Path, help="полный разбор по карточкам в json")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    deps = build_deps(settings_from_root())
    result = asyncio.run(run(args.descriptions, deps))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    print(report(result))


if __name__ == "__main__":
    main()
