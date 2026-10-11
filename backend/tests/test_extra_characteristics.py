"""Свойства вне шаблона категории: пары из описания, «как пишут на портале» и новые."""

from app.extract.extra import PortalNames, extra_pairs
from app.values import fold

PORTAL = PortalNames(["Емкость аккумулятора", "Емкость аккумулятора", "емкость аккумулятора",
                      "Материал рукоятки", "Масса, кг", "Цвет"])  # fmt: skip


def pairs(
    text: str, known: tuple[str, ...] = ("Цвет", "Масса")
) -> list[tuple[str, str, str | None, str]]:
    found = extra_pairs(text, {fold(k) for k in known}, PORTAL)
    for p in found:
        assert text[p.source.start : p.source.end] == p.source.text
    return [(p.name, p.value, p.unit, p.origin) for p in found]


def test_colon_pairs_outside_template_are_kept_portal_spelling_first() -> None:
    text = (
        "Молоток слесарный.\nЦвет: красный; материал рукоятки: фибергласс\n"
        "Ручка противоскользящая: да"
    )
    assert pairs(text) == [
        ("Материал рукоятки", "фибергласс", None, "portal"),
        ("Ручка противоскользящая", "да", None, "new"),
    ]


def test_comma_separated_pairs_and_units() -> None:
    text = "Ручка противоскользящая: да, Длина ручки: 300 мм"
    assert pairs(text) == [
        ("Ручка противоскользящая", "да", None, "new"),
        ("Длина ручки", "300", "мм", "new"),
    ]


def test_portal_export_format_without_colon_needs_a_known_name() -> None:
    text = "Емкость аккумулятора 1.50000 А.ч | Тип щётки угольная | Масса 2 кг"
    got = pairs(text)
    assert got[0][0] == "Емкость аккумулятора" and got[0][3] == "portal"
    assert [g[0] for g in got] == [
        "Емкость аккумулятора"
    ]  # «Тип щётки» порталу неизвестен, «Масса» — в шаблоне


def test_template_service_general_and_garbage_labels_are_skipped() -> None:
    text = "Описание: хороший; Производитель: Зубр; Масса: 2 кг; 12: 34; Ж: да; Цвет: синий"
    assert pairs(text) == []


def test_unit_in_label_and_cap() -> None:
    assert pairs("Длина ручки, мм: 300") == [("Длина ручки", "300", "мм", "new")]
    text = "; ".join(
        f"Свойство номер {chr(1072 + i)}{chr(1072 + i)}{chr(1072 + i)}: да" for i in range(20)
    )
    assert len(extra_pairs(text, set(), PORTAL)) == 15


def test_label_starts_after_the_sentence_end() -> None:
    assert pairs("Молоток слесарный 500 г. Ручка противоскользящая: да") == [
        ("Ручка противоскользящая", "да", None, "new")
    ]


def test_label_after_long_sentence_and_comma() -> None:
    text = "Рукоятка из фибергласса, длина 320 мм. Ручка противоскользящая: да"
    assert pairs(text) == [("Ручка противоскользящая", "да", None, "new")]
    assert pairs("Обивка ткань, подголовник съемный: есть") == [
        ("Подголовник съемный", "есть", None, "new")
    ]
