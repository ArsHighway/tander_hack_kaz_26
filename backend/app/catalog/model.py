"""Справочник позиций (КТРУ, СПГЗ): позиция и спецификации её характеристик.

Модуль не знает, откуда пришёл справочник: загрузчики (`loaders.py`) приводят любой источник
к этим моделям.
"""

import re
from typing import Literal

from pydantic import BaseModel, model_validator


class NumRange(BaseModel):
    min: float | None = None
    max: float | None = None


_PER_SQUARE_METRE = re.compile(r"1\s*(?:кв\.?\s*)?м\s*[²2]|квадратн\w*\s+метр", re.IGNORECASE)


class CharacteristicSpec(BaseModel):
    """Характеристика позиции: перечень (`values`) или диапазон (`range`), единица."""

    name: str
    kind: Literal["qualitative", "quantitative"]
    unit: str | None = None
    values: list[str] = []
    range: NumRange | None = None
    # Где лежат значения СТЕ портала этой категории (p1–p99 с запасом, `plausible_ranges`): выход —
    # замечание, а не блок, в отличие от `range` стандарта.
    plausible: NumRange | None = None
    required: bool = False
    # Характеристика определяет позицию (признак isLevelDefine портала).
    level_define: bool = False
    # Другие имена той же характеристики, коды КТРУ и СПГЗ, к которым она привязана (стандарт
    # категории портала).
    synonyms: list[str] = []
    ktru: list[str] = []
    spgz: list[str] = []
    # «Общие сведения» карточки (производитель, модель, страна): на портале это поля карточки, а не
    # характеристики — в поиск, вопросы сужения и счёт характеристик модерации не идут.
    group: Literal["general"] | None = None

    @model_validator(mode="after")
    def _level_field_is_optional(self) -> "CharacteristicSpec":
        # Поле уровня классификатора без перечня («Виды бумаги») поставщику заполнить нечем:
        # его не пишет почти ни одна СТЕ. Обязательным оно делало карточку «нужно исправить».
        if self.level_define and self.kind == "qualitative" and not self.values:
            self.required = False
        return self

    @model_validator(mode="after")
    def _areal_mass_unit(self) -> "CharacteristicSpec":
        # «Масса бумаги площадью 1 м²» в КТРУ — с единицей «г», хотя это г/м²: «80 г/м²» из текста
        # давало ложный блок «единица не подходит».
        if self.unit in ("г", "грамм") and _PER_SQUARE_METRE.search(self.name):
            self.unit = "г/м2"
        return self


# «Общие сведения» карточки портала — поля карточки, а не характеристики
# (`CharacteristicSpec.group`).
GENERAL_NAMES = ("Производитель", "Модель", "Страна происхождения")


class Position(BaseModel):
    code: str
    name: str
    okpd2: str
    kpgz: str | None = None
    unit: str | None = None
    characteristics: list[CharacteristicSpec]
    source: str
    # Категория портала (справочник из стандартов): id, имена узлов пути в дереве «Товары» и
    # картинка категории. У других источников справочника пусто.
    portal_id: int | None = None
    path: list[str] = []
    image_id: int | None = None


class CatalogError(RuntimeError):
    """Справочник не загружен или противоречив: модуль без него не работает."""
