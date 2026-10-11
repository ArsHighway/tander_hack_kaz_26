"""Опечатки в наименовании и описании.

Поиск категории исправляет незнакомое слово на единственное ближайшее слово справочника
(`BM25Index.correct`: «треггер» → «триггер») и молча находит позицию; модератор же опечатку видит.
Здесь исправление становится замечанием, только когда ему можно верить: исправленное слово есть в
тексте найденной позиции, слова различаются одной буквой (два различия — уже другое слово:
«монобиполярный» — «монополярный»), это не другая форма того же слова («вельветовые» — «вельвет»,
«колготок» — «колготки»), не бренд, модель или сокращение (латиница, капс, кавычки, заглавная
внутри фразы, точка после слова), само слово почти не встречается в наименованиях СТЕ портала, а
исправленное встречается («Электрододержатель» пишут на портале; «Разъеденительный» пишет только
справочник). На 44 тыс. карточек отложенной выборки — 113 замечаний (было 105 до удвоенных букв и
относительного порога), почти все — настоящие опечатки («Кроншейн», «треггер», «акккумуляторный»).
"""

import regex as re

from app.catalog import Position
from app.schemas import Fix, Issue
from app.search.bm25 import BM25Index
from app.search.exemplars import ExemplarIndex
from app.search.text import tokens
from app.values import damerau_levenshtein

RULE_TYPO = "слова наименования и описания написаны без опечаток"
MIN_LEN = 5
# Слово встречается в наименованиях СТЕ портала больше MAX_PORTAL_DF раз и не реже 1/20
# исправления — так пишут, это не опечатка. Реже — частая ошибка («металический» при
# «металлический» в 20+ раз чаще): отмечаем.
MAX_PORTAL_DF = 2
PORTAL_RATIO = 20
# Исправление встречается реже — так пишет только справочник («Разъеденительный»), не поверим.
MIN_FIXED_DF = 3
_WORD = re.compile(r"\p{L}+(?:-\p{L}+)*")
_LATIN = re.compile(r"[A-Za-z]")
_QUOTES = "«»\"'“”„"
_SENTENCE = re.compile(r"(?:^|[.;:!?\n]\s*)$")


def _brand_like(text: str, m: re.Match[str]) -> bool:
    word = m[0]
    if _LATIN.search(word) or (len(word) > 1 and word.isupper()):
        return True
    before = text[m.start() - 1] if m.start() > 0 else ""
    after = text[m.end()] if m.end() < len(text) else ""
    if (before and before in _QUOTES) or (after and after in _QUOTES):
        return True
    # Заглавная не в начале фразы — имя собственное: «Мебель ED Форум», «кроватка "Машенька"».
    return word[0].isupper() and not _SENTENCE.search(text[: m.start()])


def _fleeting(stem: str, fixed: str) -> bool:
    """Беглая гласная: «колготок» — «колготк(и)», «бляшек» — «бляшк(а)»."""
    return len(stem) > 2 and stem[:-2] + stem[-1] == fixed


def _doubled(stem: str, fixed: str) -> bool:
    """Лишняя удвоенная буква: «курткк(а)» — «куртк(а)». Это опечатка, а не другая форма."""
    return len(stem) == len(fixed) + 1 and any(
        stem[i] == stem[i - 1] and stem[:i] + stem[i + 1 :] == fixed for i in range(1, len(stem))
    )


def _abbreviation(text: str, m: re.Match[str]) -> bool:
    """Сокращение с точкой: «инъект. р-р», «метал. мебель»."""
    rest = text[m.end() :]
    return rest.startswith(".") and not rest[1:].lstrip()[:1].isupper()


def _corrected(word: str, stem: str, fixed: str) -> str | None:
    """Слово с исправленной основой и прежним окончанием: «треггера» → «триггера»."""
    if not word.casefold().replace("ё", "е").startswith(stem):
        return None
    out = fixed + word[len(stem) :]
    return out[0].upper() + out[1:] if word[0].isupper() else out


_EN = "qwertyuiop[]asdfghjkl;'zxcvbnm,.`QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>~"
_RU = "йцукенгшщзхъфывапролджэячсмитьбюёЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮЁ"
_LAYOUT = str.maketrans(_EN, _RU)
_KEYS = re.escape(_EN)
# Слово, набранное в английской раскладке: латиница и клавиши русских букв («[» — «х», «,» — «б»),
# без цифр рядом — модели «DF333DWYE» и «HR2460» не трогаем.
# Дефис рядом — часть составного имени («Fast-Cath», «Wi-Fi»), а не слово в раскладке.
_LAYOUT_WORD = re.compile(rf"(?<![\p{{L}}\d-])[{_KEYS}]*[A-Za-z][{_KEYS}]*(?![\p{{L}}\d-])")
_EDGE = ",.;:'\"<>"
MIN_LAYOUT_LETTERS = 4
# Служебные слова короче порога переводятся, только если рядом уже переведено слово:
# «cnek lkz jabcf» — «стул для офиса», а не «стул lkz офиса».
_SERVICE_WORDS = frozenset({"для", "из", "на", "по", "без", "под", "от", "до", "при", "над"})


