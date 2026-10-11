"""Метрики конвейера на золотом наборе (Т5, математическая модель п. 7.7).

Каждая метрика — с размером выборки и способом расчёта: страница «Метрики» показывает их как есть.
"""

import statistics
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from app.checks.detector import detect
from app.evaluation.golden import REAL_GROUPS, TEMPLATE_GROUPS, GoldenCase
from app.narrowing import CONFIRM, NOTHING_FITS, OTHER_VALUE
from app.pipeline import AnswerIn, AutofillIn, answer, autofill
from app.schemas import CardIn, CharIn
from app.values import fold

MAX_QUESTIONS = 5
LATENCY_HOW = "время ответа конвейера в процессе, без сети и без LLM, если оценка без --llm"


class MetricRow(BaseModel):
    key: str
    title: str
    value: float
    n: int
    how: str


class MetricsOut(BaseModel):
    created_at: datetime
    catalog_version: str
    catalog_source: str
    model: str
    rows: list[MetricRow]


def accuracy_at_k(ranked: list[list[str]], truth: list[str | None], k: int) -> float:
    if not truth:
        return 0.0
    return sum(1 for r, t in zip(ranked, truth, strict=True) if t in r[:k]) / len(truth)


def precision_recall(
    pred: set[tuple[str, str]], truth: set[tuple[str, str]]
) -> tuple[float, float]:
    hit = len(pred & truth)
    precision = hit / len(pred) if pred else 1.0
    recall = hit / len(truth) if truth else 1.0
    return precision, recall


def _pairs(items: dict[str, str]) -> set[tuple[str, str]]:
    return {(fold(k), fold(v)) for k, v in items.items()}


async def _resolve(case: GoldenCase, deps: Any) -> tuple[list[str], str | None, int, set, float]:
    """Кандидаты первого ответа, итоговый код после ответов «как в эталоне», число вопросов."""
    started = time.perf_counter()
    out = await autofill(AutofillIn(name=case.name, description=case.description), deps)
    latency = (time.perf_counter() - started) * 1000
    ranked = [c.code for c in out.ktru.candidates]
    questions = 0
    while out.ktru.question is not None and questions < MAX_QUESTIONS:
        q = out.ktru.question
        values = {fold(o.value): o.value for o in q.options}
        if q.characteristic == CONFIRM:  # подтверждение позиции: эталон знает верный код
            value = case.expected_code if case.expected_code in values.values() else NOTHING_FITS
        else:
            wanted = case.expected.get(q.characteristic) or case.answers.get(q.characteristic)
            value = values.get(fold(wanted)) if wanted else values.get("")
            if value is None and wanted and OTHER_VALUE in values.values():
                value = OTHER_VALUE  # значение эталона есть у всех кандидатов: выбор не сужает
        if value is None:
            break  # эталон не отвечает на этот вопрос — человек выбрал бы сам
        out = await answer(
            out.session_id, AnswerIn(characteristic=q.characteristic, value=value), deps
        )
        questions += 1
    # «Общие сведения» — поля карточки, а не характеристики: в точность и полноту не идут
    filled = {
        (fold(c.name), fold(c.value))
        for c in out.characteristics
        if c.value is not None
        and c.status in ("filled", "normalized")
        and c.method != "answer"
        and c.group != "general"
    }
    return ranked, out.ktru.final_code, questions, filled, latency


def _detected(case: GoldenCase, deps: Any) -> bool:
    card = CardIn(
        name=case.name,
        ktru_code=case.expected_code,
        characteristics=[CharIn(name=n, value=v, unit=u) for n, v, u in case.characteristics],
    )
    position = deps.catalog.get(case.expected_code) if case.expected_code else None
    field = f"characteristics.{case.corrupted_field}"
    return any(i.field == field and i.severity in ("block", "warn") for i in detect(card, position))


def _false_alarms(case: GoldenCase, deps: Any) -> bool:
    """Ошибка на верной карточке: блокирующее замечание по характеристике."""
    card = CardIn(
        name=case.name,
        ktru_code=case.expected_code,
        characteristics=[CharIn(name=n, value=v) for n, v in case.expected.items()],
    )
    position = deps.catalog.get(case.expected_code) if case.expected_code else None
    return any(
        i.field.startswith("characteristics.") and i.kind != "moderation_rule"
        for i in detect(card, position)
    )


