"""Золотой набор для оценки (Т5): описания с известным ответом, собранные из справочника.

Шаблонные группы: обычные описания одним шаблоном «Имя характеристики, ед. значение» (`clean`), они
же с опечаткой (`typo`), товары вне справочника и рядом с ним, карточки с внесённой ошибкой для
детектора. Порченые значения «Интенсивности цвета» взяты из фильтров каталога портала
(data/catalog/portal_filters_paper.md): так ошибаются настоящие поставщики.

Шаблон — ровно тот формат, который понимают правила извлечения, поэтому метрики на нём меряют
согласованность генератора с правилами. Группы «реальных форматов» (`REAL_GROUPS`) пишут так, как
пишут поставщики, и генератор их под правила не подгоняет:
- `prose` — одна строка через запятую, метка строчными, единица после числа, иногда
  «метка: значение», иногда значение перечня без метки;
- `unit` — одна характеристика в другой единице той же величины («60000 мл» при позиции в литрах);
- `synonym` — метка другим словом («Вес», «Вместимость», «Кол-во»);
- `ambiguous` — только общие первые слова наименования нескольких позиций: код выбирается вопросами;
- `noisy` — часть характеристик не указана, в тексте бренд, артикул, цена, штрихкод.
Эталон каждого кейса — только то, что есть в тексте; остальное поставщик знает (`answers`) и
отвечает этим на вопросы модуля.
"""

import random
from collections import Counter
from typing import Literal

from pydantic import BaseModel

from app.catalog import Catalog, CharacteristicSpec, Position
from app.ingest.characteristics import to_number
from app.values import convert, fold, parse_bucket

Group = Literal[
    "clean",
    "typo",
    "out_of_catalog",
    "near_domain",
    "corrupted",
    "prose",
    "unit",
    "synonym",
    "ambiguous",
    "noisy",
]
Corruption = Literal["not_in_list", "foreign_unit", "contradiction", "typo"]
TEMPLATE_GROUPS: tuple[Group, ...] = ("clean", "typo")
REAL_GROUPS: tuple[Group, ...] = ("prose", "unit", "synonym", "ambiguous", "noisy")

# Товары, которых в справочнике нет: честный ответ — «категория не найдена».
OUT_OF_CATALOG = [
    "Услуги по уборке помещений",
    "Ноутбук игровой 16 ГБ, SSD 512 ГБ",
    "Автомобиль легковой седан",
    "Кофе в зёрнах арабика 1 кг",
    "Стол письменный с тумбой",
    "Огнетушитель порошковый ОП-4",
    "Шины зимние 205/55 R16",
    "Саженцы туи западной",
    "Подписка на журнал на год",
    "Ремонт кровли",
    "Микроскоп лабораторный бинокулярный",
    "Стул офисный на колёсах",
]

# Товары рядом со справочником, но не из него: общее слово с позицией есть, позиции нет.
# Реальные строки портала (data/portal/skus.jsonl) и близкие к демо-справочнику. Честный ответ —
# без итогового кода: отказ или вопрос с подтверждением.
NEAR_DOMAIN = [
    "Белая глина 1 кг",
    "Куприн. Белый пудель",
    "Кожа натуральная Наппа, белый",
    "Бумага туалетная 4 рулона",
    "Бумага наждачная Р120",
    "Шланг высокого давления 1SN",
    "Стержень гелевый синий",
    "Маркер перманентный черный",
    "Перчатки латексные хозяйственные",
    "Папка уголок А4",
    "Папка на резинке А4",
    "Ручка дверная",
    "Средство для мытья посуды 500 мл",
    "Картридж для смесителя 40 мм",
    "Пакет для заморозки продуктов",
]

# Введённые поставщиками значения «Интенсивности цвета» (фильтры каталога портала).
PORTAL_INTENSITY_ERRORS = ["Белый", "Пастель+интенсив", "Голубой медиум", "Лимонный"]

