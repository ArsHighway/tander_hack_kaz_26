from typing import Literal

from pydantic import BaseModel, Field

from app.schemas import Span


class Extracted(BaseModel):
    """Значение характеристики позиции, найденное в тексте, — ещё не подогнанное к перечню."""

    name: str
    raw_value: str
    raw_unit: str | None = None
    source: Span
    method: Literal["rule", "llm"]
    # Модель привела значение поставщика к перечню («встроенный аккумулятор» → «От аккумуляторных
    # батарей»): исходное значение правил — здесь, чтобы при смене позиции вернуть его как было.
    was: str | None = None
    note: str | None = None
    # Значение модели, которое код подтвердил: в цитате и подпись этой характеристики, и само
    # значение («Ширина: 40 мм» → «Ширина = 40 мм»). Такое не «требует проверки».
    checked: bool = False


class LLMItem(BaseModel):
    name: str = Field(description="имя характеристики строго из списка")
    value: str = Field(description="значение; если у характеристики есть перечень — из перечня")
    unit: str | None = Field(default=None, description="единица измерения или null")
    quote: str = Field(description="точный фрагмент текста поставщика, из которого взято значение")


class LLMItems(BaseModel):
    items: list[LLMItem]


class ListMatch(BaseModel):
    """Ответ модели на «какое из допустимых значений значит то же самое»."""

    value: str | None = Field(
        description="значение из допустимых, которое значит то же самое по смыслу; null — ни одно"
    )
    quote: str = Field(
        default="", description="точный фрагмент текста поставщика, где стоит его слово"
    )


class TypeRanking(BaseModel):
    """Ответ модели: наименования типов КТРУ от лучшего к худшему и причина одной фразой."""

    order: list[str] = Field(description="наименования из списка, ровно как написаны")
    reason: str = Field(default="", description="чем первый тип подходит лучше остальных")


class TypeOrder(BaseModel):
    """Порядок типов, принятый кодом: все исходные наименования, без выдуманных."""

    names: list[str]
    reason: str = ""


class ListSuggestion(BaseModel):
    """Подгонка значения поставщика к перечню КТРУ, принятая кодом. Это предложение: в поле
    карточки оно не подставляется, поставщик принимает его кнопкой."""

    value: str = Field(description="значение перечня, ровно как в справочнике")
    source: Span = Field(description="цитата поставщика, на которой держится сопоставление")
