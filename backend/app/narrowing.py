"""Пошаговое сужение кандидатов до конечного кода (Т2.4) и подсказки, когда код не определить
(Т2.5).

Известное значение характеристики (`Known`) приходит из текста поставщика или из ответа на вопрос.
Разница важна: позиция без характеристики, о которой спросили и на которую ответили, человеку не
подходит; а значение из текста могло относиться к характеристике, которой в позиции просто нет.
"""

from functools import lru_cache
from typing import Literal

import regex as re
import snowballstemmer
from pydantic import BaseModel, Field

from app.catalog import CharacteristicSpec, Position
from app.ingest.units import normalize_unit
from app.search import Candidate
from app.search.text import tokens
from app.values import COUNT_UNITS, fit_value, fold, quantity

STEP_NOT_FOUND = (
    "Категория не найдена в справочнике: уточните наименование или опишите товар подробнее"
)
_FITS = {"exact", "normalized", "bucket", "typo"}
# Вариант «Ни одна не подходит» есть в каждом вопросе: ответ им — честный отказ, а не ближайший код.
NOTHING_FITS = "__nothing__"
# Вопрос «Подтвердите позицию»: в наименовании есть значимые слова, которых нет в позиции.
CONFIRM = "Позиция КТРУ"
# Позиции КТРУ с одним названием и теми же характеристиками различаются единицей измерения товара
# («Молоко питьевое» в литрах и в килограммах, «Бумага туалетная» упаковками и штуками):
# её и спрашиваем, а число с единицей в тексте («1 л») отвечает само.
UNIT_QUESTION = "Единица измерения товара"


class Known(BaseModel):
    value: str
    unit: str | None = None
    source: Literal["text", "answer"]


class Option(BaseModel):
    value: str
    remaining: int
    cuts: int
    codes: list[str] = Field(
        default=[], description="коды позиций, которые останутся после этого ответа (первые три)"
    )
    label: str | None = Field(
        default=None,
        description="что показать в варианте вместо `value` (наименование КТРУ у варианта-кода); "
        "пусто — показывать `value`",
    )


class Question(BaseModel):
    characteristic: str
    options: list[Option]


class TrailStep(BaseModel):
    """Шаг воронки сужения: что сузило выбор и сколько позиций КТРУ осталось."""

    label: str = Field(
        description="«Все позиции КТРУ», «Тип: Тумбочка прикроватная», «Ширина: 450 мм»"
    )
    remaining: int


class KtruState(BaseModel):
    candidates: list[Candidate]
    remaining: int
    final_code: str | None
    question: Question | None
    steps: list[str]
    trail: list[TrailStep] = Field(
        default=[], description="воронка сужения по шагам, от всего справочника КТРУ до кода"
    )


# Имена характеристик позиции, свёрнутые один раз: сужение спрашивает их сотни тысяч раз
# (7 млн вызовов fold, 26 с на карточку). Позиция хранится рядом — id не переиспользуется.
_SPEC_INDEX: dict[int, tuple[Position, dict[str, CharacteristicSpec]]] = {}
_SPEC_INDEX_LIMIT = 50_000


def _spec_index(position: Position) -> dict[str, CharacteristicSpec]:
    hit = _SPEC_INDEX.get(id(position))
    if hit is not None and hit[0] is position:
        return hit[1]
    index: dict[str, CharacteristicSpec] = {}
    for c in position.characteristics:
        if c.group != "general":
            index.setdefault(fold(c.name), c)
    if len(_SPEC_INDEX) >= _SPEC_INDEX_LIMIT:
        _SPEC_INDEX.clear()
    _SPEC_INDEX[id(position)] = (position, index)
    return index


@lru_cache(maxsize=8192)
def _name_key(name: str) -> str:
    return fold(name)


def _spec(position: Position, name: str) -> CharacteristicSpec | None:
    """Характеристика позиции по имени; поля «Общих сведений» сужение не видит: их перечни —
    подсказки, а не признаки позиции."""
    return _spec_index(position).get(_name_key(name))


# Ответ «нет такой характеристики»: пустое значение. Оставляет позиции, у которых её нет.
NONE_VALUE = ""
# Ответ «другое значение»: значения, которые есть у всех кандидатов, выбор не сужают и вариантами
# не показываются; ими отвечают одним вариантом — позиции остаются все, модуль спросит другое.
OTHER_VALUE = "__other__"


def unit_label(position: Position) -> str:
    """«Литр; кубический дециметр» → «Литр»: так единицу позиции и показываем."""
    return (position.unit or "").split(";")[0].strip()


