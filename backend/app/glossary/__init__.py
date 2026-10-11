"""Словарь аббревиатур: чтобы модель и поиск понимали язык закупщиков, и добыча новых из данных."""

import csv
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from app.ingest.units import normalize_unit

SEED_PATH = Path(__file__).with_name("abbreviations.csv")
_LETTER = "А-ЯЁа-яёA-Za-z0-9"
_TOKEN = re.compile(r"(?<![А-ЯЁа-яёA-Za-z0-9])[А-ЯЁA-Z]{2,6}\d?(?![А-ЯЁа-яёA-Za-z0-9])")
_ROMAN = re.compile(r"^[IVXLC]+$")
_PAREN = re.compile(r"([А-ЯЁа-яёA-Za-z][^()]{2,120}?)\s*\(\s*([А-ЯЁа-яёA-Za-z]{2,6})\s*\)")
_STOP = {"и", "в", "для", "на", "по", "с", "о", "от", "к", "of", "and", "for"}


@dataclass(frozen=True)
class Abbreviation:
    abbr: str
    full: str
    kind: str
    case_insensitive: bool = False
    note: str = ""


class Glossary:
    def __init__(self, entries: Iterable[Abbreviation]) -> None:
        self.by_abbr: dict[str, list[Abbreviation]] = defaultdict(list)
        for e in entries:
            self.by_abbr[e.abbr].append(e)
        # Длинные первыми: «ТР ТС» раньше «ТР».
        alts = sorted(self.by_abbr, key=len, reverse=True)
        self._exact = re.compile(
            rf"(?<![{_LETTER}])({'|'.join(map(re.escape, alts))})(?![{_LETTER}])"
        )
        ci = [a for a in alts if any(e.case_insensitive for e in self.by_abbr[a])]
        self._ci = (
            re.compile(rf"(?<![{_LETTER}])({'|'.join(map(re.escape, ci))})(?![{_LETTER}])", re.I)
            if ci
            else None
        )

    @classmethod
    def load(cls, path: Path = SEED_PATH) -> "Glossary":
        with path.open(encoding="utf-8", newline="") as fh:
            rows = csv.DictReader(fh, delimiter=";")
            return cls(
                Abbreviation(
                    r["abbr"], r["full"], r["kind"], r["case_insensitive"] == "1", r["note"]
                )
                for r in rows
            )

    def __contains__(self, abbr: str) -> bool:
        return abbr in self.by_abbr or abbr.upper() in self.by_abbr

    def lookup(self, abbr: str) -> list[Abbreviation]:
        return self.by_abbr.get(abbr) or self.by_abbr.get(abbr.upper(), [])

    def find(self, text: str) -> list[str]:
        """Аббревиатуры из словаря, встреченные в тексте (в каноническом написании)."""
        found = [m.group(1) for m in self._exact.finditer(text)]
        if self._ci:
            found += [m.group(1).upper() for m in self._ci.finditer(text)]
        return list(dict.fromkeys(found))

    def expand(self, text: str) -> str:
        """Подписывает расшифровку при первом упоминании: «ПВД (полиэтилен высокого давления)».

        Неоднозначные аббревиатуры получают все варианты через « | » — выбирает модель по контексту.
        """
        done: set[str] = set()

        def repl(m: re.Match[str]) -> str:
            key = m.group(1).upper() if m.group(1) not in self.by_abbr else m.group(1)
            if key in done or key not in self.by_abbr:
                return m.group(0)
            done.add(key)
            fulls = " | ".join(dict.fromkeys(e.full for e in self.by_abbr[key]))
            return f"{m.group(0)} ({fulls})"

        text = self._exact.sub(repl, text)
        return self._ci.sub(repl, text) if self._ci else text

    def prompt_block(self, text: str) -> str:
        """Строки словаря для промпта LLM — только по аббревиатурам из текста."""
        lines = []
        for abbr in self.find(text):
            for e in self.lookup(abbr):
                lines.append(f"- {abbr} — {e.full}" + (f" ({e.note})" if e.note else ""))
        return "\n".join(lines)


@dataclass
class Candidate:
    abbr: str
    count: int = 0
    known: bool = False
    expansions: Counter[str] = field(default_factory=Counter)
    examples: list[str] = field(default_factory=list)


def _initials_match(words: list[str], abbr: str) -> str | None:
    """Слова в хвосте фразы, первые буквы которых дают аббревиатуру (служебные — пропускаются)."""
    letters = abbr.casefold()
    for start in range(len(words) - 1, -1, -1):
        tail = [w for w in words[start:] if w.casefold() not in _STOP]
        initials = "".join(w[0].casefold() for w in tail)
        if initials == letters:
            return " ".join(words[start:])
        if len(initials) > len(letters):
            break
    # Сложное слово: АЛТ — «аланинаминотрансфераза», буквы идут внутри слова по порядку.
    last = words[-1].casefold() if words else ""
    it = iter(last)
    if last.startswith(letters[:1]) and all(ch in it for ch in letters):
        return words[-1]
    return None


def mine(
    texts: Iterable[str], glossary: Glossary | None = None, examples: int = 3
) -> list[Candidate]:
    """Кандидаты в аббревиатуры из данных организатора: заглавные токены и «полное название (АББР)».

    Отбрасываются римские цифры, единицы измерения и слова, которые в корпусе встречаются
    и строчными (ФЛАКОН, АМПУЛА — это просто слова заглавными).
    """
    found: dict[str, Candidate] = {}
    lower_words: Counter[str] = Counter()
    for text in texts:
        if not text:
            continue
        lower_words.update(w for w in re.findall(r"[а-яё]{4,}", text))
        for m in _TOKEN.finditer(text):
            tok = m.group(0)
            if _ROMAN.match(tok) or normalize_unit(tok):
                continue
            cand = found.setdefault(tok, Candidate(tok))
            cand.count += 1
            if len(cand.examples) < examples:
                cand.examples.append(text[max(0, m.start() - 40) : m.end() + 40].strip())
        for m in _PAREN.finditer(text):
            phrase, abbr = m.group(1), m.group(2).upper()
            full = _initials_match(re.findall(r"[А-ЯЁа-яёA-Za-z-]+", phrase), abbr)
            if full:
                found.setdefault(abbr, Candidate(abbr)).expansions[full.casefold()] += 1
    result = []
    for cand in found.values():
        if len(cand.abbr) >= 5 and lower_words[cand.abbr.casefold()] > 0:
            continue
        cand.known = bool(glossary and cand.abbr in glossary)
        result.append(cand)
    return sorted(result, key=lambda c: (-c.count, c.abbr))
