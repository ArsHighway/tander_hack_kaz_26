"""Модели API модуля: единственный источник формы запросов и ответов (фронт берёт их из OpenAPI)."""

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

from app.catalog import NumRange
from app.textclean import clean_text

# Лимиты длины: карточка портала короче, а мегабайты текста уходили в LLM и в заявку.
MAX_NAME = 1000
MAX_DESCRIPTION = 20_000
# Лимиты вмещают справочник: в стандартах портала имя характеристики до 400 знаков, значение
# перечня до 2 743 (07.10.2026) — с меньшими карточка такой категории падала на проверке.
MAX_CHAR_NAME = 500
MAX_VALUE = 4000
MAX_CHARACTERISTICS = 300


NameText = Annotated[str, Field(max_length=MAX_NAME), AfterValidator(clean_text)]
DescriptionText = Annotated[str, Field(max_length=MAX_DESCRIPTION), AfterValidator(clean_text)]
CharNameText = Annotated[str, Field(max_length=MAX_CHAR_NAME), AfterValidator(clean_text)]
ValueText = Annotated[str, Field(max_length=MAX_VALUE), AfterValidator(clean_text)]


class Span(BaseModel):
    """Фрагмент исходного текста: в наименовании или в описании, позиции в символах."""

    field: Literal["name", "description"]
    start: int
    end: int
    text: str


Origin = Literal["ktru", "portal", "new"]


class CharIn(BaseModel):
    name: CharNameText
    value: ValueText
    unit: str | None = Field(default=None, max_length=50)
    origin: Origin | None = Field(
        default=None,
        description="откуда характеристика (`CharacteristicOut.origin`): `ktru` и вне шаблона "
        "категории (`portal`, `new`) — не ошибка «нет в позиции»; пусто — проверка по позиции",
    )


class CardIn(BaseModel):
    name: NameText
    description: DescriptionText = ""
    characteristics: list[CharIn] = Field(default=[], max_length=MAX_CHARACTERISTICS)
    ktru_code: str | None = Field(
        default=None,
        max_length=64,
        description="Итоговый код позиции КТРУ (второй этап, `chain.ktru.final_code`). Если "
        "`category_code` пуст, здесь принимается и код категории справочника.",
    )
    category_code: str | None = Field(
        default=None,
        max_length=64,
        description="Код категории справочника (КПГЗ, `ktru.final_code` первого этапа): по ней "
        "проверяются характеристики. Пусто — категорией считается `ktru_code`.",
    )
    photo_id: int | None = Field(
        default=None,
        description="Фото товара, загруженное заранее (`POST /api/demo/photos`). Нет — `/check` "
        "предупреждает (`warn` на поле `photo`): на портале изображение у товара обязательно.",
    )


class StoredChar(BaseModel):
    name: str
    value: str
    unit: str | None = None
    origin: Origin | None = None


class StoredCard(BaseModel):
    """Карточка в заявке: как её сохранили. Лимиты и очистка — у входа (`CardIn`), а запись,
    сделанная раньше и нарушающая нынешние лимиты, всё равно читается и показывается."""

    name: str
    description: str = ""
    characteristics: list[StoredChar] = []
    ktru_code: str | None = None
    category_code: str | None = None
    photo_id: int | None = None

    def as_card_in(self) -> CardIn:
        """Для проверки модулем: без повторной валидации, сохранённое проверяется как есть."""
        return CardIn.model_construct(
            name=self.name,
            description=self.description,
            characteristics=[
                CharIn.model_construct(**c.model_dump()) for c in self.characteristics
            ],
            ktru_code=self.ktru_code,
            category_code=self.category_code,
            photo_id=self.photo_id,
        )


# «duplicate» — такая СТЕ уже есть на портале (`pipeline._duplicate`).
IssueKind = Literal["typo", "mismatch", "not_in_list", "moderation_rule", "duplicate"]


class Fix(BaseModel):
    name: str | None = None
    value: str | None = None
    unit: str | None = None


class Issue(BaseModel):
    field: str
    kind: IssueKind
    severity: Literal["block", "warn", "info"]
    message: str
    why: str
    rule: str
    fix: Fix | None = None


