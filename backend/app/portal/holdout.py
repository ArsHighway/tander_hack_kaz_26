"""Отложенная выборка СТЕ для честной оценки модуля на справочнике стандартов.

Из каждой категории берётся до `per_category` СТЕ, а из обучающих данных (перечни значений,
индекс похожих СТЕ) убираются не только они, но и их двойники: СТЕ той же категории с тем же
наименованием или той же парой «производитель + модель». После `doNotCollapse` варианты одного
товара (цвет, размер) лежат отдельными СТЕ с почти одинаковыми наименованиями: без этого оценка
находила бы в индексе сам ответ.

Вход оценки — наименование и часть характеристик, переписанная текстом («Цвет: синий; Объём:
1,5 л»), как поставщик пишет описание; остальные характеристики — то, что модуль должен заполнить.
"""

import random
from collections import Counter, defaultdict
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from app.ingest.characteristics import to_number
from app.portal.standards import value_text
from app.portal.values import value_key
from app.values import fold

# Сколько характеристик СТЕ уходит в текст описания (остальные — цель заполнения).
MAX_GIVEN = 4


@dataclass(frozen=True)
class Entry:
    id: int
    directory: int
    name_key: str
    model_key: str | None


def entry(rec: dict[str, Any]) -> Entry:
    model = fold(rec.get("model") or "")
    maker = fold(rec.get("manufacturer") or "")
    return Entry(
        id=rec["id"],
        directory=rec["directory_id"],
        name_key=fold(rec.get("name") or ""),
        # Короткая «модель» («1», «-») — не модель: по ней склеились бы разные товары.
        model_key=f"{maker}|{model}" if len(model) >= 3 else None,
    )


