"""База знаний Портала поставщиков (zakupki.mos.ru/knowledgebase): разбор и выгрузка в Markdown.

Выдача раздела — `GET /newapi/api/KnowledgeBase/GetArticlesBySectionType?sectionType=<раздел>`,
тот же запрос, что делает сайт: категории → сервисы → статьи с полным HTML. Одна статья бывает в
нескольких разделах, ключ — `staticId` (он же в адресе статьи на сайте).
"""

import html as htmllib
import re
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from typing import Any

SECTIONS = {
    "supplier": "Для поставщика",
    "customer": "Для заказчика",
    "instruction": "Инструкции",
    "general": "Общий раздел",
}
ARTICLE_URL = "https://zakupki.mos.ru/knowledgebase/article/details/ais/{id}"

_BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol"}
# h5 и h6 на портале — подписи к рисункам и пустые обёртки картинок, не заголовки.
_HEADING = {"h1", "h2", "h3", "h4"}


class _Text(HTMLParser):
    """HTML статьи → текст: абзацы и пункты списков по строкам, у ссылки — адрес в скобках."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.href: str | None = None
        self.link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")
        elif tag in _HEADING:
            self.parts.append("#" * int(tag[1]) + " ")
        elif tag in ("td", "th"):
            self.parts.append(" | ")
        elif tag == "a":
            self.href = dict(attrs).get("href")
            self.link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.href is not None:
            label = "".join(self.link_text).strip()
            if self.href.startswith("http") and self.href not in label:
                self.parts.append(f" ({self.href})")
            self.href = None
        elif tag in _BLOCK and tag != "li":  # пункты списка идут подряд, без пустой строки
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)
        if self.href is not None:
            self.link_text.append(data)


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    parser = _Text()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts).replace("\xa0", " ").replace("\u200b", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    lines = [re.sub(r"^\|\s*", "", line) for line in lines]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _date(value: str | None) -> str | None:
    """«16.07.2026 15:01:16» → «2026-07-16»; пустая дата портала («01.01.0001») — None."""
    try:
        parsed = datetime.strptime(value or "", "%d.%m.%Y %H:%M:%S")
    except ValueError:
        return None
    return None if parsed.year < 1900 else parsed.date().isoformat()


def _plain(value: Any) -> str:
    """Заголовки и названия портала приходят с HTML-сущностями: «&quot;Мои заказы&quot;»."""
    return " ".join(htmllib.unescape(str(value or "")).split())


def _values(levels: Any) -> list[str]:
    return [_plain(v) for v in levels.values()] if isinstance(levels, dict) else []


@dataclass
class Article:
    id: int
    title: str
    url: str
    sections: list[str]
    category: str
    service: str
    topics: list[str]
    updated: str | None
    text: str
    # Текст для операторов поддержки, если он отличается от текста для пользователя.
    operator_text: str = ""
    files: list[dict[str, str]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


def article_from(raw: dict[str, Any], section: str, category: str) -> Article:
    portal = html_to_text(raw.get("selfServicePortal"))
    detail = html_to_text(raw.get("detailText"))
    text = detail if len(detail) > len(portal) else portal
    if not text:
        text = html_to_text(raw.get("previewText"))
    operator = html_to_text(raw.get("instructionOperator"))
    files = raw.get("innerFile") or {}
    topics = [
        t for lvl in ("altClassLvl1", "altClassLvl2", "altClassLvl3", "altClassLvl4")
        for t in _values(raw.get(lvl))
    ]  # fmt: skip
    return Article(
        id=int(raw["staticId"]),
        title=_plain(raw.get("largeName")),
        url=ARTICLE_URL.format(id=raw["staticId"]),
        sections=[section],
        category=_plain(category),
        service=_plain(raw.get("serviceName")),
        topics=topics,
        updated=_date(raw.get("updatedDate")),
        text=text,
        operator_text=operator if operator and operator != text else "",
        files=[
            {"name": str(f.get("name", "")), "url": str(f.get("url", ""))}
            for f in files.values()
            if isinstance(f, dict)
        ],
        tags=[t.strip() for t in str(raw.get("tags") or "").split(",") if t.strip()],
    )


def parse_sections(payloads: dict[str, list[dict[str, Any]]]) -> list[Article]:
    """Выдачи разделов → статьи без повторов; статья из нескольких разделов помнит их все."""
    found: dict[int, Article] = {}
    for section, categories in payloads.items():
        for cat in categories:
            for service in cat.get("articlesByService") or []:
                for raw in service.get("articles") or []:
                    art = article_from(raw, section, str(cat.get("categoryName") or ""))
                    if art.id in found:
                        if section not in found[art.id].sections:
                            found[art.id].sections.append(section)
                    else:
                        found[art.id] = art
    return sorted(found.values(), key=lambda a: (a.sections[0], a.category, a.service, a.title))


def slug(title: str, limit: int = 60) -> str:
    """Имя файла из заголовка: кириллица транслитом (в путях проекта кириллицы нет)."""
    table = str.maketrans(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        "abvgdeejzijklmnoprstufhccss_y_eua",
    )
    words = re.findall(r"[a-z0-9]+", title.casefold().translate(table))
    return "-".join(words)[:limit].strip("-") or "article"


def to_markdown(a: Article) -> str:
    head = [
        "---",
        f"id: {a.id}",
        f'title: "{a.title.replace(chr(34), chr(39))}"',
        f"url: {a.url}",
        f"sections: [{', '.join(a.sections)}]",
        f'category: "{a.category}"',
        f'service: "{a.service}"',
        f"topics: [{', '.join(a.topics)}]",
        f"updated: {a.updated or ''}",
        "---",
        "",
        f"# {a.title}",
        "",
        a.text,
    ]
    if a.files:
        head += ["", "## Файлы", ""] + [f"- [{f['name']}]({f['url']})" for f in a.files]
    if a.operator_text:
        head += ["", "## Для оператора поддержки", "", a.operator_text]
    return "\n".join(head).rstrip() + "\n"


def index_markdown(articles: list[Article], fetched: str) -> str:
    """Оглавление: раздел → категория → статьи со ссылками на файлы и на сайт."""
    lines = [
        "# База знаний Портала поставщиков",
        "",
        f"Выгружено {fetched} из zakupki.mos.ru/knowledgebase, статей: {len(articles)}.",
        "Статья из нескольких разделов лежит в папке первого.",
    ]
    for section, title in SECTIONS.items():
        own = [a for a in articles if a.sections[0] == section]
        if not own:
            continue
        lines += ["", f"## {title} ({len(own)})"]
        category = None
        for a in own:
            if a.category != category:
                category = a.category
                lines += ["", f"### {category}", ""]
            lines.append(f"- [{a.title}]({path_of(a)}) · [на сайте]({a.url})")
    return "\n".join(lines) + "\n"


def path_of(a: Article) -> str:
    return f"{a.sections[0]}/{a.id}-{slug(a.title)}.md"
