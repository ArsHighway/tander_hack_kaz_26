"""Гигиена карточки: персональные данные, мусорное наименование, пустые и повторы характеристик.

Персональные и контактные данные (телефон, почта, паспорт, СНИЛС, ИНН, номер карты) в карточке
каталога недопустимы: она публичная. Числа товара на них похожи (артикулы, штрихкоды, ГОСТ), поэтому
номер считается данными человека только по формату с контрольной суммой или рядом со словом-меткой
(«паспорт», «ИНН»).
"""

from typing import Literal

import regex as re

from app.catalog import Position
from app.ingest.characteristics import to_number
from app.ingest.units import normalize_unit
from app.schemas import CardIn, Issue
from app.values import convert, fold

RULE_PERSONAL = "в карточке нет персональных и контактных данных"
RULE_NAME_MEANING = "наименование называет товар"
RULE_CHAR_NAME = "у характеристики есть название и оно не повторяется"
RULE_IN_POSITION = "характеристики карточки есть в позиции КТРУ"

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]{2,}")
PHONE = re.compile(r"(?<![\d-])(?:\+7|8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?![\d-])")
_CARD = re.compile(r"(?<![\d-])\d{4}(?:[\s-]?\d{4}){3}(?![\d-])")
_PASSPORT = re.compile(r"паспорт\D{0,20}\d{2}\s?\d{2}\s?\d{6}(?!\d)", re.IGNORECASE)
_SNILS = re.compile(r"(?<![\d-])(\d{3})-(\d{3})-(\d{3})[\s-](\d{2})(?![\d-])")
_INN = re.compile(r"инн\D{0,5}(\d{12}|\d{10})(?!\d)", re.IGNORECASE)
_WORD3 = re.compile(r"\p{L}{3,}")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch) * (2 if i % 2 else 1)
        total += d - 9 if d > 9 else d
    return total % 10 == 0


def _snils_ok(groups: tuple[str, ...]) -> bool:
    digits = "".join(groups[:3])
    total = sum(int(d) * (9 - i) for i, d in enumerate(digits))
    control = total if total < 100 else 0 if total in (100, 101) else total % 101 % 100
    return control == int(groups[3])


def _inn_ok(inn: str) -> bool:
    def check(weights: list[int], part: str) -> int:
        return sum(w * int(d) for w, d in zip(weights, part, strict=False)) % 11 % 10

    d = inn
    if len(d) == 10:
        return check([2, 4, 10, 3, 5, 9, 4, 6, 8], d) == int(d[9])
    w11 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
    w12 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
    return check(w11, d) == int(d[10]) and check(w12, d) == int(d[11])


def find_personal(text: str) -> str | None:
    """Вид персональных или контактных данных в тексте («телефон», «почта» …) или None."""
    if _EMAIL.search(text):
        return "почта"
    if PHONE.search(text):
        return "телефон"
    for m in _CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if digits[0] in "2456" and _luhn(digits):
            return "номер карты"
    if _PASSPORT.search(text):
        return "паспорт"
    if any(_snils_ok(m.groups()) for m in _SNILS.finditer(text)):
        return "СНИЛС"
    if any(_inn_ok(m.group(1)) for m in _INN.finditer(text)):
        return "ИНН"
    return None


def _personal_issue(field: str, where: str, kind: str) -> Issue:
    return Issue(
        field=field,
        kind="moderation_rule",
        severity="block",
        # Сами данные не повторяем: замечание видят модератор и поставщик.
        message=f"{where}: {kind}. Персональные и контактные данные в карточку не вносятся.",
        why=f"Правило модерации портала: {RULE_PERSONAL}.",
        rule=RULE_PERSONAL,
    )


def _name_issue(card: CardIn) -> Issue | None:
    """Наименование без единого слова («!!!», эмодзи, цифры) товар не называет.

    Сверку слов наименования со словами позиции КТРУ проверили на 8 520 карточках портала
    (02.10.2026): 41 ложный блок («Кубок KM2721», «Талреп №8» в категориях с другими словами)
    и 38 % предупреждений. Такой шум хуже пользы, поэтому блокируется только наименование без слов.
    """
    if _WORD3.search(card.name):
        return None
    return Issue(
        field="name",
        kind="moderation_rule",
        severity="block",
        message="В наименовании нет слов: назовите товар.",
        why=f"Правило модерации портала: {RULE_NAME_MEANING}.",
        rule=RULE_NAME_MEANING,
    )


