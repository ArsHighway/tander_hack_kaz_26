"""Конвейер модуля: описание поставщика → кандидаты → извлечение → сужение → характеристики
→ замечания.

Модуль ничего не пишет в данные портала: ответ — предложения, решение по каждому полю за человеком.
"""

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, Field

from app.card_fill import (
    SIZE_RULE,
    _characteristics,
    _issues,
    _linked_ktru,
    _number_issues,
    _origins,
    _template_for_ktru,
    _union_specs,
)
from app.catalog import Catalog, CharacteristicSpec, Position
from app.catalog.ktru_meta import KtruMerge, drop_wider, merge_same, prefer_refined
from app.catalog.ktru_types import TYPE_SOURCE
from app.catalog.model import GENERAL_NAMES
from app.checks.detector import (
    detect,
    ktru_not_chosen,
    ktru_not_linked,
    ktru_value_issues,
    missing_code,
    not_found,
    photo_issues,
    unknown_code,
    unknown_ktru,
    verdict,
)
from app.checks.injection import instruction_issue, strip_instructions
from app.checks.typos import layout_fix, layout_issues, typo_issues, unglue
from app.extract import Extracted, extract_llm, extract_rules
from app.extract.extra import PortalNames
from app.extract.llm_fill import (
    _guarded,
    suggest_list_value,
    suggest_oddities,
    suggest_option,
    suggest_type_order,
)
from app.extract.llm_pairs import split_pairs, structured_text
from app.extract.llm_review import Suggestion, _shares_stem, apply_review, review
from app.extract.llm_sanity import SANITY_RULE, sanity, sanity_issues
from app.extract.model import Oddity
from app.extract.new_names import load_new_names, remember_new_names
from app.extract.title import CUES, derive_parts
from app.extract.unread import unread
from app.glossary import Glossary
from app.ingest.characteristics import to_number
from app.ingest.units import normalize_unit
from app.llm import ChatResult, LLMError, LLMInputError, LLMOutputError, Message
from app.narrowing import (
    CONFIRM,
    NONE_VALUE,
    NOTHING_FITS,
    OTHER_VALUE,
    STEP_NOT_FOUND,
    UNIT_QUESTION,
    Known,
    KtruState,
    NearPosition,
    Question,
    TrailStep,
    build_state,
    compatible,
    confirm_question,
    narrow,
    not_found_steps,
    not_goods_steps,
    unit_label,
)
from app.schemas import (
    MAX_CHAR_NAME,
    MAX_VALUE,
    SKU_URL,
    CardIn,
    CharacteristicOut,
    CheckOut,
    DescriptionText,
    Fix,
    Issue,
    NameText,
    Span,
)
from app.search import BM25Index, Candidate, VectorSearch, find_candidates
from app.search.against_text import keep_unsaid, said_same
from app.search.candidates import AUTO_SHARE, SimilarSku, outside_kind
from app.search.exemplar_vectors import ExemplarVectors
from app.search.exemplars import ExemplarIndex
from app.search.similar import NEIGHBOURS, Similar, SimilarValues, vote
from app.search.text import negated_stems, significant_words, tokens, words_with_stems
from app.search.twins import TwinIndex, TwinRef, twin_category, twin_maker, twin_values
from app.session import Session, SessionStore
from app.values import damerau_levenshtein, fit_value, fold

if TYPE_CHECKING:
    from app.batch import BatchStore

log = logging.getLogger(__name__)

Degraded = Literal["llm", "embeddings"]
# Код позиции КТРУ: ОКПД2 и номер из восьми цифр («17.12.14.129-00000017») или пяти — так в ЕИС
# записаны ранние позиции («19.20.21.200-00001»).
KTRU_CODE = re.compile(r"^\d{2}\.\d{2}\.\d{2}\.\d{3}-\d{5}(?:\d{3})?$")


class ModuleLLM(Protocol):
    async def chat(
        self, messages: list[Message], *, schema: type[BaseModel], temperature: float = 0.0
    ) -> ChatResult: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass
class Deps:
    catalog: Catalog
    bm25: BM25Index
    vectors: VectorSearch | None
    llm: ModuleLLM
    glossary: Glossary
    sessions: SessionStore
    min_score: float
    batches: "BatchStore | None" = None
    # Похожие СТЕ для поиска категории (`EXEMPLARS_PATH`); нет — только справочник.
    exemplars: ExemplarIndex | None = None
    # Векторы тех же наименований (`EXEMPLAR_VECTORS_PATH`): второй голос категорий к BM25.
    exemplar_vectors: ExemplarVectors | None = None
    # Справочник КТРУ (`KTRU_PATHS`): второй этап — позиция КТРУ внутри найденной категории.
    ktru: Catalog | None = None
    # Результат последнего прогона app.tools.evaluate.
    metrics_path: Path = Path("data/metrics/latest.json")
    # Отчёт app.tools.portal_report по живым карточкам портала.
    portal_report_path: Path = Path("data/metrics/portal.json")
    holdout_report_path: Path = Path("data/metrics/holdout.json")
    # До какого момента не вызывать эмбеддинги после сбоя (time.monotonic()).
    embed_paused_until: float = 0.0
    # Сколько ждать модель в шаге (`LLM_BUDGET_S`); None — без предела. После отказа сервиса
    # (квота, 429, недоступна) модель не спрашивают до `llm_paused_until` — шаг не ждёт впустую.
    llm_budget_s: float | None = None
    llm_paused_until: float = 0.0
    # Новые характеристики, уже записанные раньше (`app.extract.new_names`): ключ → формулировка
    # и счётчик. Обновляется в начале каждого шага мастера из Redis.
    new_names: dict[str, tuple[str, int]] = field(default_factory=dict)
    # Модель включена (`LLM_ENABLED`, переключает модератор через `PUT /api/v1/settings`):
    # выключена — ни одного запроса к LLM, карточка по правилам и справочнику.
    llm_enabled: bool = True
    # Модель ставит первым самый вероятный тип в вопросе «какой это товар» (`LLM_RERANK_TYPES`).
    rerank_types: bool = False
    # Модель отвечает по тексту на вопрос сужения до позиции КТРУ (`LLM_PICK_OPTIONS`) и называет
    # значения, которых у такого товара не бывает (`LLM_SANITY`).
    pick_options: bool = False
    sanity: bool = False
    # Первым шагом модель делит текст на пары «название: значение» (`LLM_STRUCTURE`).
    structure: bool = False
    # Второй слой модели (`extract.llm_review`) и сколько его ждать; None — выключен.
    review_llm: Any = None
    review_budget_s: float = 12.0
    # Характеристики похожих СТЕ (`<exemplars>.values.sqlite`): подсказки полей, которых нет
    # в тексте.
    similar: SimilarValues | None = None
    # Такая СТЕ уже есть на портале (`TWINS_PATH`): категория и значения двойников.
    twins: TwinIndex | None = None
    # Код КТРУ → категория, к которой он привязан (`/check` с одним кодом КТРУ); строится лениво.
    ktru_parents: dict[str, str] | None = None
    # Имена характеристик портала по всем категориям (`extra_pairs`); строится лениво.
    portal_names: PortalNames | None = None
    # Слова наименований справочника типов КТРУ: основа → слово (`_name_typos`); у справочника
    # категорий — пусто. Строится лениво.
    name_words: dict[str, tuple[str, list[set[str]]]] | None = None
    # Постановка пакета в очередь порциями (id пакета, число строк); в тестах — подмена.
    enqueue_batch: Callable[[str, int], Awaitable[None]] | None = None


class SessionNotFound(KeyError):
    pass


class BadAnswer(ValueError):
    """Ответ не подходит к вопросу: характеристики нет у кандидатов или значения нет в вариантах."""


class AutofillIn(BaseModel):
    name: NameText = Field(min_length=1, description="наименование СТЕ, как его ввёл поставщик")
    description: DescriptionText = Field(default="", description="описание в свободной форме")


class AnswerIn(BaseModel):
    characteristic: str = Field(max_length=MAX_CHAR_NAME)
    value: str = Field(
        max_length=MAX_VALUE,
        description="вариант вопроса; пустая строка — «нет такой характеристики»",
    )
    stage: Literal["category", "ktru"] = Field(
        default="category",
        description="чей вопрос: `ktru` — сужение до позиции КТРУ внутри категории (`chain.ktru`)",
    )


class Chain(BaseModel):
    """Коды найденной категории и второй этап — позиция КТРУ внутри неё."""

    category: str = Field(description="код позиции справочника (у стандартов портала — КПГЗ)")
    category_name: str
    kpgz: str | None
    okpd2: str | None
    spgz: list[str] = Field(description="коды СПГЗ, на которые ссылаются характеристики категории")
    ktru_linked: int = Field(description="сколько позиций КТРУ привязано к категории")
    path: list[str] = Field(
        default=[],
        description="имена узлов пути категории от раздела до самой категории, без «Товары»; "
        "пусто у справочника не из стандартов портала",
    )
    portal_id: int | None = Field(
        default=None, description="id категории на портале (картинка категории, ссылка)"
    )
    ktru_missing: Literal["no_links", "not_loaded"] | None = Field(
        default=None,
        description="почему нет второго этапа: у категории нет ссылок на КТРУ (no_links) или "
        "привязанных позиций нет в справочнике КТРУ (not_loaded); код КТРУ тогда пустой",
    )
    ktru: KtruState | None = Field(
        description="сужение до позиции КТРУ; нет — у категории нет КТРУ или справочник не загружен"
    )
    ktru_type: str | None = Field(
        default=None, description="тип товара по КТРУ — наименование позиций КТРУ"
    )
    ktru_merged: list[KtruMerge] = Field(
        default=[],
        description="одинаковые позиции КТРУ (шаблон и позиция из него), из которых оставлена "
        "более свежая: что объединено и почему — для поставщика",
    )
    category_via: Literal["ktru_link", "search", "none"] | None = Field(
        default=None,
        description="откуда категория портала (КПГЗ): по связи с КТРУ, поиском или связи нет",
    )


class TwinOut(BaseModel):
    """Такая СТЕ уже есть на портале: то же наименование или та же модель."""

    id: int
    name: str
    url: str
    by: Literal["name", "model"] = Field(description="совпало наименование или модель")
    same_category: bool = Field(description="в категории, которую определил модуль")


class AutofillOut(BaseModel):
    session_id: str
    ktru: KtruState
    characteristics: list[CharacteristicOut]
    issues: list[Issue]
    degraded: list[Degraded]
    chain: Chain | None = Field(default=None, description="есть, когда категория определена")
    twins: list[TwinOut] = Field(
        default=[],
        description="такие СТЕ уже есть на портале (до пяти, сначала из определённой категории): "
        "заявку-дубль модератор отклонит с причиной «Предложена другая СТЕ»",
    )
    extra: list[CharacteristicOut] = Field(
        default=[],
        description="свойства из описания вне шаблона категории и КТРУ («Метка: значение»): "
        "`origin: portal` — так характеристику пишут на портале, `new` — новая; не выбрасываются",
    )
    name: str = Field(
        default="",
        description="наименование для карточки: текст до первой характеристики, если поставщик "
        "вставил описание одной строкой",
    )
    description: str = Field(
        default="",
        description="остаток текста поставщика после наименования: подписи и значения",
    )
    suggestions: list[Suggestion] = Field(
        default=[],
        description="куда можно поставить непонятые куски текста (второй слой модели, проверено "
        "кодом): поле, значение, есть ли оно в перечне; в карточку — только по решению поставщика",
    )
    unread: list[Span] = Field(
        default=[],
        description="куски описания, которые не легли ни в одно поле и не в extra (без рекламы и "
        "цены): поставщик видит, что из его текста не потеряно, и переносит сам",
    )
    llm: Literal["on", "off", "unavailable"] = Field(
        default="on",
        description="модель: включена; выключена модератором; включена, но не ответила",
    )


