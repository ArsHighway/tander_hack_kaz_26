"""Сборка характеристик карточки по позиции: значения из текста, ответов и подсказок, их
источник (КТРУ, шаблон портала, новые) и замечания к ним.

Конвейер (`app.pipeline`) решает, какая позиция и что спросить; этот модуль — что попадает в поля
выбранной позиции и как значение поставщика ложится на характеристику.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from app.catalog import CharacteristicSpec, NumRange, Position
from app.checks.detector import detect
from app.extract import Extracted, extract_rules
from app.extract.extra import PortalNames, extra_pairs
from app.extract.quantities import negative_notes, numeric_notes
from app.ingest.characteristics import to_number
from app.ingest.units import unit_name
from app.ktru_align import (
    Aligned,
    alias_of,
    align_ktru,
    implied_from_type,
    recover_raw,
    required_on_all,
    unit_disagrees,
)
from app.narrowing import NONE_VALUE, OTHER_VALUE, Known, same_form
from app.schemas import CardIn, CharacteristicOut, CharIn, Fix, Issue
from app.search.similar import Similar
from app.session import Session
from app.values import compact, convert, fit_value, fold, parse_bucket

if TYPE_CHECKING:
    from app.pipeline import Chain, Deps


def _union_specs(positions: list[Position], merge_values: bool = False) -> list[CharacteristicSpec]:
    """Характеристики нескольких позиций: одноимённые — одна (первой позиции).

    `merge_values` — перечень значений общий. Нужен позициям КТРУ одной категории: перечень у
    каждой свой («Рычажный» у одной, «Сенсорный» у другой), и по перечню первой значение остальных
    в тексте не нашлось бы. Кандидатам-категориям — нет: чужие значения других категорий на
    отложенной выборке снизили полноту заполнения с 43,6 до 42,2 %.
    """
    seen: dict[str, CharacteristicSpec] = {}
    for p in positions:
        for c in p.characteristics:
            first = seen.setdefault(fold(c.name), c)
            if not merge_values:
                continue
            have = {fold(v) for v in first.values}
            extra = list({fold(v): v for v in c.values if fold(v) not in have}.values())
            if extra:
                seen[fold(c.name)] = first.model_copy(update={"values": [*first.values, *extra]})
    return list(seen.values())


_NUMBER_IN_VALUE = re.compile(r"\d+(?:[.,]\d+)?")


def _template_for_ktru(
    template: list[Extracted], ktru: list[Extracted], specs: list[CharacteristicSpec]
) -> list[Extracted]:
    """Значения полей шаблона категории для характеристик КТРУ с тем же именем, которых правила по
    КТРУ не нашли. Перечень КТРУ из одних чисел («1», «2», «3») принимает число из значения
    шаблона («2 класс»), если оно в перечне и единственное."""
    by_name = {fold(c.name): c for c in specs}
    have = {fold(e.name) for e in ktru}
    out = []
    for e in template:
        spec = by_name.get(fold(e.name))
        if spec is None or fold(e.name) in have or e.method != "rule":
            continue
        value = e.raw_value
        if fit_value(value, e.raw_unit, spec).status not in _FITS_KTRU and spec.values:
            numbers = _NUMBER_IN_VALUE.findall(value)
            bare = {fold(v) for v in spec.values if _NUMBER_IN_VALUE.fullmatch(v.strip())}
            if len(numbers) != 1 or numbers[0] not in bare:
                continue
            value = numbers[0]
        out.append(e.model_copy(update={"name": spec.name, "raw_value": value}))
    return out


_FITS_KTRU = ("exact", "normalized", "bucket", "typo")


def _twin_note(hint: Similar) -> str:
    if hint.of == 1 and hint.ids:
        return f"как у такой же СТЕ №{hint.ids[0]} на портале"
    return f"как у {hint.votes} из {hint.of} таких же СТЕ портала с этим полем"


# Подсказка, которую называет текст поставщика (`Similar.source`): значение из текста, а не
# предложение. Начало `note` — признак для замера (`evaluate_holdout`).
HINT_IN_TEXT = "есть в тексте"


def _spec_constraints(spec: CharacteristicSpec) -> tuple[list[str], NumRange | None]:
    """Перечень и диапазон этой позиции, без пустых и повторных значений.

    Справочник не меняется: дубли — только в ответе, чтобы в списке не было двух одинаковых пунктов.
    """
    values: list[str] = []
    seen: set[str] = set()
    for raw in spec.values:
        if not raw.strip() or raw in seen:
            continue
        seen.add(raw)
        values.append(raw)
    rng = spec.range
    if rng is None or (rng.min is None and rng.max is None):
        return values, None
    return values, rng.model_copy()


def _characteristic(spec: CharacteristicSpec, char: CharacteristicOut) -> CharacteristicOut:
    values, rng = _spec_constraints(spec)
    return char.model_copy(update={"values": values, "range": rng})


def _from_aligned(spec: CharacteristicSpec, aligned: Aligned, source, method) -> CharacteristicOut:
    return CharacteristicOut(
        name=spec.name,
        value=aligned.value,
        unit=aligned.unit or spec.unit,
        source=source,
        normalized_from=aligned.normalized_from,
        status=aligned.status,
        required=spec.required,
        method=method,
        note=aligned.note,
        origin="ktru",
        ktru_fit=aligned.ktru_fit,
        ktru_match=aligned.ktru_match,
    )


def _bucket_answer(spec: CharacteristicSpec, value: str) -> CharacteristicOut | None:
    """Ответ корзиной выбирает код, но не подставляет значение товара."""
    bucket = parse_bucket(value)
    if bucket is None or bucket.is_point or spec.kind != "quantitative":
        return None
    return CharacteristicOut(
        name=spec.name,
        value=None,
        unit=spec.unit,
        source=None,
        status="not_found",
        required=spec.required,
        method="answer",
        origin="ktru",
        ktru_fit=None,
        ktru_match=value,
        note=f"укажите точное значение в пределах {value}",
    )


def _characteristics(
    position: Position,
    extracted: list[Extracted],
    known: dict[str, Known],
    similar: dict[str, Similar] | None = None,
    twin: dict[str, Similar] | None = None,
    ktru: bool = False,
) -> list[CharacteristicOut]:
    by_name = {fold(e.name): e for e in extracted}
    out = []
    for spec in position.characteristics:
        answered = known.get(spec.name)
        if (
            answered is not None
            and answered.source == "answer"
            and answered.value not in (NONE_VALUE, OTHER_VALUE)
        ):
            bucket_answer = _bucket_answer(spec, answered.value) if ktru else None
            if bucket_answer is not None:
                out.append(_characteristic(spec, bucket_answer))
                continue
            answered_value = same_form(answered.value, spec)
            aligned = align_ktru(answered_value, spec.unit, spec) if ktru else None
            out.append(
                _characteristic(
                    spec,
                    _from_aligned(spec, aligned, None, "answer")
                    if aligned is not None
                    else CharacteristicOut(
                        name=spec.name,
                        value=answered_value,
                        unit=spec.unit,
                        source=None,
                        status="filled",
                        required=spec.required,
                        method="answer",
                        note="ответ на уточняющий вопрос",
                    ),
                )
            )
            if ktru and out[-1].method == "answer" and out[-1].note is None:
                out[-1].note = "ответ на уточняющий вопрос"
            continue
        e = by_name.get(fold(spec.name))
        # Поставщик ответил о поле («нет такой характеристики», «Другое значение»): подсказка
        # СТЕ портала его ответ не перекрывает.
        hinted_ok = answered is None or answered.source != "answer"
        same = None if ktru else (twin or {}).get(spec.name) if hinted_ok else None
        if e is None and same is not None:
            # Правила не взяли, а такая же СТЕ на портале есть: её значение — подсказка, а если
            # текст его называет («ДЕРВАЛ» в наименовании) — значение из текста с фрагментом.
            out.append(
                CharacteristicOut(
                    name=spec.name,
                    value=same.value,
                    unit=same.unit or spec.unit,
                    source=same.source,
                    status="filled",
                    required=spec.required,
                    method="rule" if same.source else "twin",
                    note=f"{HINT_IN_TEXT}; {_twin_note(same)}" if same.source else _twin_note(same),
                )
            )
            continue
        hint = None if ktru else (similar or {}).get(spec.name) if hinted_ok else None
        if e is None and hint is not None:
            # Правила не взяли, похожие СТЕ той же категории согласны: подсказка, а не факт; если
            # текст называет это значение («латунь» у «Материала») — значение из текста.
            votes = f"у {hint.votes} из {hint.of} похожих СТЕ портала"
            out.append(
                CharacteristicOut(
                    name=spec.name,
                    value=hint.value,
                    unit=hint.unit or spec.unit,
                    source=hint.source,
                    status="filled",
                    required=spec.required,
                    method="rule" if hint.source else "similar",
                    note=f"{HINT_IN_TEXT}; как {votes}"
                    if hint.source
                    else f"как {votes} с этим полем",
                )
            )
            continue
        if e is None:
            out.append(
                _characteristic(
                    spec,
                    CharacteristicOut(
                        name=spec.name,
                        value=None,
                        unit=spec.unit,
                        source=None,
                        status="not_found",
                        required=spec.required,
                        note="нет в тексте — укажите" if ktru else "не найдено в описании",
                        origin="ktru" if ktru else "portal",
                    ),
                )
            )
            continue
        if ktru:
            aligned = align_ktru(e.raw_value, e.raw_unit, spec)
            char = _from_aligned(spec, aligned, e.source, e.method)
            char.checked = e.checked or None
            if e.note:
                char.note = e.note
            out.append(_characteristic(spec, char))
            continue
        fit = fit_value(e.raw_value, e.raw_unit, spec)
        if fit.status in ("not_in_list", "unit_mismatch", "unparsed"):
            out.append(
                _characteristic(
                    spec,
                    CharacteristicOut(
                        name=spec.name,
                        value=e.raw_value,
                        unit=e.raw_unit or spec.unit,
                        source=e.source,
                        status="conflict",
                        required=spec.required,
                        method=e.method,
                        note=fit.note,
                    ),
                )
            )
            continue
        # Показываем, во что превратился фрагмент текста: «нитриловые» → «Нитрил», «80» → корзина.
        changed = fit.value is not None and fold(fit.value) != fold(e.source.text)
        out.append(
            _characteristic(
                spec,
                CharacteristicOut(
                    name=spec.name,
                    value=fit.value,
                    unit=fit.unit,
                    source=e.source,
                    normalized_from=e.source.text if changed else None,
                    status="normalized" if changed else "filled",
                    required=spec.required,
                    method=e.method,
                    note=e.note or fit.note,
                    checked=e.checked or None,
                ),
            )
        )
    # Поле на спецификацию, по порядку: группа — из спецификации, единица — полным названием ОКЕИ.
    for spec, field in zip(position.characteristics, out, strict=True):
        field.group = spec.group
        field.unit_name = unit_name(field.unit)
        if field.status != "conflict":
            field.compact = compact(field.value, field.unit)
    return out


def _issues(
    session: Session, position: Position | None, chars: list[CharacteristicOut]
) -> list[Issue]:
    card = CardIn(
        name=session.name,
        description=session.description,
        characteristics=[
            CharIn(name=c.name, value=c.value, unit=c.unit) for c in chars if c.value is not None
        ],
    )
    ktru = _ktru_field_issues(chars)
    # «глина»: замечание КТРУ с допустимыми значениями заменяет общее «нет в перечне позиции».
    told = {(i.field, i.kind) for i in ktru}
    return [*(i for i in detect(card, position) if (i.field, i.kind) not in told), *ktru]


# Габарит одежды, мебели или инструмента больше 10 м или ноль — опечатка в единице или абсурд
# («Ширина: 20 метров» у тумбы): поле не «подтверждено», а «требует проверки».
MAX_SIZE_MM = 10_000
NUMBER_RULE = "число из текста сопоставлено с характеристикой или явно оставлено без значения"
_SIZE = re.compile(r"длин|ширин|высот|глубин|толщин|диаметр|габарит", re.IGNORECASE)
SIZE_RULE = "габарит в пределах разумного"


def _size_issues(chars: Sequence[CharacteristicOut]) -> list[Issue]:
    out = []
    for c in chars:
        if not c.value or not c.unit or not _SIZE.search(c.name):
            continue
        number = to_number(c.value)
        mm = convert(number, c.unit, "мм") if number is not None else None
        if mm is None or 0 < mm <= MAX_SIZE_MM:
            continue
        shown = f"{c.value} {c.unit}"
        out.append(
            Issue(
                field=f"characteristics.{c.name}",
                kind="mismatch",
                severity="warn",
                message=f"«{c.name}: {shown}» — так не бывает: "
                + ("ноль." if mm == 0 else f"больше {MAX_SIZE_MM // 1000} м."),
                why="Проверьте число и единицу в описании: возможно, имелись в виду сантиметры или "
                "миллиметры.",
                rule=SIZE_RULE,
            )
        )
    return out


def _number_issues(
    session: Session, positions: list[Position], chars: Sequence[CharacteristicOut] = ()
) -> list[Issue]:
    """Число из текста, которое не удалось уверенно положить в характеристику. Число, ставшее
    значением поля (`source` характеристики: подсказка, которую назвал текст), — взято."""
    specs = _union_specs(positions)
    taken = [
        Extracted(name=c.name, raw_value=c.value, raw_unit=c.unit, source=c.source, method="rule")
        for c in chars
        if c.source is not None and c.value
    ]
    negative = [
        Issue(
            field=field,
            kind="mismatch",
            severity="warn",
            message=message,
            why=why,
            rule="отрицательное число у величины без минуса",
        )
        for field, message, why in negative_notes(session.name, session.description)
    ]
    return (
        negative
        + _size_issues(chars)
        + _collapse_numbers(
            numeric_notes(session.name, session.description, specs, [*session.extracted, *taken])
        )
    )


_MANY = re.compile(r"^Число «([^»]+)» подходит нескольким характеристикам")


def _collapse_numbers(notes: list[tuple[str, str, str]]) -> list[Issue]:
    """«Число … подходит нескольким характеристикам» — одним замечанием на поле текста, а не
    строкой на каждое число: восемь одинаковых плашек мешали прочитать остальное."""
    out: list[Issue] = []
    many: dict[str, list[str]] = {}
    for field, message, why in notes:
        if m := _MANY.match(message):
            many.setdefault(field, []).append(m.group(1))
            continue
        out.append(
            Issue(
                field=field, kind="mismatch", severity="info", message=message, why=why,
                rule=NUMBER_RULE,
            )
        )  # fmt: skip
    for field, numbers in many.items():
        shown = ", ".join(f"«{n}»" for n in dict.fromkeys(numbers))
        out.append(
            Issue(
                field=field,
                kind="mismatch",
                severity="info",
                message=f"Числа без подписи не взяты: {shown} — подходят нескольким полям.",
                why="Напишите, что это за число («Ширина: 500 мм»), и модуль положит его в поле.",
                rule=NUMBER_RULE,
            )
        )
    return out


NOTE_PORTAL = "нет в шаблоне категории, так эту характеристику пишут на портале"
NOTE_NEW = (
    "характеристика не относится к позиции — перенесена в новые. "
    "Предложение: вне КТРУ, подтверждает модератор"
)
NOTE_EMPTY = "нет в тексте — укажите"


def _portal_names(deps: Deps) -> PortalNames:
    if deps.portal_names is None:
        deps.portal_names = PortalNames(
            c.name for p in deps.catalog.all() for c in p.characteristics
        )
    return deps.portal_names


def _origins(
    session: Session,
    target: Position,
    chars: list[CharacteristicOut],
    chain: Chain | None,
    answered: dict[str, Known],
    deps: Deps,
) -> tuple[list[CharacteristicOut], list[CharacteristicOut]]:
    """Источник каждой характеристики (КТРУ / шаблон портала) и свойства вне шаблона.

    Порядок унификации организаторов: КТРУ — эталон, затем как пишут на портале, затем новое. Имя
    сверяется с позицией КТРУ (итоговой, иначе — со всеми позициями КТРУ категории). Характеристики
    итоговой позиции КТРУ, которых нет в шаблоне, дописываются: в описании товара их не теряем.
    """
    final = chain.ktru.final_code if chain is not None and chain.ktru is not None else None
    own = deps.ktru.get(final) if deps.ktru is not None and final else None
    ktru_positions = [own] if own is not None else _linked_ktru(target, deps)
    ktru_names = {fold(c.name) for p in ktru_positions for c in p.characteristics}
    # Пока конечного кода нет, характеристики показываем только у одного типа: иначе в карточку
    # попадают поля всех связанных позиций. Обязательна та, что обязательна у каждой из них.
    one_type = [own] if own is not None else _one_type(ktru_positions)
    must = (
        {fold(c.name) for c in own.characteristics if c.required}
        if own is not None
        else required_on_all(one_type)
    )
    marked = [
        c.model_copy(
            update={
                "origin": "ktru" if fold(c.name) in ktru_names else "portal",
                "required": c.required or fold(c.name) in must,
            }
        )
        for c in chars
    ]
    template = {fold(s.name) for s in target.characteristics}
    type_specs = _union_specs(one_type, merge_values=True)
    missing = [spec for spec in type_specs if fold(spec.name) not in template]
    if missing:
        only_ktru = (own or one_type[0]).model_copy(update={"characteristics": missing})
        found = extract_rules(session.name, session.description, missing)
        marked += list(_characteristics(only_ktru, found, answered, ktru=True))
    marked = _polish_ktru(marked, type_specs, must, chain, own)
    known = template | ktru_names | {fold(spec.name) for spec in type_specs}
    known |= {fold(n) for s in target.characteristics for n in s.synonyms}
    # Фрагмент, из которого правила уже заполнили поле шаблона («Кабель-канал: есть» → «Наличие
    # кабель-канала»), лишним свойством не становится.
    taken = [
        (x.source.start, x.source.end) for x in session.extracted if x.source.field == "description"
    ]
    extra = [
        CharacteristicOut(
            name=e.name,
            value=e.value,
            unit=e.unit,
            source=e.source,
            status="filled",
            required=False,
            method="rule",
            origin=e.origin,
            note=_extra_note(e.name, e.unit, e.origin, e.seen),
        )
        for e in extra_pairs(
            session.description, known, _portal_names(deps), new_names=deps.new_names
        )
        if alias_of(e.name, type_specs) is None
        and not any(start < e.source.end and e.source.start < end for start, end in taken)
    ]
    return marked, extra


def _one_type(positions: list[Position]) -> list[Position]:
    if positions and len({position.name for position in positions}) == 1:
        return positions
    return []


def _extra_note(name: str, unit: str | None, origin: str, seen: int = 0) -> str:
    moved = (
        f"характеристика «{name}» не относится к позиции — перенесена в новые. "
        "Предложение: вне КТРУ, подтверждает модератор"
    )
    if seen:
        moved += f". Формулировка уже записана (встречалась {seen} раз) — используем её"
    note = NOTE_PORTAL if origin == "portal" else moved
    if unit_disagrees(name, unit):
        return f"{note}. Проверьте единицу: «{unit}» не сходится с «{name}»."
    return note


def _polish_ktru(
    marked: list[CharacteristicOut],
    specs: list[CharacteristicSpec],
    must: set[str],
    chain: Chain | None,
    own: Position | None,
) -> list[CharacteristicOut]:
    """Поля КТРУ: значение из текста, корзина отдельно, короткая метка портала не дублируется."""
    by_spec = {fold(spec.name): spec for spec in specs}
    polished: list[CharacteristicOut] = []
    for char in marked:
        spec = by_spec.get(fold(char.name))
        if spec is None or fold(char.name) not in by_spec:
            polished.append(char)
            continue
        if char.method in ("twin", "similar") and char.source is None:
            polished.append(
                char.model_copy(
                    update={
                        "value": None,
                        "status": "not_found",
                        "method": None,
                        "note": NOTE_EMPTY,
                        "origin": "ktru",
                        "ktru_fit": None,
                        "ktru_match": None,
                        "required": char.required or fold(char.name) in must,
                    }
                )
            )
            continue
        polished.append(_realign(char, spec, must))
    polished = _absorb(polished, specs, must)
    title = chain.ktru_type if chain is not None and chain.ktru_type else (own.name if own else "")
    if title:
        polished = [_imply(char, by_spec.get(fold(char.name)), title, must) for char in polished]
    return polished


def _realign(
    char: CharacteristicOut, spec: CharacteristicSpec, must: set[str]
) -> CharacteristicOut:
    required = char.required or spec.required or fold(spec.name) in must
    if char.method == "answer" and char.value:
        bucket = _bucket_answer(spec, char.value)
        if bucket is not None:
            return _characteristic(spec, bucket).model_copy(update={"required": required})
    if char.value is None:
        return char.model_copy(
            update={"origin": "ktru", "required": required, "note": char.note or NOTE_EMPTY}
        )
    raw, unit = recover_raw(char.value, char.unit, char.note)
    aligned = align_ktru(raw, unit, spec)
    fitted = _from_aligned(spec, aligned, char.source, char.method or "rule")
    if char.method == "answer" and fitted.note is None:
        fitted.note = "ответ на уточняющий вопрос"
    return _characteristic(spec, fitted).model_copy(update={"required": required})


def _absorb(
    marked: list[CharacteristicOut], specs: list[CharacteristicSpec], must: set[str]
) -> list[CharacteristicOut]:
    """«Ширина» портала переносится в «Габаритную ширину», если такая характеристика одна."""
    index = {fold(char.name): i for i, char in enumerate(marked)}
    drop: set[int] = set()
    for i, char in enumerate(marked):
        if fold(char.name) in {fold(spec.name) for spec in specs}:
            continue
        target = alias_of(char.name, specs)
        if target is None:
            continue
        host_at = index.get(fold(target.name))
        if host_at is None:
            continue
        host = marked[host_at]
        invented = char.method in ("twin", "similar") and char.source is None
        if host.value is None and char.value is not None and not invented:
            raw, unit = recover_raw(char.value, char.unit, char.note)
            aligned = align_ktru(raw, unit, target)
            required = host.required or target.required or fold(target.name) in must
            marked[host_at] = _characteristic(
                target, _from_aligned(target, aligned, char.source, char.method or "rule")
            ).model_copy(update={"required": required})
        drop.add(i)
    return [char for i, char in enumerate(marked) if i not in drop]


def _imply(
    char: CharacteristicOut, spec: CharacteristicSpec | None, title: str, must: set[str]
) -> CharacteristicOut:
    if spec is None or char.value is not None or char.origin != "ktru":
        return char
    implied = implied_from_type(title, spec)
    if implied is None:
        return char
    aligned = align_ktru(implied, None, spec)
    fitted = _from_aligned(spec, aligned, None, "answer")
    fitted.note = "из выбранного типа"
    return _characteristic(spec, fitted).model_copy(
        update={"required": char.required or fold(spec.name) in must}
    )


def _ktru_field_issues(chars: Sequence[CharacteristicOut]) -> list[Issue]:
    issues: list[Issue] = []
    for char in chars:
        if char.origin != "ktru" or char.ktru_fit not in ("not_in_list", "out_of_range"):
            continue
        fix = None
        if char.ktru_fit == "out_of_range" and char.note and "Возможно" in char.note:
            fix = Fix(value=_fix_value(char.note), unit=char.unit)
        severity: Literal["block", "warn"] = (
            "block" if char.required and char.ktru_fit == "not_in_list" else "warn"
        )
        issues.append(
            Issue(
                field=f"characteristics.{char.name}",
                kind="not_in_list" if char.ktru_fit == "not_in_list" else "mismatch",
                severity=severity,
                message=char.note or "",
                why=char.note or "",
                rule="значение поставщика сверяется с перечнем и диапазонами позиции КТРУ",
                fix=fix if fix and fix.value else None,
            )
        )
    return issues


def _fix_value(note: str) -> str | None:
    found = re.search(r"\((\d[\d\s]*)", note)
    return found.group(1).replace(" ", "") if found else None


def _linked_ktru(position: Position, deps: Deps) -> list[Position]:
    """Позиции КТРУ, на которые ссылаются характеристики категории и которые есть в справочнике."""
    if deps.ktru is None:
        return []
    codes = dict.fromkeys(k for c in position.characteristics for k in c.ktru)
    return [p for code in codes if (p := deps.ktru.get(code)) is not None]
