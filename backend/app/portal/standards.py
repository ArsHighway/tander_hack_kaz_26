"""Справочник из стандартов категорий портала: состав характеристик — шаблон категории,
допустимые значения — то, что поставщики реально пишут в СТЕ этой категории.

Стандарт (`fetch_standards`) знает, какие характеристики у категории, их тип, единицу, диапазон,
обязательность и привязку к КТРУ и СПГЗ, но значения справочников отдаёт ссылками (расшифровка
закрыта), а 9 из 10 характеристик шаблона добавлены поставщиками. Поэтому официальная
характеристика попадает в позицию, если она обязательна или заполнена хотя бы у одной СТЕ, а
добавленная поставщиком — только при доле заполнения не ниже порога. План и замеры —
`docs/plans/2026-10-06-standard-catalog.md`.
"""

import contextlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal

from app.catalog import CharacteristicSpec, NumRange, Position
from app.catalog.model import GENERAL_NAMES
from app.ingest.characteristics import split_name_unit
from app.ingest.units import AMBIGUOUS_SYMBOLS, normalize_unit
from app.portal.values import canonical_map, value_key
from app.search.text import tokens
from app.textclean import clean_text
from app.values import fold

Kind = Literal["int", "decimal", "bool", "dict", "text"]
_KINDS: dict[int, Kind] = {1: "int", 2: "decimal", 4: "bool", 5: "dict", 6: "text"}
# Ссылки на значения справочника: «292902520.292902522».
_DICT_REFS = re.compile(r"^\d+(?:\.\d+)*$")
_NINES = re.compile(r"^9{5,}$")
_PARENS = re.compile(r"\(([^)]*)\)")
# Больше разных значений на характеристику не копим: это уже свободный текст (модели, артикулы).
MAX_TRACKED = 2000


def clean_bound(value: float | None) -> float | None:
    """Граница диапазона без заглушек портала: ±2³¹, ±1e14 и «девятки» (999999) — не граница."""
    if value is None:
        return None
    if abs(value) >= 2147483647 or abs(value) >= 1e14:
        return None
    if float(value).is_integer() and _NINES.match(str(int(abs(value)))):
        return None
    return value


def clean_range(start: float | None, end: float | None) -> NumRange | None:
    lo, hi = clean_bound(start), clean_bound(end)
    return None if lo is None and hi is None else NumRange(min=lo, max=hi)


# «градус» без уточнения: угол или температура — по имени характеристики.
_DEGREES = {"градус", "градусов", "градусы", "град", "град.", "° градус", "°"}
_HEAT = re.compile(r"температур|нагрев|охлажд|мороз|хранени|плавлен|кипен|заморо|термо|цельс")


def canon_unit(raw: str | None, name: str | None = None) -> str | None:
    """Единица стандарта к канону `units.py`: «г; лет» → «лет», «миллиметр (мм)» → «мм».

    «Градус» — угол («Угол наклона»), если имя характеристики не о температуре («Температура
    хранения», «Условия хранения»): словарь сам по одному слову не решает."""
    if not raw or not raw.strip():
        return None
    if raw.strip().casefold() in _DEGREES:
        return "°C" if name and _HEAT.search(name.casefold()) else "°"
    text = raw.replace("[0*]", "°").strip()
    if normalize_unit(text) is None and any(
        m.strip().casefold() in AMBIGUOUS_SYMBOLS for m in _PARENS.findall(text)
    ):
        return text  # «мегаватт (мвт)»: слово и обозначение спорят — как написано
    parts = [p.strip() for p in text.split(";")]
    parts += [m.strip() for p in parts for m in _PARENS.findall(p)]
    parts += [_PARENS.sub("", p).strip() for p in parts]
    known = [(len(p), unit) for p in parts if p and (unit := normalize_unit(p))]
    if known:
        return max(known, key=lambda t: t[0])[1]
    return text


@dataclass
class StandardChar:
    id: int
    name: str
    kind: Kind
    unit: str | None = None
    range: NumRange | None = None
    required: bool = False
    official: bool = False
    level_define: bool = False
    in_name: bool = False
    base_id: int | None = None
    synonyms: list[str] = field(default_factory=list)
    link_ids: set[int] = field(default_factory=set)
    ktru: list[str] = field(default_factory=list)
    spgz: list[str] = field(default_factory=list)
    values: list[str] = field(default_factory=list)