# Другое слово вместо первого слова имени характеристики. «Вес», «Вместимость», «Ёмкость»,
# «Плотность» — основы из NAME_ALIASES правил модерации (так их пишут в наименованиях), «Кол-во» и
# «Число» — обычные сокращения в карточках портала.
LABEL_SYNONYMS = {
    "масса": ["Вес", "Плотность"],
    "объем": ["Вместимость", "Емкость"],
    "количество": ["Кол-во", "Число"],
}

# Другая единица той же величины, в которой поставщик мог написать значение. Пересчёт — через
# `values.convert`; счётные единицы («шт» ↔ «лист») сюда не входят: это не другая единица.
ALT_UNITS = {
    "л": "мл",
    "мл": "л",
    "мкм": "мм",
    "мм": "см",
    "см": "мм",
    "г": "кг",
    "кг": "г",
    "т": "кг",
    "мг": "г",
    "Вт": "кВт",
    "кВт": "Вт",
    "МБ": "ГБ",
    "ГБ": "ТБ",
    "ТБ": "ГБ",
    "мА·ч": "А·ч",
    "А·ч": "мА·ч",
}

# Лишнее в тексте поставщика: бренд в наименовании, реквизиты товара в описании.
BRANDS = ["ТМ «Северная звезда»", "«Офис-Профи»", "«Чистый мир»", "«Лидер»"]
_PACK_WORDS = ("упаков", "пачк", "набор", "комплект")


class GoldenCase(BaseModel):
    id: str
    group: Group
    name: str
    description: str = ""
    expected_code: str | None = None
    # Характеристика → ожидаемое значение после подгонки к перечню (корзина, значение перечня).
    expected: dict[str, str] = {}
    # Что поставщик знает о товаре, но в тексте не написал: этим эталон отвечает на вопросы модуля;
    # в полноту заполнения не входит.
    answers: dict[str, str] = {}
    # Для группы corrupted: какая характеристика испорчена и как; карточка — в characteristics.
    corruption: Corruption | None = None
    corrupted_field: str | None = None
    characteristics: list[tuple[str, str, str | None]] = []


def _fmt(x: float) -> str:
    return str(int(x)) if x == int(x) else f"{x:g}".replace(".", ",")


def _sample_value(spec: CharacteristicSpec, rnd: random.Random) -> tuple[str, str] | None:
    """Значение для текста и ожидаемое значение после подгонки."""
    if spec.kind == "qualitative":
        if not spec.values:
            return None
        v = rnd.choice(spec.values)
        return v, v
    if spec.values:
        option = rnd.choice(spec.values)
        b = parse_bucket(option)
        if b is None:
            return None
        if b.is_point and b.lo is not None:
            return option, option
        lo = b.lo if b.lo is not None else (b.hi or 1) / 2
        hi = b.hi if b.hi is not None else lo * 1.2 + 1
        x: float = round(lo + (hi - lo) / 3)
        if not b.contains(x):
            x = lo if b.lo_incl else lo + 1
        return _fmt(x), option
    if spec.range is not None and spec.range.min is not None and spec.range.max is not None:
        x = rnd.randint(int(spec.range.min), int(spec.range.max))
        return _fmt(x), _fmt(x)
    return None


def _line(spec: CharacteristicSpec, text_value: str) -> str:
    unit = f", {spec.unit}" if spec.unit else ""
    return f"{spec.name}{unit} {text_value}"


def _typo(word: str, rnd: random.Random) -> str:
    """Перестановка двух соседних букв в середине слова."""
    i = rnd.randrange(1, len(word) - 2)
    return word[:i] + word[i + 1] + word[i] + word[i + 2 :]


def _short_name(p: Position, values: dict[str, str]) -> str:
    head = " ".join(p.name.split()[:2])
    defining = [values[c.name] for c in p.characteristics if c.level_define and c.name in values]
    return " ".join([head, *defining])


_PREPOSITIONS = {"для", "из", "с", "со", "без", "на", "по", "в"}