def _unit_kind(position: Position) -> str | None:
    """Величина единицы позиции (объём, масса); счётные (штука, упаковка) — None: «20 шт» в тексте
    про мешки в рулоне, а не про то, как считается позиция."""
    unit = normalize_unit(position.unit)
    if unit is None or unit in COUNT_UNITS:
        return None
    return quantity(unit)


_TEXT_QUANTITY = re.compile(r"(?<![\p{L}\p{N}])\d+(?:[.,]\d+)?\s*(?P<unit>[\p{L}][\p{L}0-9/.]*)")


def _text_kinds(text: str) -> set[str]:
    """Величины чисел с единицей в тексте: «1 л» — объём, «2,5 кг» — масса."""
    kinds = set()
    for m in _TEXT_QUANTITY.finditer(text):
        unit = normalize_unit(m["unit"].rstrip("."))
        if unit is not None and unit not in COUNT_UNITS and (kind := quantity(unit)):
            kinds.add(kind)
    return kinds


def _by_text_unit(positions: list[Position], text: str) -> list[Position]:
    """Позиции, различающиеся единицей величины, которую текст называет одну: «Молоко … 1 л» — из
    позиций в литрах и в килограммах остаются литровые. Текст молчит или называет обе — все."""
    kinds = {p.code: _unit_kind(p) for p in positions}
    present = {k for k in kinds.values() if k is not None}
    if len(present) < 2:
        return positions
    said = present & _text_kinds(text)
    if len(said) != 1:
        return positions
    return [p for p in positions if kinds[p.code] in said]


def _unit_question(positions: list[Position], known: dict[str, Known]) -> Question | None:
    """Позиции без различающих характеристик, но с разной единицей измерения — вопрос о ней."""
    if UNIT_QUESTION in known:
        return None
    groups: dict[str, list[Position]] = {}
    for p in positions:
        if label := unit_label(p):
            groups.setdefault(label, []).append(p)
    if len(groups) < 2:
        return None
    n = len(positions)
    options = [
        Option(value=label, remaining=len(ps), cuts=n - len(ps), codes=[p.code for p in ps[:3]])
        for label, ps in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]
    return Question(
        characteristic=UNIT_QUESTION,
        options=[*options, Option(value=NOTHING_FITS, remaining=0, cuts=n)],
    )


_STEMMER = snowballstemmer.stemmer("russian")


# Окончание, которым словоформы одного значения отличаются по длине («Смесовое» и «Смесовая» — 0,
# «Мужской» и «Мужские» — 0): длиннее — уже другое слово («Комплект» и «Комплектующие»).
MAX_ENDING = 2


@lru_cache(maxsize=65536)
def _form_words(value: str) -> tuple[tuple[str, str], ...]:
    words = re.findall(r"[\p{L}\p{N}]+|[<>=≤≥]+", fold(value))
    return tuple(zip(words, _STEMMER.stemWords(words), strict=True))


def form_key(value: str) -> str:
    """Основы слов значения: у «Смесовое» и «Смесовая» одна. Служебные слова («не», «без», «с») и
    знаки корзин остаются: «Не горючий» — не «Горючий», «>=100» — не «<100»."""
    return " ".join(s for _, s in _form_words(value))


@lru_cache(maxsize=65536)
def same_value_form(a: str, b: str) -> bool:
    """Одно значение в другом роде или числе: основы слов те же, а слова различаются только
    окончанием (не длиннее `MAX_ENDING`): «Агрос» и «Агросила» — разные значения."""
    wa, wb = _form_words(a), _form_words(b)
    return len(wa) == len(wb) and all(
        sa == sb and abs(len(x) - len(y)) <= MAX_ENDING
        for (x, sa), (y, sb) in zip(wa, wb, strict=True)
    )


def same_form(value: str, spec: CharacteristicSpec) -> str:
    """Значение перечня позиции в той же форме, что ответ: ответ «Смесовое» у позиции со
    «Смесовая» ложится в карточку как «Смесовая». Нет такого — сам ответ."""
    return next((v for v in spec.values if same_value_form(value, v)), value)


