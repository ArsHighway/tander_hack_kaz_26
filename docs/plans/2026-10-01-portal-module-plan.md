# Модуль автозаполнения характеристик СТЕ — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** модуль автозаполнения характеристик СТЕ с API `/api/v1`, который Портал поставщиков может встроить, и
образец экранов портала (форма СТЕ, массовая загрузка, заявки модератора, метрики, страница для разработчиков) на
этом API.

**Architecture:** FastAPI-модуль без состояния (сессия сужения в Redis), справочник в памяти процесса из файлов
`CATALOG_PATHS`, эмбеддинги в pgvector, пакет через RabbitMQ и воркер, заявки демо-стенда в Postgres. Фронт —
React SPA, экраны работают только через `src/api/v1.ts`. Логика правил модерации переносится из прототипа
(`frontend/prototype/rules.ts`) в Python с дифференциальной сверкой на корпусе.

**Tech Stack:** Python 3.12, uv, FastAPI, pydantic v2, SQLAlchemy async, Alembic, FastStream/RabbitMQ, Redis,
pgvector, openpyxl, `regex`; React 19, Vite, TypeScript, Tailwind v4.

**Spec:** `docs/plans/2026-10-01-portal-module-design.md`

## Global Constraints

- Ничего не коммитить без команды пользователя (п. 7.5 Положения, решение о коммитах за ним).
- Префиксы: модуль — `/api/v1`, демо-стенд портала — `/api/demo`, служебное — `/api/health*`; OpenAPI —
  `/api/docs`, `/api/openapi.json`.
- Значение принимается, только если есть в перечне или диапазоне позиции справочника; единица — только из канона
  `app/ingest/units.py`. Проверяет код, а не промпт.
- LLM — только через `LLMClient` (`app/llm`), эмбеддинги — через `LLMClient.embed`; облачные API в итоговой
  сборке запрещены, переключение только `.env`.
- Сбой — громкий и по месту: `degraded: ["llm" | "embeddings"]` в ответе, пустое поле с пометкой, а не выдумка.
- Данные (`data/`) в git не идут; организаторские данные во внешние сервисы не отправлять.
- UI — только токены и компоненты `DESIGN.md` / `frontend/src/ui/`; в тексте интерфейса нет «Эталон», нет
  индикатора состояния, нет em-dash; бренд не придумывать.
- Python: ruff (`line-length 100`), аннотации публичных функций, `logging` вместо `print` в библиотечном коде.
- Воркеров и параллельных процессов не больше 4.
- Схема ответов API — pydantic-модели в `backend/app/schemas.py`; фронт берёт типы из OpenAPI, не дублирует руками.

## Review Focus

1. Только наименование, без описания (у 93 % карточек портала описания нет) → `/autofill` заполняет что может из
   наименования и задаёт вопрос, а не падает и не возвращает пустоту молча. Тест — Task 10.
2. Товар вне справочника («Услуги по уборке помещений», «Ноутбук игровой») → кандидатов нет, `final_code: null`,
   `steps` с текстом «категория не найдена», никакого «ближайшего» кода с уверенностью. Тест — Task 7 и Task 10.
3. LLM вернула значение не из перечня, цитату, которой нет в тексте, или невалидный JSON → значение отброшено,
   поле `not_found`, `degraded` не выставлен (это не отказ сервиса). Тест — Task 8.
4. Файл пакета с чужими колонками, пустыми строками, 1 000+ строк или больше лимита → роли колонок угадываются
   (`app/ingest/profile.py`), пустые строки пропущены с пометкой, лимит → 413 с текстом; API не обрабатывает файл
   сам, только ставит в очередь. Тест — Task 11.
5. Истёкшая или чужая сессия сужения → 404 «сессия заполнения не найдена или истекла», фронт показывает «Заполнить
   заново» и не теряет ввод. Тест — Task 10 (API) и Task 15 (экран).

---

### Task 1: Шапка и маршруты без служебных страниц

**Files:**
- Modify: `frontend/src/App.tsx` (таблица маршрутов вместо `StatusPage`/`UiKitPage`)
- Modify: `frontend/src/layout/Layout.tsx` (`PRODUCT_NAME`, группы навигации, без `StatusIndicator`)
- Modify: `frontend/index.html` (`<title>`)
- Modify: `frontend/prototype/App.tsx` (только если сломается от смены пропсов `AppHeader`; прототип вне git)
- Delete: `frontend/src/layout/StatusIndicator.tsx`, `frontend/src/pages/StatusPage.tsx`,
  `frontend/src/api/health.ts` (перед удалением — `grep` по `src/` и `prototype/`, других потребителей быть не должно)
- Create: `frontend/src/routes.ts`, `frontend/src/routes.test.ts`, `frontend/src/pages/Pending.tsx`

**Interfaces:**
- Produces: `type RouteId = 'create' | 'batch' | 'moderation' | 'metrics' | 'developers' | 'ui' | 'not-found'`;
  `resolveRoute(path: string): RouteId`; `NAV_GROUPS: NavGroup[]`, где
  `type NavGroup = { role?: string; items: NavItem[] }`; `AppHeader({ groups, aside, current })`.
- Маршруты: `/` → create, `/batch`, `/moderation`, `/metrics`, `/developers`, `/ui` (вне навигации), прочее →
  not-found с ссылкой на `/`. `/status` → not-found.

- [ ] Тест `routes.test.ts` (node --test, импорт с `.ts`): `resolveRoute('/')==='create'`,
  `resolveRoute('/batch/')==='batch'` (хвостовой слэш), `resolveRoute('/status')==='not-found'`,
  `resolveRoute('/ui')==='ui'`; `NAV_GROUPS` не содержит `/ui` и `/status`.
- [ ] Шапка: слева текст «Автозаполнение характеристик СТЕ»; группы «Поставщик: Создание СТЕ · Массовая
  загрузка», «Модератор: Заявки»; справа «Метрики · API». Подпись роли — `text-subtle`, не бейдж.
- [ ] Экраны, которых ещё нет, рендерят `Pending` (`EmptyState` «Экран появится в следующих задачах»); его
  удаляет Task 19.
- [ ] Проверка: `npm run build`, `npm run lint`, `node --test src/routes.test.ts` — зелёные; в `src/` нет строк
  «Эталон», «StatusIndicator», «/status» (`grep`).

### Task 2: Справочник: модель, загрузчики, каталог в памяти

**Files:**
- Create: `backend/app/catalog/__init__.py`, `backend/app/catalog/model.py`, `backend/app/catalog/loaders.py`,
  `backend/app/catalog/catalog.py`, `backend/tests/test_catalog.py`