def _real_name(p: Position, values: dict[str, str]) -> str:
    """Как `_short_name`, но число в наименовании — с единицей, а предлог не обрывает товар:
    «Мешки для мусора 120 л», не «Мешки для 120»."""
    words = p.name.split()
    head = " ".join(words[: 3 if len(words) > 2 and words[1].lower() in _PREPOSITIONS else 2])
    defining = [
        f"{values[c.name]} {c.unit}" if c.kind == "quantitative" and c.unit else values[c.name]
        for c in p.characteristics
        if c.level_define and c.name in values
    ]
    return " ".join([head, *defining])


def _clean_case(p: Position, rnd: random.Random, idx: int) -> GoldenCase:
    text_values: dict[str, str] = {}
    expected: dict[str, str] = {}
    lines = []
    for spec in p.characteristics:
        sample = _sample_value(spec, rnd)
        if sample is None:
            continue
        text_values[spec.name], expected[spec.name] = sample
        lines.append(_line(spec, sample[0]))
    rnd.shuffle(lines)
    return GoldenCase(
        id=f"clean-{p.code}-{idx}",
        group="clean",
        name=_short_name(p, text_values),
        description="\n".join(lines),
        expected_code=p.code,
        expected=expected,
    )


def _typo_case(clean: GoldenCase, p: Position, rnd: random.Random) -> GoldenCase | None:
    long_values = [
        v
        for c in p.characteristics
        if c.kind == "qualitative" and (v := clean.expected.get(c.name)) and len(v) >= 6
    ]
    if not long_values:
        return None
    target = rnd.choice(long_values)
    spoiled = _typo(target, rnd)
    return clean.model_copy(
        update={
            "id": clean.id.replace("clean", "typo"),
            "group": "typo",
            "description": clean.description.replace(target, spoiled),
        }
    )


def _corrupted(p: Position, clean: GoldenCase, catalog: Catalog) -> list[GoldenCase]:
    """Карточка с одной ошибкой на вид порчи; остальные характеристики верные."""
    base = [(name, value, next((c.unit for c in p.characteristics if c.name == name), None))
            for name, value in clean.expected.items()]  # fmt: skip
    out: list[GoldenCase] = []

    def card(kind: Corruption, field: str, value: str, unit: str | None, name: str) -> None:
        chars = [(n, value if n == field else v, unit if n == field else u) for n, v, u in base]
        out.append(
            GoldenCase(
                id=f"corrupted-{kind}-{p.code}-{len(out)}",
                group="corrupted",
                name=name,
                expected_code=p.code,
                corruption=kind,
                corrupted_field=field,
                characteristics=chars,
            )
        )

    qualitative = [c for c in p.characteristics if c.kind == "qualitative" and c.values]
    numeric = [c for c in p.characteristics if c.kind == "quantitative" and c.unit]
    if qualitative:
        spec = qualitative[0]
        foreign = [v for q in catalog.all() for c in q.characteristics if c.kind == "qualitative"
                   for v in c.values if v not in spec.values and len(v) > 3]  # fmt: skip
        if spec.name == "Интенсивность цвета":
            foreign = PORTAL_INTENSITY_ERRORS + foreign
        if foreign:
            card("not_in_list", spec.name, foreign[len(out) % len(foreign)], None, clean.name)
        long_values = [v for v in spec.values if len(v) >= 6]
        if long_values:
            v = long_values[0]
            card("typo", spec.name, v[:2] + v[3] + v[2] + v[4:], None, clean.name)
        if len(spec.values) >= 2:
            right, other = spec.values[0], spec.values[1]
            # Верного значения в наименовании быть не должно: иначе случай честно неоднозначен.
            base_name = " ".join(w for w in clean.name.split() if fold(w) != fold(right))
            card("contradiction", spec.name, right, None, f"{base_name} {other.lower()}")
    if numeric:
        spec = numeric[0]
        value = clean.expected.get(spec.name, "1")
        number = to_number(value)
        shown = value if number is not None else "10"
        foreign_unit = "кг" if spec.unit not in ("кг", "г", "т", "мг") else "л"
        card("foreign_unit", spec.name, shown, foreign_unit, clean.name)
    return out


