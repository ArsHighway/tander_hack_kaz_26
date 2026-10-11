"""Диапазон правдоподобия по значениям СТЕ категории."""

from app.tools.plausible_ranges import plausible


def test_range_is_percentiles_with_margin() -> None:
    whiteness = [90.0 + i % 81 for i in range(200)]  # 90…170 %
    rng = plausible(whiteness)
    assert rng is not None and rng.min == 30 and rng.max == 340
    assert not rng.min <= 1460 <= rng.max  # «Белизна 1460 %» — вне
    assert rng.min <= 146 <= rng.max


def test_few_or_same_values_give_no_range() -> None:
    assert plausible([80.0] * 10 + [90.0] * 9) is None  # меньше 20 значений
    assert plausible([220.0] * 30 + [230.0] * 30) is None  # два разных значения


def test_zero_in_category_keeps_lower_bound_at_zero() -> None:
    heights = [0.0] * 5 + [700.0 + i for i in range(100)]
    rng = plausible(heights)
    assert rng is not None and rng.min == 0