def _known_from_text(extracted: list[Extracted], positions: list[Position]) -> dict[str, Known]:
    """Значения из текста, которые сужают: подходят хотя бы одной позиции и не обнуляют выбор.

    Значение, которое не подходит ни одной позиции («Белизна 98 %» при диапазоне 146–175), —
    конфликт в карточке, а не повод выбрать позицию без этой характеристики. Значения от модели
    только предлагаются в карточку: код КТРУ по ним не выбирается. Исключение — ответ модели на
    вопрос сужения (`Extracted.picks`): вариант из списка с цитатой из текста, с пометкой.
    """
    known: dict[str, Known] = {}
    left = positions
    for e in extracted:
        if e.method != "rule" and not e.picks:
            continue
        k = Known(value=e.raw_value, unit=e.raw_unit, source="text")
        # Подходит — только к характеристике с перечнем или диапазоном: поле свободного текста
        # примет и «глину», и тогда чушь из описания выбирала бы товар, отсекая настоящий.
        fits_some = any(
            (spec := next((c for c in p.characteristics if fold(c.name) == fold(e.name)), None))
            and (spec.values or spec.range is not None)
            and fit_value(k.value, k.unit, spec).status in ("exact", "normalized", "bucket", "typo")
            for p in left
        )
        if not fits_some and (mapped := _stem_option(e.raw_value, e.name, left)) is not None:
            k, fits_some = Known(value=mapped, source="text"), True
        narrowed = [p for p in left if compatible(p, {e.name: k})]
        if fits_some and narrowed:
            known[e.name] = k
            left = narrowed
    return known


def _said_not_listed(
    chars: list[CharacteristicOut], specs: list[CharacteristicSpec]
) -> list[Issue]:
    """Пустое поле с перечнем, о котором текст говорит в другом поле: «Материал: глина» при
    «Вид материала корпуса: Массив дерева, МДФ, ДСП, ЛДСП». Раньше поле писало «в описании не
    нашли», хотя материал в тексте есть — просто не из перечня. Теперь поставщик видит, что
    именно сказано и что допустимо."""
    empty = {
        c.name
        for c in chars
        if c.value is None
        and (spec := _spec_of(specs, c.name)) is not None
        and spec.values
        and spec.required  # необязательных «Вид материала …» много: замечание на каждое — шум
    }
    said = [c for c in chars if c.value and c.method == "rule" and c.source is not None]
    out: list[Issue] = []
    for name in sorted(empty):
        spec = _spec_of(specs, name)
        assert spec is not None
        for c in said:
            if fold(c.name) == fold(name) or not _label_within(c.name, name):
                continue
            if fit_value(c.value or "", c.unit, spec).status in ("exact", "normalized", "typo"):
                continue
            allowed = ", ".join(spec.values[:8])
            quote = c.source.text if c.source else c.value
            out.append(
                Issue(
                    field=f"characteristics.{name}",
                    kind="not_in_list",
                    severity="warn",
                    message=(
                        f"В описании «{quote}» ({c.name}), но в перечне КТРУ для «{name}» "
                        f"такого значения нет: {allowed}. Выберите подходящее или исправьте "
                        "описание."
                    ),
                    why="Текст называет значение, которого нет среди допустимых в позиции КТРУ.",
                    rule="ktru.not_in_list",
                )
            )
            break
    return out


def _label_within(short: str, long: str) -> bool:
    """Все слова подписи «Материал» есть в «Вид материала корпуса» (по основе)."""
    words = [w for w in tokens(short) if len(w) >= 4]
    return bool(words) and all(_shares_stem(w, long) for w in words)


_SPREAD_LIMIT = 2
_NOT_SUBJECT = set(tokens("вид тип наличие количество требования значение характеристика"))


def _one_span_few_fields(
    chars: list[CharacteristicOut], session: Session
) -> list[CharacteristicOut]:
    """Одно слово текста — не в десяток полей. «материал ЛДСП» ложилось во все «Вид материала …»,
    «Материал кромки» и «Требования к товару»: перечни у них общие, а правила разных этапов
    (категория, КТРУ) не видят друг друга. Если фрагмент занял больше двух полей, остаются поля,
    чьё имя лучше всех совпало со словами вокруг фрагмента; при ничьей среди многих — обязательные
    и поле с именем ровно из этих слов («Материал»). Остальные пусты: поставщик заполнит сам."""
    texts = {"name": session.name, "description": session.description}
    groups: dict[tuple[str, int, int], list[CharacteristicOut]] = {}
    for c in chars:
        if c.method == "rule" and c.source is not None and c.value:
            groups.setdefault((c.source.field, c.source.start, c.source.end), []).append(c)
    drop: set[str] = set()
    for (where, start, end), items in groups.items():
        if len(items) <= _SPREAD_LIMIT:
            continue
        text = texts.get(where, "")
        left = re.split(r"[,;\n.|]", text[:start])[-1]
        right = re.split(r"[,;\n.|]", text[end:])[0]
        around = set(tokens(f"{left} {right}"))

        def score(c: CharacteristicOut, around: set[str] = around) -> int:
            return len((set(tokens(c.name)) - _NOT_SUBJECT) & around)

        best = max(score(c) for c in items)
        keep = [c for c in items if score(c) == best]
        if len(keep) > _SPREAD_LIMIT:
            whole = [c for c in keep if set(tokens(c.name)) <= around]
            keep = [c for c in keep if c.required] + whole or keep[:1]
        drop |= {c.name for c in items} - {c.name for c in keep}
    return [
        c.model_copy(
            update={
                "value": None,
                "source": None,
                "normalized_from": None,
                "status": "not_found",
                "method": None,
                "note": None,
                "compact": None,
                "ktru_fit": None,
            }
        )
        if c.name in drop
        else c
        for c in chars
    ]


def _specs_for(target: Position, chain: "Chain | None", deps: Deps) -> list[CharacteristicSpec]:
    """Характеристики итоговой позиции КТРУ (если есть) и выбранной категории."""
    code = chain.ktru.final_code if chain is not None and chain.ktru else None
    own = deps.ktru.get(code) if deps.ktru is not None and code else None
    return [*(own.characteristics if own else []), *target.characteristics]


KIN_NOTE = "Взято из «{name}» в тексте — проверьте"
_KIN_SKIP = set(tokens("значение характеристика"))


def _kin_words(name: str) -> set[str]:
    return {w for w in tokens(name) if len(w) >= 4} - _KIN_SKIP - _NOT_SUBJECT


def _from_kin_fields(
    chars: list[CharacteristicOut],
    extra: list[CharacteristicOut],
    specs: list[CharacteristicSpec],
) -> list[CharacteristicOut]:
    """Обязательное поле пусто, а текст назвал то же другой подписью: «Максимальные обороты на 2ой
    скорости 1300 об/мин» при «Максимальное количество оборотов». Все слова имени пустого поля есть
    в подписи (по основе), значение подходит полю — ставим из текста с пометкой. Подходящих подписей
    несколько — у «Максимального …» берём наибольшее число, иначе не угадываем."""
    filled = [
        c for c in [*chars, *extra] if c.value and c.method == "rule" and c.source is not None
    ]
    out: list[CharacteristicOut] = []
    for c in chars:
        spec = _spec_of(specs, c.name) if c.value is None and c.required else None
        words = _kin_words(c.name)
        if spec is None or not words:
            out.append(c)
            continue
        kin = []
        for f in filled:
            if fold(f.name) == fold(c.name):
                continue
            if not all(_shares_stem(w, f.name) for w in words):
                continue
            fit = fit_value(f.value or "", f.unit, spec)
            if fit.status in ("exact", "normalized", "bucket"):
                kin.append((f, fit))
        numbers = [to_number(f.value or "") for f, _ in kin]
        if len(kin) > 1 and c.name.casefold().startswith("максимальн") and None not in numbers:
            kin = [max(kin, key=lambda k: to_number(k[0].value or "") or 0.0)]
        if len(kin) != 1:
            out.append(c)
            continue
        f, fit = kin[0]
        out.append(
            c.model_copy(
                update={
                    "value": fit.value if fit.value is not None else f.value,
                    "unit": fit.unit or spec.unit or f.unit,
                    "source": f.source,
                    "status": "filled",
                    "method": "rule",
                    "note": KIN_NOTE.format(name=f.name),
                }
            )
        )
    return out


def _spec_of(specs: list[CharacteristicSpec], name: str) -> CharacteristicSpec | None:
    return next((c for c in specs if fold(c.name) == fold(name)), None)


_NEGATION = {"не", "без", "нет"}
_AFTER_LABEL = re.compile(r"\s*:?\s*([^|;\n.]{1,60})")


def _known_from_labels(
    text: str, positions: list[Position], known: dict[str, Known]
) -> dict[str, Known]:
    """«Тип питания Сменный аккумулятор» — подпись характеристики и слово не из перечня: правила
    его не берут, и поставщика спрашивали «От сети или от аккумуляторных батарей?» при ответе в
    тексте. Слово после подписи с основой ровно одного варианта сужает, как значение из текста."""
    low = text.casefold()
    out: dict[str, Known] = {}
    left = narrow(positions, known)
    names = {c.name for p in left for c in p.characteristics if c.values}
    for name in sorted(names, key=len, reverse=True):
        if name in known:
            continue
        for m in re.finditer(r"(?<!\w)" + re.escape(name.casefold()) + r"(?!\w)", low):
            said = _AFTER_LABEL.match(text, m.end())
            mapped = _stem_option(said[1], name, left) if said else None
            if mapped is None:
                continue
            k = Known(value=mapped, source="text")
            narrowed = narrow(left, {name: k})
            if narrowed:
                out[name], left = k, narrowed
            break
    return out


def _stem_option(raw: str, name: str, positions: list[Position]) -> str | None:
    """Слово поставщика не из перечня, но с той же основой у ровно одного варианта: «Сменный
    аккумулятор» при «От сети / От аккумуляторных батарей» — второй. «Без аккумулятора» и два
    варианта с той же основой — не выбор."""
    words = tokens(raw)
    if not words or _NEGATION & set(words):
        return None
    options = {
        v
        for p in positions
        if (spec := next((c for c in p.characteristics if fold(c.name) == fold(name)), None))
        for v in spec.values
    }
    return _stem_pick(raw, options)


def _stem_pick(raw: str, options: Iterable[str]) -> str | None:
    words = tokens(raw)
    if not words or _NEGATION & set(words):
        return None
    hits = [v for v in set(options) if _shares_stem(raw, v)]
    return hits[0] if len(hits) == 1 else None


LABEL_NOTE = "По подписи в тексте «{said}» — вариант перечня с той же основой, проверьте"


def _from_labels(
    chars: list[CharacteristicOut], session: Session, specs: list[CharacteristicSpec]
) -> list[CharacteristicOut]:
    """Пустое поле с перечнем, подпись которого есть в тексте, а слово после неё — основой ровно
    одного варианта: «Тип питания Сменный аккумулятор» → «От аккумуляторных батарей». Тем же
    правилом текст уже сузил позицию КТРУ (`_known_from_labels`); теперь и поле не пусто."""
    texts: dict[Literal["name", "description"], str] = {
        "name": session.name,
        "description": session.description,
    }
    out: list[CharacteristicOut] = []
    for c in chars:
        spec = _spec_of(specs, c.name) if c.value is None else None
        if spec is None or not spec.values:
            out.append(c)
            continue
        filled = None
        for where, text in texts.items():
            for m in re.finditer(
                r"(?<!\w)" + re.escape(c.name.casefold()) + r"(?!\w)", text.casefold()
            ):
                said = _AFTER_LABEL.match(text, m.end())
                value = _stem_pick(said[1], spec.values) if said else None
                if value is None or said is None:
                    continue
                phrase = said[1].strip()
                at = text.index(phrase, m.end())
                filled = c.model_copy(
                    update={
                        "value": value,
                        "source": Span(field=where, start=at, end=at + len(phrase), text=phrase),
                        "status": "normalized",
                        "normalized_from": phrase,
                        "method": "rule",
                        "note": LABEL_NOTE.format(said=phrase),
                    }
                )
                break
            if filled is not None:
                break
        out.append(filled or c)
    return out


