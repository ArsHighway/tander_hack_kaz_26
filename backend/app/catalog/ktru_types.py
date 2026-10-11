"""Типы товаров КТРУ — первый этап подбора: что за товар по КТРУ, а не по категории портала.

Тип — позиции КТРУ с одним наименованием («Тумба прикроватная»: три позиции, различаются значениями
характеристик). Позиции без характеристик (укрупнённые: «Спецодежда мужская», «Одежда верхняя
прочая …») в типы не идут: им нечего подогнать, а общим словом они перехватывали бы поиск.

Справочник типов кладётся в `CATALOG_PATHS` вместо категорий портала, и конвейер работает без
изменений: поиск и вопросы первого этапа выбирают тип, второй этап (`Chain.ktru`) сужает до
конечного кода среди позиций типа — они перечислены в ссылках `ktru` характеристик.

Категория портала (КПГЗ) — производная: та, чьи характеристики ссылаются на позиции типа. Связанный
тип получает её код, путь и картинку (голоса похожих СТЕ портала и двойники приходят с кодом
категории и потому работают как раньше), а характеристики её шаблона, которых нет в КТРУ,
дописываются — это раздел «Характеристики портала». Тип без связи получает код `T-<первая позиция>`
и только характеристики КТРУ.
"""

from collections import Counter

from app.catalog.model import GENERAL_NAMES, CharacteristicSpec, Position
from app.values import fold

TYPE_SOURCE = "ktru-type"
UNLINKED_PREFIX = "T-"


def _parents(categories: list[Position]) -> dict[str, Counter[str]]:
    """Код позиции КТРУ → категории портала, которые на неё ссылаются (сколько раз)."""
    parents: dict[str, Counter[str]] = {}
    for category in categories:
        for c in category.characteristics:
            for code in c.ktru:
                parents.setdefault(code, Counter())[category.code] += 1
    return parents


def _ktru_specs(members: list[Position]) -> list[CharacteristicSpec]:
    """Характеристики типа: объединение по имени, перечень общий, обязательна — если обязательна
    у всех позиций типа. Ссылки `ktru` — все позиции типа: по ним второй этап их находит."""
    codes = [p.code for p in members]
    merged: dict[str, CharacteristicSpec] = {}
    count: Counter[str] = Counter()
    must: Counter[str] = Counter()
    for p in members:
        for c in p.characteristics:
            key = fold(c.name)
            count[key] += 1
            must[key] += c.required
            first = merged.get(key)
            if first is None:
                merged[key] = c.model_copy(update={"ktru": codes, "values": list(c.values)})
                continue
            have = {fold(v) for v in first.values}
            first.values.extend(v for v in c.values if fold(v) not in have)
    n = len(members)
    for key, spec in merged.items():
        spec.required = count[key] == n and must[key] == n
        spec.level_define = False
    return list(merged.values())


def _general() -> list[CharacteristicSpec]:
    return [CharacteristicSpec(name=n, kind="qualitative", group="general") for n in GENERAL_NAMES]


def build_types(ktru: list[Position], categories: list[Position]) -> list[Position]:
    """Типы КТРУ из позиций `ktru` с привязкой к категориям портала `categories`.

    Категория типа — та, что ссылается на большинство его позиций. Две категории не делят один код:
    тип, чья категория уже занята, остаётся без связи (код `T-…`).
    """
    parents = _parents(categories)
    by_code = {c.code: c for c in categories}
    groups: dict[str, list[Position]] = {}
    for p in ktru:
        if p.characteristics:
            groups.setdefault(p.name, []).append(p)
    taken: set[str] = set()
    out: list[Position] = []
    for name, members in groups.items():
        votes: Counter[str] = Counter()
        for p in members:
            votes.update(parents.get(p.code, Counter()).keys())
        linked = next((code for code, _ in votes.most_common() if code not in taken), None)
        category = by_code.get(linked) if linked else None
        specs = _ktru_specs(members)
        own = {fold(s.name) for s in specs}
        if category is not None:
            taken.add(category.code)
            # Шаблон портала без того, что уже есть в КТРУ; ссылки на КТРУ у него — свои, чужие
            # позиции во второй этап не попадают.
            portal = [
                s.model_copy(update={"ktru": [], "spgz": []})
                for s in category.characteristics
                if fold(s.name) not in own
            ]
            # Пределы, обычные для СТЕ категории (`plausible`), — и одноимённой характеристике
            # КТРУ: корзина «>=600» без верхней границы примет и 20 м ширины тумбы, а правдоподобие
            # скажет, что так не бывает.
            usual = {fold(s.name): s.plausible for s in category.characteristics if s.plausible}
            for spec in specs:
                if spec.plausible is None and (found := usual.get(fold(spec.name))) is not None:
                    spec.plausible = found
            if not any(s.group == "general" for s in portal):
                portal += _general()
        else:
            portal = _general()
        out.append(
            Position(
                code=category.code if category else f"{UNLINKED_PREFIX}{members[0].code}",
                name=name,
                okpd2=members[0].okpd2,
                kpgz=category.kpgz if category else None,
                characteristics=[*specs, *portal],
                source=TYPE_SOURCE,
                portal_id=category.portal_id if category else None,
                path=category.path if category else [],
                image_id=category.image_id if category else None,
            )
        )
    return out