def _sample_all(p: Position, rnd: random.Random) -> tuple[dict[str, str], dict[str, str]]:
    """Значения для текста и эталон по всем характеристикам позиции, где значение можно выбрать."""
    text_values: dict[str, str] = {}
    expected: dict[str, str] = {}
    for spec in p.characteristics:
        if (sample := _sample_value(spec, rnd)) is not None:
            text_values[spec.name], expected[spec.name] = sample
    return text_values, expected


def _prose_label(spec: CharacteristicSpec) -> str:
    """Как метку пишут в строке: до второго значимого слова, с маленькой буквы («масса бумаги»)."""
    out: list[str] = []
    content = 0
    for w in spec.name.split():
        out.append(w)
        content += len(w) > 2
        if content == 2:
            break
    label = " ".join(out)
    return label[:1].lower() + label[1:]


def _prose_unit(unit: str) -> str:
    return "листов" if unit == "лист" else unit


def _bare_ok(p: Position, spec: CharacteristicSpec, value: str) -> bool:
    """Значение без метки однозначно: оно есть в перечне только одной характеристики позиции."""
    if value in ("Да", "Нет") or len(value) < 2:  # «C» одной буквой без метки никто не пишет
        return False
    owners = [c for c in p.characteristics if fold(value) in {fold(v) for v in c.values}]
    return owners == [spec]


def _as_written(value: str) -> str:
    """Значение перечня, как его пишут в строке: аббревиатуры и коды как есть, слова строчными."""
    keep = len(value) <= 3 or value.isupper() or any(ch.isdigit() for ch in value)
    return value if keep else value.lower()


def _prose_part(p: Position, spec: CharacteristicSpec, text_value: str, rnd: random.Random) -> str:
    label = _prose_label(spec) if spec.kind == "quantitative" else spec.name.lower()
    if spec.kind == "qualitative":
        shown = _as_written(text_value)
        if text_value in ("Да", "Нет"):
            return f"{label}: {text_value.lower()}"
        forms = [f"{label} {shown}", f"{label}: {shown}"]
        if _bare_ok(p, spec, text_value):
            forms.append(shown)
        return rnd.choice(forms)
    if not spec.unit:
        number = text_value
    elif spec.unit == "%":
        number = f"{text_value}%"
    else:
        number = f"{text_value} {_prose_unit(spec.unit)}"
    return rnd.choice([f"{label} {number}", f"{label}: {number}"])


def _prose_case(p: Position, rnd: random.Random, idx: int) -> GoldenCase:
    text_values, expected = _sample_all(p, rnd)
    parts = [
        _prose_part(p, spec, text_values[spec.name], rnd)
        for spec in p.characteristics
        if spec.name in text_values
    ]
    rnd.shuffle(parts)
    return GoldenCase(
        id=f"prose-{p.code}-{idx}",
        group="prose",
        name=_real_name(p, text_values),
        description=", ".join(parts),
        expected_code=p.code,
        expected=expected,
    )


def _fmt_converted(x: float) -> str:
    return _fmt(round(x, 6))


def _unit_case(p: Position, rnd: random.Random, idx: int) -> GoldenCase | None:
    text_values, expected = _sample_all(p, rnd)
    convertible = []
    for spec in p.characteristics:
        alt = ALT_UNITS.get(spec.unit or "")
        number = to_number(text_values.get(spec.name, ""))
        if alt is None or number is None or spec.unit is None:
            continue
        if (converted := convert(number, spec.unit, alt)) is not None:
            convertible.append((spec, alt, _fmt_converted(converted)))
    if not convertible:
        return None
    target, alt, shown = rnd.choice(convertible)
    lines = []
    for spec in p.characteristics:
        if spec.name not in text_values:
            continue
        if spec is target:
            lines.append(rnd.choice([f"{spec.name}, {alt} {shown}", f"{spec.name} {shown} {alt}"]))
        else:
            lines.append(_line(spec, text_values[spec.name]))
    rnd.shuffle(lines)
    return GoldenCase(
        id=f"unit-{p.code}-{idx}",
        group="unit",
        name=_real_name(p, text_values),
        description="\n".join(lines),
        expected_code=p.code,
        expected=expected,
    )