def _fits(position: Position, name: str, k: Known) -> bool:
    if name == UNIT_QUESTION:
        return k.value == OTHER_VALUE or fold(unit_label(position)) == fold(k.value)
    spec = _spec(position, name)
    if k.source == "answer" and k.value == OTHER_VALUE:
        return True
    if k.source == "answer" and k.value == NONE_VALUE:
        return spec is None
    if spec is None:
        return k.source == "text"
    status = fit_value(k.value, k.unit, spec).status
    if k.source == "answer" and status not in _FITS - {"typo"}:
        # Варианты, различающиеся родом или числом, склеены в один: ответ подходит всем им.
        return any(same_value_form(k.value, v) for v in spec.values)
    # Ответ выбран из вариантов модуля: опечатки в нём нет, «Желтый» — не «Белый» с ошибкой.
    return status in _FITS and not (k.source == "answer" and status == "typo")


def compatible(position: Position, known: dict[str, Known]) -> bool:
    return all(_fits(position, name, k) for name, k in known.items())


def narrow(positions: list[Position], known: dict[str, Known]) -> list[Position]:
    return [p for p in positions if compatible(p, known)]


def _options(positions: list[Position], name: str) -> list[str]:
    """Значения, которые можно предложить: перечни позиций, у которых характеристика есть."""
    seen: dict[str, list[str]] = {}  # основы → варианты; «Смесовое» и «Смесовая» — один
    out: list[str] = []
    for p in positions:
        spec = _spec(p, name)
        if spec is None:
            continue
        for v in spec.values:
            shown = seen.setdefault(form_key(v), [])
            if not any(same_value_form(s, v) for s in shown):
                shown.append(v)
                out.append(v)
    return out


def _has_level_define(positions: list[Position]) -> bool:
    return any(c.level_define for p in positions for c in p.characteristics)


def _known_defining(positions: list[Position], known: dict[str, Known]) -> int:
    names = {fold(c.name) for p in positions for c in p.characteristics if c.level_define}
    return sum(1 for name in known if fold(name) in names)


# Вопрос с перечнем длиннее этого задаётся, только когда коротких не осталось.
MAX_OPTIONS = 8


def _long(options: list[Option]) -> bool:
    return (
        sum(o.value not in (NONE_VALUE, OTHER_VALUE, NOTHING_FITS) for o in options) > MAX_OPTIONS
    )


def best_question(
    positions: list[Position],
    known: dict[str, Known],
    *,
    defining_only: bool | None = None,
) -> Question | None:
    """Характеристика, ответ на которую в среднем оставит меньше всего позиций.

    Порядок при равенстве: есть у всех кандидатов, меньше остаётся в среднем, обязательная,
    определяющая позицию, меньше вариантов, по имени. Перечни пересекаются («A4» есть у нескольких
    позиций), поэтому мера — средний остаток по вариантам, а не энтропия разбиения.

    `defining_only=True` — только `level_define`. `False` — только остальные, когда определяющие
    уже не различают оставшиеся позиции (тогда спрашиваем белизну, если у кандидатов она разная).
    """
    asked = {fold(n) for n in known}
    names: dict[str, str] = {}
    for p in positions:
        for c in p.characteristics:
            if c.group == "general" or fold(c.name) in asked:
                continue
            if defining_only is True and not c.level_define:
                continue
            if defining_only is False and c.level_define:
                continue
            names.setdefault(fold(c.name), c.name)
    n = len(positions)
    ranked = []
    for name in names.values():
        specs = [s for p in positions if (s := _spec(p, name)) is not None]
        everyone = len(specs) == n
        values = _options(positions, name) + ([] if everyone else [NONE_VALUE])
        options = []
        for value in values:
            kept = narrow(positions, {name: Known(value=value, source="answer")})
            left = len(kept)
            if left:
                codes = [p.code for p in kept[:3]]
                options.append(Option(value=value, remaining=left, cuts=n - left, codes=codes))
        if len(options) < 2 or all(o.remaining == n for o in options):
            continue  # вопрос ничего не отсекает или выбирать не из чего
        mean_left = sum(o.remaining for o in options) / len(options)
        cutting = [o for o in options if o.cuts]
        if len(cutting) < len(options):
            # Вариант, после которого остаются все позиции, — не вопрос: такие значения сводятся в
            # один «другое значение».
            options = [*cutting, Option(value=OTHER_VALUE, remaining=n, cuts=0)]
        if sum(o.value not in (NONE_VALUE, OTHER_VALUE) for o in options) <= 1:
            continue  # «ПВХ или нет такой характеристики» — выбирать не из чего
        required = any(s.required for s in specs)
        level = any(s.level_define for s in specs)
        # Длинный перечень проигрывает любому короткому: средний остаток у 72 цветов столешницы
        # самый маленький, но человек такой список не прочтёт.
        key = (_long(options), not everyone, mean_left, not required, not level, len(options), name)
        ranked.append((key, Question(characteristic=name, options=options)))
    if not ranked:
        return None
    question = min(ranked, key=lambda kq: kq[0])[1]
    question.options.append(Option(value=NOTHING_FITS, remaining=0, cuts=n))
    return question