def _layout_word(word: str, bm25: BM25Index) -> str | None:
    """Перевод слова по раскладке, если он — слова справочника, а исходное слово — нет."""
    if any(ch.isupper() for ch in word[1:]):
        return None  # капс и «GLL», «dGH» — модели и бренды: «GFC» — не «ПАС»
    for cand in dict.fromkeys((word, word.strip(_EDGE), word.rstrip(_EDGE))):
        if sum(ch.isalpha() for ch in cand) < MIN_LAYOUT_LETTERS:
            continue
        own = tokens(cand)
        if any(bm25.knows(t) for t in own):
            return None  # латинское слово справочник знает — это не раскладка
        ru = cand.translate(_LAYOUT)
        stems = tokens(ru)
        # Короткая основа совпадает случайно: «Vest» — «Муые» → «му».
        if stems and all(len(t) >= MIN_LAYOUT_LETTERS and bm25.knows(t) for t in stems):
            return word.replace(cand, ru)
    return None


def layout_fix(text: str, bm25: BM25Index) -> tuple[str, list[str]]:
    """Текст с переведёнными по раскладке словами и список исходных слов
    («cnek jabcysq» → «стул офисный»). Бренды латиницей остаются: их перевод — не слова."""
    changed: list[tuple[int, str]] = []

    def sub(m: re.Match[str]) -> str:
        ru = _layout_word(m[0], bm25)
        if ru is None:
            return m[0]
        changed.append((m.start(), m[0]))
        return ru

    def service(m: re.Match[str]) -> str:
        ru = m[0].translate(_LAYOUT)
        if not m[0].islower() or ru not in _SERVICE_WORDS:
            return m[0]
        changed.append((m.start(), m[0]))
        return ru

    out = _LAYOUT_WORD.sub(sub, text)
    if changed:  # перевод по раскладке посимвольный: позиции слов не сдвигаются
        out = _LAYOUT_WORD.sub(service, out)
    return out, [word for _, word in sorted(changed)]


def _capital(name: str) -> str:
    """Наименование на портале начинается с заглавной: «cnek jabcysq» — «Стул офисный»."""
    return name[:1].upper() + name[1:]


def layout_issues(name: str, description: str, bm25: BM25Index) -> list[Issue]:
    """Замечание «похоже на раскладку» (warn) с исправленным наименованием."""
    issues: list[Issue] = []
    for field, text in (("name", name), ("description", description)):
        fixed, changed = layout_fix(text, bm25)
        if not changed:
            continue
        was = " ".join(changed)
        right = layout_fix(was, bm25)[0]
        issues.append(
            Issue(
                field=field,
                kind="typo",
                severity="warn",
                message=f"Похоже на раскладку: «{was}» — «{right}».",
                why="Текст набран в английской раскладке. Поиск понял его по-русски, "
                "а модератор увидит набор латинских букв.",
                rule=RULE_TYPO,
                fix=Fix(value=_capital(fixed)) if field == "name" else None,
            )
        )
    return issues


def typo_issues(
    name: str,
    description: str,
    position: Position,
    bm25: BM25Index,
    exemplars: ExemplarIndex | None,
) -> list[Issue]:
    """Замечания «похоже на опечатку» по наименованию (warn, с исправленным наименованием) и
    описанию (info)."""
    doc = bm25.doc_tokens(position.code)
    issues: list[Issue] = []
    for field, text in (("name", name), ("description", description)):
        fixed_text, seen = text, set()
        for m in _WORD.finditer(text):
            word = m[0]
            stems = tokens(word)
            if len(word) < MIN_LEN or len(stems) != 1 or _brand_like(text, m):
                continue
            stem = stems[0]
            fixed = bm25.correct(stem)
            if fixed == stem or fixed not in doc:
                continue
            form = stem.startswith(fixed) or fixed.startswith(stem) or _fleeting(stem, fixed)
            if form and not _doubled(stem, fixed):
                continue  # другая форма того же слова, а не опечатка
            if damerau_levenshtein(stem, fixed) != 1 or _abbreviation(text, m):
                # Два различия — уже другое слово («монобиполярный» — «монополярный»).
                continue
            if exemplars is not None and (
                (
                    exemplars.df(stem) > MAX_PORTAL_DF
                    and exemplars.df(stem) * PORTAL_RATIO >= exemplars.df(fixed)
                )
                or exemplars.df(fixed) < MIN_FIXED_DF
            ):
                continue
            right = _corrected(word, stem, fixed)
            if right is None or word in seen:
                continue
            seen.add(word)
            fixed_text = fixed_text.replace(word, right)
            issues.append(
                Issue(
                    field=field,
                    kind="typo",
                    severity="warn" if field == "name" else "info",
                    message=f"Похоже на опечатку: «{word}» — «{right}».",
                    why=f"Так пишут в справочнике: позиция «{position.name}». Поиск понял слово, "
                    "а модератор увидит опечатку.",
                    rule=RULE_TYPO,
                    fix=Fix(value=fixed_text) if field == "name" else None,
                )
            )
        if field == "name" and len(issues) > 1:
            # Исправление наименования — одно, со всеми найденными опечатками сразу.
            for issue in issues:
                issue.fix = Fix(value=fixed_text)
    return issues