@dataclass
class Standard:
    id: int
    path: str
    name: str
    root: str
    kpgz: str | None
    okpd2: str
    chars: dict[int, StandardChar]
    image_id: int | None = None


def _synonym_names(row: dict[str, Any]) -> list[str]:
    names = [s.get("name") for s in row.get("productCharacteristicSynonyms") or []]
    raw = row.get("productCharacteristicNameSynonymsJson")
    if raw:
        with contextlib.suppress(ValueError, TypeError):
            names += json.loads(raw)
    return [n.strip() for n in names if isinstance(n, str) and n.strip()]


def clean_name(raw: str) -> str:
    """Имя характеристики из стандарта портала для карточки: латинские двойники в русском слове
    («Ресурc»), лишние пробелы и знаки в конце («Тип упаковки:») — убраны."""
    return clean_text(raw).strip().rstrip(":.;, ").strip()


def _parse_char(pcid: int, rows: list[dict[str, Any]]) -> StandardChar:
    first = rows[0]
    kind = _KINDS.get(first.get("productCharacteristicDescriptionTypeId") or 0, "text")
    raw_name = first["productCharacteristicName"].strip()
    char = StandardChar(
        id=pcid,
        name=clean_name(raw_name) or raw_name,
        kind=kind,
        unit=canon_unit(first.get("productCharacteristicUnitDesignationNational"), raw_name),
        base_id=first.get("productCharacteristicBaseCharacteristicId"),
        link_ids={pcid},
    )
    for r in rows:
        char.required |= bool(r.get("isObligatory"))
        char.level_define |= bool(r.get("isLevelDefine"))
        char.in_name |= bool(r.get("isUseInOfferName"))
        char.official |= any(
            r.get(k) is not None for k in ("isObligatory", "ktruValues", "spgzValues")
        )
        char.ktru += [k["code"] for k in r.get("ktruValues") or [] if k.get("code")]
        char.spgz += [s["spgzCode"] for s in r.get("spgzValues") or [] if s.get("spgzCode")]
        char.synonyms += _synonym_names(r)
        char.link_ids |= {
            s["id"] for s in r.get("productCharacteristicSynonyms") or [] if "id" in s
        }
        if kind in ("int", "decimal") and char.range is None:
            prefix = "int" if kind == "int" else "decimal"
            char.range = clean_range(r.get(f"{prefix}StartValue"), r.get(f"{prefix}EndValue"))
        value = r.get("stringValue")
        if isinstance(value, str) and value.strip() and not _DICT_REFS.match(value.strip()):
            char.values.append(value.strip())
    char.official |= char.level_define or char.base_id is not None
    if char.base_id is not None:
        char.link_ids.add(char.base_id)
    char.ktru = list(dict.fromkeys(char.ktru))
    char.spgz = list(dict.fromkeys(char.spgz))
    # Прежнее написание — синоним: СТЕ без id характеристики связываются и по нему.
    char.synonyms = [
        s for s in dict.fromkeys([*char.synonyms, raw_name]) if fold(s) != fold(char.name)
    ]
    char.values = list(dict.fromkeys(char.values))
    return char


def parse_standard(rec: dict[str, Any]) -> Standard:
    """Строка `standards.jsonl` → стандарт; строки шаблона одной характеристики склеиваются."""
    rows: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in rec.get("items") or []:
        if item.get("deleted") or not item.get("productCharacteristicName"):
            continue
        rows[item["productCharacteristicId"]].append(item)
    entity = rec.get("entity") or {}
    return Standard(
        id=rec["id"],
        path=rec["path"],
        name=rec["name"],
        root=rec.get("root") or "",
        kpgz=(entity.get("kpgz") or {}).get("code"),
        okpd2=(entity.get("okpd2") or {}).get("code") or "",
        chars={pcid: _parse_char(pcid, group) for pcid, group in rows.items()},
        image_id=entity.get("image_id"),
    )