class NearPosition(BaseModel):
    code: str
    name: str
    matched: list[str]


def not_goods_steps(kind: Literal["works", "services"]) -> list[str]:
    """Наименование похоже на работы или услуги: справочник модуля — товары."""
    what, branch = ("работы", "Работы") if kind == "works" else ("услугу", "Услуги")
    return [
        f"Похоже на {what}, а не на товар: такие наименования на портале — в ветке «{branch}» "
        "каталога, а справочник модуля — ветка «Товары».",
        "Если это товар — начните наименование с него самого («Мастика битумная кровельная», "
        "а не «Ремонт кровли»). Если работа или услуга — категорию выбирают в её ветке каталога, "
        "код КТРУ модуль не подставит.",
    ]


def not_found_steps(near: list[NearPosition], rejected: bool, head: str = "") -> list[str]:
    """Что делать, когда позиция не найдена: похожие позиции с совпавшими словами или что дописать.

    Код не подставляется: ближайшая позиция — подсказка человеку, а не ответ модуля.
    """
    if near and not rejected:
        listed = "; ".join(
            f"«{p.name}» ({p.code}) — совпало {', '.join(f'«{w}»' for w in p.matched)}"
            for p in near
        )
        return [
            STEP_NOT_FOUND,
            f"Похожие позиции справочника: {listed}. Если товар из них — назовите его словами "
            f"позиции (например, «{near[0].name}») и заполните карточку ещё раз; если нет — "
            "позиции для товара в справочнике нет, и код КТРУ модуль не подставит.",
        ]
    if head:
        return [
            STEP_NOT_FOUND,
            f"По слову «{head}» подходящей позиции в справочнике нет. Проверьте, так ли называется "
            "товар "
            "(синоним, полное название), и добавьте, из чего он и для чего. Если товара в "
            "справочнике нет, код КТРУ модуль не подставит: карточку проверит модератор.",
        ]
    return [
        STEP_NOT_FOUND,
        "Начните наименование с самого товара и добавьте, из чего он и для чего («Мешки для мусора "
        "ПНД 60 л»). Если товара в справочнике нет, код КТРУ модуль не подставит: карточку "
        "проверит модератор.",
    ]


_OPTION_TEXT = {NONE_VALUE: "нет такой характеристики", OTHER_VALUE: "другое значение"}


def question_note(question: Question, steps: list[str]) -> str:
    """Вопрос одной строкой для пакета: что ответить и сколько позиций останется."""
    if question.characteristic == CONFIRM:
        codes = [o.value for o in question.options if o.value != NOTHING_FITS]
        if len(codes) == 1:
            head = f"Подтвердите позицию КТРУ {codes[0]} или выберите «Ни одна не подходит»"
        else:
            head = f"Выберите позицию: {', '.join(codes)} или «Ни одна не подходит»"
    else:
        options = ", ".join(
            f"«{_OPTION_TEXT.get(o.value, o.value)}» (останется {o.remaining})"
            for o in question.options
            if o.value != NOTHING_FITS
        )
        head = f"Уточните «{question.characteristic}»: {options} или «Ни одна не подходит»"
    return "; ".join([head, *steps])


def _dead_end_step(positions: list[Position], known: dict[str, Known]) -> str:
    """Какое из известных значений отсекло последние позиции — его и предложить проверить."""
    for name, k in known.items():
        rest = {n: v for n, v in known.items() if n != name}
        if narrow(positions, rest):
            return f"Нет позиции с такими значениями: проверьте «{name}» = «{k.value}»"
    return "Нет позиции с такими значениями: проверьте введённые характеристики"


MAX_CHOICE = 6
NOTHING_TEXT = "Ни одна не подходит"
MIN_COMMON = 4


def _common_word(positions: list[Position]) -> bool:
    """Есть ли слово, общее для названий всех позиций («Бумага … белая» и «Бумага … цветная»)."""
    sets = [{t for t in tokens(p.name) if len(t) >= MIN_COMMON} for p in positions]
    return bool(set.intersection(*sets)) if sets else True


