import re

import pytest

from app.catalog.hack_scope import CATEGORIES, category_of, is_service, okpd2_prefixes


def test_exactly_the_three_hackathon_categories() -> None:
    assert set(CATEGORIES) == {"одежда", "мебель", "инструменты"}


@pytest.mark.parametrize(
    ("okpd2", "expected"),
    [
        ("14.12.30.160", "одежда"),
        ("14.19.42.141", "одежда"),
        ("31.01.10.000", "мебель"),
        ("25.73.30.141", "инструменты"),
        ("28.24.11.000", "инструменты"),
        ("26.51.33.121", "инструменты"),  # штангенциркули, рулетки: измерительный инструмент
        ("26.51.43.110", "инструменты"),  # мультиметры
        # соседи списка в группе: не инструмент
        ("26.51.43.000", None),
        ("25.71.11.120", None),  # ножницы канцелярские и портновские
        ("25.99.29.190", None),
        ("32.50.13.110", None),  # медицинские изделия
        ("17.12.14.121", None),  # бумага: прежняя категория проекта
    ],
)
def test_category_of_okpd2(okpd2: str, expected: str | None) -> None:
    assert category_of(okpd2) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Услуги по производству верхней одежды", True),
        ("Услуги по отделке новой мебели; услуги по обивке стульев", True),
        ("  услуги по ремонту инструмента", True),
        ("Работы по монтажу мебели", True),
        ("Услуга по пошиву сценического костюма", True),
        ("Работа по заказу", True),
        ("Одежда из натуральной или композиционной кожи", False),
        ("Куртка для работ на высоте", False),  # «работ» внутри названия товара — не услуга
        ("Услужливый робот", False),
    ],
)
def test_is_service_by_position_name(name: str, expected: bool) -> None:
    assert is_service(name) is expected


def test_prefixes_are_whole_groups_or_full_codes() -> None:
    # «25.73.» не зацепит «25.730…», а полный код — только себя
    for prefix in okpd2_prefixes():
        assert prefix.endswith(".") or re.fullmatch(r"\d\d\.\d\d\.\d\d\.\d{3}", prefix), prefix


def test_categories_do_not_overlap() -> None:
    owners: dict[str, str] = {}
    for category, prefixes in CATEGORIES.items():
        for prefix in prefixes:
            for other, owner in owners.items():
                assert not (prefix.startswith(other) or other.startswith(prefix)), (
                    f"{prefix} ({category}) пересекается с {other} ({owner})"
                )
            owners[prefix] = category


def test_okpd2_prefixes_lists_every_category_prefix() -> None:
    assert set(okpd2_prefixes()) == {p for ps in CATEGORIES.values() for p in ps}
