"""Токены для поиска: свёртка регистра и латинских двойников, стемминг, без служебных слов."""

from functools import lru_cache
from itertools import pairwise

import regex as re
import snowballstemmer

from app.ingest.units import normalize_unit
from app.values import fold

_STOP = {"и", "в", "во", "на", "по", "для", "с", "со", "из", "от", "до", "к", "о", "об", "у", "не"}
_NEGATION = {"не", "без"}
_TOKEN = re.compile(r"[\p{L}\p{N}]+")
_WORD = re.compile(r"\p{L}+")
_stemmer = snowballstemmer.stemmer("russian")
# Короткие токены (коды «AA», «A4», размер «M») не стеммим: «аа» не должно стать «а»
# и совпасть с «A».
MIN_STEM_LEN = 4
# Значимое слово наименования — не короче этого: «для», «шт», «и» смысла товара не несут.
MIN_SIGNIFICANT_LEN = 4


@lru_cache(maxsize=65536)
def _stem(word: str) -> str:
    return _stemmer.stemWord(word) if word.isalpha() and len(word) >= MIN_STEM_LEN else word


def tokens(text: str) -> list[str]:
    return [_stem(w) for w in _TOKEN.findall(fold(text)) if w not in _STOP]


def words_with_stems(text: str) -> list[tuple[str, str]]:
    """Исходное слово и его стем — чтобы объяснять совпадение словами пользователя."""
    return [(w, _stem(fold(w))) for w in _TOKEN.findall(text) if fold(w) not in _STOP]


def negated_stems(text: str) -> set[str]:
    """Стемы слов после «не» и «без»: «не нитриловые», «без хлора»."""
    words = [fold(w) for w in _TOKEN.findall(text)]
    return {_stem(b) for a, b in pairwise(words) if a in _NEGATION}


def without_negations(text: str) -> str:
    """Текст без отрицаний и отрицаемых слов: по ним позицию не ищем."""
    out, skip = [], False
    for w in _TOKEN.findall(text):
        if fold(w) in _NEGATION:
            skip = True
            continue
        if not skip:
            out.append(w)
        skip = False
    return " ".join(out)


def _brand_like(word: str, first: bool) -> bool:
    """Бренд, модель, аббревиатура: заглавная не в начале, все заглавные, латиница."""
    if word.isascii():
        return True
    if len(word) > 1 and word.isupper():
        return True
    return not first and word[:1].isupper()


# Цвет и служебные слова («артикул», «размер») — свойства, а не сам товар: «синие» перчатки не
# требуют подтверждать позицию «Перчатки смотровые».
_COLOUR_BASES = (
    "бел", "черн", "чёрн", "син", "красн", "зелен", "зелён", "желт", "жёлт", "сер", "оранжев",
    "фиолетов", "розов", "голуб", "коричнев", "бежев", "серебрист", "золотист", "прозрачн",
    "бирюзов", "салатов", "бордов", "сиренев", "графитов",
)  # fmt: skip
_ADJ_ENDING = re.compile(r"(?:ый|ий|ой|ая|яя|ое|ее|ые|ие|ую|юю|ого|его|ых|их|ым|им)\Z")
_SERVICE = {"цвет", "цвета", "размер", "размера", "модель", "модели", "артикул", "арт", "серия"}


def _attribute_word(folded: str) -> bool:
    if folded in _SERVICE:
        return True
    end = _ADJ_ENDING.search(folded)
    return end is not None and folded[: end.start()] in _COLOUR_BASES


def significant_words(name: str) -> list[tuple[str, str]]:
    """Слова наименования, которые описывают товар: (как в тексте, стем).

    Не считаются: короткие и служебные, единицы, числа, бренды и модели, слова после «не»/«без».
    """
    out = []
    negated = negated_stems(name)
    for i, m in enumerate(_WORD.finditer(name)):
        word = m[0]
        folded = fold(word)
        if len(folded) < MIN_SIGNIFICANT_LEN or folded in _STOP or normalize_unit(folded):
            continue
        if _brand_like(word, first=i == 0) or _attribute_word(folded):
            continue
        stem = _stem(folded)
        if stem not in negated:
            out.append((word, stem))
    return out