class CheckOut(BaseModel):
    issues: list[Issue]
    verdict: Literal["ready", "needs_fix"]


# Страница СТЕ на Портале поставщиков.
SKU_URL = "https://zakupki.mos.ru/sku/view/{id}"
RULE_CHAR_LIMITS = "характеристика укладывается в лимиты полей"
MAX_UNIT = 50
_SHOWN = 60


def accept_chars(raw: list[tuple[str, str, str | None]]) -> tuple[list[CharIn], list[Issue]]:
    """Характеристики из файла или портала: принятые и отвергнутые лимитами — замечанием, а не
    исключением. Одна длинная строка не должна ронять весь файл или отчёт."""
    accepted: list[CharIn] = []
    rejected: list[Issue] = []
    for name, value, unit in raw:
        too_long = (
            ("название", MAX_CHAR_NAME) if len(name) > MAX_CHAR_NAME
            else ("значение", MAX_VALUE) if len(value) > MAX_VALUE
            else ("единица", MAX_UNIT) if unit is not None and len(unit) > MAX_UNIT
            else None
        )  # fmt: skip
        if too_long is None:
            accepted.append(CharIn(name=name, value=value, unit=unit))
            continue
        what, limit = too_long
        shown = name if len(name) <= _SHOWN else name[:_SHOWN].rstrip() + "…"
        rejected.append(
            Issue(
                field="characteristics",
                kind="moderation_rule",
                severity="block",
                message=f"Характеристика не принята: {what} «{shown}» длиннее {limit} знаков.",
                why="Так длинно пишут текст, а не название, значение или единицу: похоже, в поле "
                "попало описание.",
                rule=RULE_CHAR_LIMITS,
            )
        )
    return accepted, rejected


class CharacteristicOut(BaseModel):
    name: str
    value: str | None
    unit: str | None
    source: Span | None
    normalized_from: str | None = None
    status: Literal["filled", "normalized", "not_found", "conflict"]
    required: bool
    method: Literal["rule", "llm", "answer", "twin", "similar"] | None = Field(
        default=None,
        description="откуда значение: правило по тексту, модель по тексту, ответ на вопрос, "
        "такая же СТЕ портала (`twins`) или похожие СТЕ портала — подсказки, в тексте значения нет",
    )
    note: str | None = None
    group: Literal["general"] | None = None
    unit_name: str | None = Field(default=None, description="единица полным названием по ОКЕИ")
    compact: str | None = Field(
        default=None,
        description="то же значение короче в другой единице той же величины («1,5 м» при "
        "1500 мм) — подсказка для чтения; значение остаётся в единице позиции",
    )
    origin: Literal["ktru", "portal", "new"] = Field(
        default="portal",
        description="откуда характеристика: `ktru` — есть у позиции КТРУ (итоговой, а пока её "
        "нет — у позиций КТРУ категории); `portal` — шаблон категории портала или, в `extra`, так "
        "её пишут на портале; `new` — новая, её нет ни в КТРУ, ни на портале",
    )
    ktru_fit: Literal["in_list", "normalized", "in_range", "not_in_list", "out_of_range"] | None = (
        Field(
            default=None,
            description="как значение поставщика легло в характеристику КТРУ: есть в перечне, "
            "приведено к формулировке КТРУ («глина» → «Глиняный»), попало в диапазон, нет в "
            "перечне, вне всех диапазонов; пусто — характеристика не из КТРУ или значения нет",
        )
    )
    ktru_match: str | None = Field(
        default=None,
        description="значение или корзина КТРУ, к которой привязано значение поставщика "
        "(«Деревянный», «>=400 ... <=500»); в `value` остаётся конкретное значение поставщика",
    )
    checked: bool | None = Field(
        default=None,
        description="значение модели подтверждено кодом: в цитате есть и подпись этой "
        "характеристики, и само значение — не «требует проверки»",
    )

    # Перечень и диапазон спецификации выбранной позиции. Пусто — ограничения нет.
    values: list[str] = Field(
        default_factory=list,
        description="допустимые значения выбранной позиции; пусто — перечня нет",
    )
    range: NumRange | None = Field(
        default=None,
        description="допустимый числовой диапазон выбранной позиции",
    )
