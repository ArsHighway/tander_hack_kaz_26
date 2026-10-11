"""Очистка текста поставщика на входе API.

Из Word и с сайтов приходят невидимые символы (нулевой ширины, мягкий перенос) и неразрывные
пробелы: «Бу\u200bмага» не находится поиском. Латинская «o» в «листoв» ломает и поиск,
и сверку с перечнем. Латиница меняется на кириллицу только внутри слова, где кириллицы больше:
коды «CE285A» и «A4» остаются.

Карточку копируют и с сайтов: HTML-теги («<li><b>Мощность:</b> 780 Вт</li>») становятся
переносами строк, а JSON («{"Модель": "Ольха-45"}») — строками «Модель: Ольха-45». Меняется только
разметка, не содержание: иначе теги попадали в значения полей.
"""

import html
import json
from typing import Any

import regex as re

_INVISIBLE = re.compile(r"[\u00ad\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
_SPACES = re.compile(r"[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]")
_WORD = re.compile(r"\p{L}+")
_CYRILLIC = re.compile(r"\p{Cyrillic}")
_LATIN = re.compile(r"\p{Latin}")
_LOOKALIKE = str.maketrans(
    {"a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у", "k": "к", "m": "м",
     "t": "т", "h": "н", "b": "в", "A": "А", "C": "С", "E": "Е", "O": "О", "P": "Р", "X": "Х",
     "K": "К", "M": "М", "T": "Т", "H": "Н", "B": "В"}
)  # fmt: skip


def _cyrillize(m: re.Match[str]) -> str:
    word = m.group()
    if len(_CYRILLIC.findall(word)) > len(_LATIN.findall(word)) > 0:
        return word.translate(_LOOKALIKE)
    return word


_BLOCK_TAG = re.compile(
    r"</?(?:p|div|li|ul|ol|br|h[1-6]|tr|table|tbody|thead|section|article|dl|dt|dd)\b[^<>]*>",
    re.IGNORECASE,
)
_CELL_TAG = re.compile(r"</t[dh]>\s*<t[dh]\b[^<>]*>", re.IGNORECASE)
_INLINE_TAG = re.compile(
    r"</?(?:b|i|u|em|strong|span|small|sup|sub|font|a|mark|td|th|code)\b[^<>]*>", re.IGNORECASE
)
_BLANK_LINES = re.compile(r"[ \t]*\n\s*")


def _unwrap_html(text: str) -> str:
    """Теги сайта — переносы строк: «<li><b>Окрас:</b> синий</li>» → «Окрас: синий»."""
    if not (_BLOCK_TAG.search(text) or _INLINE_TAG.search(text)):
        return text
    text = _CELL_TAG.sub(" | ", text)
    text = _BLOCK_TAG.sub("\n", text)
    text = _INLINE_TAG.sub("", text)
    return _BLANK_LINES.sub("\n", html.unescape(text)).strip()


def _flat(value: dict[str, Any] | list[Any], out: list[str]) -> None:
    items = value.items() if isinstance(value, dict) else enumerate(value)
    for key, v in items:
        if isinstance(v, dict | list):
            _flat(v, out)
        else:
            out.append(f"{key}: {v}" if isinstance(value, dict) else str(v))


def _unwrap_json(text: str) -> str:
    """Объект JSON в тексте — строками «ключ: значение»; не разобрался — текст как был."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start or '":' not in text[start:end]:
        return text
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return text
    if not isinstance(data, dict | list):
        return text
    lines: list[str] = []
    _flat(data, lines)
    if not lines:
        return text
    parts = (text[:start].strip(), *lines, text[end + 1 :].strip())
    return "\n".join(x for x in parts if x)


def clean_text(text: str) -> str:
    text = _SPACES.sub(" ", _INVISIBLE.sub("", text))
    text = _unwrap_json(_unwrap_html(text))
    return _WORD.sub(_cyrillize, text)