@dataclass
class _Scored:
    """Прогон кейсов с известным кодом: кандидаты, итог после вопросов, заполнение."""

    cases: list[GoldenCase] = field(default_factory=list)
    ranked: list[list[str]] = field(default_factory=list)
    finals: list[bool] = field(default_factory=list)
    questions: list[int] = field(default_factory=list)
    latencies: list[float] = field(default_factory=list)
    pred: set[tuple[str, str, str]] = field(default_factory=set)
    truth: set[tuple[str, str, str]] = field(default_factory=set)

    def acc(self, k: int) -> float:
        return accuracy_at_k(self.ranked, [c.expected_code for c in self.cases], k)

    def final(self) -> float:
        return sum(self.finals) / len(self.finals) if self.finals else 0.0

    def mean_questions(self) -> float:
        return statistics.fmean(self.questions) if self.questions else 0.0

    def fill(self) -> tuple[float, float]:
        return precision_recall(self.pred, self.truth)  # type: ignore[arg-type]


async def _score(cases: list[GoldenCase], deps: Any) -> _Scored:
    s = _Scored(cases=cases)
    for c in cases:
        r, final, q, filled, ms = await _resolve(c, deps)
        s.ranked.append(r)
        s.finals.append(final == c.expected_code)
        s.questions.append(q)
        s.latencies.append(ms)
        # Значение, которого нет в тексте, но поставщик его знает (`answers`), — не ошибка модуля
        # (например, единственное значение позиции после выбора кода); в точность не идёт.
        known = _pairs(c.answers) - _pairs(c.expected)
        s.pred |= {(c.id, *p) for p in filled if p not in known}
        s.truth |= {(c.id, *p) for p in _pairs(c.expected)}
    return s


_REAL_TITLE = {
    "prose": "проза одной строкой",
    "unit": "другая единица",
    "synonym": "метка-синоним",
    "ambiguous": "неоднозначное наименование",
    "noisy": "неполное и с шумом",
}
REAL_HOW = (
    "реальные форматы: проза одной строкой через запятую (единица после числа, «метка: значение», "
    "значение перечня без метки), значение в другой единице той же величины («мл» вместо «л»), "
    "метка-синоним («Вес», «Вместимость», «Кол-во»), только общие слова наименования нескольких "
    "позиций, часть характеристик опущена и есть бренд, артикул, цена"
)


def _real_rows(s: _Scored) -> list[MetricRow]:
    """Те же ключевые метрики на реальных форматах и полнота заполнения по каждому формату."""
    n = len(s.cases)
    precision, recall = s.fill()
    rows = [
        MetricRow(key="ktru_acc1_real", title="Реальные форматы: код КТРУ первым кандидатом", n=n,
                  value=s.acc(1),
                  how=f"{REAL_HOW}; доля, где эталонная позиция первая до вопросов"),
        MetricRow(key="ktru_final_real", title="Реальные форматы: код КТРУ после вопросов", n=n,
                  value=s.final(),
                  how=f"{REAL_HOW}; на вопросы отвечает эталон тем, что поставщик знает о товаре, "
                  f"не больше {MAX_QUESTIONS}"),
        MetricRow(key="questions_real", title="Реальные форматы: вопросов до кода, в среднем", n=n,
                  value=s.mean_questions(), how=f"{REAL_HOW}; среднее число уточняющих вопросов"),
        MetricRow(key="fill_precision_real", title="Реальные форматы: точность заполнения",
                  n=len(s.pred), value=precision,
                  how=f"{REAL_HOW}; доля предложенных значений, совпавших с эталоном"),
        MetricRow(key="fill_recall_real", title="Реальные форматы: полнота заполнения",
                  n=len(s.truth), value=recall,
                  how=f"{REAL_HOW}; эталон — только характеристики, которые есть в тексте"),
    ]  # fmt: skip
    for group, title in _REAL_TITLE.items():
        ids = {c.id for c in s.cases if c.group == group}
        truth = {t for t in s.truth if t[0] in ids}
        pred = {p for p in s.pred if p[0] in ids}
        if group == "ambiguous":
            finals = [f for c, f in zip(s.cases, s.finals, strict=True) if c.group == group]
            if finals:
                rows.append(
                    MetricRow(key="ktru_final_ambiguous", n=len(finals),
                              title=f"Код КТРУ после вопросов: {title}",
                              value=sum(finals) / len(finals),
                              how="наименование — только общие первые слова нескольких позиций "
                              "(«Бумага для», «Перчатки смотровые»), код выбирается вопросами")
                )  # fmt: skip
            continue
        if truth:
            rows.append(
                MetricRow(key=f"fill_recall_{group}", n=len(truth),
                          title=f"Полнота заполнения: {title}",
                          value=precision_recall(pred, truth)[1],  # type: ignore[arg-type]
                          how=f"доля эталонных значений группы «{group}», заполненных модулем "
                          "без вопросов")
            )  # fmt: skip
    return rows


