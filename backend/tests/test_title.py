"""Одна вставка делится на наименование и описание без переноса строки."""

from app.extract.title import derive_parts

TABLE = (
    "Стол офисный «Гигант-5» Офисный стол для переговорной, столешница прямоугольная. "
    "Модель: Гигант-5. Производитель: ОфисМебель. Высота: 5 метров."
)


def test_one_line_cuts_before_the_first_characteristic() -> None:
    name, description = derive_parts(TABLE, "")
    assert name == "Стол офисный «Гигант-5» Офисный стол для переговорной, столешница прямоугольная"
    assert description.startswith("Модель: Гигант-5")


def test_the_first_sentence_is_the_name_even_if_it_has_no_label() -> None:
    # Кейсы Богдана одной строкой: без деления «Масса молотка: 500 г» и «Ростовая группа: 2»
    # читались из наименования и терялись или ложились не в то поле.
    name, description = derive_parts(
        "Молоток столярный. Тип: столярный. Масса молотка: 500 г. Длина рукоятки: 320 мм.", ""
    )
    assert name == "Молоток столярный"
    assert description == "Тип: столярный. Масса молотка: 500 г. Длина рукоятки: 320 мм."
    name, description = derive_parts(
        "курка зимняя муржская из синтетическаго материала, женская. Размер 60. Утеплитель: пух", ""
    )
    assert name == "курка зимняя муржская из синтетическаго материала, женская"
    assert description == "Размер 60. Утеплитель: пух"


def test_a_label_without_a_sentence_break_ends_the_name() -> None:
    assert derive_parts("Куртка зимняя Размер: 52", "") == ("Куртка зимняя", "Размер: 52")


def test_a_one_piece_portal_name_is_kept() -> None:
    # Так пишут наименования на портале: по ним ищутся двойники и голые числа «А4 80 500».
    for name in (
        "Молоток слесарный 500 г",
        "Стол Гигант-5 высота 750 мм",
        "Бумага для принтера формата А4, плотность 80 г/м², пачка 500 листов",
        "Перчатки смотровые нитрил. р-р M",
    ):
        assert derive_parts(name, "") == (name, "")


def test_a_prepared_description_is_not_recut() -> None:
    name, description = derive_parts("Бумага А4 500 л", "Плотность 80 г/м2")
    assert name == "Бумага А4 500 л"
    assert description == "Плотность 80 г/м2"


def test_text_without_a_characteristic_stays_the_name() -> None:
    assert derive_parts("Стул офисный", "") == ("Стул офисный", "")