- Modify: `backend/app/config.py` (`catalog_paths: list[Path]` из `CATALOG_PATHS`, через запятую),
  `backend/app/resources.py` (поле `catalog: Catalog`, загрузка в `create`), `.env.example`
  (`CATALOG_PATHS=data/catalog/demo.json`), `docker-compose.yml` (api и worker: том `./data:/app/data:ro`,
  `CATALOG_PATHS`)

**Interfaces:**
- Produces (контракт, имена дословно):
  ```python
  class NumRange(BaseModel): min: float | None; max: float | None
  class CharacteristicSpec(BaseModel):
      name: str; kind: Literal["qualitative", "quantitative"]; unit: str | None = None
      values: list[str] = []; range: NumRange | None = None; required: bool = False; level_define: bool = False
  class Position(BaseModel):
      code: str; name: str; okpd2: str; unit: str | None = None
      characteristics: list[CharacteristicSpec]; source: str
  class CatalogError(RuntimeError): ...
  def load_catalog_json(path: Path) -> list[Position]      # формат backend/tests/fixtures/fake/ktru.json
  def load_catalog_csv(path: Path) -> list[Position]       # длинный формат ktru_characteristics.csv (`;`)
  class Catalog:
      version: str
      def __init__(self, positions: list[Position], version: str) -> None
      @classmethod
      def load(cls, paths: list[Path]) -> "Catalog"         # по расширению; пусто или нет файла → CatalogError
      def get(self, code: str) -> Position | None
      def all(self) -> list[Position]
      def __len__(self) -> int
  ```
- Решение: справочник в памяти процесса, а не в Postgres (отступление от спеки §4, записать в ledger). Причина:
  справочник читается на каждом запросе и меняется раз в загрузку; в памяти каждого реплики API и воркера нет
  сетевых походов, масштабирование не страдает. В Postgres остаются эмбеддинги и заявки.

- [ ] Тесты: JSON фикстуры → 6 позиций; `get("17.12.14.129-00000101").characteristics` — 5 спецификаций, у
  «Масса бумаги площадью 1 м2» `values == [">=80 ... <90", ">=90 ... <100", ">=100"]`, `unit == "г/м2"`;
  CSV и JSON фикстуры дают одинаковые коды и имена характеристик (обе делает `scripts/make_fake_dataset.py`);
  несуществующий путь → `CatalogError` с путём в тексте; пустой список путей → `CatalogError`; дубль кода в двух
  файлах → `CatalogError` (молча перезаписывать нельзя).
- [ ] `Resources.create` грузит каталог; API без каталога не стартует, текст ошибки называет `CATALOG_PATHS`.
- [ ] Проверка: `uv run pytest -q`, `uv run ruff check .` [проверено: uv 0.11.4, 01.10.2026; база 51 passed].

### Task 3: Демо-справочник с реальными перечнями

**Files:**
- Create: `data/portal/fetch_catalog.py` (вне git, рядом с `fetch_portal.py`), `data/catalog/demo.json`,
  `data/catalog/stats.md`
- Modify: — (формат — контракт Task 2)

**Interfaces:**
- Consumes: формат `load_catalog_json` (Task 2).
- Produces: `data/catalog/demo.json` — позиции с непустыми перечнями; `stats.md` — сколько карточек запрошено,
  сколько позиций собрано, сколько характеристик с перечнем, источник каждого.

- [ ] Зонд до сбора: `GetSku` карточки 47453345 (как в `docs/research/sku-47453345.json`) отдаёт у характеристик
  `ktruValues`/`spgzValues`. Второй зонд: публичная карточка КТРУ на zakupki.gov.ru для бумаги
  (`17.12.14.129-…`) — открывается ли без входа и есть ли перечни. Итог зондов — в ledger.
- [ ] Сбор: карточки из `data/portal/skus.jsonl` по областям «Бумажная продукция», «Изделия канцелярские, бумага и
  бумажные изделия», «Принадлежности канцелярские», «Химия бытовая», «Товары хозяйственные», «Предметы гигиены»;
  последовательно, пауза 0,8 с, как `fetch_details.py`. Позиция — СПГЗ карточки (код `spgz:<id>`, имя СПГЗ) или
  КТРУ, если есть; характеристики — описания характеристик карточки с перечнем из `ktruValues`, иначе
  `spgzValues`; `required` ← `isObligatory`, `level_define` ← `isLevelDefine`; `source` — `portal-spgz` или
  `portal-ktru`. Позиции с одинаковым кодом из разных карточек сливаются (объединение перечней).
- [ ] Если перечней нет нигде: `demo.json` = позиции фикстуры плюс 10–15 синтетических позиций тех же областей,
  `source: "synthetic"`, и пометка «демо-справочник» уходит в `/metrics` (Task 13). Решение — в ledger.
- [ ] Проверка: `Catalog.load([Path("data/catalog/demo.json")])` грузится, позиций ≥ 30, у ≥ 70 % характеристик
  непустой перечень или диапазон (печатать числа, а не «ок»).

### Task 4: Значения и единицы против перечня позиции

**Files:**
- Create: `backend/app/values.py`, `backend/tests/test_values.py`
- Modify: `backend/app/ingest/units.py` (только если не хватит конверсий; канон не менять)

**Interfaces:**
- Consumes: `CharacteristicSpec` (Task 2), `parse_value`/`to_number` (`app/ingest/characteristics.py`),
  `normalize_unit` (`app/ingest/units.py`).
- Produces:
  ```python
  def fold(text: str) -> str          # casefold, ё→е, латинские двойники → кириллица (A↔А, B↔В, C↔С, ...), пробелы
  def levenshtein(a: str, b: str) -> int
  def nearest_allowed(value: str, allowed: list[str]) -> tuple[str, int] | None
      # как nearestAllowed в rules.ts: d == 0 или (d <= 2 и d <= len(fold(value)) // 3)
  class Bucket(BaseModel): lo: float | None; lo_incl: bool; hi: float | None; hi_incl: bool; text: str
  def parse_bucket(text: str) -> Bucket | None     # ">=80 ... <90", ">=500", "<=5", "80" (точка = lo=hi)
  def convert(value: float, from_unit: str, to_unit: str) -> float | None   # г↔кг↔т, мл↔л, мм↔см↔м, мкм↔мм
  FitStatus = Literal["exact", "normalized", "bucket", "typo", "not_in_list", "unit_mismatch", "unparsed"]
  class Fit(BaseModel): status: FitStatus; value: str | None; unit: str | None; note: str | None
  def fit_value(raw_value: str, raw_unit: str | None, spec: CharacteristicSpec) -> Fit
  ```
