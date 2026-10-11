import pytest

from app.checks.detector import detect
from app.checks.profanity import RULE_PROFANITY, find_profanity
from app.schemas import CardIn, CharIn

ZWSP = "\u200b"

# Мат так, как его прячут поставщики: прямой, латинскими и цифровыми двойниками, звёздочками,
# пробелами и точками между буквами, невидимыми символами, повтором букв, транслитом.
OBSCENE = [
    "Бумага хуёвая А4",
    "ХУЙНЯ",
    "пиздец какой товар",
    "охуенный набор",
    "долбоёб",
    "заебал этот товар",
    "сука",
    "пидор",
    "блядь",
    "б л я д ь",
    "б.л.я.д.ь",
    "бл*ть",
    "х*й",
    "xуй",
    "п1зда",
    f"х{ZWSP}уй",
    "хууууй",
    "blyat",
    "pizdec",
    "nahuy",
    "дебил",
]

# Обычные слова, внутри которых есть те же буквы: их блокировать нельзя.
CLEAN = [
    "Психиатрия",
    "застрахуй имущество",
    "Скипидар живичный",
    "Хуанхэ",
    "Сабля сувенирная",
    "оскорбление",
    "команда",
    "страхование",
    "употреблять до",
    "ослаблять натяжение",
    "Бляха ременная",
    "атеросклеротическая бляшка",
    "Набор для педикюра",
    "Сукно шинельное",
    "небо",
    "хлебать",
    "Huawei MatePad",
    "Планшет HUION",
    "Мандарин",
    "ребята",
    "корабля",
    "сук дерева",
    "Книга Достоевский Идиот",
    "Бумага офисная А4 500 листов",
    # Ложные срабатывания на живых карточках портала (прогон по 8 520 СТЕ, 02.10.2026).
    "Возможность установки кустореза/сучкореза",
    "Цвет бледно-лиловый (LA12)",
    "Накопитель ADATA XPG BLADE S70",
    "при дегенеративно-дистрофических заболеваниях",
    "Доска обрезная без сучков",
    # Цифра рядом с одной буквой — обозначение, а не двойник буквы.
    "Мегаомметр Е6-24",
    "Набор бит Torx Е4, Е5, Е6, Е7",
]


@pytest.mark.parametrize("text", OBSCENE)
def test_obscene_is_found(text: str) -> None:
    assert find_profanity(text) is not None


@pytest.mark.parametrize("text", CLEAN)
def test_clean_text_is_not_blocked(text: str) -> None:
    assert find_profanity(text) is None


def _card(name: str = "Бумага офисная А4", description: str = "", value: str = "А4") -> CardIn:
    return CardIn(
        name=name, description=description, characteristics=[CharIn(name="Формат", value=value)]
    )


def _profanity(card: CardIn) -> list:
    return [i for i in detect(card, None) if i.rule == RULE_PROFANITY]


def test_profanity_in_name_blocks_the_card() -> None:
    [issue] = _profanity(_card(name="Бумага хуёвая А4"))
    assert (issue.field, issue.severity) == ("name", "block")
    assert "хуёв" not in issue.message


def test_profanity_in_description_and_value_is_reported_on_its_field() -> None:
    issues = _profanity(_card(description="Отличная, бл*ть, бумага", value="пиздатый"))
    assert {i.field for i in issues} == {"description", "characteristics.Формат"}
    assert all(i.severity == "block" for i in issues)


def test_clean_card_has_no_profanity_issue() -> None:
    assert _profanity(_card(name="Скипидар живичный", description="Застрахуй груз")) == []