def _directory(path: str) -> int | None:
    tail = path.strip(".").split(".")[-1]
    return int(tail) if tail.isdigit() else None


def value_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text or None


# Организационно-правовые формы в названии производителя: на портале «ООО "Туринский ЦБЗ"» и
# «АО ТУРИНСКИЙ ЦБЗ» — один завод. Длинные формы раньше коротких: «АО» — хвост «ПАО».
_FORMS = (
    "общество с ограниченной ответственностью",
    "непубличное акционерное общество",
    "публичное акционерное общество",
    "закрытое акционерное общество",
    "открытое акционерное общество",
    "акционерное общество",
    "индивидуальный предприниматель",
    "федеральное государственное унитарное предприятие",
    "государственное унитарное предприятие",
    "муниципальное унитарное предприятие",
    "производственный кооператив",
    "фгуп",
    "гуп",
    "муп",
    "ооо",
    "оао",
    "зао",
    "пао",
    "нао",
    "ао",
    "ип",
)
_FORM_ALT = "|".join(re.escape(f) for f in sorted(_FORMS, key=len, reverse=True))
_LEADING_FORM = re.compile(rf"^(?:{_FORM_ALT})(?=[\s\"«“„]|$)", re.IGNORECASE)
_TRAILING_FORM = re.compile(rf"[\s,]+(?:{_FORM_ALT})$", re.IGNORECASE)
_QUOTES = re.compile(r"[\"«»“”„]")


def maker_name(raw: str) -> str:
    """Производитель без организационно-правовой формы и кавычек, регистр исходный:
    `ООО "Туринский ЦБЗ"` → «Туринский ЦБЗ». Ключ сравнения — `fold` от результата."""
    text = " ".join(raw.split())
    text = _TRAILING_FORM.sub("", _LEADING_FORM.sub("", text))
    text = " ".join(_QUOTES.sub(" ", text).split())
    return text or " ".join(raw.split())


def _capital(word: str) -> str:
    return re.sub(r"[^\W\d_]", lambda m: m.group().upper(), word, count=1)


def country_title(raw: str) -> str:
    """Страна как пишут люди: «КОРЕЯ, РЕСПУБЛИКА» → «Корея, Республика», союз «и» строчной."""
    words = " ".join(raw.split()).lower().split(" ")
    return " ".join(w if w == "и" else "-".join(_capital(p) for p in w.split("-")) for w in words)