def _target(state: KtruState, catalog: Catalog) -> Position | None:
    code = state.final_code or (state.candidates[0].code if state.candidates else None)
    return catalog.get(code) if code else None


def _state(session: Session, deps: Deps) -> KtruState:
    """Состояние сужения по сессии: код ставится сразу или после подтверждения при сомнении."""
    positions = [p for c in session.candidates if (p := deps.catalog.get(c.code)) is not None]
    state = build_state(
        session.candidates,
        positions,
        session.known,
        unexplained=session.unexplained,
        confirmed=session.confirmed,
        rejected=session.rejected,
        weak_match=session.weak_match,
        text=f"{session.name}\n{session.description}",
    )
    log.info(
        "итог: кандидатов=%d осталось=%d точный_код=%s вопрос=%s",
        len(state.candidates),
        state.remaining,
        "да" if state.final_code else "нет",
        state.question.characteristic if state.question else "нет",
    )
    confident = len(session.candidates) == 1 and (
        (session.candidates[0].similar or 0) >= AUTO_SHARE or session.named == state.final_code
    )
    if state.final_code and session.confirmed != state.final_code and not confident:
        position = deps.catalog.get(state.final_code)
        # Кандидаты на экране, а не в сессии: слабые (ниже половины лучшего) сужение отбрасывает.
        # Код после ответа поставщика (сузил до одной сам) — не повод переспрашивать.
        by_answer = any(k.source == "answer" for k in session.known.values())
        why = (None if by_answer else _low_share(state.candidates)) or (
            _doubtful_code(session.name, position, deps) if position is not None else None
        )
        if position is not None and why:
            # Код — авторитетный результат: при сомнении он ставится только после подтверждения.
            state = KtruState(
                candidates=state.candidates,
                remaining=1,
                final_code=None,
                question=confirm_question(position),
                steps=[why],
            )
    return state


def _extracted_for(session: Session, target: Position) -> list[Extracted]:
    """Значения из текста для выбранной категории. Первое извлечение шло по полям всех кандидатов,
    и «Размер 60» могло лечь в «Размер» чужого типа, а не в «Российский размер» выбранного:
    правила прогоняются по полям выбранной категории ещё раз, прежние значения — добором."""
    if len(session.candidates) < 2:
        return session.extracted
    own = extract_rules(session.name, session.description, target.characteristics, [target.name])
    names = {fold(e.name) for e in own}
    return [*own, *(e for e in session.extracted if fold(e.name) not in names)]


def render(session: Session, deps: Deps) -> AutofillOut:
    """Ответ по сессии: состояние сужения, характеристики целевой позиции, замечания."""
    state = _state(session, deps)
    positions = [p for c in session.candidates if (p := deps.catalog.get(c.code)) is not None]
    state.trail = _first_trail(state, positions, session, deps)
    _mark_picks(state.trail, [e for e in session.extracted if e.picks])
    state.candidates = _explained(
        state.candidates, deps.catalog.get, session.extracted, session.known
    )
    state.candidates = [_with_ktru_count(c, deps) for c in state.candidates]
    _conflicts_last(state, session)
    # Справочник типов КТРУ: пока человек выбирает тип товара (вопрос «Позиция КТРУ»), полей нет —
    # поля первого кандидата (а с ними правило «не меньше 4 характеристик») выдавали бы чужой товар
    # за свой. При вопросе о характеристике и в справочниках позиций — как раньше.
    choosing = state.question is None or state.question.characteristic == CONFIRM
    typed = any(p.source == TYPE_SOURCE for p in positions)
    shown = state.final_code or not (typed and choosing)
    target = _target(state, deps.catalog) if shown else None
    # Ответ про позицию КТРУ («Цветность: Цветная») заполняет и одноимённое поле карточки; ответ
    # первого этапа важнее, а значение из текста первого этапа — нет: человек ответил о поле, и
    # поле показывает его ответ, а не «Угловая» из описания. Сужение категории ответы КТРУ не
    # трогают.
    answered = {
        **{k: v for k, v in session.known.items() if v.source != "answer"},
        **session.ktru_known,
        **{k: v for k, v in session.known.items() if v.source == "answer"},
    }
    # Подсказки похожих СТЕ и двойников — для позиции, по которой их собрали (код определён).
    ours = target is not None and target.code == session.similar_for
    hints = session.similar if ours else {}
    twin = session.twin_hints if ours else {}
    found = _extracted_for(session, target) if target else []
    chars = _characteristics(target, found, answered, hints, twin) if target else []
    chain = _chain(session, state.final_code, deps)
    extra: list[CharacteristicOut] = []
    if target is not None:
        chars, extra = _origins(session, target, chars, chain, answered, deps)
        # Новая характеристика заводится, только если по смыслу относится к товару: ту, которую
        # модель назвала чужой, не заводим — её текст остаётся в «Не разобрано».
        alien = (
            {fold(o.field) for o in session.oddities if o.kind == "field"}
            if target.code == session.llm_for
            else set()
        )
        extra = [e for e in extra if e.origin != "new" or fold(e.name) not in alien]
        chars = _one_span_few_fields(chars, session)
        chars = _from_kin_fields(chars, extra, _specs_for(target, chain, deps))
        chars = _from_labels(chars, session, _specs_for(target, chain, deps))
    if target is not None:
        issues = _issues(session, target, chars)
        if (dup := _duplicate(session.twins, target.code)) is not None:
            issues.insert(0, dup)
        issues.extend(
            typo_issues(session.name, session.description, target, deps.bm25, deps.exemplars)
        )
    else:
        # Без позиции проверить можно только текст поставщика: мат, данные людей, мусор.
        issues = [i for i in _issues(session, None, []) if i.field in ("name", "description")]
    issues[0:0] = layout_issues(
        session.layout.get("name", ""), session.layout.get("description", ""), deps.bm25
    )
    if session.typos:
        issues.insert(0, _typo_issue(session))
    if session.injected:
        issues.insert(0, instruction_issue(session.injected))
    if chain is not None:
        issues.extend(_merge_issue(m) for m in chain.ktru_merged)
    if target is not None:
        # Код уже сказал о поле («ноль — так не бывает», «необычно для категории»): мнение модели
        # о том же поле — повтор.
        flagged = {i.field for i in issues if i.severity != "info" and i.kind == "mismatch"}
        issues.extend(
            i for i in session.sanity if i.field == "description" or i.field not in flagged
        )
        issues.extend(_said_not_listed([*chars, *extra], _specs_for(target, chain, deps)))
    kind = None
    if state.steps == [STEP_NOT_FOUND]:
        kind = outside_kind(deps.glossary.expand(session.name), deps.exemplars)
        state.steps = (
            not_goods_steps(kind)
            if kind is not None
            else not_found_steps(
                _near(session.name, deps), session.rejected, _head_word(session.name)
            )
        )
    if state.final_code is None and state.question is None:
        issues.insert(0, not_found(state.steps, kind))
    if chain is not None and chain.ktru is None:
        issues.insert(0, _no_ktru(chain))
    issues.extend(_number_issues(session, positions, chars))
    # «Высота: 50000 мм» — одно замечание: если о поле уже сказали «необычно для категории»,
    # общее «так не бывает» не повторяем.
    flagged = {i.field for i in issues if i.severity != "info" and i.rule != SIZE_RULE}
    issues = [i for i in issues if i.rule != SIZE_RULE or i.field not in flagged]
    if target is not None and target.code == session.llm_for:
        issues.extend(_oddity_issues(session, target, chars, issues))
    # Одно и то же замечание дважды («Похоже на опечатку: «Шырина»» из наименования и проверки
    # описания) — один раз.
    issues = list({(i.field, i.message): i for i in reversed(issues)}.values())[::-1]
    return AutofillOut(
        session_id=session.id,
        ktru=state,
        characteristics=chars,
        issues=issues,
        degraded=[d for d in session.degraded if d in ("llm", "embeddings")],  # type: ignore[misc]
        chain=chain,
        twins=_twins_out(session.twins, state.final_code),
        extra=extra,
        name=session.name,
        description=session.description,
        suggestions=session.suggestions if target is not None else [],
        unread=unread(
            session.description, [c.source for c in [*chars, *extra] if c.source is not None]
        )
        if target is not None
        else [],
        llm="off" if not deps.llm_enabled else "unavailable" if "llm" in session.degraded else "on",
    )


def _conflicts_last(state: KtruState, session: Session) -> None:
    """Варианты, которым противоречит текст, — в конце списка, порядок внутри групп прежний:
    так поставщику легче отсеять. На эталоне hard.csv верный тип первым — 57,1 % против 58,4 %
    без этого (абсурдное значение «глина» противоречит и верному типу), решение пользователя."""
    q = state.question
    if q is None or q.characteristic != CONFIRM:
        return
    likely = session.type_order[0] if session.type_order else None
    conflicting = {c.code for c in state.candidates if c.conflicts}
    # Типы без позиций КТРУ (категории портала: перфораторы, металлические тумбы) — после типов
    # с КТРУ: ТЗ про КТРУ, но такой товар без них останется «не найден».
    no_ktru = {c.code for c in state.candidates if c.ktru_total is None}
    rank = {o.value: i for i, o in enumerate(q.options)}
    q.options.sort(
        key=lambda o: (
            o.value == NOTHING_FITS,
            o.value != likely,
            o.value in conflicting,
            o.value in no_ktru,
            rank[o.value],
        )
    )
    name = next((c.name for c in state.candidates if c.code == likely), None)
    if likely in rank and name:
        reason = (session.type_reason or "").rstrip(" .")
        why = f": {reason[:1].lower()}{reason[1:]}" if reason else ""
        state.steps.append(f"Вероятнее всего это «{name}» — поставлен первым{why}.")


def _with_ktru_count(candidate: Candidate, deps: Deps) -> Candidate:
    """Сколько позиций КТРУ у типа и сколько из них различимы: «2 позиции КТРУ» у тумбы, где
    обе — шаблон и позиция из него, поставщику врали бы о выборе на следующем шаге."""
    position = deps.catalog.get(candidate.code)
    if position is None or position.source != TYPE_SOURCE:
        return candidate
    linked = _linked_ktru(position, deps)
    if not linked:
        return candidate
    distinct, _ = merge_same(linked)
    return candidate.model_copy(update={"ktru_total": len(linked), "ktru_distinct": len(distinct)})


# Объяснение — у вариантов, которые человек видит (вопрос показывает до шести).
EXPLAINED = 10
MAX_MISSING = 5
_FITS = ("exact", "normalized", "bucket", "typo")