def _synonym_label(spec: CharacteristicSpec, rnd: random.Random) -> str | None:
    words = spec.name.split()
    options = LABEL_SYNONYMS.get(fold(words[0])) if words else None
    if not options:
        return None
    rest = _prose_label(spec).split()[1:]
    return " ".join([rnd.choice(options), *rest])


def _synonym_case(p: Position, rnd: random.Random, idx: int) -> GoldenCase | None:
    text_values, expected = _sample_all(p, rnd)
    lines = []
    renamed = 0
    for spec in p.characteristics:
        if spec.name not in text_values:
            continue
        label = _synonym_label(spec, rnd)
        renamed += label is not None
        unit = f", {spec.unit}" if spec.unit else ""
        lines.append(f"{label or spec.name}{unit} {text_values[spec.name]}")
    if not renamed:
        return None
    rnd.shuffle(lines)
    return GoldenCase(
        id=f"synonym-{p.code}-{idx}",
        group="synonym",
        name=_real_name(p, text_values),
        description="\n".join(lines),
        expected_code=p.code,
        expected=expected,
    )


def _head(p: Position, k: int) -> str:
    words = p.name.split()
    if 1 < k < len(words) and words[k - 1].lower() in _PREPOSITIONS:
        k += 1  # «Мешки для мусора», а не «Мешки для»
    return " ".join(words[:k])


def ambiguous_heads(catalog: Catalog) -> dict[str, tuple[str, list[Position]]]:
    """Код позиции → общее начало наименования (2 слова, иначе 1) и позиции с тем же началом.

    Позиции без такого начала (единственные в своём роде) в словарь не попадают.
    """
    positions = catalog.all()
    out: dict[str, tuple[str, list[Position]]] = {}
    for k in (2, 1):
        counts = Counter(fold(_head(p, k)) for p in positions)
        for p in positions:
            if p.code in out or counts[fold(_head(p, k))] < 2:
                continue
            group = [q for q in positions if fold(_head(q, k)) == fold(_head(p, k))]
            out[p.code] = (_head(p, k), group)
    return out


def fits_spec(spec: CharacteristicSpec, value: str) -> bool:
    """Эталонное значение допустимо для характеристики: из перечня или внутри диапазона."""
    if spec.values:
        return fold(value) in {fold(v) for v in spec.values}
    number = to_number(value)
    r = spec.range
    if number is None or r is None:
        return False
    return (r.min is None or number >= r.min) and (r.max is None or number <= r.max)


def _shared(spec: CharacteristicSpec, value: str, group: list[Position]) -> bool:
    """Характеристика с этим значением есть у всех позиций группы: она их не различает."""
    for q in group:
        other = next((c for c in q.characteristics if fold(c.name) == fold(spec.name)), None)
        if other is None or other.unit != spec.unit or not fits_spec(other, value):
            return False
    return True


def _ambiguous_cases(
    p: Position, head: str, group: list[Position], rnd: random.Random
) -> list[GoldenCase]:
    text_values, values = _sample_all(p, rnd)
    shared = [
        spec
        for spec in p.characteristics
        if not spec.level_define and spec.name in values and _shared(spec, values[spec.name], group)
    ]
    variants: list[list[CharacteristicSpec]] = [[]]
    if shared:
        variants.append(rnd.sample(shared, min(2, len(shared))))
    out = []
    for i, described in enumerate(variants):
        expected = {s.name: values[s.name] for s in described}
        lines = [_line(s, text_values[s.name]) for s in described]
        out.append(
            GoldenCase(
                id=f"ambiguous-{p.code}-{i}",
                group="ambiguous",
                name=head,
                description="\n".join(lines),
                expected_code=p.code,
                expected=expected,
                answers={k: v for k, v in values.items() if k not in expected},
            )
        )
    return out