class ValueStats:
    """Значения СТЕ по характеристикам стандарта; копятся потоком по одной записи jsonl."""

    def __init__(self, standards: list[Standard], exclude: set[int] | None = None) -> None:
        # СТЕ отложенной выборки (`build_holdout`) в перечни не идут: иначе оценка видит ответы.
        self.exclude = exclude or set()
        self._by_id: dict[int, dict[int, int]] = {}
        self._by_name: dict[int, dict[str, int]] = {}
        for std in standards:
            ids: dict[int, int] = {}
            names: dict[str, int] = {}
            # Свой id характеристики важнее чужого синонима или базовой: сначала собственные.
            for char in std.chars.values():
                ids.setdefault(char.id, char.id)
            for char in std.chars.values():
                for link in char.link_ids:
                    ids.setdefault(link, char.id)
                for name in (char.name, *char.synonyms):
                    names.setdefault(fold(name), char.id)
            self._by_id[std.id] = ids
            self._by_name[std.id] = names
        self.cards: Counter[int] = Counter()
        self.present: Counter[tuple[int, int]] = Counter()
        self.linked = 0
        self.unlinked: Counter[str] = Counter()
        self.overflow: set[tuple[int, int]] = set()
        self._values: defaultdict[tuple[int, int], Counter[str]] = defaultdict(Counter)
        self._spelling: defaultdict[tuple[int, int, str], Counter[str]] = defaultdict(Counter)
        self._units: defaultdict[tuple[int, int], Counter[str]] = defaultdict(Counter)
        # «Общие сведения»: производители и страны СТЕ категории — подсказки для полей карточки.
        self._general: defaultdict[tuple[int, str], Counter[str]] = defaultdict(Counter)
        self._general_spelling: defaultdict[tuple[int, str, str], Counter[str]] = defaultdict(
            Counter
        )

    def link(self, directory: int, char_id: int | None, name: str) -> int | None:
        ids = self._by_id.get(directory, {})
        if char_id is not None and char_id in ids:
            return ids[char_id]
        return self._by_name.get(directory, {}).get(fold(name))

    def add(self, rec: dict[str, Any]) -> None:
        directory = rec.get("directory_id") or _directory(rec.get("category_path") or "")
        if directory not in self._by_id or rec.get("id") in self.exclude:
            return
        self.cards[directory] += 1
        for kind, source, clean in (
            ("maker", "manufacturer", maker_name),
            ("country", "country", country_title),
        ):
            raw = rec.get(source)
            if isinstance(raw, str) and raw.strip():
                shown = clean(raw)
                self._general[(directory, kind)][fold(shown)] += 1
                self._general_spelling[(directory, kind, fold(shown))][shown] += 1
        seen: set[int] = set()
        for ch in rec.get("characteristics") or []:
            text = value_text(ch.get("value"))
            if text is None:
                continue
            pcid = self.link(directory, ch.get("char_id"), ch.get("name") or "")
            if pcid is None:
                self.unlinked[ch.get("name") or ""] += 1
                continue
            self.linked += 1
            if pcid in seen:
                continue
            seen.add(pcid)
            key = (directory, pcid)
            self.present[key] += 1
            if ch.get("unit"):
                self._units[key][ch["unit"]] += 1
            vkey = value_key(text)
            values = self._values[key]
            if vkey not in values and len(values) >= MAX_TRACKED:
                self.overflow.add(key)
                continue
            values[vkey] += 1
            self._spelling[(directory, pcid, vkey)][text] += 1

    def share(self, directory: int, pcid: int) -> float:
        total = self.cards[directory]
        return self.present[(directory, pcid)] / total if total else 0.0

    def unit(self, directory: int, pcid: int, name: str | None = None) -> str | None:
        units = self._units.get((directory, pcid))
        return canon_unit(units.most_common(1)[0][0], name) if units else None

    def frequent(self, directory: int, pcid: int, min_cards: int = 1) -> dict[str, int]:
        """Варианты значения (`value_key`) не реже `min_cards` СТЕ, без склейки опечаток."""
        raw = self._values.get((directory, pcid), Counter())
        return {v: n for v, n in raw.items() if n >= max(min_cards, 1)}

    def counts(self, directory: int, pcid: int, min_cards: int = 1) -> Counter[str]:
        """Значения со склейкой написаний и опечаток → число СТЕ; редкие (< min_cards) отброшены."""
        frequent = self.frequent(directory, pcid, min_cards)
        canon = canonical_map(
            frequent, lambda v: self._spelling[(directory, pcid, v)].most_common(1)[0][0]
        )
        merged: Counter[str] = Counter()
        for vkey, n in frequent.items():
            merged[canon[vkey]] += n
        return merged

    def general(self, directory: int, kind: str, min_cards: int = 2, limit: int = 50) -> list[str]:
        """Частые производители (`maker`) или страны (`country`) категории: самое частое
        написание, по убыванию числа СТЕ; реже `min_cards` — не подсказка, а случай."""

        def shown(key: str) -> str:
            return self._general_spelling[(directory, kind, key)].most_common(1)[0][0]

        counts = self._general.get((directory, kind), Counter())
        top = sorted(
            ((shown(k), n) for k, n in counts.items() if n >= max(min_cards, 1)),
            key=lambda sn: (-sn[1], sn[0]),
        )
        return [s for s, _ in top[:limit]]

    def values(self, directory: int, pcid: int, min_cards: int = 1) -> list[str]:
        merged = self.counts(directory, pcid, min_cards)
        return [v for v, _ in sorted(merged.items(), key=lambda vn: (-vn[1], vn[0]))]


def _validated(spec: CharacteristicSpec, required: bool) -> CharacteristicSpec:
    """Спецификация собирается по шагам (вид, перечень): правила модели — по итоговому виду и
    перечню, обязательность — из стандарта."""
    return CharacteristicSpec.model_validate({**spec.model_dump(), "required": required})


