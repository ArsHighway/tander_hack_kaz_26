"""Ненормативная лексика и оскорбления в карточке: блокирующее замечание на поле, где они найдены.

Поставщики прячут мат: латинские и цифровые двойники букв («xуй», «п1зда»), звёздочки («бл*ть»),
пробелы и точки между буквами («б л я д ь»), невидимые символы, повтор букв, транслит («blyat»).
Текст нормализуется, слова сверяются с корнями. Корни привязаны к началу слова (с типовыми
приставками), иначе ловятся обычные слова: «употреблять», «скипидар», «застрахуй». Слова-исключения
проверяются до корней.
"""

import unicodedata

import regex as re

from app.schemas import CardIn, Issue

RULE_PROFANITY = "в карточке нет ненормативной лексики и оскорблений"

# Двойники в смешанном слове: латиница и цифры вместо кириллицы.
_LOOKALIKE = str.maketrans(
    {"a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у", "k": "к", "m": "м",
     "t": "т", "h": "н", "b": "в", "0": "о", "3": "з", "4": "ч", "6": "б", "1": "и", "@": "а"}
)  # fmt: skip
_INVISIBLE = re.compile(r"[\u00ad\u200b-\u200f\u2060\ufeff\p{M}]")
# Буквы через разделитель: «б л я д ь», «б.л.я.д.ь», «б-л-я».
_SPACED = re.compile(r"(?<![\p{L}*])[\p{L}*](?:[\s.\-_·•]+[\p{L}*](?![\p{L}*])){2,}")
_WORD = re.compile(r"[\p{L}\d@*]+")
_CYRILLIC = re.compile(r"\p{Cyrillic}")
_VOWELS = "аеиоуыэюя"

_PREFIX = r"(?:за|на|по|от|вы|до|у|о|об|раз|рас|при|под|съ|подъ|изъ|отъ|въ|ни|долбо|за|с)?"
_CYR_ROOTS = re.compile(
    rf"^{_PREFIX}ху[йеияю]"
    r"|пизд|пезд"
    r"|^бля[дт]|^бля$"
    rf"|^{_PREFIX}еб(?:а|у|л|ну|и|ы|ош|ен|ет|$)"
    r"|мудак|мудил|мудач"
    r"|^пид[оа]р|^пидр"
    r"|^педик(?!юр)"
    r"|г[ао]ндон|шлюх|залуп|говн|^жоп"
    r"|^сук[аиуо]$|^сучар|^сучий|^сукин"
    r"|дебил|^дегенерат(?!ив)"
)
_LATIN_ROOTS = re.compile(
    r"^bl[iyj]a[dt]|^pizd|^(?:na|po)?hu[iy]$|^(?:na|po)?xu[iy]$|^khu[iy]|^eba[tln]|^yoban"
    r"|^suk[ai]$|^pid[oa]r|^mudak|^gandon|^debil"
)
_EXCEPTIONS = re.compile(r"^(?:за)?страху|^хуан|^бляш|^бляха|^бляхи|^скипидар|^педикюр")


def _variants(word: str) -> set[str]:
    """Слово как написано, без повторов букв и со звёздочкой, заменённой гласной."""
    out = {word, re.sub(r"(.)\1+", r"\1", word)}
    if "*" in word:
        out |= {v.replace("*", g, 1) for v in list(out) for g in _VOWELS}
        out = {v.replace("*", "") for v in out}
    return out


def _bad(word: str) -> bool:
    if sum(ch.isalpha() for ch in word) < 2 and any(ch.isdigit() for ch in word):
        return False  # «Е6», «Т4» — буква с номером (бита, прибор), цифра тут не двойник буквы
    if _CYRILLIC.search(word) or not word.isascii():
        word = word.translate(_LOOKALIKE)
        return any(not _EXCEPTIONS.match(v) and _CYR_ROOTS.search(v) for v in _variants(word))
    return any(_LATIN_ROOTS.search(v) for v in _variants(word))


def find_profanity(text: str) -> str | None:
    """Первое недопустимое слово в нормализованном виде или None."""
    norm = _INVISIBLE.sub("", unicodedata.normalize("NFKC", text).lower()).replace("ё", "е")
    collapsed = _SPACED.sub(lambda m: re.sub(r"[\s.\-_·•]+", "", m.group()), norm)
    for source in (collapsed, norm):
        for word in _WORD.findall(source):
            if _bad(word):
                return word
    return None


def _issue(field: str, where: str) -> Issue:
    return Issue(
        field=field,
        kind="moderation_rule",
        severity="block",
        # Само слово не повторяем: замечание читают модератор и поставщик.
        message=f"{where}: ненормативная лексика или оскорбление.",
        why=f"Правило модерации портала: {RULE_PROFANITY}.",
        rule=RULE_PROFANITY,
    )


def profanity_issues(card: CardIn) -> list[Issue]:
    issues = []
    if find_profanity(card.name):
        issues.append(_issue("name", "В наименовании"))
    if find_profanity(card.description):
        issues.append(_issue("description", "В описании"))
    for c in card.characteristics:
        if find_profanity(f"{c.name} {c.value}"):
            issues.append(_issue(f"characteristics.{c.name}", f"В характеристике «{c.name}»"))
    return issues