def _noise(p: Position, rnd: random.Random) -> list[str]:
    phrases = [
        f"Артикул {rnd.randint(10000, 99999)}-{rnd.choice('АБВГК')}",
        f"Цена {rnd.randint(50, 3000)} руб.",
        "Страна происхождения: Россия",
        f"Гарантия {rnd.choice([6, 12, 24])} мес.",
        f"Штрихкод 46{rnd.randint(10**10, 10**11 - 1)}",
    ]
    # «В упаковке 10 шт» — шум, только если у позиции нет такой характеристики: иначе это данные.
    if not any(w in fold(c.name) for c in p.characteristics for w in _PACK_WORDS):
        phrases.append(f"В упаковке {rnd.choice([5, 10, 20])} шт")
    return rnd.sample(phrases, rnd.randint(2, 3))


def _noisy_case(p: Position, rnd: random.Random, idx: int) -> GoldenCase:
    text_values, values = _sample_all(p, rnd)
    optional = [s for s in p.characteristics if not s.level_define and s.name in values]
    kept = rnd.sample(optional, max(1, len(optional) // 2)) if optional else []
    shown = {s.name for s in kept} | {
        s.name for s in p.characteristics if s.level_define and s.name in values
    }
    lines = [_line(s, text_values[s.name]) for s in kept] + _noise(p, rnd)
    rnd.shuffle(lines)
    return GoldenCase(
        id=f"noisy-{p.code}-{idx}",
        group="noisy",
        name=f"{_real_name(p, text_values)} {rnd.choice(BRANDS)}",
        description=rnd.choice(["\n", "; "]).join(lines),
        expected_code=p.code,
        expected={k: v for k, v in values.items() if k in shown},
        answers={k: v for k, v in values.items() if k not in shown},
    )


def _characteristics_only(catalog: Catalog) -> Catalog:
    """Справочник без «Общих сведений»: эталон заполнения и порча — только по характеристикам.

    Производитель, модель и страна — поля карточки: детектор их перечни не проверяет (это
    подсказки), а модуль берёт их из наименования только целым значением перечня.
    """
    positions = [
        p.model_copy(
            update={"characteristics": [c for c in p.characteristics if c.group != "general"]}
        )
        for p in catalog.all()
    ]
    return Catalog(positions, catalog.version)


def build_real_formats(catalog: Catalog, seed: int = 42, per_position: int = 2) -> list[GoldenCase]:
    """Группы реальных форматов (`REAL_GROUPS`) — своим генератором случайных чисел от `seed`.

    Отдельный генератор: шаблонные группы при добавлении этих не меняются.
    """
    catalog = _characteristics_only(catalog)
    rnd = random.Random(seed * 7919 + 1)
    heads = ambiguous_heads(catalog)
    cases: list[GoldenCase] = []
    for p in catalog.all():
        for i in range(per_position):
            cases.append(_prose_case(p, rnd, i))
            if (unit := _unit_case(p, rnd, i)) is not None:
                cases.append(unit)
            if (synonym := _synonym_case(p, rnd, i)) is not None:
                cases.append(synonym)
            cases.append(_noisy_case(p, rnd, i))
        if p.code in heads:
            head, group = heads[p.code]
            cases.extend(_ambiguous_cases(p, head, group, rnd))
    return cases


def build_golden(catalog: Catalog, seed: int = 42, per_position: int = 3) -> list[GoldenCase]:
    catalog = _characteristics_only(catalog)
    rnd = random.Random(seed)
    cases: list[GoldenCase] = []
    for p in catalog.all():
        for i in range(per_position):
            clean = _clean_case(p, rnd, i)
            cases.append(clean)
            if (typo := _typo_case(clean, p, rnd)) is not None:
                cases.append(typo)
        cases.extend(_corrupted(p, _clean_case(p, rnd, per_position), catalog))
    for i, text in enumerate(OUT_OF_CATALOG):
        cases.append(GoldenCase(id=f"outside-{i}", group="out_of_catalog", name=text))
    for i, text in enumerate(NEAR_DOMAIN):
        cases.append(GoldenCase(id=f"near-{i}", group="near_domain", name=text))
    return cases + build_real_formats(catalog, seed=seed)