def _components(group: list[Entry]) -> list[list[Entry]]:
    """Двойники связаны наименованием или моделью — и транзитивно: A и B с одним наименованием,
    B и C с одной моделью — один товар. Компоненты связности, СТЕ внутри — по id."""
    parent = list(range(len(group)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first: dict[str, int] = {}
    for i, e in enumerate(group):
        for key in (f"n:{e.name_key}", f"m:{e.model_key}" if e.model_key else None):
            if key is None:
                continue
            if key in first:
                parent[find(i)] = find(first[key])
            else:
                first[key] = i
    comps: defaultdict[int, list[Entry]] = defaultdict(list)
    for i, e in enumerate(group):
        comps[find(i)].append(e)
    return sorted(comps.values(), key=lambda c: c[0].id)


def split(entries: list[Entry], per_category: int, seed: int) -> tuple[set[int], set[int]]:
    """(id отложенных СТЕ, id всех СТЕ, которые убираются из обучения — отложенные и двойники)."""
    rnd = random.Random(seed)
    by_dir: defaultdict[int, list[Entry]] = defaultdict(list)
    for e in entries:
        by_dir[e.directory].append(e)
    chosen: set[int] = set()
    excluded: set[int] = set()
    for directory in sorted(by_dir):
        comps = _components(sorted(by_dir[directory], key=lambda e: e.id))
        # Категория из одного-двух товаров: отложи — и учить нечему.
        if len(comps) < 3:
            continue
        # Не больше половины товаров категории: остальные — то, по чему модуль её узнаёт.
        for comp in rnd.sample(comps, min(per_category, len(comps) // 2)):
            chosen.add(rnd.choice(comp).id)
            excluded |= {e.id for e in comp}
    return chosen, excluded


def _char_text(ch: dict[str, Any]) -> str | None:
    value = value_text(ch.get("value"))
    if value is None or not ch.get("name"):
        return None
    unit = f" {ch['unit']}" if ch.get("unit") else ""
    return f"{ch['name']}: {value}{unit}"


def make_case(rec: dict[str, Any], code: str, rnd: random.Random) -> dict[str, Any]:
    """Строка выборки: вход (наименование, описание из части характеристик) и эталон."""
    chars = [ch for ch in rec.get("characteristics") or [] if _char_text(ch)]
    order = list(range(len(chars)))
    rnd.shuffle(order)
    given = sorted(order[: min(MAX_GIVEN, len(chars) // 2)])
    return {
        "id": rec["id"],
        "code": code,
        "directory_id": rec["directory_id"],
        "category": rec.get("category") or "",
        "name": rec.get("name") or "",
        "description": "; ".join(t for i in given if (t := _char_text(chars[i]))),
        "given": [
            {"name": chars[i]["name"], "value": value_text(chars[i]["value"])} for i in given
        ],
        "target": [
            {"name": ch["name"], "value": value_text(ch["value"]), "unit": ch.get("unit")}
            for i, ch in enumerate(chars)
            if i not in given
        ],
    }


def same_value(got: str, want: str) -> bool:
    """Значение модуля совпало с эталоном: после `fold` или как число («15» и «15,0»)."""
    if value_key(got) == value_key(want):
        return True
    a, b = to_number(got), to_number(want)
    return a is not None and b is not None and abs(a - b) <= 1e-6 * max(1.0, abs(b))


def score_case(
    case: dict[str, Any],
    codes: list[str],
    final_code: str | None,
    target_code: str | None,
    chars: list[tuple[str, str | None, str]],
    general: Collection[str] = (),
    aliases: Mapping[str, str] | None = None,
    required: Collection[str] = (),
    ktru_asked: bool = False,
    hinted: Collection[str] = (),
    extra: Collection[tuple[str, str | None]] = (),
    from_text: Collection[str] = (),
) -> Counter[str]:
    """Счётчики одного случая. `chars` — (имя, значение, статус) по позиции, которую модуль
    заполнил (`target_code`); заполнение считается, только если это верная категория.
    `general` — имена полей «Общих сведений» этой позиции (по группе): в заполнение не идут.
    `aliases` — свёрнутые синонимы имён позиции → её имя («формат листа» → «формат»).
    `required` — обязательные характеристики позиции (без «Общих сведений»): что осталось без
    значения, поставщик вводит руками. `ktru_asked` — второй этап спросил «Позицию КТРУ»."""
    c: Counter[str] = Counter(cases=1)
    gold = case["code"]
    c["top1"] += codes[:1] == [gold]
    c["top5"] += gold in codes[:5]
    c["final"] += final_code == gold
    c["decided"] += final_code is not None  # код без единого вопроса: верный или нет
    c["not_found"] += not codes
    c["targets"] += len(case["target"])
    c["givens"] += len(case["given"])
    c["ktru_questions"] += ktru_asked
    if target_code != gold:
        return c
    c["right_category"] += 1
    c["targets_right"] += len(case["target"])
    got = {fold(n): (v, s) for n, v, s in chars}
    # Значение из подсказки (такая же или похожие СТЕ) — не найденное в тексте поставщика.
    copied = {fold(n) for n in hinted}
    # Свойства вне шаблона (`AutofillOut.extra`): характеристика поставщика не потеряна.
    outside = {fold(n): v for n, v in extra}
    for kind, items in (("target", case["target"]), ("given", case["given"])):
        for item in items:
            key = fold(item["name"])
            name = (aliases or {}).get(key, key)
            value, status = got.get(name, (None, "absent"))
            c[f"{kind}_in_spec"] += status != "absent"
            if kind == "given" and status == "absent" and key in outside:
                c["given_extra"] += 1
                c["given_extra_correct"] += same_value(outside[key] or "", item["value"])
            if kind == "given":
                # Не потеряно: значение есть в поле из текста (под своим или унифицированным именем:
                # «Высота до сидения» → «Высота сиденья») или отдельной характеристикой вне шаблона.
                c["given_kept"] += same_value(outside.get(key) or "", item["value"]) or any(
                    same_value(v, item["value"]) for v in from_text
                )
            if value is None or status == "conflict" or (kind == "given" and name in copied):
                continue
            c[f"{kind}_filled"] += 1
            c[f"{kind}_correct"] += same_value(value, item["value"])
    filled_required = 0
    for n in required:
        value, status = got.get(fold(n), (None, "absent"))
        filled_required += value is not None and status != "conflict"
    c["required"] += len(required)
    c["required_filled"] += filled_required
    # Без единого действия: код верный без вопроса, обязательные заполнены.
    c["zero_touch"] += final_code == gold and filled_required == len(required) and not ktru_asked
    card_fields = {fold(n) for n in general}  # поля карточки, а не характеристики
    c["filled_total"] += sum(
        1 for n, v, s in chars if v is not None and s != "conflict" and fold(n) not in card_fields
    )
    return c


def summary(c: Counter[str]) -> dict[str, float | int]:
    def share(a: str, b: str) -> float:
        return round(c[a] / c[b], 4) if c[b] else 0.0

    return {
        "cases": c["cases"],
        "category_top1": share("top1", "cases"),
        "category_top5": share("top5", "cases"),
        "category_final": share("final", "cases"),
        # Автовыбор: решено без вопроса, из решённого верно, уверенные ошибки.
        "decided": share("decided", "cases"),
        "decided_precision": share("final", "decided"),
        "decided_cases": c["decided"],
        "givens": c["givens"],
        "targets": c["targets"],
        "target_filled": c["target_filled"],
        "confident_errors": round((c["decided"] - c["final"]) / c["cases"], 4)
        if c["cases"]
        else 0.0,
        "not_found": share("not_found", "cases"),
        "right_category": share("right_category", "cases"),
        # Из текста описания: «Цвет: синий» — значение должно попасть в своё поле.
        "given_recall": share("given_correct", "givens"),
        # Вне шаблона категории, но не потеряно: отдельной характеристикой (`extra`).
        "given_extra": share("given_extra_correct", "givens"),
        "given_kept": share("given_kept", "givens"),
        # Чего в тексте нет: модуль заполнил сам (LLM, вывод из наименования).
        "target_precision": share("target_correct", "target_filled"),
        "target_recall": share("target_correct", "targets"),
        "target_in_spec": share("target_in_spec", "targets"),
        # Что остаётся человеку: вопрос о категории, поиск вручную (верной нет в пятёрке),
        # обязательные поля без значения (при верной категории).
        "question_rate": round((c["cases"] - c["decided"]) / c["cases"], 4) if c["cases"] else 0.0,
        "manual_search_rate": round((c["cases"] - c["top5"]) / c["cases"], 4)
        if c["cases"]
        else 0.0,
        "required_auto": share("required_filled", "required"),
        "manual_required_per_card": round(
            (c["required"] - c["required_filled"]) / c["right_category"], 4
        )
        if c["right_category"]
        else 0.0,
        "zero_touch": share("zero_touch", "cases"),
        "ktru_question_rate": share("ktru_questions", "cases"),
        # Характеристики настоящей СТЕ, которые модуль не заполнил верно: ввести или исправить.
        "manual_fields_per_card": round(
            (c["targets_right"] - c["target_correct"]) / c["right_category"], 4
        )
        if c["right_category"]
        else 0.0,
        "required_fields": c["required"],
        "right_category_cases": c["right_category"],
    }