def _explained(
    candidates: list[Candidate],
    position_of: Callable[[str], Position | None],
    extracted: list[Extracted],
    known: dict[str, Known],
) -> list[Candidate]:
    """Почему кандидат подходит или нет: что из текста и ответов к нему подошло, каких его
    обязательных характеристик в тексте нет, что ему противоречит (`Candidate.matched` и др.)."""
    out = []
    for i, c in enumerate(candidates):
        position = position_of(c.code) if i < EXPLAINED else None
        if position is None:
            out.append(c)
            continue
        specs = {fold(s.name): s for s in position.characteristics if s.group != "general"}
        matched: list[str] = []
        conflicts: list[str] = []
        said: set[str] = set()
        for e in extracted:
            spec = specs.get(fold(e.name))
            if spec is None or fold(spec.name) in said:
                continue
            said.add(fold(spec.name))
            value = f"{e.raw_value} {e.raw_unit}" if e.raw_unit else e.raw_value
            if fit_value(e.raw_value, e.raw_unit, spec).status in _FITS:
                matched.append(f"{spec.name}: {value}")
            else:
                conflicts.append(f"{spec.name}: {value}")
        for name, k in known.items():
            if k.source != "answer" or k.value in (NONE_VALUE, OTHER_VALUE):
                continue
            spec = specs.get(fold(name))
            if spec is None or fold(spec.name) in said:
                continue
            said.add(fold(spec.name))
            ok = compatible(position, {name: k})
            (matched if ok else conflicts).append(f"{spec.name}: {k.value}")
        missing = [s.name for key, s in specs.items() if s.required and key not in said]
        out.append(
            c.model_copy(
                update={
                    "matched": matched,
                    "missing": missing[:MAX_MISSING],
                    "conflicts": conflicts,
                }
            )
        )
    return out


def _no_ktru(chain: Chain) -> Issue:
    """Кода КТРУ нет — сказать прямо: категория портала остаётся, код КТРУ пустой."""
    why = (
        "Для этой категории в справочнике КТРУ нет позиций (связи нет)."
        if chain.ktru_missing == "no_links"
        else "Позиции КТРУ этой категории не загружены в справочник модуля."
    )
    return Issue(
        field="ktru_code",
        kind="moderation_rule",
        severity="info",
        message=f"Кода КТРУ нет: карточка уйдёт с категорией портала «{chain.category_name}».",
        why=f"{why} Код КТРУ модуль не подставляет наугад; модератор проверит карточку.",
        rule="код КТРУ — только из позиций, связанных с категорией",
    )


KTRU_NOT_FOUND = (
    "Позиция КТРУ не подобрана: ни одна позиция КТРУ категории «{category}» не подошла. "
    "Карточку можно отправить с кодом категории без кода КТРУ или вернуться к вопросу и выбрать "
    "другой ответ."
)
# Модератор отклоняет заявку, если такая СТЕ уже есть (база знаний портала, статья 214102).
RULE_DUPLICATE = (
    "нет дублей: заявку на СТЕ, которая уже есть в каталоге, отклоняют с причиной «Предложена "
    "другая СТЕ» (база знаний портала, статья 214102)"
)
MAX_TWINS_SHOWN = 5


def _duplicate(twins: list[TwinRef], code: str) -> Issue | None:
    """Замечание «такая СТЕ уже есть»: СТЕ с тем же наименованием в той же категории."""
    same = next((t for t in twins if t.by == "name" and t.code == code), None)
    if same is None:
        return None
    return Issue(
        field="name",
        kind="duplicate",
        severity="warn",
        message=f"Такая СТЕ уже есть на портале: №{same.id}",
        why="Наименование совпадает с существующей СТЕ этой категории. Если товар тот же, "
        "воспользуйтесь ею: заявку-дубль отклонят. Если отличается, укажите отличие в "
        "наименовании и характеристиках.",
        rule=RULE_DUPLICATE,
    )


def _twins_out(twins: list[TwinRef], code: str | None) -> list[TwinOut]:
    ordered = sorted(twins, key=lambda t: (t.code != code, t.by != "name"))
    return [
        TwinOut(
            id=t.id,
            name=t.name,
            url=SKU_URL.format(id=t.id),
            by=t.by,
            same_category=code is not None and t.code == code,
        )
        for t in ordered[:MAX_TWINS_SHOWN]
    ]


MERGE_RULE = "одинаковые позиции КТРУ — берётся более свежая"


def _merge_issue(merge: KtruMerge) -> Issue:
    return Issue(
        field="ktru_code",
        kind="duplicate",
        severity="info",
        message=merge.message,
        why="По описанию одинаковые позиции не различить, поэтому вопроса нет: модуль берёт более "
        "свежую и говорит об этом. Если по документам нужна другая — укажите её код в поле КТРУ.",
        rule=MERGE_RULE,
    )


def _first_trail(
    state: KtruState, positions: list[Position], session: Session, deps: Deps
) -> list[TrailStep]:
    """Воронка первого этапа: справочник → подходят по описанию → значения и ответы → выбор."""
    typed = bool(_name_words(deps))
    what = "Типы КТРУ и категории портала" if typed else "Позиции справочника"
    trail = [TrailStep(label=what, remaining=len(deps.catalog))]
    if not positions:
        return trail
    first = TrailStep(label="Подходят по описанию", remaining=len(positions))
    trail = [trail[0], *_trail(first, positions, session.known)]
    final = deps.catalog.get(state.final_code) if state.final_code else None
    if final is not None and trail[-1].remaining != 1:
        trail.append(TrailStep(label=f"Выбран тип «{final.name}»", remaining=1))
    return trail


def _trail(first: TrailStep, positions: list[Position], known: dict[str, Known]) -> list[TrailStep]:
    """Воронка сужения: сколько позиций остаётся после каждого значения из текста и ответа.

    Значение, которое ничего не отсекло, шагом не считается: воронка показывает, что сузило выбор.
    """
    out = [first]
    left = positions
    for name, k in known.items():
        if k.value in (NONE_VALUE, OTHER_VALUE):
            continue
        kept = [p for p in left if compatible(p, {name: k})]
        if not kept or len(kept) == len(left):
            continue
        left = kept
        value = f"{k.value} {k.unit}" if k.unit else k.value
        out.append(TrailStep(label=f"{name}: {value}", remaining=len(left)))
    return out


def _chain(session: Session, code: str | None, deps: Deps) -> Chain | None:
    """Цепочка для определённой категории: её коды и сужение до позиции КТРУ.

    Значения для КТРУ — из текста поставщика по характеристикам самих позиций КТРУ (правила, без
    LLM: этап пересчитывается на каждый ответ) и из ответов второго этапа.
    """
    position = deps.catalog.get(code) if code else None
    if position is None:
        return None
    merged: list[KtruMerge] = []
    # Справочник типов КТРУ (`app.catalog.ktru_types`): найденная позиция — тип товара по КТРУ,
    # категория портала — производная по связи.
    typed = position.source == TYPE_SOURCE
    linked = dict.fromkeys(k for c in position.characteristics for k in c.ktru)
    ktru_positions = _linked_ktru(position, deps)
    state = None
    if KTRU_CODE.match(position.code):
        # Справочник собран из позиций КТРУ: найденная позиция и есть код КТРУ, второго этапа нет.
        own = Candidate(code=position.code, name=position.name, score=0.0, why=[])
        state = KtruState(
            candidates=[own], remaining=1, final_code=position.code, question=None, steps=[]
        )
    elif ktru_positions:
        loaded = len(ktru_positions)
        # Шаблон и позиция из него с одинаковыми характеристиками: по тексту не различить — одна,
        # более свежая, и поставщик видит, какие объединены и почему (`Chain.ktru_merged`).
        ktru_positions, merged = merge_same(ktru_positions)
        ktru_specs = _union_specs(ktru_positions, merge_values=True)
        extracted = extract_rules(session.name, session.description, ktru_specs)
        # Значение, найденное в поле шаблона категории («Класс одежды…: 2 класс»), сужает и КТРУ,
        # даже если перечень КТРУ записан иначе («2»): решённое модулем не спрашивается.
        extracted += _template_for_ktru(session.extracted, extracted, ktru_specs)
        # Ответ модели на вопрос сужения по тексту («аккумуляторная» → «От аккумуляторных
        # батарей», `_answer_from_text`): правила этого слова не взяли, а позицию оно выбирает.
        have = {fold(e.name) for e in extracted}
        picked = [e for e in session.extracted if e.picks and fold(e.name) not in have]
        extracted += picked
        # Ответы первого этапа («Цветность: Белая» при выборе категории) сужают и КТРУ: тот же
        # вопрос второй раз не задаётся. Ответы второго этапа важнее.
        ktru_names = {fold(c.name) for p in ktru_positions for c in p.characteristics}
        answered = {
            name: k
            for name, k in session.known.items()
            if k.source == "answer"
            and k.value not in (NONE_VALUE, OTHER_VALUE)
            and fold(name) in ktru_names
        }
        known = {**_known_from_text(extracted, ktru_positions), **answered, **session.ktru_known}
        text = f"{session.name}\n{session.description}"
        known = {**_known_from_labels(text, ktru_positions, known), **known}
        named = {fold(n) for n, k in known.items() if k.value not in (NONE_VALUE, OTHER_VALUE)}
        _, refined = prefer_refined(narrow(ktru_positions, known), named)
        if refined:
            gone = {code for m in refined for code in m.dropped}
            ktru_positions = [p for p in ktru_positions if p.code not in gone]
            merged = [*merged, *refined]
        # Общая позиция (шаблон КТРУ со всеми вариантами) рядом с точной из него: на «от
        # аккумулятора» подходят обе, и поставщик выбирал из двух одинаковых. Пока точная
        # подходит, общая не предлагается; подтверждённую поставщиком не прячем.
        narrowed, wider = drop_wider(ktru_positions, lambda p: compatible(p, known))
        if session.ktru_confirmed in wider:
            wider = {}
        else:
            ktru_positions = narrowed
        candidates = [
            Candidate(code=p.code, name=p.name, score=0.0, why=[]) for p in ktru_positions
        ]
        state = build_state(
            candidates,
            ktru_positions,
            known,
            text=f"{session.name}\n{session.description}",
            confirmed=session.ktru_confirmed,
            rejected=session.ktru_rejected,
        )
        if state.steps == [STEP_NOT_FOUND]:
            # Отказ на втором этапе — не «категория не найдена»: категория есть, кода КТРУ нет.
            state.steps = [KTRU_NOT_FOUND.format(category=position.name)]
        partial = loaded < len(linked)
        if partial and state.final_code and session.ktru_confirmed != state.final_code:
            # Загружена только часть позиций категории: единственная подходящая из загруженных
            # может быть не той, что подошла бы из всех. Код ставит человек, а не модуль.
            final = next(p for p in ktru_positions if p.code == state.final_code)
            state = KtruState(
                candidates=state.candidates,
                remaining=1,
                final_code=None,
                question=confirm_question(final),
                steps=[
                    f"Справочник КТРУ содержит {len(ktru_positions)} из {len(linked)} позиций "
                    "категории: подтвердите позицию или выберите «Ни одна не подходит»."
                ],
            )
        state.candidates = _explained(
            state.candidates, {p.code: p for p in ktru_positions}.get, extracted, known
        )
        what = "типа" if typed else "категории"
        first = TrailStep(label=f"Позиции КТРУ {what} «{position.name}»", remaining=len(linked))
        state.trail = _trail(first, ktru_positions, known)
        if merged:
            state.trail.insert(
                1,
                TrailStep(
                    label=(
                        "Одинаковые позиции и шаблоны объединены, осталась более точная или "
                        "свежая: "
                        if refined
                        else "Одинаковые позиции объединены, осталась более свежая: "
                    )
                    + ", ".join(m.kept for m in merged),
                    remaining=len(ktru_positions),
                ),
            )
        if wider:
            state.trail.insert(
                1 + bool(merged),
                TrailStep(
                    label="Общая позиция со всеми вариантами (шаблон КТРУ) не предлагается, пока "
                    "подходит более точная: " + ", ".join(wider),
                    remaining=len(ktru_positions),
                ),
            )
        if state.final_code and state.trail[-1].remaining != 1:
            state.trail.append(TrailStep(label=f"Выбрана позиция {state.final_code}", remaining=1))
        # Что выбор сделала модель, видно в воронке и в шагах. Шаги — в начало: последний шаг
        # мастер показывает как пояснение к вопросу.
        used = [e for e in picked if (k := known.get(e.name)) is not None and k.source == "text"]
        _mark_picks(state.trail, used)
        state.steps[0:0] = [
            PICKED_STEP.format(name=e.name, value=e.raw_value, quote=e.source.text) for e in used
        ]
    return Chain(
        category=position.code,
        category_name=position.name,
        kpgz=position.kpgz,
        okpd2=position.okpd2 or None,
        spgz=list(dict.fromkeys(s for c in position.characteristics for s in c.spgz)),
        ktru_linked=len(linked),
        ktru=state,
        path=position.path,
        portal_id=position.portal_id,
        # Второго этапа нет: у категории нет ссылок на КТРУ, или привязанных позиций нет в
        # загруженном справочнике. Код КТРУ тогда пустой, а не ближайший.
        ktru_missing=None if state else ("not_loaded" if linked else "no_links"),
        ktru_type=position.name if typed else None,
        ktru_merged=merged,
        category_via=("ktru_link" if position.kpgz else "none") if typed else "search",
    )