def _spec(
    std: Standard,
    char: StandardChar,
    stats: ValueStats,
    *,
    min_value_cards: int,
    max_values: int,
    min_coverage: float,
) -> CharacteristicSpec:
    spec = CharacteristicSpec(
        name=char.name,
        kind="qualitative",
        unit=char.unit,
        required=char.required,
        level_define=char.level_define,
        synonyms=char.synonyms,
        ktru=char.ktru,
        spgz=char.spgz,
    )
    if char.kind in ("int", "decimal"):
        spec.kind = "quantitative"
        spec.unit = char.unit or stats.unit(std.id, char.id, char.name)
        spec.range = char.range
        return _validated(spec, char.required)
    if char.kind == "bool":
        spec.values = ["Да", "Нет"]
        return _validated(spec, char.required)
    filled = stats.present[(std.id, char.id)]
    if (std.id, char.id) not in stats.overflow and filled:
        # Решение «перечень или свободный текст» — до склейки опечаток: она квадратична по числу
        # вариантов, а на тысячах вариантов ответ всё равно «свободный текст».
        frequent = stats.frequent(std.id, char.id, min_value_cards)
        covered = sum(frequent.values()) / filled
        if len(frequent) <= max_values and covered >= min_coverage:
            merged = stats.counts(std.id, char.id, min_value_cards)
            spec.values = [v for v, _ in sorted(merged.items(), key=lambda vn: (-vn[1], vn[0]))]
    return _validated(spec, char.required)


# Слова, которые уточняют имя, не меняя характеристику: «Формат листа» — тот же «Формат» у бумаги,
# «Количество штук в упаковке» — «Количество в упаковке».
_FILLER = {"лист", "пачк", "издел", "товар", "штук"}
# Слишком общие имена: к ним присоединяются только уточнители, а не слова категории — «Тип кожи» у
# тоников — не «Тип» тоника.
_GENERIC = {"тип", "вид"}
_WORDS = re.compile(r"[^\W_]+")


def _name_tokens(name: str) -> set[str]:
    """Слова имени без единицы («Вес, грамм»); короткие слова тоже: «грипп А» и «грипп В» —
    разные характеристики, хотя без «А» и «В» имена совпали бы."""
    bare, _ = split_name_unit(name)
    return {stem for w in _WORDS.findall(bare) for stem in (tokens(w) or [fold(w)])}


def _merge_duplicates(specs: list[CharacteristicSpec], category: str) -> list[CharacteristicSpec]:
    """Одноимённые по смыслу характеристики категории — одна: «Формат», «Формат листа», «Формат
    листов». Имя — как в КТРУ: характеристика со ссылками на позиции КТРУ, затем обязательная,
    затем короткая; остальные имена — её синонимы, перечни сливаются. Сливается только то, что
    отличается словами названия категории, единицей или словами-уточнителями («Цвет бумаги» —
    «Цвет» у бумаги, «Цвет корпуса» — нет)."""
    # Только главное слово категории («Плотность бумаги» у «Бумаги для офисной техники»): прочие
    # её слова («с изоляцией») могут назвать другую характеристику — «Материал изоляции».
    lead = _WORDS.findall(category)[:1]
    in_category = set(tokens(lead[0])) if lead else set()
    toks = {id(sp): _name_tokens(sp.name) for sp in specs}

    def extra_ok(extra: set[str], into: set[str]) -> bool:
        allowed = _FILLER if into <= _GENERIC else _FILLER | in_category
        return extra <= allowed

    def compatible(a: CharacteristicSpec, b: CharacteristicSpec) -> bool:
        if a.kind != b.kind or a.group == "general" or b.group == "general":
            return False
        ua, ub = normalize_unit(a.unit), normalize_unit(b.unit)
        return a.kind == "qualitative" or ua is None or ub is None or ua == ub

    def rank(sp: CharacteristicSpec) -> tuple[bool, bool, int, str]:
        # Имя — последний ключ: при равном весе главное одно, а не каждое — другое.
        return (not sp.ktru, not sp.required, len(toks[id(sp)]), sp.name)

    target: dict[int, CharacteristicSpec] = {}
    for sp in specs:
        mine = toks[id(sp)]
        if not mine:
            continue
        options = [
            other
            for other in specs
            if other is not sp
            and toks[id(other)]
            and toks[id(other)] <= mine
            and extra_ok(mine - toks[id(other)], toks[id(other)])
            and compatible(sp, other)
            and rank(other) <= rank(sp)
        ]
        if options:
            target[id(sp)] = min(options, key=rank)

    def final(sp: CharacteristicSpec) -> CharacteristicSpec:
        seen = set()
        while id(sp) in target and id(sp) not in seen:
            seen.add(id(sp))
            sp = target[id(sp)]
        return sp

    merged: dict[int, CharacteristicSpec] = {}
    order: list[int] = []
    for sp in specs:
        head = final(sp)
        if id(head) not in merged:
            merged[id(head)] = head.model_copy(deep=True)
            order.append(id(head))
        if head is sp:
            continue
        into = merged[id(head)]
        into.synonyms = list(dict.fromkeys([*into.synonyms, sp.name, *sp.synonyms]))
        have = {fold(v) for v in into.values}
        into.values = [*into.values, *(v for v in sp.values if fold(v) not in have)]
        into.ktru = list(dict.fromkeys([*into.ktru, *sp.ktru]))
        into.required = into.required or sp.required
    return [merged[i] for i in order]