def _same_kind(positions: list[Position]) -> bool:
    """Один товар: первое слово названий одно («Куртки для взрослых…», «Куртки для детей…»); общее
    слово в середине («для взрослых» у курток и шапок) — не тот же товар."""
    heads = {words[0] for p in positions if (words := tokens(p.name))}
    return len(heads) <= 1


def _same_name(positions: list[Position]) -> bool:
    """Названия различаются только значениями характеристик («Бумага … белая» и «… цветная» —
    перечень «Цветность») — различают характеристики. Иначе («Столы письменные» и «Столы … под
    станки», «Футболки … для взрослых» и «… для детей») — разные категории одного товара: человек
    выберет свою сам."""
    names = [{t for t in tokens(p.name) if t.isalpha()} for p in positions]
    common: set[str] = names[0].intersection(*names[1:]) if names else set()
    differ = set[str]().union(*names) - common
    if not differ:
        return True
    values = {
        t
        for p in positions
        for c in p.characteristics
        if c.group != "general"
        for v in c.values
        for t in tokens(v)
    }
    # «бумажные» в названии — «Бумага» в перечне: основы совпадают первыми буквами.
    return all(any(t[:MIN_COMMON] == v[:MIN_COMMON] for v in values) for t in differ)


# В подписи варианта — не больше стольких различающих характеристик.
MAX_LABEL_DIFFS = 2


def _labels(positions: list[Position]) -> dict[str, str]:
    """Подпись варианта: наименование, а у одноимённых позиций — и чем они различаются («Стул
    детский …: Ростовая группа 2; Вид материала: Фанера»), иначе два одинаковых варианта."""
    names = [p.name for p in positions]
    out = {}
    for p in positions:
        if names.count(p.name) < 2:
            out[p.code] = p.name
            continue
        twins = [q for q in positions if q.name == p.name and q.code != p.code]
        diffs = []
        for c in p.characteristics:
            if c.group == "general" or not c.values:
                continue
            others = [_spec(q, c.name) for q in twins]
            mine = {fold(v) for v in c.values}
            if any(o is None or {fold(v) for v in o.values} != mine for o in others):
                diffs.append(f"{c.name}: {', '.join(c.values[:3])}")
            if len(diffs) == MAX_LABEL_DIFFS:
                break
        out[p.code] = f"{p.name} — {'; '.join(diffs)}" if diffs else f"{p.name} ({p.code})"
    return out


def fix_hint(positions: list[Position]) -> str | None:
    """Конкретный шаг для поставщика, когда по тексту позицию не выбрать: какую характеристику
    дописать в описание и с каким значением, чтобы осталась одна («Тип по форме рабочей части:
    Плоская» есть только у одной позиции)."""
    for spec in positions[0].characteristics if positions else []:
        if spec.group == "general" or not spec.values:
            continue
        sets = [
            {fold(v): v for v in s.values} if (s := _spec(p, spec.name)) is not None else {}
            for p in positions
        ]
        if all(set(s) == set(sets[0]) for s in sets):
            continue
        rare = next(
            (v for s in sets for k, v in s.items() if sum(k in o for o in sets) < len(sets)), None
        )
        if rare is not None:
            return (
                f"Позиции различаются характеристикой «{spec.name}». Чтобы модуль выбрал сам, "
                f"допишите её в описание, например: «{spec.name}: {rare}»."
            )
    return None


def _choice_question(positions: list[Position]) -> Question:
    labels = _labels(positions)
    options = [
        Option(
            value=p.code, remaining=1, cuts=len(positions) - 1, codes=[p.code], label=labels[p.code]
        )
        for p in positions
    ]
    return Question(
        characteristic=CONFIRM,
        options=[*options, Option(value=NOTHING_FITS, remaining=0, cuts=len(positions))],
    )


def confirm_question(position: Position) -> Question:
    return _confirm_question(position)


def _confirm_question(position: Position) -> Question:
    return Question(
        characteristic=CONFIRM,
        options=[
            Option(
                value=position.code, remaining=1, cuts=0, codes=[position.code], label=position.name
            ),
            Option(value=NOTHING_FITS, remaining=0, cuts=1),
        ],
    )


def weak_step(word: str) -> str:
    return (
        f"Позиция подобрана только по слову «{word}»: другие слова наименования в ней не "
        "встречаются. Подтвердите позицию или выберите «Ни одна не подходит»."
    )