NEAR_LIMIT = 3


MIN_SHOWN_WORD = 3


def shown_match(word: str) -> bool:
    """Слово для показа совпадения: не число, не единица («г», «см») и не огрызок."""
    return (
        len(word) >= MIN_SHOWN_WORD
        and not any(ch.isdigit() for ch in word)
        and normalize_unit(word) is None
    )


def _near(name: str, deps: Deps) -> list[NearPosition]:
    """Позиции с совпавшими словами наименования, не прошедшие порог «вне справочника»."""
    query = deps.glossary.expand(name)
    words = [w for w, _ in words_with_stems(name) if shown_match(w)]
    out = []
    for code, _score in deps.bm25.search(query, NEAR_LIMIT):
        position = deps.catalog.get(code)
        matched_stems = deps.bm25.matched(query, code)
        matched = list(
            dict.fromkeys(
                w.lower()
                for w in words
                if deps.bm25.correct(tokens(w)[0] if tokens(w) else "") in matched_stems
            )
        )
        near = NearPosition(code=code, name=position.name, matched=matched) if position else None
        # Одно случайное слово («фильтр» у «Чай … пакетики-фильтры») — не похожесть: нужно главное
        # слово наименования или два совпавших слова.
        if near is not None and (_has_head(near, name) or len(matched) >= MIN_NEAR_WORDS):
            out.append(near)
    return out


MIN_NEAR_WORDS = 2
_ACCESSORY = ("запасн", "запчаст", "расходн", "комплектующ", "принадлежн", "составн")


def _low_share(candidates: list[Candidate]) -> str | None:
    """Единственный кандидат, но похожие СТЕ за него меньше порога автовыбора: остальные варианты
    отсеяны, а не проиграли. Код ставится только после подтверждения («Фурнитура для
    смесителей» при совпадении 43 % без вопроса)."""
    if len(candidates) != 1 or (share := candidates[0].similar) is None or share >= AUTO_SHARE:
        return None
    return (
        f"Совпадение с похожими СТЕ портала {int(share * 100)} % — ниже {int(AUTO_SHARE * 100)} %. "
        "Подтвердите позицию или выберите «Ни одна не подходит»."
    )


def _doubtful_code(name: str, position: Position, deps: Deps) -> str | None:
    """Причина не ставить код без подтверждения: главное слово наименования есть только в значениях
    характеристик позиции («Ведро» у красок в «Вид тары») или позиция — запчасти к самому товару
    («Проектор Epson» → «Запасные части … к проекторам»)."""
    head = [deps.bm25.correct(t) for t in tokens(_head_word(name))]  # «бумаага» — та же «бумага»
    if not head:
        return None
    own = [deps.bm25.correct(t) for t in tokens(position.name)]
    if head[0] not in own:
        return (
            f"Слово «{_head_word(name)}» есть в характеристиках позиции «{position.name}», но не "
            "в её названии. Подтвердите позицию или выберите «Ни одна не подходит»."
        )
    part = own[0].startswith(_ACCESSORY) and not head[0].startswith(_ACCESSORY)
    if part:
        return (
            f"Подобрана позиция запчастей и принадлежностей «{position.name}», а в наименовании — "
            f"сам товар «{_head_word(name)}». Подтвердите позицию или выберите «Ничего не "
            "подходит»."
        )
    return None


def _head_word(name: str) -> str:
    """Главное слово наименования — первое слово-товар («Ноутбук Apple …» → «ноутбук»)."""
    return next((w.lower() for w, _ in words_with_stems(name) if shown_match(w)), "")


def _has_head(near: NearPosition, name: str) -> bool:
    """Главное слово наименования — главное слово позиции: «Ноутбук Apple …» и «Ноутбук»,
    а не «Фильтр масляный» и «Чай … в пакетиках-фильтрах»."""
    head, own = tokens(_head_word(name)), tokens(near.name)
    return bool(head) and bool(own) and head[0] == own[0]


# После отказа эмбеддингов не дёргаем модель на каждом запросе: пауза, затем новая попытка.
EMBED_COOLDOWN_S = 60.0
# После отказа сервиса LLM (квота, 429, недоступна) — пауза: каждый шаг иначе ждал бы бюджет.
LLM_COOLDOWN_S = 120.0


def _embedder(deps: Deps) -> Callable[[str], Awaitable[list[float]]]:
    """Один текст → один вектор поверх LLMClient.embed (список текстов → список векторов)."""

    async def embed_one(text: str) -> list[float]:
        if time.monotonic() < deps.embed_paused_until:
            raise LLMError("эмбеддинги на паузе после сбоя")
        try:
            return (await deps.llm.embed([text]))[0]
        except LLMInputError:
            # Сервер жив, не справился с этим текстом (NaN у bge-m3): пауза выключила бы векторы
            # всему процессу на минуту — у пакета это сотни строк только по BM25.
            raise
        except LLMError:
            deps.embed_paused_until = time.monotonic() + EMBED_COOLDOWN_S
            raise

    return embed_one


async def prepare_session(inp: AutofillIn, deps: Deps) -> Session:
    """Кандидаты, извлечение и известные значения — без сохранения (пакет сессии не хранит)."""
    # «cnek jabcysq» — набрано в английской раскладке: ищем и извлекаем по «стул офисный»,
    # исходный текст — для замечания (`layout_issues`).
    name, _ = layout_fix(inp.name, deps.bm25)
    description, _ = layout_fix(inp.description, deps.bm25)
    typed = (("name", inp.name, name), ("description", inp.description, description))
    layout = {field: was for field, was, now in typed if was != now}
    # «столдеревянный» — пропущен пробел: ищем и читаем по «стол деревянный», о замене — замечание.
    name, glued = unglue(name, deps.bm25, deps.exemplars)
    description, _ = unglue(description, deps.bm25, deps.exemplars)
    typos = _name_typos(name, deps)
    for was, now in typos.items():
        name = re.sub(rf"\b{re.escape(was)}\b", now, name)
    # «Модель: Гигант-5. Высота: 5 метров» одной строкой — наименование до первой подписи,
    # остальное описание. Поиск категории идёт по наименованию, характеристики читаются из обоих.
    name, description = derive_parts(name, description)
    # «ВАЖНО ДЛЯ АССИСТЕНТА: …» — не сведение о товаре: не читается ни правилами, ни моделью.
    description, injected = strip_instructions(description)
    stripped_name, injected_name = strip_instructions(name)
    if stripped_name:
        name, injected = stripped_name, [*injected_name, *injected]
    if deps.structure:
        # Первый шаг: модель делит текст на пары «название: значение» (`extract.llm_pairs`),
        # дальше строки «Название: значение» раскладывает код — КТРУ, портал, новая характеристика.
        description = await _structured(name, description, deps)
    session = Session.new(name=name, description=description)
    session.layout, session.typos = layout, {**glued, **typos}
    session.injected = injected
    query = deps.glossary.expand(f"{name}\n{description}")
    embed = (
        _embedder(deps) if deps.vectors is not None or deps.exemplar_vectors is not None else None
    )
    candidates, degraded = await find_candidates(
        query,
        deps.catalog,
        deps.bm25,
        deps.vectors,
        embed,
        min_score=deps.min_score,
        gate=deps.glossary.expand(name),
        exemplars=deps.exemplars,
        exemplar_vectors=deps.exemplar_vectors,
    )
    candidates = _with_siblings(candidates, deps.catalog)
    session.twins = _twins(name, deps)
    if (decided := twin_category(session.twins, AUTO_SHARE)) is not None:
        candidates = _decided_by_twins(candidates, *decided, session.twins, deps)
    top = deps.catalog.get(candidates[0].code) if candidates else None
    ktru_types = top is not None and top.source == TYPE_SOURCE
    if ktru_types and (named := _named_in_text(candidates, name)) is not None:
        candidates, session.named = [named], named.code
    weak = None
    if not candidates and (near := _near(name, deps)) and _has_head(near[0], name):
        # Порог не пройден из-за бренда и модели, но главное слово — эта позиция: спросим, а не
        # скажем «не найдено» с ней же в «похожих».
        candidates = [
            Candidate(code=near[0].code, name=near[0].name, score=0.0, why=near[0].matched)
        ]
        weak = _head_word(name)
    positions = [p for c in candidates if (p := deps.catalog.get(c.code)) is not None]
    specs = _union_specs(positions)
    extracted = extract_rules(name, description, specs, [p.name for p in positions])
    session.candidates = candidates
    session.extracted = extracted
    session.known = _known_from_text(extracted, positions)
    session.degraded = list(degraded)
    # Почти единогласные похожие СТЕ объясняют наименование целиком (бренд, модель): слова, которых
    # нет в тексте позиции, — не повод переспрашивать.
    confident = len(candidates) == 1 and (candidates[0].similar or 0) >= AUTO_SHARE
    session.unexplained = [] if confident else _unexplained(name, candidates, deps)
    session.weak_match = weak
    # Модель — только о характеристиках определённой позиции: пока код не определён, вопрос по
    # объединению кандидатов стоил 5–13 с и квоты, а ответ шёл и в чужие позиции. Не определён —
    # спросим в `answer`, когда ответ поставщика его определит. Вопрос сужения о характеристике —
    # другое дело: вариантов мало, и если текст на него отвечает, поставщика не спрашиваем.
    await _answer_from_text(session, None, deps)
    state = _state(session, deps)
    if state.final_code is not None:
        await _fill_missing(session, state.final_code, deps)
    elif deps.rerank_types and state.question is not None:
        await _rank_types(session, state, deps)
    return session


async def _structured(name: str, description: str, deps: Deps) -> str:
    """Описание, приведённое моделью к строкам «Название: значение» (`split_pairs`): пару код
    принимает, только если цитата есть в тексте, а значение — в цитате; непринятое и неразобранное
    остаётся как было. Модель выключена, на паузе или не успела — описание без изменений."""
    pairs = await _guarded(
        deps,
        lambda: split_pairs(name, description, deps.llm),
        "текст разбирают только правила",
    )
    return structured_text(description, pairs or [])


async def _rank_types(session: Session, state: KtruState, deps: Deps) -> None:
    """Вопрос «какой это товар»: модель называет самый вероятный тип по тексту («Тумбочка для
    спальни» → «Тумба прикроватная», хотя слова «прикроватная» в тексте нет). Только порядок:
    выбор остаётся за поставщиком, наименования — только из списка (`rerank_types`)."""
    q = state.question
    if q is None or q.characteristic != CONFIRM:
        return
    names = {c.code: c.name for c in state.candidates}
    shown = [names[o.value] for o in q.options if o.value in names]
    text = f"{session.name}\n{session.description}"
    order = await suggest_type_order(deps, text, shown)
    if order is None or not order.names:
        return
    by_name = {v: k for k, v in names.items()}
    session.type_order = [by_name[n] for n in order.names if n in by_name]
    session.type_reason = order.reason or None