def _value_key(value: str, unit: str | None) -> tuple[str, str]:
    """Ключ значения: «450 608» = «450608», «6300 г» = «6,3 кг» (число в базовой единице)."""
    number = to_number(value.replace(" ", ""))
    canon = normalize_unit(unit) or (unit or "")
    if number is not None and (base := convert(number, canon, _BASE.get(canon, canon))) is not None:
        return f"{base:.9g}", _BASE.get(canon, canon)
    return fold(value.replace(" ", "")), fold(canon)


_BASE = {"кг": "г", "мг": "г", "т": "г", "л": "мл", "см": "мм", "м": "мм", "км": "мм", "кВт": "Вт"}


def _duplicate(
    name: str, values: list[tuple[str, str | None]]
) -> tuple[Literal["block", "warn"], str] | None:
    """Повтор названия: то же значение дважды — блок; разные значения — предупреждение.

    Многозначные характеристики портал хранит повтором строки («Конструктивные особенности»:
    «Система отжима», «С ручкой», «Диаметр сверления» 6, 8, 10 мм), поэтому разные значения —
    предупреждение, а не возврат.
    """
    keys = [_value_key(v, u) for v, u in values]
    if len(set(keys)) < len(keys):
        return "block", f"Характеристика «{name}» указана дважды с тем же значением."
    shown = ", ".join(f"«{v}»" for v, _ in values[:4])
    return "warn", (
        f"Характеристика «{name}» указана несколько раз: {shown}. Если это перечень значений, "
        "оставьте; если нет — уберите лишнее."
    )


def _char_name_issues(card: CardIn) -> list[Issue]:
    issues = []
    groups: dict[str, list[tuple[str, str | None]]] = {}
    first: dict[str, str] = {}
    for c in card.characteristics:
        key = fold(c.name.strip())
        if not key:
            issues.append(
                _char_name_issue("characteristics", "block", "У характеристики нет названия.")
            )
            continue
        groups.setdefault(key, []).append((c.value, c.unit))
        first.setdefault(key, c.name)
    names = {fold(c.name.strip()): c.name for c in card.characteristics}  # последнее написание
    for key, values in groups.items():
        if len(values) > 1 and (dup := _duplicate(first[key].strip(), values)) is not None:
            severity, message = dup
            issues.append(_char_name_issue(f"characteristics.{names[key]}", severity, message))
    return issues


def _char_name_issue(field: str, severity: Literal["block", "warn"], message: str) -> Issue:
    return Issue(
        field=field,
        kind="moderation_rule",
        severity=severity,
        message=message,
        why=f"Правило модерации портала: {RULE_CHAR_NAME}.",
        rule=RULE_CHAR_NAME,
    )


def not_in_position(char_name: str, position: Position) -> Issue:
    return Issue(
        field=f"characteristics.{char_name}",
        kind="mismatch",
        severity="warn",
        message=f"Характеристики «{char_name}» нет в позиции КТРУ {position.code}.",
        why="В карточку идут характеристики позиции справочника.",
        rule=RULE_IN_POSITION,
    )


RULE_EXTRA = (
    "свойство вне шаблона категории не выбрасывается: КТРУ, затем как пишут на портале, затем новое"
)


def extra_characteristic(char_name: str, origin: str, position: Position) -> Issue:
    where = (
        "так её пишут на портале у других категорий"
        if origin == "portal"
        else "новая: её нет ни в КТРУ, ни на портале"
    )
    return Issue(
        field=f"characteristics.{char_name}",
        kind="mismatch",
        severity="info",
        message=f"«{char_name}» — вне шаблона категории {position.code}, {where}.",
        why="Свойство из описания поставщика сохранено отдельной характеристикой; модератор "
        "проверит формулировку.",
        rule=RULE_EXTRA,
    )


def hygiene_issues(card: CardIn, position: Position | None) -> list[Issue]:
    issues = []
    if kind := find_personal(card.name):
        issues.append(_personal_issue("name", "В наименовании", kind))
    if kind := find_personal(card.description):
        issues.append(_personal_issue("description", "В описании", kind))
    for c in card.characteristics:
        if kind := find_personal(c.value):
            issues.append(_personal_issue(f"characteristics.{c.name}", f"В «{c.name}»", kind))
    if issue := _name_issue(card):
        issues.append(issue)
    issues.extend(_char_name_issues(card))
    return issues