def _next_question(positions: list[Position], known: dict[str, Known]) -> Question | None:
    """Сначала определяющая характеристику, потом любая, которая ещё режет список.

    Число записей в справочнике не выбирает значение: 500 листов не побеждают 250 только потому,
    что таких позиций больше.
    """
    if _has_level_define(positions):
        defining = best_question(positions, known, defining_only=True)
        if defining is not None and not _long(defining.options):
            return defining
        other = best_question(positions, known, defining_only=False)
        if defining is not None and (other is None or _long(other.options)):
            return defining  # короткого нет и среди остальных: длинный определяющий лучше
        return other
    return best_question(positions, known)


def unexplained_step(words: list[str]) -> str:
    listed = ", ".join(f"«{w}»" for w in words)
    return (
        f"В наименовании есть слова, которых нет в подобранных позициях: {listed}. "
        "Подтвердите позицию или выберите «Ни одна не подходит»."
    )


def build_state(
    candidates: list[Candidate],
    positions: list[Position],
    known: dict[str, Known],
    *,
    unexplained: list[str] | None = None,
    confirmed: str | None = None,
    rejected: bool = False,
    weak_match: str | None = None,
    text: str = "",
) -> KtruState:
    """Состояние сужения.

    Итоговый код — авторитетный результат: если в наименовании остались слова, которых нет ни в
    одной позиции-кандидате (`unexplained`), код ставится только после подтверждения человеком.
    """
    if not positions or rejected:
        return KtruState(
            candidates=[], remaining=0, final_code=None, question=None, steps=[STEP_NOT_FOUND]
        )
    left = narrow(positions, known)
    if confirmed is not None and (chosen := [p for p in left if p.code == confirmed]):
        left = chosen
    if len(left) > 1 and UNIT_QUESTION not in known:
        # Текст не выбирает значение характеристики, которого в нём нет, но число с единицей
        # («1 л») говорит, в чём считается товар.
        left = _by_text_unit(left, text)
    codes = {p.code for p in left}
    kept = [c for c in candidates if c.code in codes]
    if not left:
        step = _dead_end_step(positions, known)
        return KtruState(candidates=[], remaining=0, final_code=None, question=None, steps=[step])
    doubt = [unexplained_step(unexplained)] if unexplained else []
    if weak_match:
        doubt.insert(0, weak_step(weak_match))
    defined = _known_defining(left, known)
    if len(left) == 1:
        # Одно слово вроде «принтера» не отменяет код, если определяющие характеристики уже названы.
        # Пока их мало, незнакомое слово по-прежнему требует подтверждения.
        if doubt and confirmed != left[0].code and defined < 2:
            return KtruState(
                candidates=kept,
                remaining=1,
                final_code=None,
                question=_confirm_question(left[0]),
                steps=doubt,
            )
        return KtruState(
            candidates=kept, remaining=1, final_code=left[0].code, question=None, steps=[]
        )
    steps = list(doubt)
    other_kind = not _common_word(left) or not _same_kind(left)
    if other_kind or (len(left) <= MAX_CHOICE and not _same_name(left)):
        # Разные товары («Аккумуляторы» и «Шины», «Куртки…» и «Шапки…»): вопрос по характеристике
        # одного из них («Подкладка», «Описание») человек не поймёт — пусть выберет позицию из
        # первых по рангу поиска. Так же — несколько позиций с разными названиями одного товара.
        order = {c.code: i for i, c in enumerate(candidates)}
        shown = sorted(left, key=lambda p: order.get(p.code, len(order)))[:MAX_CHOICE]
        what = "разных товаров" if other_kind else "с разными названиями"
        steps.append(f"Подошли позиции {what}: выберите свою или «{NOTHING_TEXT}».")
        if not other_kind and (hint := fix_hint(shown)):
            steps.append(hint)
        return KtruState(
            candidates=kept,
            remaining=len(left),
            final_code=None,
            question=_choice_question(shown),
            steps=steps,
        )
    question = _next_question(left, known) or _unit_question(left, known)
    if question is None:
        if len(left) <= MAX_CHOICE:
            steps.append(
                "Подошли позиции без различающих характеристик: выберите свою или "
                f"«{NOTHING_TEXT}»."
            )
            if hint := fix_hint(left):
                steps.append(hint)
            return KtruState(
                candidates=kept, remaining=len(left), final_code=None,
                question=_choice_question(left), steps=steps,
            )  # fmt: skip
        listed = "; ".join(f"{p.code} ({p.name})" for p in left)
        steps.append(f"Выберите позицию вручную: {listed}")
    return KtruState(
        candidates=kept, remaining=len(left), final_code=None, question=question, steps=steps
    )