def _product_stems(name: str) -> set[str]:
    """Основы слов наименования, кроме слова сразу после подписи характеристики: «стол … высота
    столешницы 50 см» — о столешнице стола, а не товар «Столешницы»."""
    out: set[str] = set()
    prev = ""
    for _, stem in words_with_stems(name):
        if prev not in CUES:
            out.add(stem)
        prev = stem
    return out


def _named_in_text(candidates: list[Candidate], name: str) -> Candidate | None:
    """Справочник типов КТРУ: наименование типа целиком есть в наименовании поставщика («Дрель
    ручная электрическая 800 Вт») — это он, вопрос «какой это товар» не нужен. Из нескольких таких
    берётся самое длинное («Брюки спортивные», а не «Брюки»); равных несколько — решает человек.
    """
    have = _product_stems(name) - negated_stems(name)
    named = [
        (len(words), c)
        for c in candidates
        if (words := {stem for _, stem in words_with_stems(c.name)}) and words <= have
    ]
    if not named:
        return None
    longest = max(n for n, _ in named)
    top = [c for n, c in named if n == longest]
    return top[0] if len(top) == 1 else None


# Слово короче этого опечаткой не считаем: «стул» и «стол» — оба слова.
MIN_TYPO_WORD = 5
_CYRILLIC_WORD = re.compile(r"[А-Яа-яЁё]+")


def _name_words(deps: Deps) -> dict[str, tuple[str, list[set[str]]]]:
    """Основа → (слово, основы наименований, где оно есть) по справочнику типов КТРУ; справочник
    категорий — пусто."""
    if deps.name_words is None:
        positions = deps.catalog.all()
        words: dict[str, tuple[str, list[set[str]]]] = {}
        if any(p.source == TYPE_SOURCE for p in positions):
            for p in positions:
                own = set(tokens(p.name))
                for w in _CYRILLIC_WORD.findall(p.name):
                    for stem in tokens(w):
                        words.setdefault(stem, (w.lower(), []))[1].append(own)
        deps.name_words = words
    return deps.name_words


# Числительные словами («сорок второй размер») — не опечатки: «сорок» в одну букву от «сороч(ка)»
# уводило ботинки к сорочкам.
_NUMERAL = re.compile(
    r"(?:один|одна|одно|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять|полтора|полторы"
    r"|\w*надцат\w*|\w*дцат\w*|сорок\w{0,2}|\w*десят\w*|девяност\w{0,2}|\w*сот\w{0,2}|двест\w*"
    r"|тысяч\w*|(?:перв|втор|трет|четв[её]рт|пят|шест|седьм|восьм|девят|десят)"
    r"(?:ый|ой|ий|ая|ое|ые|ого|ому|ым|ом|ых|ую|ья|ье|ьи|ьего|ьей|ьему|ьим))",
    re.IGNORECASE,
)


def _name_typos(name: str, deps: Deps) -> dict[str, str]:
    """Опечатка в наименовании («курка зимняя»): слова нет среди слов наименований типов КТРУ, а на
    одну букву от него есть. Ищем по исправленному: иначе «курка» уводила к пальто и обуви. Из
    нескольких близких («куртка», «кирки») выбирают остальные слова наименования — «зимняя» бывает
    только рядом с курткой; не выбрали — не угадываем. Слово из справочника и обрезок основы
    («материала» и «материалов» стеммер режет по-разному) — не опечатка."""
    words = _name_words(deps)
    if not words:
        return {}
    said = set(tokens(name))
    out: dict[str, str] = {}
    for w in _CYRILLIC_WORD.findall(name):
        stems = tokens(w)
        if len(w) < MIN_TYPO_WORD or len(stems) != 1 or stems[0] in words:
            continue
        if _NUMERAL.fullmatch(w):
            continue
        stem = stems[0]
        near = [
            s
            for s in words
            if s[:1] == stem[:1]
            and abs(len(s) - len(stem)) <= 1
            and not (s.startswith(stem) or stem.startswith(s))
            and damerau_levenshtein(stem, s, limit=1) <= 1
        ]
        if len(near) > 1:
            context = said - {stem}
            score = {s: max(len(n & context) for n in words[s][1]) for s in near}
            best = max(score.values())
            near = [s for s in near if score[s] == best] if best else []
        if len(near) != 1 or _portal_word(stem, near[0], deps):
            continue
        fixed = words[near[0]][0]
        out[w] = fixed.capitalize() if w[:1].isupper() else fixed
    return out


# Слово портала, а не опечатка: встречается в наименованиях СТЕ хотя бы столько раз и не в 20 раз
# реже исправления («Матрица», «Поваренок», «Грамота» — названия товаров, а не ошибки).
PORTAL_WORD_DF = 3


def _portal_word(stem: str, fixed: str, deps: Deps) -> bool:
    if deps.exemplars is None:
        return False
    seen = deps.exemplars.df(stem)
    return seen >= PORTAL_WORD_DF and deps.exemplars.df(fixed) < 20 * seen


def _typo_issue(session: Session) -> Issue:
    fixed = ", ".join(f"«{a}» — «{b}»" for a, b in session.typos.items())
    return Issue(
        field="name",
        kind="typo",
        severity="warn",
        message=f"Похоже на опечатку: {fixed}. Товар подобран по исправленному наименованию.",
        why="Такого слова нет в справочнике: на одну букву от него — одно слово, или это два "
        "слова без пробела.",
        rule="наименование без опечаток",
        fix=Fix(name="name", value=session.name),
    )


# Одноимённых позиций в сужение берём не больше: дальше вопрос всё равно задаётся по значению.
MAX_SIBLINGS = 300


def _with_siblings(candidates: list[Candidate], catalog: Catalog) -> list[Candidate]:
    """Все кандидаты — позиции с одним наименованием (поиск их не различил): в сужение идут все
    позиции с этим наименованием, а не первые 20 по порядку поиска — различит их значение из текста.
    Блокер команды 06.10: 100 перчаток, отличающихся толщиной; без этого «75 мкм» находило верную
    позицию лишь при случайном попадании в двадцатку (`app.evaluation.near_twins`)."""
    if not candidates or len({c.name for c in candidates}) != 1:
        return candidates
    if len(candidates) == 1 and (candidates[0].similar or 0) >= AUTO_SHARE:
        return candidates  # похожие СТЕ или двойники уже назвали ровно эту позицию
    have = {c.code for c in candidates}
    score = candidates[-1].score  # для поиска они неразличимы: та же оценка, что у последнего
    extra = [p for p in catalog.same_name(candidates[0].name) if p.code not in have]
    return candidates + [
        Candidate(code=p.code, name=p.name, score=score, why=candidates[-1].why)
        for p in extra[: max(0, MAX_SIBLINGS - len(candidates))]
    ]


def _twins(name: str, deps: Deps) -> list[TwinRef]:
    """Такие СТЕ уже есть на портале: двойники наименования с кодом позиции их категории."""
    if deps.twins is None:
        return []
    return [
        TwinRef(
            id=t.id,
            name=t.name,
            code=p.code if (p := deps.catalog.by_portal(t.directory_id)) is not None else None,
            by=t.by,
        )
        for t in deps.twins.find(name)
    ]


def _decided_by_twins(
    candidates: list[Candidate], code: str, group: list[TwinRef], twins: list[TwinRef], deps: Deps
) -> list[Candidate]:
    """Двойники почти единогласно называют категорию: она — единственный кандидат, шаг выбора
    категории не нужен. Пример у варианта — сами двойники."""
    position = deps.catalog.get(code)
    top = candidates[0] if candidates else None
    if position is None or (
        len(candidates) == 1
        and top is not None
        and top.code == code
        and (top.similar or 0) >= AUTO_SHARE
    ):
        return candidates
    total = sum(t.by == group[0].by for t in twins)
    old = next((c for c in candidates if c.code == code), None)
    return [
        Candidate(
            code=code,
            name=position.name,
            score=old.score if old else 0.0,
            why=old.why if old else [],
            similar=round(len(group) / total, 4),
            examples=[SimilarSku(id=t.id, name=t.name) for t in group[:2]],
            similar_count=len(group),
            similar_total=total,
        )
    ]


async def _ask_model(
    name: str,
    description: str,
    specs: list[CharacteristicSpec],
    deps: Deps,
    all_specs: list[CharacteristicSpec] | None = None,
) -> tuple[list[Extracted], bool]:
    """(значения модели, модель не помогла): не дольше `llm_budget_s`, на паузе — сразу."""
    if not specs or not deps.llm_enabled:
        return [], False
    if time.monotonic() < deps.llm_paused_until:
        return [], True
    call = extract_llm(name, description, specs, deps.llm, deps.glossary, all_specs)
    try:
        if deps.llm_budget_s is None:
            return await call, False
        return await asyncio.wait_for(call, timeout=deps.llm_budget_s), False
    except TimeoutError:
        log.warning(
            "LLM не ответила за %.1f с — характеристики только по правилам", deps.llm_budget_s
        )
        return [], True
    except LLMError as exc:
        log.warning("LLM недоступна, характеристики только по правилам: %s", exc)
        # Пауза — только на отказ сервиса (квота, 429, сеть, 5xx): плохой вход одного поставщика
        # (400 «контекст переполнен», NaN на тексте) не выключает модель остальным.
        if not isinstance(exc, LLMInputError | LLMOutputError):
            deps.llm_paused_until = time.monotonic() + LLM_COOLDOWN_S
        return [], True


async def _fill_missing(session: Session, code: str, deps: Deps) -> None:
    """Код определён: чего правила не нашли в тексте, ищет модель (тоже в тексте), остальное
    берётся у такой же СТЕ портала, что осталось — подсказывают похожие СТЕ этой категории. Значения
    модели для прежней позиции (код сменился ответом) — вон: их имена могли совпасть с чужими."""
    position = deps.catalog.get(code)
    if position is None:
        return
    # Значения модели прежней позиции — вон, приведённые моделью к перечню — обратно как в тексте.
    # Ответ модели на вопрос сужения остаётся: по нему выбрана сама позиция.
    kept = [
        e.model_copy(update={"raw_value": e.was, "method": "rule", "was": None, "note": None})
        if e.was is not None
        else e
        for e in session.extracted
        if e.method != "llm" or e.was is not None or e.picks
    ]
    session.extracted = kept
    # Что поставщик уже сказал ответом, модель и подсказки не переспрашивают.
    answered = {
        fold(n) for n, k in {**session.ktru_known, **session.known}.items() if k.source == "answer"
    }
    twin = _twin_hints(session, position, deps, answered)
    # Без описания текст — одно наименование, а оно у такой же СТЕ то же: о её полях модель не
    # спрашивают (быстрее и без квоты; всё есть у двойника — без модели вовсе). С описанием модель
    # читает его обо всём, чего не нашли правила: значение поставщика важнее двойника.
    skip_model = set() if session.description.strip() else {fold(n) for n in twin}
    found = {fold(e.name) for e in kept} | answered | skip_model
    rest = [s for s in position.characteristics if fold(s.name) not in found]
    (items, failed), kept = await asyncio.gather(
        _ask_model(session.name, session.description, rest, deps, position.characteristics),
        _match_lists(session, kept, position, deps),
    )
    session.extracted = [*kept, *items]
    _, _, session.oddities = await asyncio.gather(
        _review(session, position, deps),
        _sanity(session, position, deps),
        _oddities(session, position, deps),
    )
    await _answer_from_text(session, code, deps)
    read = {fold(e.name) for e in items}
    session.twin_hints = {n: h for n, h in twin.items() if fold(n) not in read}
    taken = answered | {fold(n) for n in session.twin_hints}
    # «Спросили» — только если модель ответила: после отказа следующий шаг спросит снова.
    # Модель выключена модератором — тоже не «спросили»: после включения следующий шаг спросит.
    session.llm_for = None if failed or not deps.llm_enabled else code
    if failed and "llm" not in session.degraded:
        session.degraded = ["llm", *session.degraded]
    session.similar = _similar_hints(session, position, deps, taken)
    session.similar_for = code


