from app.portal.knowledgebase import (
    article_from,
    html_to_text,
    index_markdown,
    parse_sections,
    path_of,
    slug,
    to_markdown,
)


def _raw(static_id: int, **over: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "id": static_id + 1000,
        "staticId": static_id,
        "largeName": "Как  создать СТЕ?",
        "previewText": "<p>Коротко.</p>",
        "selfServicePortal": "<p>Нажмите&nbsp;«Создать».</p><ul><li>Шаг 1</li><li>Шаг 2</li></ul>",
        "detailText": "",
        "instructionOperator": "<p>Нажмите «Создать».</p>",
        "serviceName": "Работа с СТЕ",
        "altClassLvl1": {"4": "Портал поставщиков"},
        "altClassLvl2": {"516": "Работа с СТЕ"},
        "altClassLvl3": {},
        "updatedDate": "16.07.2026 15:01:16",
        "innerFile": {"1": {"url": "https://help.mos.ru/upload/a.png", "name": "a.png"}},
        "tags": "СТЕ, оферта",
    }
    raw.update(over)
    return raw


def _payload(*articles: dict[str, object], category: str = "Работа с СТЕ") -> list:
    return [{"categoryName": category, "articlesByService": [{"articles": list(articles)}]}]


def test_html_keeps_paragraphs_lists_and_file_links() -> None:
    html = (
        '<p>Скачайте <a href="https://zakupki.mos.ru/doc.docx">инструкцию</a>.</p>'
        "<ol><li>Первый&nbsp;шаг</li><li>Второй</li></ol><table><tr><td>A</td><td>B</td></tr></table>"
    )
    assert html_to_text(html) == (
        "Скачайте инструкцию (https://zakupki.mos.ru/doc.docx).\n\n- Первый шаг\n- Второй\n\nA | B"
    )
    assert html_to_text(None) == ""


def test_article_takes_the_longer_text_and_portal_fields() -> None:
    a = article_from(_raw(171501), "supplier", "Работа с СТЕ")
    assert a.id == 171501
    assert a.url == "https://zakupki.mos.ru/knowledgebase/article/details/ais/171501"
    assert a.title == "Как создать СТЕ?"
    assert a.text == "Нажмите «Создать».\n\n- Шаг 1\n- Шаг 2"
    assert a.operator_text == "Нажмите «Создать»."
    assert a.topics == ["Портал поставщиков", "Работа с СТЕ"]
    assert a.updated == "2026-07-16"
    assert a.files == [{"name": "a.png", "url": "https://help.mos.ru/upload/a.png"}]
    assert a.tags == ["СТЕ", "оферта"]


def test_detail_text_wins_when_longer_and_empty_date_is_none() -> None:
    raw = _raw(1, detailText="<p>Подробно: шаги и примеры.</p>", selfServicePortal="<p>Кратко</p>")
    raw["updatedDate"] = "01.01.0001 00:00:00"
    a = article_from(raw, "general", "Общее")
    assert a.text == "Подробно: шаги и примеры."
    assert a.updated is None


def test_article_from_several_sections_is_kept_once_with_all_sections() -> None:
    articles = parse_sections(
        {
            "supplier": _payload(_raw(1), _raw(2, largeName="Оферта")),
            "general": _payload(_raw(1), category="Общее"),
        }
    )
    assert [(a.id, a.sections) for a in articles] == [
        (1, ["supplier", "general"]),
        (2, ["supplier"]),
    ]


def test_markdown_file_and_index() -> None:
    a = article_from(_raw(7), "supplier", "Работа с СТЕ")
    assert path_of(a) == "supplier/7-kak-sozdat-ste.md"
    md = to_markdown(a)
    assert md.startswith("---\nid: 7\n")
    assert "# Как создать СТЕ?" in md and "## Для оператора поддержки" in md
    index = index_markdown([a], "2026-10-06")
    assert "## Для поставщика (1)" in index
    assert "[Как создать СТЕ?](supplier/7-kak-sozdat-ste.md)" in index


def test_slug_is_ascii() -> None:
    assert slug("Работа с СТЕ: шаг 2!") == "rabota-s-ste-sag-2"
    assert slug("???") == "article"


def test_html_entities_in_titles_are_decoded() -> None:
    raw = _raw(3, largeName="Вкладка &quot;Мои заказы&quot;")
    a = article_from(raw, "supplier", "Работа &amp; СТЕ")
    assert (a.title, a.category) == ('Вкладка "Мои заказы"', "Работа & СТЕ")


def test_picture_captions_are_plain_lines_and_empty_wrappers_vanish() -> None:
    html = '<p>Шаг</p><h6><img src="a.png"></h6><h6>Рисунок 1</h6><h2>Итог</h2>'
    assert html_to_text(html) == "Шаг\n\nРисунок 1\n\n## Итог"