def _absorb(into: CharacteristicSpec, sp: CharacteristicSpec) -> None:
    into.synonyms = list(dict.fromkeys([*into.synonyms, sp.name, *sp.synonyms]))
    have = {fold(v) for v in into.values}
    into.values = [*into.values, *(v for v in sp.values if fold(v) not in have)]
    into.ktru = list(dict.fromkeys([*into.ktru, *sp.ktru]))
    into.required = into.required or sp.required


def merge_pairs(
    specs: list[CharacteristicSpec], pairs: list[list[str]]
) -> list[CharacteristicSpec]:
    """Пары «одна характеристика» по смыслу (`app.tools.find_synonyms`, проверены по СТЕ): поле с
    именем как в КТРУ (со ссылками на КТРУ, затем обязательное, затем короткое), второе имя —
    синоним, перечни и ссылки сливаются. Пара с именем не из позиции пропускается."""
    by_name = {sp.name: sp for sp in specs}
    parent = {sp.name: sp.name for sp in specs}

    def root(name: str) -> str:
        while parent[name] != name:
            name = parent[name]
        return name

    def rank(sp: CharacteristicSpec) -> tuple[bool, bool, int, str]:
        return (not sp.ktru, not sp.required, len(sp.name), sp.name)

    for a, b in (pair[:2] for pair in pairs if len(pair) >= 2):
        if a not in by_name or b not in by_name:
            continue
        ra, rb = root(a), root(b)
        if ra == rb:
            continue
        head, tail = sorted((ra, rb), key=lambda n: rank(by_name[n]))
        parent[tail] = head
    merged: dict[str, CharacteristicSpec] = {}
    for sp in specs:
        head = root(sp.name)
        if head not in merged:
            merged[head] = by_name[head].model_copy(deep=True)
    for sp in specs:
        head = root(sp.name)
        if head != sp.name:
            _absorb(merged[head], sp)
    return [merged[root(sp.name)] for sp in specs if root(sp.name) == sp.name]


def _general_specs(directory: int, stats: ValueStats, min_cards: int) -> list[CharacteristicSpec]:
    """«Общие сведения»: производитель и страна с подсказками из СТЕ категории, модель — свободно.
    Перечни — подсказки, а не ограничение: новый производитель модерацию не блокирует."""
    maker, model, country = GENERAL_NAMES
    makers = stats.general(directory, "maker", min_cards)
    countries = stats.general(directory, "country", min_cards)
    return [
        CharacteristicSpec(name=maker, kind="qualitative", values=makers, group="general"),
        CharacteristicSpec(name=model, kind="qualitative", group="general"),
        CharacteristicSpec(name=country, kind="qualitative", values=countries, group="general"),
    ]