async def _review(session: Session, position: Position, deps: Deps) -> None:
    """Второй слой: неподтверждённые значения первой модели и непонятые куски текста
    (`llm_review`). Не ответил за `review_budget_s` или ошибся — всё остаётся как после первого."""
    session.suggestions = []
    if deps.review_llm is None or not deps.llm_enabled:
        return
    doubts = [e for e in session.extracted if e.method == "llm" and not e.checked]
    left = unread(session.description, [e.source for e in session.extracted])
    if not doubts and not left:
        return
    text = f"{session.name}\n{session.description}"
    call = review(text, position.characteristics, doubts, left, deps.review_llm)
    try:
        answer = await asyncio.wait_for(call, timeout=deps.review_budget_s)
    except (TimeoutError, LLMError) as exc:
        log.warning("вторая модель не ответила: %s", type(exc).__name__)
        return
    if answer is not None:
        session.extracted, session.suggestions = apply_review(
            answer, session.extracted, position.characteristics, left
        )


MAX_PICKS = 3
PICKED_NOTE = "Модель выбрала по тексту «{quote}» — проверьте"
PICKED_STEP = (
    "Позицию КТРУ помогла выбрать модель: «{name}» — «{value}» по тексту «{quote}». Проверьте."
)
PICKED_TRAIL = " — модель по тексту «{quote}»"


def _mark_picks(trail: list[TrailStep], picked: list[Extracted]) -> None:
    """Шаг воронки, который сделал ответ модели, так и подписан: «Тип питания: От аккумуляторных
    батарей — модель по тексту «аккумуляторная»»."""
    quotes = {f"{e.name}: {e.raw_value}": e.source.text for e in picked}
    for step in trail:
        if (quote := quotes.get(step.label)) is not None:
            step.label += PICKED_TRAIL.format(quote=quote)


def _open_question(session: Session, code: str | None, deps: Deps) -> Question | None:
    """Вопрос, который сейчас получил бы поставщик: первого этапа (`code` нет) или второго."""
    if code is None:
        return _state(session, deps).question
    chain = _chain(session, code, deps)
    return chain.ktru.question if chain is not None and chain.ktru is not None else None


async def _answer_from_text(session: Session, code: str | None, deps: Deps) -> bool:
    """Вопрос сужения, на который отвечает сам текст, поставщику не задаётся — на любом этапе:
    о характеристике при выборе категории (`code` нет) и при выборе позиции КТРУ внутри типа.

    Правила берут значение, только если оно записано словами перечня; текст же часто называет
    его иначе — признаком в наименовании, синонимом. Модель называет вариант и цитату
    (`pick_option`), код проверяет и сужает дальше — не больше `MAX_PICKS` вопросов подряд.
    Значение — в поле с пометкой «проверьте», в шагах сказано, что выбор сделала модель. Вопрос
    «какой это товар» (`CONFIRM`) модель не решает — только ставит вероятный тип первым
    (`_rank_types`). True — что-то выбрано."""
    asked: set[str] = set()
    changed = False
    started = time.monotonic()
    for _ in range(MAX_PICKS if deps.pick_options else 0):
        q = _open_question(session, code, deps)
        if q is None or q.characteristic in (CONFIRM, UNIT_QUESTION) or q.characteristic in asked:
            break
        if deps.llm_budget_s is not None and time.monotonic() - started > deps.llm_budget_s:
            break  # все вопросы подряд — не дольше одного бюджета модели: шаг мастера ждёт
        asked.add(q.characteristic)
        options = [
            o.value for o in q.options if o.value not in (NONE_VALUE, OTHER_VALUE, NOTHING_FITS)
        ]
        name = fold(q.characteristic)
        # Первая модель уже прочла это поле с цитатой («Тип питания = От сети»): второй вопрос
        # не нужен, её значение из вариантов и выбирает позицию.
        own = next(
            (
                e
                for e in session.extracted
                if fold(e.name) == name and e.method == "llm" and e.raw_value in options
            ),
            None,
        )
        if own is not None:
            pick = own.model_copy(update={"picks": True})
        else:
            hit = await suggest_option(
                deps, session.name, session.description, q.characteristic, options
            )
            if hit is None:
                break
            pick = Extracted(
                name=q.characteristic,
                raw_value=hit.value,
                source=hit.source,
                method="llm",
                note=PICKED_NOTE.format(quote=hit.source.text),
                picks=True,
            )
        session.extracted = [*(e for e in session.extracted if fold(e.name) != name), pick]
        changed = True
        if code is None:
            session.known = {**_text_known(session, deps), **_answered(session)}
    return changed


def _answered(session: Session) -> dict[str, Known]:
    return {n: k for n, k in session.known.items() if k.source == "answer"}


def _text_known(session: Session, deps: Deps) -> dict[str, Known]:
    """Значения из текста, сужающие первый этап (правила и ответы модели на вопрос сужения)."""
    positions = [p for c in session.candidates if (p := deps.catalog.get(c.code)) is not None]
    return _known_from_text(session.extracted, positions)


async def _oddities(session: Session, position: Position, deps: Deps) -> list[Oddity]:
    """Значения из текста, которых у такого товара не бывает («Материал: глина» у тумбочки), по
    мнению модели (`find_oddities`) — замечание поставщику, значения не меняются.

    Проверяется карточка, какой её увидит поставщик: все поля со значением из текста, включая
    характеристики позиций КТРУ, которых нет в шаблоне типа («Материал изделия»)."""
    if not deps.sanity:
        return []
    card = render(session, deps)
    # Новые характеристики (подписи нет ни в позиции, ни на портале) — тоже: к товару должна
    # относиться сама характеристика, иначе она не заводится (`render`).
    fields = [*card.characteristics, *(e for e in card.extra if e.origin == "new")]
    values = {
        c.name: f"{c.value} {c.unit}" if c.unit else c.value
        for c in fields
        if c.value and c.method in ("rule", "llm")
    }
    new = frozenset(e.name for e in card.extra if e.origin == "new")
    return await suggest_oddities(deps, position.name, list(values.items()), new)


_FITS_CATALOG = ("in_list", "normalized", "in_range")


def _oddity_issues(
    session: Session, target: Position, chars: list[CharacteristicOut], issues: list[Issue]
) -> list[Issue]:
    """Замечания модели о здравом смысле — для полей, взятых из текста, если о поле ещё не
    сказано «необычно» (пределы СТЕ категории, габарит): одно и то же дважды не говорим.

    Данные важнее мнения модели: значение из перечня позиции и число, для которого у категории
    есть свои пределы по СТЕ портала (`CharacteristicSpec.plausible`), модель не оспаривает —
    она говорит только там, где у кода своей меры нет."""
    said = {i.field for i in issues if i.severity == "warn"}
    # О поле уже сказала проверка текста второй моделью (`llm_sanity`) — второй раз не говорим.
    told = {i.field for i in issues if i.rule == SANITY_RULE}
    by_name = {fold(c.name): c for c in chars}
    specs = {fold(s.name): s for s in target.characteristics}
    out = []
    for o in session.oddities:
        c = by_name.get(fold(o.field))
        field = f"characteristics.{o.field}"
        if c is None or c.value is None or c.method not in ("rule", "llm") or field in told:
            continue
        spec = specs.get(fold(o.field))
        # О числе «необычно» уже сказали данные — модель не повторяет. О слове («глина») код
        # говорит только «нет в перечне»: почему оно не подходит товару, добавляет модель.
        if field in said and (spec is None or spec.kind == "quantitative"):
            continue
        fits = c.status != "conflict" and c.ktru_fit not in ("not_in_list", "out_of_range")
        # Справочник сам задаёт перечень или диапазон, и значение в него укладывается.
        listed = bool(c.values) or c.range is not None or c.ktru_fit in _FITS_CATALOG
        allowed = fits and listed
        if allowed or (spec is not None and spec.plausible is not None):
            continue
        shown = f"{c.value} {c.unit}" if c.unit else c.value
        reason = o.reason.rstrip(" .")
        out.append(
            Issue(
                field=field,
                kind="mismatch",
                severity="warn",
                message=f"«{c.name}: {shown}» — необычно для товара «{target.name}»: "
                f"{reason[:1].lower()}{reason[1:]}. Проверьте значение.",
                why="Так считает модель: она сверяет значения из текста со здравым смыслом. "
                "Значение не изменено — решает поставщик.",
                rule=SANITY_RULE,
            )
        )
    return out


async def _sanity(session: Session, position: Position, deps: Deps) -> None:
    """Проверка правдоподобия (`llm_sanity`): тумбочка из глины, тумба глубиной 100 м. Только
    замечания `warn`; не ответила за `review_budget_s` — замечаний нет."""
    session.sanity = []
    if deps.review_llm is None or not deps.llm_enabled:
        return
    text = f"{session.name}\n{session.description}"
    try:
        answer = await asyncio.wait_for(
            sanity(position.name, text, deps.review_llm), timeout=deps.review_budget_s
        )
    except (TimeoutError, LLMError) as exc:
        log.warning("проверка правдоподобия не ответила: %s", type(exc).__name__)
        return
    if answer is not None:
        session.sanity = sanity_issues(answer, text, session.extracted)


MATCH_LIMIT = 6
MATCHED_NOTE = "Модель привела «{was}» из текста к перечню КТРУ — проверьте"


async def _match_lists(
    session: Session, extracted: list[Extracted], position: Position, deps: Deps
) -> list[Extracted]:
    """Слово поставщика есть в тексте, но не в перечне («встроенный аккумулятор» при «От сети,
    От аккумуляторных батарей»): модель называет вариант перечня того же смысла. Код берёт ответ,
    только если он ровно из перечня, а цитата с исходным словом есть в тексте (`match_to_list`);
    в поле — с фрагментом текста и пометкой. Код КТРУ по такому значению не выбирается
    (`_known_from_text` берёт только правила)."""
    specs = {fold(s.name): s for s in position.characteristics if s.values and s.group != "general"}
    todo = [
        (i, e, spec)
        for i, e in enumerate(extracted)
        if e.method == "rule"
        and (spec := specs.get(fold(e.name))) is not None
        # Число к перечню приводит код (единицы, корзины), а не модель: «1010 г» при перечне
        # «0.3, 0.4, …» она «приводила» к 0.3 кг.
        and not e.raw_value[:1].isdigit()
        and fit_value(e.raw_value, e.raw_unit, spec).status == "not_in_list"
    ][:MATCH_LIMIT]
    if not todo:
        return extracted
    found = await asyncio.gather(
        *(
            suggest_list_value(
                deps, session.name, session.description, spec, e.raw_value, e.source.text
            )
            for _, e, spec in todo
        )
    )
    out = list(extracted)
    for (i, e, _), hit in zip(todo, found, strict=True):
        if hit is not None:
            out[i] = e.model_copy(
                update={
                    "raw_value": hit.value,
                    "raw_unit": None,
                    "method": "llm",
                    "was": e.raw_value,
                    "note": MATCHED_NOTE.format(was=e.raw_value),
                }
            )
    return out


