"""Почти одинаковые товары: 100 позиций, которые различаются одним признаком.

Блокер команды 06.10: «сможет ли система выбрать верный товар среди почти одинаковых», «каталог
из 100 товаров, отличающихся лишь по одному признаку — толщине». Каталог синтетический и строится
кодом (в git, файлов данных нет): нитриловые перчатки с одинаковыми материалом и размерами и
толщиной от 0,060 до 0,159 мм с шагом 0,001, рядом 20 других товаров. Мера:

- толщина в тексте в разной записи («0,075 мм», «75 мкм», «0.075мм») — модуль ставит ровно эту
  позицию, без вопроса и без чужой соседней;
- толщины в тексте нет — модуль не угадывает, а спрашивает о толщине.
"""

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.catalog import Catalog, CharacteristicSpec, Position

if TYPE_CHECKING:
    from app.evaluation.metrics import MetricRow

NAME = "Перчатки смотровые нитриловые"
THICKNESS = "Толщина"
N = 100


def thickness(i: int) -> float:
    return round(0.060 + i * 0.001, 3)


def code(i: int) -> str:
    return f"NEAR-{i:03d}"


def _mm(t: float) -> str:
    return f"{t:.3f}".replace(".", ",")


def near_catalog(n: int = N) -> Catalog:
    positions = [
        Position(
            code=code(i),
            name=NAME,
            okpd2="22.19.60.119",
            characteristics=[
                CharacteristicSpec(
                    name="Материал", kind="qualitative", values=["Нитрил"], required=True
                ),
                CharacteristicSpec(name="Размер", kind="qualitative", values=["S", "M", "L", "XL"]),
                CharacteristicSpec(
                    name=THICKNESS,
                    kind="quantitative",
                    unit="мм",
                    values=[_mm(thickness(i))],
                    required=True,
                ),
            ],
            source="near_twins",
        )
        for i in range(n)
    ]
    # Другие товары справочника: без них у слов «перчатки», «нитриловые» нулевой вес (они в каждой
    # позиции) и поиск не отличил бы перчатки от чего угодно; в настоящем справочнике так не бывает.
    positions += [
        Position(
            code=f"OTHER-{k:02d}",
            name=other,
            okpd2="00.00.00.000",
            characteristics=[
                CharacteristicSpec(name="Цвет", kind="qualitative", values=["Белый", "Синий"])
            ],
            source="near_twins",
        )
        for k, other in enumerate(OTHERS)
    ]
    return Catalog(positions, version="near_twins")


OTHERS = [
    "Бумага для офисной техники",
    "Ручка шариковая",
    "Батарейка алкалиновая",
    "Маска медицинская",
    "Халат медицинский",
    "Бахилы одноразовые",
    "Салфетки бумажные",
    "Мыло жидкое",
    "Картридж лазерный",
    "Папка-регистратор",
    "Скрепки канцелярские",
    "Степлер",
    "Клей-карандаш",
    "Тетрадь школьная",
    "Карандаш чернографитный",
    "Мешки для мусора",
    "Полотенца бумажные",
    "Губка для посуды",
    "Перчатки хозяйственные резиновые",
    "Перчатки рабочие трикотажные",
]


@dataclass(frozen=True)
class NearCase:
    text: str
    expected: str | None  # None — толщины нет в тексте, ждём вопрос о ней


def near_cases(n: int = N) -> list[NearCase]:
    out = []
    for i in range(n):
        t = thickness(i)
        out += [
            NearCase(f"{NAME}, размер M, толщина {_mm(t)} мм", code(i)),
            NearCase(f"{NAME} размер L толщина {round(t * 1000)} мкм", code(i)),
            NearCase(f"{NAME}, толщиной {t:.3f}мм, размер S", code(i)),
        ]
    out.append(NearCase(f"{NAME}, размер M", None))
    return out


def score(case: NearCase, final_code: str | None, question: str | None) -> Counter[str]:
    c: Counter[str] = Counter(cases=1)
    if case.expected is None:
        c["no_value"] += 1
        c["no_value_asked"] += final_code is None and question == THICKNESS
        return c
    c["with_value"] += 1
    c["right"] += final_code == case.expected
    c["wrong"] += final_code is not None and final_code != case.expected
    return c


async def near_rows(llm: Any) -> list["MetricRow"]:
    """Строки «Метрик»: 300 текстов с толщиной и один без неё на каталоге `near_catalog`."""
    from app.evaluation.metrics import MetricRow
    from app.glossary import Glossary
    from app.pipeline import AutofillIn, Deps, autofill
    from app.search import build_bm25
    from app.session import MemoryKV, SessionStore

    catalog = near_catalog()
    deps = Deps(
        catalog=catalog,
        bm25=build_bm25(catalog),
        vectors=None,
        llm=llm,
        glossary=Glossary([]),
        sessions=SessionStore(MemoryKV(), ttl_s=600),  # type: ignore[arg-type]
        min_score=0.2,
        llm_paused_until=float("inf"),  # мера — поиск и сужение, не модель
    )
    c: Counter[str] = Counter()
    for case in near_cases():
        out = await autofill(AutofillIn(name=case.text), deps)
        q = out.ktru.question.characteristic if out.ktru.question else None
        c += score(case, out.ktru.final_code, q)
    how = (f"{N} позиций «{NAME}» различаются только толщиной; толщина в тексте в трёх записях "
           "(«0,075 мм», «75 мкм», «0.075мм»)")  # fmt: skip
    return [
        MetricRow(key="lookalike_right", title="Почти одинаковые товары: верная позиция",
                  value=round(c["right"] / c["with_value"], 4), n=c["with_value"], how=how),
        MetricRow(key="lookalike_wrong", title="Почти одинаковые товары: соседняя вместо верной",
                  value=round(c["wrong"] / c["with_value"], 4), n=c["with_value"], how=how),
        MetricRow(key="lookalike_asked", title="Толщины нет в тексте — вопрос о ней, а не догадка",
                  value=round(c["no_value_asked"] / c["no_value"], 4), n=c["no_value"],
                  how="тот же каталог, текст без толщины: верно — вопрос о толщине без кода"),
    ]  # fmt: skip