def tree_index(tree: list[dict[str, Any]]) -> dict[int, str]:
    """id узла дерева `tree.json` (узлы `id`, `name`, `subCategories`) → его имя."""
    names: dict[int, str] = {}
    stack = list(tree)
    while stack:
        node = stack.pop()
        if isinstance(node.get("id"), int) and node.get("name"):
            names[node["id"]] = str(node["name"]).strip()
        stack.extend(node.get("subCategories") or [])
    return names


def _on_path(names: dict[int, str], tree_path: str) -> list[str]:
    ids = [int(p) for p in tree_path.strip(".").split(".") if p.isdigit()]
    return [names[i] for i in ids if i in names]


def path_names(tree: list[dict[str, Any]], tree_path: str) -> list[str]:
    """Имена узлов пути категории `.1.<id>.<id>.` по дереву `tree.json`. Корня «Товары» в дереве
    нет — его id пропускается, как и любой незнакомый."""
    return _on_path(tree_index(tree), tree_path)


def position_codes(standards: list[Standard]) -> dict[int, str]:
    """Код позиции категории: код КПГЗ; у повторяющегося кода — «<КПГЗ>/<id категории>»."""
    kpgz = Counter(s.kpgz for s in standards if s.kpgz)
    return {
        s.id: (s.kpgz if kpgz[s.kpgz] == 1 else f"{s.kpgz}/{s.id}") if s.kpgz else f"КАТ {s.id}"
        for s in standards
    }


def build_standard_positions(
    standards: list[Standard],
    stats: ValueStats,
    *,
    min_share: float = 0.1,
    min_value_cards: int = 2,
    max_values: int = 300,
    min_coverage: float = 0.8,
    tree: list[dict[str, Any]] | None = None,
    images: dict[int, int | None] | None = None,
) -> list[Position]:
    """Позиция на категорию: официальные характеристики (обязательные или заполненные) и
    добавленные поставщиками с долей заполнения не ниже `min_share`.

    Перечень — значения не реже `min_value_cards` СТЕ; если их больше `max_values` или они
    покрывают меньше `min_coverage` заполнений, это свободный текст: перечень пустой.
    """
    codes = position_codes(standards)
    names = tree_index(tree) if tree else {}
    positions = []
    for std in standards:
        specs: dict[str, CharacteristicSpec] = {}
        level_only = False  # были поля уровня — категория остаётся позицией и без характеристик
        ranked = sorted(std.chars.values(), key=lambda c: -stats.present[(std.id, c.id)])
        for char in ranked:
            filled = stats.present[(std.id, char.id)]
            keep = (
                char.required
                or (char.official and filled > 0)
                or stats.share(std.id, char.id) >= min_share
            )
            # Поле уровня классификатора без перечня, которое не заполняет ни одна СТЕ («Виды бумаги
            # для канцелярских нужд»), — признак категории, а не характеристика поставщика: 30 тыс.
            # таких «обязательных» делали каждую карточку «нужно исправить».
            if char.level_define and char.kind in ("text", "dict") and not filled:
                level_only = True
                continue
            # Два id с одним именем в шаблоне — одна характеристика: остаётся более заполненная.
            if not keep or fold(char.name) in specs:
                continue
            specs[fold(char.name)] = _spec(
                std,
                char,
                stats,
                min_value_cards=min_value_cards,
                max_values=max_values,
                min_coverage=min_coverage,
            )
        if not specs and not level_only:
            continue
        specs = {fold(sp.name): sp for sp in _merge_duplicates(list(specs.values()), std.name)}
        # Поля карточки портала. Если стандарт сам задаёт такую характеристику, она остаётся
        # характеристикой категории, а не дублируется полем «Общих сведений».
        for spec in _general_specs(std.id, stats, min_value_cards):
            specs.setdefault(fold(spec.name), spec)
        positions.append(
            Position(
                code=codes[std.id],
                name=std.name,
                okpd2=std.okpd2,
                kpgz=std.kpgz,
                characteristics=list(specs.values()),
                source="portal-standards",
                portal_id=std.id,
                path=_on_path(names, std.path),
                # Картинка из повторной выгрузки привязок (`entities.jsonl`) важнее старой строки,
                # но пустая в ней не стирает найденную полной выгрузкой.
                image_id=(images.get(std.id) if images else None) or std.image_id,
            )
        )
    return positions