def _twin_hints(
    session: Session, position: Position, deps: Deps, answered: set[str]
) -> dict[str, Similar]:
    """Значения двойников этой позиции по наименованию (`twin_values`) для полей, которых нет
    в тексте и ответах, и их производитель для «Общих сведений» (`twin_maker`)."""
    group = [t for t in session.twins if t.code == position.code and t.by == "name"]
    if deps.twins is None or not group:
        return {}
    ids = [t.id for t in group]
    found = deps.twins.chars(ids)
    skip = {fold(e.name) for e in session.extracted} | answered
    hints = twin_values(
        position.characteristics, [c for _, c in found], skip, ids=[i for i, _ in found]
    )
    hints = keep_unsaid(hints, position.characteristics, _texts(session), session.extracted)
    general = {s.name: s for s in position.characteristics if s.group == "general"}
    maker = general.get(GENERAL_NAMES[0])
    if (
        maker is not None
        and fold(maker.name) not in skip
        and (value := twin_maker(deps.twins.makers(ids))) is not None
    ):
        # Производитель двойника, названный в тексте («ДЕРВАЛ БРАЙТ»), — значение из текста.
        texts = _texts(session)
        said = said_same(maker, value.value, texts, session.extracted, position.characteristics)
        hints[maker.name] = value.model_copy(update={"source": said}) if said else value
    return hints


def _similar_hints(
    session: Session, position: Position, deps: Deps, answered: set[str]
) -> dict[str, Similar]:
    """Подсказки похожих СТЕ той же категории для полей, которых нет в тексте и ответах."""
    if deps.exemplars is None or deps.similar is None:
        return {}
    index = deps.exemplars
    hits = index.neighbors(session.name, k=NEIGHBOURS * 8)
    ids = [
        sid
        for i, _ in hits
        if index.codes[i] == position.code and (sid := index.ids[i]) is not None
    ][:NEIGHBOURS]
    skip = {fold(e.name) for e in session.extracted} | answered
    hints = vote(position.characteristics, deps.similar.get(ids), skip)
    return keep_unsaid(hints, position.characteristics, _texts(session), session.extracted)


def _texts(session: Session) -> list[tuple[str, str]]:
    return [("name", session.name), ("description", session.description)]


def _unexplained(name: str, candidates: list[Candidate], deps: Deps) -> list[str]:
    """Значимые слова наименования, которых нет ни в одной позиции-кандидате."""
    if not candidates:
        return []
    known_tokens: set[str] = set()
    for c in candidates:
        known_tokens |= deps.bm25.doc_tokens(c.code)
    words = [
        w for w, stem in significant_words(name) if deps.bm25.correct(stem) not in known_tokens
    ]
    return list(dict.fromkeys(w.lower() for w in words))


async def autofill(inp: AutofillIn, deps: Deps) -> AutofillOut:
    session = await prepare_session(inp, deps)
    await deps.sessions.save(session)
    return await _render_new_names(session, deps, remember=True)


async def _render_new_names(session: Session, deps: Deps, *, remember: bool) -> AutofillOut:
    """Ответ с формулировками новых характеристик, записанными раньше; `remember` — записать новые
    (один раз на описание, а не на каждый ответ): одна и та же характеристика у разных поставщиков
    называется одинаково."""
    redis = deps.sessions.redis
    deps.new_names = await load_new_names(redis)
    out = render(session, deps)
    fresh = [e.name for e in out.extra if e.origin == "new"]
    if remember and fresh:
        await remember_new_names(redis, fresh, deps.new_names)
    return out


def _check_unit_answer(inp: AnswerIn, positions: list[Position]) -> None:
    """Ответ на вопрос о единице измерения (`UNIT_QUESTION`) — единица одной из позиций."""
    if inp.value != OTHER_VALUE and fold(inp.value) not in {fold(unit_label(p)) for p in positions}:
        raise BadAnswer(f"«{inp.value}» нет среди единиц измерения позиций")


def _check_answer(session: Session, inp: AnswerIn, deps: Deps) -> None:
    """Ответ подходит к вопросу: код из кандидатов, характеристика и значение из вариантов."""
    if inp.value == NOTHING_FITS:
        return
    codes = [c.code for c in session.candidates]
    if inp.characteristic == CONFIRM:
        if inp.value not in codes:
            raise BadAnswer(f"позиции {inp.value} нет среди кандидатов")
        return
    if inp.characteristic == UNIT_QUESTION:
        found = [p for code in codes if (p := deps.catalog.get(code)) is not None]
        _check_unit_answer(inp, found)
        return
    specs = [
        spec
        for code in codes
        if (p := deps.catalog.get(code)) is not None
        for spec in p.characteristics
        if fold(spec.name) == fold(inp.characteristic)
    ]
    if not specs:
        raise BadAnswer(f"у подобранных позиций нет характеристики «{inp.characteristic}»")
    if inp.value not in (NONE_VALUE, OTHER_VALUE) and not any(
        fit_value(inp.value, None, spec).status in ("exact", "normalized", "bucket")
        for spec in specs
    ):
        raise BadAnswer(f"«{inp.value}» нет среди вариантов «{inp.characteristic}»")


def _check_ktru_answer(session: Session, inp: AnswerIn, deps: Deps) -> None:
    """Ответ второго этапа: категория определена, позиция и значение — из её КТРУ."""
    final = render(session, deps).ktru.final_code
    category = deps.catalog.get(final) if final else None
    positions = _linked_ktru(category, deps) if category is not None else []
    if not positions:
        raise BadAnswer("у категории нет позиций КТРУ: отвечать не на что")
    if inp.value == NOTHING_FITS:
        return
    if inp.characteristic == CONFIRM:
        if inp.value not in {p.code for p in positions}:
            raise BadAnswer(f"позиции КТРУ {inp.value} нет среди кандидатов")
        return
    if inp.characteristic == UNIT_QUESTION:
        _check_unit_answer(inp, positions)
        return
    specs = [
        spec
        for p in positions
        for spec in p.characteristics
        if fold(spec.name) == fold(inp.characteristic)
    ]
    if not specs:
        raise BadAnswer(f"у позиций КТРУ нет характеристики «{inp.characteristic}»")
    if inp.value not in (NONE_VALUE, OTHER_VALUE) and not any(
        fit_value(inp.value, None, spec).status in ("exact", "normalized", "bucket")
        for spec in specs
    ):
        raise BadAnswer(f"«{inp.value}» нет среди вариантов «{inp.characteristic}»")


async def answer(session_id: str, inp: AnswerIn, deps: Deps) -> AutofillOut:
    session = await deps.sessions.load(session_id)
    if session is None:
        raise SessionNotFound(session_id)
    if inp.stage == "ktru":
        _check_ktru_answer(session, inp, deps)
    else:
        _check_answer(session, inp, deps)
    before = _state(session, deps).final_code if inp.stage != "ktru" else None
    await deps.sessions.add_answer(session_id, inp.characteristic, inp.value, inp.stage)
    fresh = await deps.sessions.load(session_id)
    if fresh is None:
        raise SessionNotFound(session_id)
    if inp.stage != "ktru" and await _answer_from_text(fresh, None, deps):
        # Ответ снял один вопрос первого этапа — следующий модель может снять по тексту сама.
        await deps.sessions.update(
            session_id, extracted=fresh.extracted, known=_text_known(fresh, deps)
        )
    final = _state(fresh, deps).final_code
    if before is not None and final != before and (fresh.ktru_known or fresh.ktru_confirmed):
        # Другой тип или категория: ответы второго этапа были о позициях КТРУ прежнего выбора
        # (одноимённая «Тип питания» у дрели и шуруповёрта) — в новую карточку они не переходят.
        await deps.sessions.drop_ktru_answers(session_id)
        fresh.ktru_known, fresh.ktru_confirmed, fresh.ktru_rejected = {}, None, False
    if final is not None and (fresh.llm_for != final or fresh.similar_for != final):
        # Ответ определил код: модель и похожие СТЕ — о характеристиках этой позиции (модель —
        # пока не ответила: после отказа сервиса следующий шаг спросит снова).
        await _fill_missing(fresh, final, deps)
        await deps.sessions.update(
            session_id,
            extracted=fresh.extracted,
            llm_for=fresh.llm_for,
            degraded=fresh.degraded,
            similar=fresh.similar,
            similar_for=fresh.similar_for,
            twin_hints=fresh.twin_hints,
            suggestions=fresh.suggestions,
            oddities=fresh.oddities,
            sanity=fresh.sanity,
        )
    elif final is not None and inp.stage == "ktru" and await _answer_from_text(fresh, final, deps):
        # Ответ снял один вопрос второго этапа — следующий модель может снять по тексту сама.
        await deps.sessions.update(session_id, extracted=fresh.extracted)
    return await _render_new_names(fresh, deps, remember=False)


def _ktru_parent(code: str, deps: Deps) -> Position | None:
    """Категория, характеристики которой ссылаются на позицию КТРУ (первая по справочнику)."""
    if deps.ktru_parents is None:
        parents: dict[str, str] = {}
        for p in deps.catalog.all():
            for c in p.characteristics:
                for k in c.ktru:
                    parents.setdefault(k, p.code)
        deps.ktru_parents = parents
    parent = deps.ktru_parents.get(code)
    return deps.catalog.get(parent) if parent else None


def _card_positions(
    card: CardIn, deps: Deps
) -> tuple[Position | None, Position | None, list[Issue]]:
    """Категория карточки, позиция КТРУ и замечания к кодам.

    Категория — `category_code`; пуст — `ktru_code`, если он есть в справочнике категорий (старые
    клиенты, демо-справочник из позиций КТРУ, сохранённые заявки). Код КТРУ без категории —
    категория по ссылкам стандартов; не нашлась — проверка по самой позиции КТРУ.
    """
    code = card.category_code or card.ktru_code
    if not code:
        return None, None, [missing_code()]
    field = "category_code" if card.category_code else "ktru_code"
    category = deps.catalog.get(code)
    if category is None:
        own = deps.ktru.get(code) if deps.ktru is not None and not card.category_code else None
        if own is None:
            return None, None, [unknown_code(code, field)]
        parent = _ktru_parent(code, deps)
        return (parent, own, []) if parent is not None else (own, None, [])
    ktru_code = card.ktru_code if card.category_code and card.ktru_code != code else None
    linked = {k for c in category.characteristics for k in c.ktru}
    if not ktru_code:
        chosen_needed = card.category_code and linked and not KTRU_CODE.match(category.code)
        return category, None, [ktru_not_chosen(category)] if chosen_needed else []
    ktru = deps.ktru.get(ktru_code) if deps.ktru is not None else None
    if ktru is None:
        return category, None, [unknown_ktru(ktru_code, loaded=deps.ktru is not None)]
    return category, ktru, [] if ktru_code in linked else [ktru_not_linked(ktru_code, category)]


def check(card: CardIn, deps: Deps) -> CheckOut:
    position, ktru, code_issues = _card_positions(card, deps)
    # КТРУ — эталон: значение из перечня выбранной позиции КТРУ шаблон категории не блокирует.
    issues = detect(card, position, ktru)
    if position is not None:
        issues.extend(
            typo_issues(card.name, card.description or "", position, deps.bm25, deps.exemplars)
        )
    if ktru is not None:
        issues.extend(ktru_value_issues(card, ktru, issues, position))
    issues.extend(photo_issues(card))
    issues[0:0] = layout_issues(card.name, card.description or "", deps.bm25)
    issues[0:0] = code_issues
    if position is not None and (dup := _duplicate(_twins(card.name, deps), position.code)):
        # Модератору и мастеру после правки — то же «такая СТЕ уже есть», что в ответе /autofill.
        issues.insert(0, dup)
    return CheckOut(issues=issues, verdict=verdict(issues))