- Решение: латинские двойники складываются в `fold`, потому что в перечнях «A4» латиницей, а поставщики пишут
  «А4» кириллицей (фикстура и портал); без этого каждое такое значение уходит в `not_in_list`.

- [ ] Тесты (ожидания выведены из правил выше):
  - «Масса…»: `fit_value("80", "г/м2", spec)` → `bucket`, `value == ">=80 ... <90"`, `unit == "г/м2"`;
  - `fit_value("0,08", "кг/м2", spec)` → `unit_mismatch` (г/м2 и кг/м2 не конвертируем: `convert` знает только
    перечисленные пары) `# иллюстративно — если исполнитель добавит пару кг/м2→г/м2, ожидание bucket`;
  - «Формат» `["A3","A4","A5"]`: `fit_value("А4", None, spec)` (кириллица) → `normalized`, `value == "A4"`;
  - «Интенсивность цвета» `["Интенсив","Медиум","Неон","Пастель"]`: `"Белый"` → `not_in_list`, `note` перечисляет
    допустимые; `"Мeдиум"` (латинская e) → `normalized` к «Медиум»; `"Медиму"` → `typo`, `value == "Медиум"`;
  - «Количество листов в пачке» `["250","500"]`: `"300"` → `not_in_list`; `"500"` → `exact`;
  - «Белизна по CIE» диапазон 146–175, единица `%`: `"160"` → `exact`; `"98"` → `not_in_list`; `"160"` с `"кг"`
    → `unit_mismatch`;
  - `parse_bucket(">=80 ... <90")` → `lo=80, lo_incl=True, hi=90, hi_incl=False`; `parse_bucket("абв") is None`;
  - `nearest_allowed("Медиму", [...]) == ("Медиум", 2)`; `nearest_allowed("Бел", ["Белый"]) is None` (d=2 > 3//3).

### Task 5: Правила модерации — перенос `rules.ts` в Python со сверкой

**Files:**
- Create: `backend/app/checks/__init__.py`, `backend/app/checks/moderation.py`,
  `backend/tests/test_moderation_rules.py`, `backend/tests/fixtures/rules_golden.json`
- Create (вне git): `frontend/prototype/checks/export-golden.ts` — прогоняет TS-правила по корпусу и пишет golden
- Modify: `backend/pyproject.toml`, `backend/uv.lock` через `uv add regex`

**Interfaces:**
- Consumes: `levenshtein`, `nearest_allowed` (Task 4) — `rules.ts` содержит свои копии, в Python берём из `values`.
- Produces (имена и тексты правил дословно из `rules.ts`):
  ```python
  class Char(BaseModel): name: str; value: str; unit: str | None = None
  class Card(BaseModel): name: str; chars: list[Char]; allowed: dict[str, list[str]] | None = None
  RuleId = Literal["min-chars", "unit-in-char-name", "value-repeats-name", "value-unit-in-title", "value-not-allowed"]
  class Finding(BaseModel):
      rule: RuleId; level: Literal["block", "normalize", "advice"]; text: str
      char: str | None = None; suggestion: str | None = None
  RULE_TEXT: dict[RuleId, str]; RULE_PROBLEM: dict[RuleId, str]
  def title_value_units(title: str) -> list[str]
  def check_card(card: Card) -> list[Finding]
  class Verdict(BaseModel): returns: bool; reasons: list[Finding]
  def moderation_verdict(findings: list[Finding]) -> Verdict
  def extract_from_title(card: Card) -> tuple[str, list[Char]]
  class Change(BaseModel): kind: Literal["title-to-char", "unit-from-name"]; text: str
  def auto_fix(card: Card) -> tuple[Card, list[Change]]
  ```
- Решение: модуль `regex`, а не `re`. Причина: в `rules.ts` переменная длина lookbehind
  (`(?<!(?:^|[\s/(])[букв]{1,2}\/)`), `re` её не умеет. `\d` в JS — только ASCII: в Python писать `[0-9]`
  (или флаг `ASCII` там, где это не ломает кириллицу).
- Решение: сверка дифференциальная. Тесты `rules.test.ts` (275 случаев) — не единственная мера: golden
  содержит вывод TS по всем 2 549 наименованиям `prototype/portal-names.json`, 37 карточкам `portal-batch.json` и
  входам из `rules.test.ts`; Python обязан совпасть JSON-равенством на каждом.

- [ ] `export-golden.ts`: для каждого входа пишет `{input, titleValueUnits, checkCard, autoFix, extractFromTitle}`;
  запуск — `node prototype/checks/export-golden.ts` из `frontend/` [вызов платформы не запускался; Node 24
  исполняет .ts с type stripping — см. `node --test prototype/*.test.ts`, проверено: Node 24.14.1, 01.10.2026].
- [ ] Перенести функции; тест-сверка `test_golden_parity` параметризован по записям golden, в отчёте падения —
  вход и обе стороны.
- [ ] Перенести именованные случаи `rules.test.ts` как отдельные тесты (читаемость на код-ревью), не меньше 60
  самых показательных: литры и листы, год против граммов, круглые тысячи, метки «набор/комплект», допуски.
- [ ] Проверка: golden 100 % совпадений (число записей и число совпавших печатаются), `uv run pytest -q`,
  `uv run ruff check .`.

### Task 6: Детектор ошибок карточки

**Files:**
- Create: `backend/app/schemas.py` (общие модели API), `backend/app/checks/detector.py`,
  `backend/tests/test_detector.py`

**Interfaces:**
- Consumes: `Position`, `CharacteristicSpec` (Task 2); `fit_value`, `nearest_allowed` (Task 4); `check_card`,
  `RULE_TEXT` (Task 5).
- Produces (в `app/schemas.py`, имена дословно, их же видит фронт через OpenAPI):
  ```python
  class Span(BaseModel): start: int; end: int; text: str
  class CharIn(BaseModel): name: str; value: str; unit: str | None = None
  class CardIn(BaseModel):
      name: str; description: str = ""; characteristics: list[CharIn] = []; ktru_code: str | None = None
  IssueKind = Literal["typo", "mismatch", "not_in_list", "moderation_rule"]
  class Fix(BaseModel): name: str | None = None; value: str | None = None; unit: str | None = None
  class Issue(BaseModel):
      field: str          # "name" | "description" | "characteristics.<имя>"
      kind: IssueKind; severity: Literal["block", "warn", "info"]
      message: str; why: str; rule: str; fix: Fix | None = None
  class CheckOut(BaseModel): issues: list[Issue]; verdict: Literal["ready", "needs_fix"]
  ```
  ```python
  def detect(card: CardIn, position: Position | None) -> list[Issue]
  def verdict(issues: list[Issue]) -> Literal["ready", "needs_fix"]   # needs_fix, если есть severity == "block"
  ```
- Правила сборки замечаний:
  - находки `check_card` → `moderation_rule`, `rule = RULE_TEXT[id]`, `block` → `block`, иначе `warn`/`info`;
  - характеристика против спецификации позиции: `not_in_list` → `block`, `why` перечисляет допустимые (до 8);
    `typo` → `warn` с `fix.value`; `unit_mismatch` → `mismatch`, `block`; имя характеристики не из позиции, но
    `nearest_allowed` по именам находит → `typo` по имени с `fix.name`;
  - противоречие наименованию: значение качественной характеристики, когда в наименовании стоит другое
    допустимое значение той же характеристики (слово целиком, `fold`) → `mismatch`, `block`;
  - обязательная характеристика позиции не заполнена → `moderation_rule`, `warn`,
    `message` «не заполнена обязательная характеристика «…»».

- [ ] Тест «живая карточка 47453345» на позиции цветной бумаги фикстуры (`…00000102`): характеристика
  «Интенсивность цвета: Белый» → `not_in_list`, `block`, в `why` есть «Пастель»; «Толщина, мкм: 104» →
  `moderation_rule` с `rule == "единица измерения в отдельном поле"` и `fix == {name: "Толщина", unit: "мкм"}`;
  `verdict == "needs_fix"`.
- [ ] Тест без позиции (`position=None`): работают только правила модерации, `not_in_list` не появляется.
- [ ] Тест противоречия: наименование «Бумага цветная А4», «Цветность: Белая» при перечне `["Белая","Цветная"]`
  (синтетическая спецификация в тесте) → `mismatch`.
- [ ] Тест чистой карточки → `issues == []`, `verdict == "ready"`.

### Task 7: Поиск кандидатов в справочнике

**Files:**
- Create: `backend/app/search/__init__.py`, `backend/app/search/text.py`, `backend/app/search/bm25.py`,
  `backend/app/search/vectors.py`, `backend/app/search/candidates.py`, `backend/tests/test_search.py`,
  `backend/migrations/versions/<rev>_catalog_embedding.py`, `backend/app/tools/index_catalog.py`
- Modify: `backend/pyproject.toml`/`uv.lock` (`uv add snowballstemmer` — стемминг без словарей; замена на
  pymorphy3 допустима с записью в ledger)

**Interfaces:**
- Consumes: `Catalog`, `Position` (Task 2); `Glossary.expand` (`app/glossary`); `LLMClient.embed`.
- Produces:
  ```python
  def tokens(text: str) -> list[str]                 # fold, слова и коды вида «А4», стемы
  class BM25Index:
      def __init__(self, docs: dict[str, str], k1: float = 1.5, b: float = 0.75) -> None
      def search(self, query: str, k: int = 20) -> list[tuple[str, float]]
  def rrf(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]
  class EmbeddingIndex:                              # таблица catalog_embedding(code, model, catalog_version, vec)
      async def search(self, vector: list[float], k: int = 20) -> list[tuple[str, float]]
  class Candidate(BaseModel): code: str; name: str; score: float; why: list[str]
  async def find_candidates(text: str, catalog: Catalog, bm25: BM25Index, vectors: EmbeddingIndex | None,
                            embed: Callable[[str], Awaitable[list[float]]] | None, k: int = 20
                            ) -> tuple[list[Candidate], list[Literal["embeddings"]]]
  ```
  Документ позиции для BM25: наименование + ОКПД2 + имена характеристик + значения перечней (через пробел).
- Решение: порог «вне справочника» — нормированная оценка BM25 лучшего кандидата ниже `MIN_SCORE` при пустом
  векторном списке → кандидатов нет. `MIN_SCORE` подбирается в Task 13 на золотом наборе
  (`# иллюстративно: стартовое значение 0.2`), хранится в `config.py`.
- Решение: модель эмбеддингов и версия каталога пишутся в строку индекса; при несовпадении с текущими индекс не
  используется и отдаётся `degraded: ["embeddings"]` (урок Перми: векторы от разных моделей).

- [ ] Тесты: `rrf([["a","b"],["b","c"]])[0][0] == "b"`; BM25 на фикстуре: «бумага офисная белая А4 80 г/м2» →
  первый `…00000101`; «бумага цветная неон» → первый `…00000102`; «перчатки нитриловые M» → первый
  `22.19.60.119-00000301`; «услуги по уборке помещений» → `[]` при пороге из конфига; `embed`, бросающий
  `LLMError`, → кандидаты только BM25 и `degraded == ["embeddings"]`.
- [ ] `index_catalog`: строит эмбеддинги всех позиций пачками по 32, пишет в таблицу; без доступной модели —
  выход с кодом 1 и текстом, а не пустая таблица.

### Task 8: Извлечение характеристик из текста

**Files:**
- Create: `backend/app/extract/__init__.py`, `backend/app/extract/rules.py`, `backend/app/extract/llm_fill.py`,
  `backend/tests/test_extract.py`, `backend/tests/fakes.py` (FakeLLM для этой и следующих задач)

**Interfaces:**
- Consumes: `CharacteristicSpec` (Task 2), `fold`, `fit_value` (Task 4), `Span` (Task 6), `Glossary`,
  `LLMClient.chat(messages, schema=)`, `LLMOutputError`.
- Produces:
  ```python
  class Extracted(BaseModel):
      name: str                    # имя спецификации позиции
      raw_value: str; raw_unit: str | None; source: Span; method: Literal["rule", "llm"]
  def extract_rules(text: str, specs: list[CharacteristicSpec]) -> list[Extracted]
  async def extract_llm(text: str, specs: list[CharacteristicSpec], llm: LLMClient, glossary: Glossary
                        ) -> list[Extracted]          # LLMError пробрасывается — решает конвейер
  class FakeLLM:                                      # tests/fakes.py
      def __init__(self, replies: list[BaseModel | Exception]) -> None   # chat(...) отдаёт по очереди
  ```
- Правила извлечения (намерение; механизм — подсказка):
  - сегменты описания по строкам и `;`; в сегменте «метка[, ед.] значение [ед.]» метка сопоставляется со
    спецификацией по пересечению стемов (`tokens`, Task 7) ≥ 0,5 или по синониму из словаря аббревиатур;
  - качественное значение ищется по всему тексту как слово целиком из перечня спецификации (через `fold`);
  - число с единицей без метки отдаётся спецификации с той же размерностью единицы, если такая одна;
  - `source` — точные позиции в исходном тексте (`text[start:end] == source.text`).
- LLM: в промпт — имена спецификаций с перечнями и `Glossary.prompt_block(text)`; схема
  `LLMItems(items: list[LLMItem(name, value, unit | None, quote)])`; элемент отбрасывается, если `name` не из
  спецификаций, `quote` не подстрока текста или `fit_value` даёт `not_in_list`/`unit_mismatch`/`unparsed`.
  Один повтор при `LLMOutputError`, затем пустой список.

- [ ] Тест на описании карточки 47453345 (строки из `docs/research/zakupki-portal.md` §2) и спецификациях белой
  бумаги фикстуры: извлечены «Масса бумаги площадью 1 м2» (`raw_value "80"`, `source.text == "80"`), «Формат»
  (`"А4"`), «Количество листов в пачке» (`"500"`); для каждого `text[s.start:s.end] == s.text`.
- [ ] Тест «Пакеты для мусора 60 л, 20 шт, ПНД» на спецификациях пакетов: «Объем» = `60`, «Материал» = `ПНД`
  `# иллюстративно: имена спецификаций сверить с фикстурой`.
- [ ] Тест LLM: FakeLLM отдаёт элементы — один верный, один с цитатой не из текста, один со значением не из
  перечня, один с чужим именем → остаётся один, `method == "llm"`.
- [ ] Тест LLM: два `LLMOutputError` подряд → `[]`, исключение наружу не выходит; `LLMError` (сеть) → пробрасывается.

### Task 9: Сужение до кода и сессия

**Files:**
- Create: `backend/app/narrowing.py`, `backend/app/session.py`, `backend/tests/test_narrowing.py`

**Interfaces:**
- Consumes: `Position` (Task 2), `fit_value` (Task 4), `Candidate` (Task 7).
- Produces:
  ```python
  class Option(BaseModel): value: str; remaining: int; cuts: int
  class Question(BaseModel): characteristic: str; options: list[Option]
  class KtruState(BaseModel):
      candidates: list[Candidate]; remaining: int; final_code: str | None
      question: Question | None; steps: list[str]
  def compatible(position: Position, known: dict[str, str]) -> bool
      # known: имя характеристики → подогнанное значение; нет спецификации у позиции → совместима
  def narrow(positions: list[Position], known: dict[str, str]) -> list[Position]
  def best_question(positions: list[Position], known: dict[str, str]) -> Question | None
      # максимум энтропии разбиения по значению; ничьи — обязательная раньше, затем по имени
  def build_state(candidates: list[Candidate], positions: list[Position], known: dict[str, str]) -> KtruState
  class Session(BaseModel):
      id: str; name: str; description: str; candidate_codes: list[str]; known: dict[str, str]
  class SessionStore:
      def __init__(self, redis: Redis, ttl_s: int) -> None
      async def save(self, s: Session) -> None; async def load(self, session_id: str) -> Session | None
  ```
- Тексты `steps` (контракт, фронт показывает дословно): нет кандидатов → «Категория не найдена в справочнике:
  уточните наименование или опишите товар подробнее»; кандидаты кончились после ответов → «Нет позиции с такими
  значениями: проверьте «<характеристика>» = «<значение>»»; осталось больше одной позиции и различающих
  характеристик нет → «Выберите позицию вручную: <код> — <наименование>; …».

- [ ] Тесты (свойства, а не числа, где числа зависят от фикстуры): после выбора варианта `o` вопроса
  `remaining == o.remaining`; `cuts == before - o.remaining`; при `remaining == 1` → `final_code` = код,
  `question is None`; бумага белая и цветная, `known={}` → вопрос по характеристике, которая есть только у одной
  из них или различается перечнем; `known={"Цветность": "Цветная"}` → `final_code == "17.12.14.129-00000102"`.
- [ ] Тест тупика: `known={"Формат": "A5", "Цветность": "Цветная"}` → `remaining == 0`, `steps[0]` начинается с
  «Нет позиции с такими значениями».
- [ ] `SessionStore` — тест на fakeredis не тянем: проверка в Task 10 через API с подменой хранилища на словарь.

### Task 10: Конвейер `/autofill` и API v1

**Files:**
- Create: `backend/app/pipeline.py`, `backend/app/api/v1.py`, `backend/tests/test_api_v1.py`
- Modify: `backend/app/main.py` (`docs_url="/api/docs"`, `openapi_url="/api/openapi.json"`, роутер v1),
  `backend/app/resources.py` (BM25-индекс, `SessionStore`, `Glossary`), `backend/app/schemas.py`

**Interfaces:**
- Consumes: всё из Task 2–9.
- Produces (`app/schemas.py`, дословно):
  ```python
  class AutofillIn(BaseModel): name: str = Field(min_length=1); description: str = ""
  class CharacteristicOut(BaseModel):
      name: str; value: str | None; unit: str | None; source: Span | None
      normalized_from: str | None = None
      status: Literal["filled", "normalized", "not_found", "conflict"]
      required: bool; method: Literal["rule", "llm"] | None = None; note: str | None = None
  class AutofillOut(BaseModel):
      session_id: str; ktru: KtruState; characteristics: list[CharacteristicOut]; issues: list[Issue]
      degraded: list[Literal["llm", "embeddings"]]
  class AnswerIn(BaseModel): characteristic: str; value: str
  ```
  ```python
  class Deps:  # собирается из Resources; в тестах — из фикстуры и FakeLLM
      catalog: Catalog; bm25: BM25Index; vectors: EmbeddingIndex | None; llm: LLMClient | FakeLLM
      glossary: Glossary; sessions: SessionStore
  async def autofill(inp: AutofillIn, deps: Deps) -> AutofillOut
  async def answer(session_id: str, inp: AnswerIn, deps: Deps) -> AutofillOut   # KeyError → 404 в роутере
  def get_deps(request: Request) -> Deps
  ```
- Эндпоинты: `POST /api/v1/autofill`, `POST /api/v1/autofill/{session_id}/answer`, `POST /api/v1/check`
  (`CardIn` → `CheckOut`), `GET /api/v1/ktru/{code}` (`Position`, 404 «позиция не найдена»).
- Порядок конвейера: `Glossary.expand` → `find_candidates` → спецификации лучших кандидатов → `extract_rules` на
  «наименование + описание» → `extract_llm` для оставшихся спецификаций → `fit_value` → `known` →
  `build_state` → характеристики позиции (итоговой или лучшей) → `detect` → сессия.

- [ ] Тест сценария бумаги (фикстура, FakeLLM без ответов не вызывается для уже найденного): `autofill` →
  `ktru.remaining == 2`, есть `question`; `answer(Цветность=…)` → `final_code` и характеристики итоговой позиции.
- [ ] Review Focus 1: только наименование «Бумага офисная А4 500 листов» → 200, `characteristics` не пустой
  (формат, листы), `question` или `final_code` есть.
- [ ] Review Focus 2: «Услуги по уборке помещений» → `candidates == []`, `final_code is None`, `steps` с
  «Категория не найдена».
- [ ] LLM недоступна (FakeLLM бросает `LLMError`) → 200, `"llm" in degraded`, правила отработали.
- [ ] Review Focus 5: неизвестный `session_id` → 404, `detail == "сессия заполнения не найдена или истекла"`.
- [ ] `/check` на карточке из Task 6 → `verdict == "needs_fix"`; `/ktru/{code}` → 200 и 404.
- [ ] `GET /api/openapi.json` содержит пути `/api/v1/autofill`, `/api/v1/check` (контракт для фронта).

### Task 11: Массовая загрузка через очередь

**Files:**
- Create: `backend/app/batch.py`, `backend/tests/test_batch.py`
- Modify: `backend/app/api/v1.py` (три эндпоинта), `backend/app/jobs.py` (`BATCH_QUEUE`, в `QUEUES`),
  `backend/app/worker.py` (подписчик `handle_batch`, сборка `Deps` в воркере), `backend/app/schemas.py`

**Interfaces:**
- Consumes: `read_tables` (`app/ingest/readers.py`), `profile_table`/`column_for` (`app/ingest/profile.py`),
  `parse_characteristics` (`app/ingest/characteristics.py`), `autofill`, `detect` (Task 10, 6).
- Produces:
  ```python
  class BatchRow(BaseModel): row: int; name: str; description: str = ""; characteristics: list[CharIn] = []
  class BatchRowResult(BaseModel):
      row: int; name: str; status: Literal["ready", "needs_answer", "needs_fix", "skipped", "failed"]
      ktru_code: str | None; filled: int; issues: list[Issue]; error: str | None = None
  class BatchState(BaseModel):
      job_id: str; status: Literal["queued", "running", "done", "failed"]; filename: str
      total: int; processed: int; rows: list[BatchRowResult]
  def parse_upload(filename: str, data: bytes) -> list[BatchRow]   # пустые строки → skipped, не теряются
  async def process_row(row: BatchRow, deps: Deps) -> BatchRowResult  # исключение строки → failed + error
  def result_workbook(state: BatchState, rows: list[BatchRow]) -> bytes
  ```
- Эндпоинты: `POST /api/v1/batch` (multipart `file`; > 5 МБ или > 5 000 строк → 413 с текстом лимита; 202 и
  `{job_id, status_url}`), `GET /api/v1/batch/{job_id}` (`BatchState`, 404), `GET
  /api/v1/batch/{job_id}/result.xlsx` (409, пока не `done`). Строки и состояние — Redis `batch:{job_id}`, TTL
  `job_ttl_s`.

- [ ] Тест `parse_upload` на `tests/fixtures/fake/ste.xlsx`, `ste_cp1251.csv`, `ste_headerless.csv` → одно и то
  же число строк и одинаковые имена (роли колонок по содержимому).
- [ ] Review Focus 4: таблица с пустой строкой в середине → строка `skipped`; 5 001 строка → 413.
- [ ] Тест: `process_row`, у которого `autofill` бросает, → `failed`, `error` с текстом; соседние строки `ready`.
- [ ] Тест `result_workbook`: заголовок исходных колонок + «Код КТРУ», «Заполнено», «Замечания», «Статус»;
  строк столько же, сколько в загрузке.

### Task 12: Заявки на модерацию (демо-стенд портала)

**Files:**
- Create: `backend/migrations/versions/<rev>_submission.py`, `backend/app/demo/__init__.py`,
  `backend/app/demo/submissions.py`, `backend/app/api/demo.py`, `backend/tests/test_submissions.py`
- Modify: `backend/app/main.py` (роутер `/api/demo`)

**Interfaces:**
- Consumes: `CardIn`, `CheckOut`, `CharacteristicOut` (Task 6, 10), `detect`/`verdict`.
- Produces:
  ```python
  class SubmissionIn(BaseModel): card: CardIn; accepted: list[CharacteristicOut] = []
  class Submission(BaseModel):
      id: int; created_at: datetime; card: CardIn; accepted: list[CharacteristicOut]
      status: Literal["pending", "accepted", "returned"]; decision_note: str | None = None
  class SubmissionView(Submission): check: CheckOut
  class DecisionIn(BaseModel): decision: Literal["accept", "return"]; note: str | None = None
  class SubmissionRepo(Protocol):
      async def add(self, s: SubmissionIn) -> Submission
      async def list(self) -> list[Submission]
      async def get(self, id: int) -> Submission | None
      async def decide(self, id: int, d: DecisionIn) -> Submission | None
  class PgSubmissionRepo: ...      # SQLAlchemy Core, таблица submission(id, created_at, card jsonb, accepted jsonb,
                                   # status, decision_note)
  class MemorySubmissionRepo: ...  # для тестов
  ```
- Эндпоинты: `POST /api/demo/submissions` (201), `GET /api/demo/submissions`, `GET /api/demo/submissions/{id}`
  (с `check`, посчитанным на лету), `POST /api/demo/submissions/{id}/decision`; повторное решение по заявке не
  в `pending` → 409.

- [ ] Тесты API на `MemorySubmissionRepo`: создание → в списке `pending`; `GET` содержит `check.issues`;
  `return` с заметкой → `returned`, `decision_note`; повторное решение → 409; чужой id → 404.
- [ ] Миграция проверяется в Task 21 (`migrate` в compose проходит, таблица есть).

### Task 13: Оценка качества и `/metrics`

**Files:**
- Create: `backend/app/evaluation/__init__.py`, `backend/app/evaluation/golden.py`,
  `backend/app/evaluation/metrics.py`, `backend/app/tools/evaluate.py`, `backend/tests/test_evaluation.py`
- Modify: `backend/app/api/v1.py` (`GET /api/v1/metrics`), `backend/app/config.py` (`metrics_path`,
  `MIN_SCORE` калибруется здесь), `.env.example`

**Interfaces:**
- Consumes: `Catalog` (Task 2), `autofill`/`answer` (Task 10), `detect` (Task 6).
- Produces:
  ```python
  class GoldenCase(BaseModel):
      id: str; group: Literal["clean", "typo", "out_of_catalog", "corrupted"]; name: str; description: str
      expected_code: str | None; expected: dict[str, str]; corruption: str | None = None
  def build_golden(catalog: Catalog, seed: int = 42, per_position: int = 3) -> list[GoldenCase]
  def accuracy_at_k(ranked: list[list[str]], truth: list[str | None], k: int) -> float
  def precision_recall(pred: set[tuple[str, str]], truth: set[tuple[str, str]]) -> tuple[float, float]
  class MetricRow(BaseModel): key: str; title: str; value: float; n: int; how: str
  class MetricsOut(BaseModel):
      created_at: datetime; catalog_version: str; catalog_source: str; model: str; rows: list[MetricRow]
  ```
- Метрики (каждая отдельной строкой `MetricRow`): Accuracy@1 и @5 кода; среднее число вопросов до кода (ответы
  берутся из `expected`); precision и recall заполнения; precision, recall, F1 детектора по виду порчи (опечатка,
  чужая единица, значение не из перечня, противоречие наименованию); доля честных отказов на `out_of_catalog`;
  p50 и p95 задержки `/autofill`. `catalog_source` — `source` позиций (если `synthetic`, страница пишет
  «демо-справочник»).
- `evaluate` пишет `data/metrics/latest.json`; `GET /api/v1/metrics` → `MetricsOut` или 404 «оценка ещё не
  запускалась».

- [ ] Тесты: `build_golden` с одним seed дважды → равные списки; группы присутствуют все четыре;
  `accuracy_at_k([["a","b"],["c","d"]], ["b","x"], 1) == 0.0`, `k=2 → 0.5`;
  `precision_recall({("a","1"),("b","2")}, {("a","1")}) == (0.5, 1.0)`.
- [ ] Прогон `uv run python -m app.tools.evaluate` на демо-справочнике; калибровка `MIN_SCORE` по доле честных
  отказов и Accuracy@5 записывается в ledger числами.

### Task 14: Клиент API на фронте из OpenAPI

**Files:**
- Create: `frontend/src/api/schema.d.ts` (генерируется), `frontend/src/api/http.ts`, `frontend/src/api/v1.ts`
- Modify: `frontend/package.json` (`openapi-typescript` в devDependencies, скрипт `gen:api`)

**Interfaces:**
- Consumes: `/api/openapi.json` (Task 10–13).
- Produces:
  ```ts
  export class ApiError extends Error { status: number; detail: string }
  export type AutofillOut = components['schemas']['AutofillOut']   // и остальные модели из schemas.py
  export const api = {
    autofill(name: string, description: string): Promise<AutofillOut>,
    answer(sessionId: string, characteristic: string, value: string): Promise<AutofillOut>,
    check(card: CardIn): Promise<CheckOut>,
    ktru(code: string): Promise<Position>,
    uploadBatch(file: File): Promise<{ job_id: string; status_url: string }>,
    batch(jobId: string): Promise<BatchState>,
    batchResultUrl(jobId: string): string,
    metrics(): Promise<MetricsOut | null>,               // 404 → null
    submit(s: SubmissionIn): Promise<Submission>,
    submissions(): Promise<Submission[]>,
    submission(id: number): Promise<SubmissionView>,
    decide(id: number, d: DecisionIn): Promise<Submission>,
  }
  ```
- Решение: типы генерируются, а не пишутся руками (единственный источник — pydantic). `schema.d.ts` в репо, чтобы
  сборка в Docker не ходила к API; `gen:api` запускается после правок `schemas.py`.

- [ ] `npm run gen:api` при поднятом API → `schema.d.ts`; `npm run build` зелёный.
- [ ] `http.ts`: не-2xx → `ApiError` с `detail` из тела FastAPI (`{"detail": "..."}`), сетевой сбой →
  `ApiError(0, "сервис недоступен")`.

### Task 15: Экран «Создание СТЕ»

**Files:**
- Create: `frontend/src/pages/create/CreatePage.tsx`, `DescriptionField.tsx`, `KtruBlock.tsx`,
  `CharacteristicsBlock.tsx`, `IssueNote.tsx`, `examples.ts`, `createModel.ts`, `createModel.test.ts`
- Modify: `frontend/src/App.tsx` (маршрут `create` вместо `Pending`)
- Источник вёрстки: `frontend/prototype/Workspace.tsx` (вне git) — переносится визуальный язык, данные — из API.

**Interfaces:**
- Consumes: `api` (Task 14), компоненты `src/ui/`.
- Produces: `createModel.ts` — чистые функции состояния формы:
  `applyFix(chars, issue)`, `acceptAll(chars)`, `toCardIn(name, description, chars)`, `highlightSpans(text,
  chars) → Segment[]` (тест на пересекающиеся и соседние фрагменты).

- [ ] «Заполнить по описанию» → строки характеристик со статусом; фрагмент-источник подсвечен маркером в тексте
  описания; исправленное зачёркнуто красным карандашом (`DESIGN.md`).
- [ ] Блок «Категория КТРУ»: «осталось N», вопрос с вариантами и «отсечёт K»; ответ → новое состояние; итоговый
  код с наименованием; `steps` выводятся текстом.
- [ ] По каждой строке «Принять», «Изменить», «Убрать»; обязательная пустая — с пометкой.
- [ ] Замечание у поля: `message`, `why`, `rule`; кнопка «Исправить», если есть `fix`.
- [ ] «Проверить перед отправкой» → `/check`, итог «готово к отправке» или список блокирующих.
- [ ] «Отправить на модерацию» → заявка создана, ссылка «Открыть в заявках».
- [ ] `degraded` → строка в блоке «Характеристики» («часть характеристик не извлечена автоматически, заполните
  вручную»), без общей плашки в шапке.
- [ ] Примеры над формой: «Чистый товар», «С опечаткой», «Неоднозначный КТРУ», «Недопустимое значение» —
  заполняют поля, не отправляют сами.
- [ ] Review Focus 5: 404 сессии → заметка «Сессия истекла» и кнопка «Заполнить заново», ввод не теряется.
- [ ] Ошибка API → `Notice` с `detail`, ввод не теряется.
- [ ] Проверка: `node --test src/pages/create/createModel.test.ts`, `npm run build`, `npm run lint`.

### Task 16: Экран «Массовая загрузка»

**Files:**
- Create: `frontend/src/pages/batch/BatchPage.tsx`, `batchView.ts`, `batchView.test.ts`
- Modify: `frontend/src/App.tsx`
- Источник: `frontend/prototype/Batch.tsx`, `batchModel.ts`

**Interfaces:**
- Consumes: `api.uploadBatch`, `api.batch`, `api.batchResultUrl`.
- Produces: `batchView.ts` — `summarize(state) → {ready, needsAnswer, needsFix, skipped, failed}`, тест на
  `BatchState` из пяти строк разных статусов.

- [ ] Загрузка файла (xlsx, csv), ошибка 413 → текст лимита.
- [ ] Прогресс «обработано N из M», опрос раз в секунду до `done`/`failed`, остановка опроса при уходе со
  страницы.
- [ ] Таблица строк: наименование, код, заполнено, замечания, статус словом; «Открыть в форме» переносит
  наименование и описание в `/` (через `sessionStorage`, ключ `create:prefill`).
- [ ] «Скачать файл с исправлениями» активна только при `done`.

### Task 17: Экран «Заявки»

**Files:**
- Create: `frontend/src/pages/moderation/ModerationPage.tsx`
- Modify: `frontend/src/App.tsx`
- Источник: `frontend/prototype/Moderation.tsx`

**Interfaces:**
- Consumes: `api.submissions`, `api.submission`, `api.decide`.

- [ ] Очередь слева (новые сверху, статус словом), заявка справа в режиме вычитки: наименование, описание,
  характеристики, замечания `check` со ссылкой на правило.
- [ ] «Принять» и «Вернуть с замечаниями»; текст возврата предзаполнен из блокирующих замечаний, редактируется.
- [ ] Пустая очередь → `EmptyState` со ссылкой «Создать СТЕ».
- [ ] 409 при повторном решении → `Notice`.

### Task 18: Экран «Метрики»

**Files:**
- Create: `frontend/src/pages/metrics/MetricsPage.tsx`
- Modify: `frontend/src/App.tsx`
- Источник: `frontend/prototype/Quality.tsx`

- [ ] Таблица `MetricRow`: название, значение, n, «как считали»; дата прогона, версия и источник справочника,
  модель.
- [ ] `catalog_source == "synthetic"` → строка «демо-справочник: числа показывают работу конвейера, а не качество
  на данных организаторов».
- [ ] 404 → `EmptyState` «Оценка ещё не запускалась» с командой запуска.
- [ ] Ни одного числа в тексте страницы, которого нет в ответе API (сверка кадра с `latest.json` в Task 21).

### Task 19: Страница «API» для разработчиков портала

**Files:**
- Create: `frontend/src/pages/developers/DevelopersPage.tsx`, `frontend/src/pages/developers/examples.ts`,
  `frontend/src/pages/developers/FlowDiagram.tsx`
- Modify: `frontend/src/App.tsx` (последний маршрут), Delete: `frontend/src/pages/Pending.tsx`
- Источник: `frontend/prototype/How.tsx`

- [ ] Схема потока инлайн-SVG: «Форма портала → POST /autofill → предложения → решение поставщика → поля портала
  → /check → модерация»; читается в светлой и тёмной теме (токены).
- [ ] Таблица методов (из спеки §3) с примером запроса и ответа; примеры — реальные ответы, снятые с API на
  фикстуре (`examples.ts`), а не придуманные.
- [ ] Ссылки: `/api/docs`, `/api/openapi.json`, BPMN (`/bpmn/module.svg`, Task 20).
- [ ] `Pending.tsx` удалён, все маршруты ведут на настоящие экраны.

### Task 20: BPMN-схема процесса

**Files:**
- Create: `backend/scripts/bpmn.py`, `docs/bpmn/module.bpmn`, `docs/bpmn/module.svg`,
  `frontend/public/bpmn/module.svg`, `backend/tests/test_bpmn.py`

**Interfaces:**
- Produces: `build_bpmn() -> str` (BPMN 2.0 XML с DI), дорожки «Поставщик», «Модуль автозаполнения», «Модератор
  портала»; шаги: ввод описания → автозаполнение → вопрос сужения (шлюз «код однозначен?») → решение по полям →
  проверка → отправка → проверка модератором (шлюз «замечания?») → публикация или возврат.

- [ ] Тест: XML парсится; у каждого `sequenceFlow` существуют `sourceRef`/`targetRef`; у каждого элемента
  процесса есть фигура DI; две дорожки не пересекаются по y.
- [ ] SVG рендерится через bpmn-js в браузере (playwright) [вызов платформы не запускался: нужен интернет для
  CDN bpmn-js во время генерации, в рантайме не нужен]; копия в `frontend/public/bpmn/`.

### Task 21: Сборка целиком, сценарии, кадры, документация

**Files:**
- Create: `frontend/e2e/flows.js` (сценарии playwright), `docs/demo-scenarios.md`
- Modify: `CLAUDE.md` (карта кода, команды), `README.md`, `.env.example`, `docker-compose.yml` (если Task 2 не
  закрыла), память `local-prototype-outside-git` (прототип заменён экранами в `src/`)

- [ ] `docker compose up -d --build` [проверено: Docker 29.3.1, 01.10.2026] → `migrate` выходит с 0,
  `api` healthy, `GET /api/health/ready` без `degraded` по postgres/redis/rabbitmq.
- [ ] В контейнере api: `python -m app.tools.index_catalog` (если есть модель эмбеддингов), `python -m
  app.tools.evaluate` → `/metrics` отдаёт строки.
- [ ] `flows.js` на 8080: четыре примера формы (чистый, опечатка, неоднозначный, недопустимое значение) до
  итогового кода или шагов; отправка → заявка в очереди → возврат с замечаниями; пакет из `ste.xlsx` до `done` и
  скачивание; метрики и API открываются; ноль ошибок консоли.
- [ ] Кадры 390×844, 1366×768, 1440×720 каждого экрана, просмотр глазами (Read png), очевидное исправлено.
- [ ] Скан доступности (`accessibility-scan`) по пяти экранам, блокеры исправлены.
- [ ] Итог проверок одной таблицей: `uv run pytest -q` (N passed), `ruff`, `npm run build`, `npm run lint`,
  `node --test`, e2e — с числами из вывода.

---

## Ledger

Формат: `Task <N>: Ruling: <находка> — <что решил и почему>`.

- Task 2: Ruling: справочник в памяти процесса, а не в Postgres (спека §4) — читается на каждом запросе, меняется
  раз в загрузку; эмбеддинги и заявки остаются в Postgres.