async def run_evaluation(cases: list[GoldenCase], deps: Any, model: str) -> MetricsOut:
    known = [c for c in cases if c.group in TEMPLATE_GROUPS]
    outside = [c for c in cases if c.group == "out_of_catalog"]
    corrupted = [c for c in cases if c.group == "corrupted"]
    template = await _score(known, deps)
    real = await _score([c for c in cases if c.group in REAL_GROUPS], deps)
    ranked, finals, questions, latencies = (
        template.ranked,
        template.finals,
        template.questions,
        template.latencies,
    )
    pred, truth = template.pred, template.truth
    refusals = []
    for c in outside:
        out = await autofill(AutofillIn(name=c.name), deps)
        refusals.append(not out.ktru.candidates)
    near = [c for c in cases if c.group == "near_domain"]
    near_ok = []
    for c in near:
        out = await autofill(AutofillIn(name=c.name), deps)
        near_ok.append(out.ktru.final_code is None)
    truth_codes = [c.expected_code for c in known]
    precision, recall = precision_recall(pred, truth)  # type: ignore[arg-type]
    rows = [
        MetricRow(key="ktru_acc1", title="Код КТРУ: верный первым кандидатом", n=len(known),
                  value=accuracy_at_k(ranked, truth_codes, 1),
                  how="доля описаний, где эталонная позиция стоит первой до вопросов"),
        MetricRow(key="ktru_acc5", title="Код КТРУ: верный среди первых пяти", n=len(known),
                  value=accuracy_at_k(ranked, truth_codes, 5),
                  how="доля описаний, где эталонная позиция среди пяти первых кандидатов"),
        MetricRow(key="ktru_final", title="Код КТРУ после уточняющих вопросов", n=len(known),
                  value=sum(finals) / len(finals) if finals else 0.0,
                  how=f"на вопросы отвечает эталон, не больше {MAX_QUESTIONS}; "
                  "доля верных итоговых кодов"),
        MetricRow(key="questions", title="Вопросов до кода, в среднем", n=len(known),
                  value=statistics.fmean(questions) if questions else 0.0,
                  how="среднее число уточняющих вопросов на описание"),
        MetricRow(key="fill_precision", title="Заполнение: точность", n=len(pred),
                  value=precision, how="доля предложенных значений, совпавших с эталоном"),
        MetricRow(key="fill_recall", title="Заполнение: полнота", n=len(truth),
                  value=recall, how="доля эталонных значений, которые модуль заполнил сам"),
        MetricRow(key="refusal", title="Честный отказ вне справочника", n=len(outside),
                  value=sum(refusals) / len(refusals) if refusals else 0.0,
                  how="доля далёких товаров вне справочника без подставленной позиции"),
        MetricRow(key="near_no_false_code", title="Без чужого кода рядом со справочником",
                  n=len(near), value=sum(near_ok) / len(near) if near else 0.0,
                  how="доля товаров с общим словом, но без позиции в справочнике («Белая глина», "
                  "«Бумага туалетная»), где модуль не поставил итоговый код без подтверждения"),
    ]  # fmt: skip
    if real.cases:
        rows += _real_rows(real)
    clean_cases = [c for c in known if c.group == "clean"]
    alarms = sum(_false_alarms(c, deps) for c in clean_cases)
    for kind in ("not_in_list", "typo", "contradiction", "foreign_unit"):
        of_kind = [c for c in corrupted if c.corruption == kind]
        if not of_kind:
            continue
        tp = sum(_detected(c, deps) for c in of_kind)
        rec = tp / len(of_kind)
        prec = tp / (tp + alarms) if tp + alarms else 1.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        rows.append(
            MetricRow(
                key=f"detector_{kind}",
                title=f"Детектор: {_KIND_TITLE[kind]}",
                value=f1,
                n=len(of_kind),
                how=f"F1; полнота {rec:.2f} на испорченных карточках, точность {prec:.2f} с учётом "
                f"ложных замечаний на {len(clean_cases)} верных",
            )
        )
    if latencies:
        lat = sorted(latencies)
        rows += [
            MetricRow(key="latency_p50", title="Задержка /autofill, медиана, мс", n=len(lat),
                      value=round(statistics.median(lat), 1), how=LATENCY_HOW),
            MetricRow(key="latency_p95", title="Задержка /autofill, 95-й перц., мс", n=len(lat),
                      value=round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 1),
                      how=LATENCY_HOW),
        ]  # fmt: skip
    sources = sorted({p.source for p in deps.catalog.all()})
    return MetricsOut(
        created_at=datetime.now(UTC),
        catalog_version=deps.catalog.version,
        catalog_source=", ".join(sources),
        model=model,
        rows=rows,
    )


_KIND_TITLE = {
    "not_in_list": "значение не из перечня",
    "typo": "опечатка в значении",
    "contradiction": "противоречие наименованию",
    "foreign_unit": "единица не той величины",
}
