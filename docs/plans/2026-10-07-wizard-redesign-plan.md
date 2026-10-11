# Мастер создания СТЕ: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (Native, рекомендуется для этого плана)
> или superpowers:subagent-driven-development. Шаги — чекбоксы `- [ ]`. Расхождение плана с реальностью — строкой
> в «Журнал решений» внизу: `Task <N>: Ruling: <находка> — <решение и почему>`.

**Goal:** заменить экран «Создание СТЕ» мастером из пяти шагов с живой карточкой, предпросмотром «как на
портале», фото и загрузкой файлов, в новой теме (белый фон, IBM Plex, кобальт).

**Architecture:** бэкенд отдаёт всё, что видит мастер, в ответе `/autofill` (цепочка кодов, похожие СТЕ, путь
категории, коды у вариантов, названия единиц) и через небольшие эндпоинты (картинки, фото, текст из файла).
Логика фронта — в чистых модулях без React (`stepOf`, `path`, `options`, `liveCard`, `portalPreview`,
`ktruFormat`) с тестами `node --test`; компоненты MUI только рисуют их результат.

**Tech Stack:** FastAPI, pydantic 2, SQLAlchemy + Alembic, httpx, pytest; React 19, MUI 9, Tailwind 4, Vite 8,
TypeScript 6, `npm test` (`node --test "src/**/*.test.ts"`).

**Spec:** `docs/plans/2026-10-07-wizard-redesign-design.md` (читать вместе с планом).

**Ветка:** `feat/wizard-redesign` от `main` (68daeed). Документы брейншторма (спека, `docs/research/ux-competitors.md`,
правка `docs/tz/03-requirements.md`, этот план) идут первым коммитом, когда пользователь разрешит коммитить.

## Решения при планировании (дополняют раздел 7 спецификации)

1. **`Option.codes`** — коды позиций, которые останутся после варианта: спецификация требует код КТРУ у
   варианта шага 3, а `Option` знает только `remaining`.
2. **`Candidate.similar_count` и `similar_total`** — подпись «14 из 30 похожих» честна только как счёт соседей;
   `Candidate.similar` — взвешенная доля голоса, а не число.
3. **`CharacteristicOut.unit_name` и `UNIT_NAMES` в `ingest/units.py`** — формат КТРУ требует единицу ОКЕИ
   полным названием, а канон единиц — короткие обозначения; словарь живёт рядом с каноном, фронт получает готовое.
4. **`CharacteristicSpec.group` и `CharacteristicOut.group` вводятся в Task 4**, а содержимое «Общих сведений» —
   в Task 12: типы фронта готовы раньше, шаги 4–5 и живая карточка не переписываются.
5. **`SimilarSku.id: int`** (как в спецификации): примеры берутся только из строк `exemplars.jsonl` с `id`;
   старый файл без `id` даёт пустые примеры, а не `id: null`.
6. **Повторный ответ на шаге 2 начинает новую сессию** (тот же текст, `/autofill`, затем ответ о категории):
   ответы шага 3 лежат в хэше сессии и относятся к позициям прежней категории. Повторный ответ на шаге 3 —
   обычный `/answer` со `stage="ktru"`, поле хэша перезаписывается.
7. **«Общие сведения» не участвуют в поиске, сужении и счёте характеристик модерации**: на портале
   производитель, модель и страна — поля карточки, а не характеристики.
8. **Текст ожидания `/answer`** — «проверяем ответ» (спецификация называет только тексты для `/autofill`).

## Global Constraints

- Цвета: `ink #14161b`, `ink2 #3b414c`, `mute #5f6773`, `line #e3e6eb`, `acc #2547d0`, `acc-soft #eef1fd`,
  `mark #fff0b3`, `err #c4321f`, фон `#ffffff`. Жёлтый `mark` — только «взято из вашего текста».
- Шрифты: IBM Plex Sans 400/500/600 и IBM Plex Mono 400/500/600 из `@fontsource` (с кириллицей); Open Sans —
  только внутри предпросмотра. Google Fonts нельзя: демо без интернета.
- Радиусы 6 (кнопки, поля), 8 (списки, полоса шагов, панели), 10 (внешние блоки). Теней нет, кроме нижней
  полосы и мобильной шторки. Отступы по сетке 4/8/12/16/24. Разделители — 1 px `line`.
- Запреты (antislop): заглавные подписи с разрядкой, пилюли-бейджи без функции, карточка в карточке, тени на
  всём, эмодзи, длинное тире в текстах интерфейса, кнопки «Далее» без глагола, выдуманные цифры, мёртвые кнопки и
  ссылки (элемент появляется в той задаче, где начинает работать). Исключение: предпросмотр копирует портал
  (Open Sans, вкладки капсом, его синий) и только там.
- Тексты интерфейса русские; коды КПГЗ, ОКПД2, КТРУ, СПГЗ — моноширинным. Тексты в кавычках «» в этом плане —
  контракт, пишутся дословно.
- Мастер открывается на первом шаге, где нужен человек; код КТРУ не выдумывается.
- «Готово N из M» считает только обязательные (`required`) характеристики; «Общие сведения» в M не входят.
- Фото поставщика: JPEG, PNG, WebP, до 5 МБ, хранится на сервере.
- Контракт API меняется в той же задаче: `cd backend && uv run python -m app.tools.export_openapi`, затем
  `cd frontend && npm run gen:api`   [проверено: 2026-10-06]. Тест `test_openapi_snapshot_is_current` ловит забытое.
- `npm run build` (`tsc -b`) проверяет и тесты: новые обязательные поля моделей дописываются в объекты-литералы
  тестов (`formModel.test.ts` и другие `*.test.ts`), иначе сборка красная.
- Проверки как в CI: бэкенд `cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy &&
  uv run pytest -q`; фронт `cd frontend && npm test && npm run lint && npm run build`
  [проверено: CI 37522088218 на 68daeed, 2026-10-06].
- Перед вёрсткой (Task 1, 5, 6, 7, 9) — скиллы `taste` и `antislop-ui` (режим «во время работы»); спорят с
  `DESIGN.md` — побеждает `DESIGN.md`, расхождение назвать пользователю.
- После пересборки файлов в `data/catalog/` перезапустить api и worker, чтобы они перечитали справочник
  [вызов платформы не запускался: перезапуск сервисов compose].
- Коммиты — conventional, по задаче, только если пользователь разрешил коммитить.

## Review Focus

1. Наименование на 200 символов и длинное имя категории: в полосе шагов и живой карточке текст переносится,
   коды не обрезаются, горизонтальной прокрутки нет на 390 и 1366 px (Task 5 и Task 7, скриншоты с фикстурой).
2. Похожих СТЕ нет (в `exemplars.jsonl` нет `id` или у категории нет соседей): у варианта нет строки «Похожие»
   и миниатюр, подписи «N из M похожих» нет, вариант выбирается как обычно (Task 2 и Task 5, тесты).
3. Портал недоступен или отвечает дольше 5 с: прокси картинок отдаёт 404 не позже таймаута и не запоминает
   «нет фото», превью показывает заглушку без битой иконки (Task 8 и Task 9, тесты).
4. Сессия истекла посреди мастера (`/answer` → 404): мастер показывает «Заполнить заново», текст поставщика
   сохранён (Task 5, тест `answerError`).
5. Цифры `1`…`6` и `Enter` при фокусе в поле ввода (например, «Другое значение») не выбирают вариант
   (Task 5, тест `shouldHandleKey`).

---

### Task 1: Тема, шрифты, шапка, DESIGN.md

**Files:**
- Modify: `frontend/package.json`, `frontend/package-lock.json`, `frontend/src/main.tsx` (импорт шрифтов),
  `frontend/src/theme.ts`, `frontend/src/App.tsx` (`GlobalStyles`, если меняется), `frontend/src/index.css`,
  `frontend/src/shell/AppShell.tsx`, `frontend/src/shell/Page.tsx`, все места, где используются `PORTAL_BLUE`,
  `MARK`, `MARK_FIELD` (найти поиском)
- Rewrite: `DESIGN.md`
- Modify: `CLAUDE.md` (строки `DESIGN.md` и `theme.ts` в карте кода)

**Interfaces:**
- Produces: `theme.ts` экспортирует `TOKENS` (`ink`, `ink2`, `mute`, `line`, `acc`, `accSoft`, `mark`, `err`),
  `MONO` (строка `font-family` Plex Mono) и `theme`. `PORTAL_BLUE`, `MARK`, `MARK_FIELD` удаляются, их места
  переводятся на `TOKENS`.

- [x] Загрузить `taste` и `antislop-ui`, проверить журнал наблюдений по ним (grep из `~/.claude/CLAUDE.md`).
- [x] Поставить шрифты IBM Plex Sans, IBM Plex Mono, Open Sans из `@fontsource` (кириллица в подмножестве)
  `[вызов платформы не запускался: установка трёх пакетов npm]`. Onest и JetBrains Mono удалить, если после
  замены их никто не импортирует.
- [x] `theme.ts`: `primary.main = acc`, `text.primary = ink`, `text.secondary = mute`, `error.main = err`,
  `background.default = #ffffff`; шрифт Plex Sans; `shape.borderRadius = 6`; `MuiPaper` без теней; `MuiButton`
  без тени; рамка полей `#cfd4db`, фокус кобальтом.
- [x] `AppShell`: имя продукта строчными моноширинным (`brand.ts`), вкладки роли с подчёркиванием 2 px
  кобальтом у активной, переключатель «Поставщик / Модератор» сегментом (активный `ink`, белый текст),
  «Метрики» и «API» справа.
- [x] Старые экраны («Массовая загрузка», «Мои заявки», «Модерация», «Метрики», «API») открываются и читаются в
  новой теме: скриншот каждого 1366×768 (`playwright-cli`), очевидное починить.
- [x] `DESIGN.md` переписан по разделу 3 спецификации: строка Design Read, токены с причинами, шрифты, форма,
  мотивы, запреты, исключение для предпросмотра. `CLAUDE.md` обновлён.
- [x] Проверки фронта (Global Constraints).
- [x] Коммит `feat(web): тема IBM Plex и кобальт, новая шапка`

### Task 2: Похожие СТЕ у вариантов категории

**Files:**
- Modify: `backend/app/tools/build_exemplars.py` (в строке появляется `id` СТЕ), `backend/app/search/exemplars.py`,
  `backend/app/search/candidates.py`, `frontend/src/api/openapi.json`, `frontend/src/api/schema.d.ts`
- Test: `backend/tests/test_exemplars.py`; тест `find_candidates` — рядом с существующими тестами похожих СТЕ
  (найти поиском по `exemplar` в `backend/tests`)

**Interfaces:**
- Produces:
  - `ExemplarIndex.__init__(items: list[tuple[str, str]], ids: list[int] | None = None, k1: float = 1.2, b: float = 0.75)`;
    `ExemplarIndex.load` читает `id`, если он есть в строке.
  - `ExemplarIndex.examples(hits: list[tuple[int, float]], code: str, k: int = 2) -> list[tuple[int, str]]` —
    из уже найденных соседей `(документ, оценка)`: СТЕ этой категории с `id`, без повторов наименования, в порядке
    соседей.
  - `ExemplarIndex.counts(hits: list[tuple[int, float]]) -> dict[str, int]` — сколько соседей у каждой категории.
  - `class SimilarSku(BaseModel): id: int; name: str` (в `candidates.py`).
  - `Candidate.examples: list[SimilarSku] = []`, `Candidate.similar_count: int | None = None`,
    `Candidate.similar_total: int | None = None`.
- Требование: один поиск соседей на запрос; голос, покрытие, примеры и счёт считаются по одним `hits`. Причина:
  p50 конвейера с похожими СТЕ 57 мс, второй и третий поиск по 200 тыс. наименований его умножат. Механизм
  (например, `vote` принимает готовые `hits`) необязателен.

- [x] Тесты индекса (`ITEMS` из `test_exemplars.py`, `ids=[1, 2, 3, 4, 5, 6]`,
  `hits = idx.neighbors("Смеситель однорычажный для мойки")`):
  - [x] `idx.examples(hits, "СМЕС", 2)` — два кортежа, `id` из {1, 2, 3}, имена начинаются со «Смеситель»;
  - [x] `idx.examples(hits, "ОТВ", 2) == []`;
  - [x] `sum(idx.counts(hits).values()) == len(hits)` и `idx.counts(hits)["СМЕС"] >= 2`;
  - [x] индекс без `ids` → `examples(...) == []` (Review Focus 2);
  - [x] `load` файла без поля `id` → примеров нет; с полем `id` → есть.
- [x] Тест `find_candidates`: у кандидата из похожих СТЕ `examples` не пуст, `similar_total` равен числу соседей,
  `similar_count >= 1`; у кандидата только из BM25 `examples == []` и `similar_count is None`.
- [x] Реализация; пересобрать похожие СТЕ:
  `cd backend && uv run python -m app.tools.build_exemplars --outside ../data/ste_root2/ste.jsonl:works --outside ../data/ste_root3/ste.jsonl:services`
  [проверено: 2026-10-06]
- [x] Поиск не сдвинулся:
  `uv run python -m app.tools.evaluate_holdout --limit 2000 --no-llm --no-vectors --exemplars ../data/catalog/exemplars.jsonl`
  → первой 73,9 %, топ-5 86,3 %, итоговый код 32,3 %, p50 около 57 мс (не выше 70 мс: один поиск соседей)
  [проверено: 2026-10-06, таблица в
  `docs/plans/2026-10-06-exemplar-search.md`]; демо-оценка `app.tools.evaluate` на `demo.json` не сдвинулась
  [вызов платформы не запускался: как подставить `demo.json` — по `--help` инструмента].
- [x] Контракт, проверки обеих сторон.
- [x] Коммит `feat(search): похожие СТЕ у вариантов категории`

### Task 3: Путь и картинка категории

**Files:**
- Modify: `backend/app/tools/fetch_standards.py` (флаг `--entity-only`, `image_id` в `entity_summary`),
  `backend/app/portal/standards.py` (`path_names`; позиция получает `portal_id`, `path`, `image_id`),
  `backend/app/tools/build_standard_catalog.py` (аргументы `--tree ../data/standards/tree.json` и
  `--entities ../data/standards/entities.jsonl`; нет файла — поля пустые), `backend/app/catalog/model.py`
  (поля `Position`), `backend/app/pipeline.py` (`Chain.path`, `Chain.portal_id`), контракт фронта
- Test: `backend/tests/test_fetch_standards.py`, `backend/tests/test_standards.py`, `backend/tests/test_chain.py`

**Interfaces:**
- Produces:
  - `Position.portal_id: int | None = None`, `Position.path: list[str] = []`, `Position.image_id: int | None = None` —
    необязательные: другие загрузчики (КТРУ, CSV, демо) оставляют значения по умолчанию (шапка `model.py`:
    модель не знает, откуда справочник).
  - `path_names(tree: list[dict[str, Any]], tree_path: str) -> list[str]` — имена узлов пути `.1.<id>.<id>.` без
    корня `1`; неизвестный id пропускается. `tree` — формат `data/standards/tree.json` (узлы `id`, `name`,
    `subCategories`).
  - `entity_summary(...)["image_id"]: int | None` — из `fileStorage.id`.
  - `fetch_standards --entity-only` → `<out>/entities.jsonl`, строки `{"id": <id категории>, "image_id": int | null}`;
    при повторном запуске пропускает id, которые уже есть в файле (причина: 06.10 портал отдавал 502 два часа).
  - `Chain.path: list[str]`, `Chain.portal_id: int | None`.
- Требование: `path` и `image_id` не входят в текст поиска; поиск не должен сдвинуться.

- [x] Тест `path_names`: `tree = [{"id": 10, "name": "Бумага", "subCategories": [{"id": 11, "name": "Для офисной техники", "subCategories": []}]}]`;
  `".1.10.11."` → `["Бумага", "Для офисной техники"]`; `".1.10.99."` → `["Бумага"]`; `""` → `[]`.
- [x] Тест `entity_summary`: `fileStorage.id = 231277051` → `image_id == 231277051`; без `fileStorage` → `None`.
- [x] Тест сборки позиций: с деревом и `entities.jsonl` у позиции есть `portal_id`, `path`, `image_id`; без этих
  файлов справочник собирается как раньше.
- [x] Тест цепочки: `chain.path == position.path`, `chain.portal_id == position.portal_id` для найденной категории.
- [x] Реализация; выгрузка в фоне: `uv run python -m app.tools.fetch_standards --entity-only`
  [вызов платформы не запускался: новый флаг] — 2 070 категорий, пауза 0,6 с, около 25 мин; в конце в
  `entities.jsonl` 2 070 строк, напечатана доля категорий с картинкой.
- [x] Пересобрать справочник: `uv run python -m app.tools.build_standard_catalog` (по умолчанию
  `--standards ../data/standards/standards.jsonl --ste ../data/ste_full/ste.jsonl --out ../data/catalog/standards.json`)
  [проверено: 2026-10-06]. `index_catalog` не нужен: текст позиций не менялся. Оценка из Task 2 дала те же цифры.
- [x] Контракт, проверки, перезапуск api и worker.
- [x] Коммит `feat(catalog): путь и картинка категории в цепочке`

### Task 4: КТРУ в ответе: «нет в КТРУ», коды вариантов, формат КТРУ

**Files:**
- Modify: `backend/app/pipeline.py` (`Chain.ktru_missing`, `CharacteristicOut.group`, `CharacteristicOut.unit_name`),
  `backend/app/narrowing.py` (`Option.codes`), `backend/app/ingest/units.py` (`UNIT_NAMES`, `unit_name`),
  `backend/app/catalog/model.py` (`CharacteristicSpec.group`), контракт фронта,
  `frontend/src/pages/create/chainView.ts`, `chainView.test.ts`, `frontend/src/pages/create/formModel.ts`
  (`FieldState.group`, `FieldState.unitName` в `buildFields`), объекты-литералы `FieldState` и
  `CharacteristicSpec` в `*.test.ts`
- Create: `frontend/src/pages/create/wizard/ktruFormat.ts`, `ktruFormat.test.ts`
- Test: `backend/tests/test_chain.py`, тесты сужения и единиц (найти поиском по `narrowing` и `units` в
  `backend/tests`)

**Interfaces:**
- Produces:
  - `Chain.ktru_missing: Literal["no_links", "not_loaded"] | None` — `"no_links"`: `ktru_linked == 0`;
    `"not_loaded"`: `ktru_linked > 0`, а привязанных позиций нет в справочнике КТРУ; иначе `None`.
  - `Option.codes: list[str] = []` — коды позиций, которые останутся после варианта, первые 3 в порядке
    кандидатов; у вопроса выбора позиции (`_choice_question`) — `[код]`; у `__nothing__` и `__other__` — `[]`.
  - `CharacteristicSpec.group: Literal["general"] | None = None`; `CharacteristicOut.group` — из спецификации;
    `CharacteristicOut.unit_name: str | None` — `unit_name(unit)`.
  - `UNIT_NAMES: dict[str, str]` (канон → полное название по ОКЕИ) и `unit_name(canon: str | None) -> str | None`.
  - Фронт: `FieldState.group: 'general' | null`, `FieldState.unitName: string | null`;
    `ktruValue(value: string) => string`; `ktruText(chain)` разбирает `ktru_missing`.
- Второй производитель: `chainView.ktruText` до сих пор видел одно значение «`chain.ktru === null`» (нет КТРУ или
  справочник не загружен, одна ветка). Теперь значений два, у каждого своя ветка и свой тест; сочетание
  `ktru === null` при `ktru_missing === null` бэкенд не выдаёт (тест цепочки).

- [x] Тесты цепочки: категория без ссылок → `ktru_missing == "no_links"`, `ktru is None`; ссылки есть, позиций в
  справочнике КТРУ нет → `"not_loaded"`; ссылки и позиции есть → `None`.
- [x] Тесты сужения: у каждого варианта вопроса КТРУ `len(codes) <= 3`; вариант с `remaining == 1` → `codes` — код
  этой одной позиции; вопрос выбора позиции → `codes == [code]`.
- [x] Тесты единиц: `set(UNIT_NAMES) == set(_UNITS)` (у каждого канона есть название); `unit_name("г/м2") ==
  "Грамм на квадратный метр"`, `unit_name("шт") == "Штука"`, `unit_name("мм") == "Миллиметр"`,
  `unit_name("нечто") is None`, `unit_name(None) is None`. Названия — по ОКЕИ (ОК 015-94), пишет исполнитель;
  пропуск ловит тест на равенство множеств.
- [x] Тест конвейера: поле с единицей `г/м2` → `unit_name == "Грамм на квадратный метр"`; поле из спецификации с
  `group="general"` → `group == "general"`.
- [x] Тесты `ktruText`: `no_links` → значение «В КТРУ нет», подсказка «Позиции КТРУ для этого товара нет. Код КТРУ
  останется пустым, характеристики оформлены в формате КТРУ»; `not_loaded` при `ktru_linked = 3` → «Справочник
  КТРУ не содержит 3 привязанных позиций»; 1 и 21 → «…1 привязанной позиции», «…21 привязанной позиции»; 11 →
  «…11 привязанных позиций» (через `plural` из `lib/format.ts`).
- [x] Тесты `ktruValue`: `">=80 ... <90"` → `"≥ 80 и < 90"`; `">=80"` → `"≥ 80"`; `"<90"` → `"< 90"`; `"A4"` →
  `"A4"`; `"500"` → `"500"`; многоточие `…` и лишние пробелы понимаются так же.
- [x] Тест `buildFields`: `group` и `unitName` переходят из ответа модуля в `FieldState`.
- [x] Контракт, проверки обеих сторон.
- [x] Коммит `feat(chain): «нет в КТРУ», коды вариантов и формат КТРУ`

### Task 5: Мастер: полоса шагов, шаги 1–3

**Files:**
- Create: `frontend/src/pages/create/wizard/stepOf.ts`, `stepOf.test.ts`, `path.ts`, `path.test.ts`, `options.ts`,
  `options.test.ts`, `keys.ts`, `keys.test.ts`, `history.ts`, `history.test.ts`, `groups.ts`, `groups.test.ts`,
  `WizardPath.tsx`, `StepDescribe.tsx`, `StepQuestion.tsx`
- Modify: `frontend/src/pages/create/CreatePage.tsx` (каркас мастера; префилл из пакета и «Моих заявок»,
  проверка `/check` через 500 мс и истёкшая сессия сохраняются)
- Delete после переноса: `frontend/src/pages/create/Composer.tsx`, `KtruQuestion.tsx` (сначала поиск импортов)

**Interfaces:**
- Consumes: `Candidate.examples`, `similar_count`, `similar_total` (Task 2); `Chain.path`, `Chain.portal_id`
  (Task 3); `Chain.ktru_missing`, `Option.codes` (Task 4); `questionTitle`, `optionLabel`, `NOTHING_FITS`,
  `OTHER` (`ktruView.ts`); `ktruText` (`chainView.ts`); `plural` (`lib/format.ts`).
- Produces:
  - `type Step = 1 | 2 | 3 | 4 | 5`.
  - `stepOf(out: AutofillOut | null, ui: { reviewing: boolean }) => Step`: нет ответа → 1; `reviewing` → 5;
    `out.ktru.question` или «не найдено» (`!final_code && !question`, то же условие, что в `CreatePage` сейчас) → 2;
    `out.chain?.ktru?.question` → 3; иначе 4.
  - `readiness(fields: FieldState[]) => { done: number; total: number; left: string[] }` (в `groups.ts`) — только
    обязательные вне «Общих сведений»; `done` — заполненные; `left` — имена пустых в порядке полей. Один счёт на
    полосу шагов, шаг 4 и живую карточку: цифры не расходятся.
  - `type Pending = 'autofill' | 'answer' | null`;
    `pathSegments(out: AutofillOut | null, step: Step, pending: Pending, ready: { done: number; total: number } | null) => Segment[]`,
    `Segment = { step: Step; label: string; value: string; code: string | null; state: 'done' | 'current' | 'todo' | 'busy' }`.
  - `optionsOf(state: KtruState, stage: 'category' | 'ktru') => Variant[]`,
    `Variant = { value: string; title: string; hint: string | null; codes: string[]; effect: string; examples: SimilarSku[]; share: string | null }`;
    `NOTHING_FITS` в список не входит (это отдельная кнопка).
  - `shouldHandleKey(target: EventTarget | null) => boolean` — `false` для `input`, `textarea`, `select`,
    `[contenteditable]`.
  - `answerError(e: unknown) => 'expired' | 'failed'` — `ApiError` с 404 → `expired`.
  - `reanswer(step: 2 | 3) => 'restart' | 'answer'` (решение 6).

- [x] Тесты `stepOf`: нет ответа → 1; вопрос категории → 2; «не найдено» → 2; вопрос КТРУ → 3; вопросов нет → 4;
  `reviewing` → 5.
- [x] Тесты `pathSegments`:
  - [x] категория определена сама (`chain.category_name = "Бумага для офисной техники"`, `chain.kpgz = "01.15.01.02.01"`)
    → сегмент 2 `done`, значение — имя, код — КПГЗ;
  - [x] `ktru_missing = "no_links"` → сегмент 3 `done`, «В КТРУ нет», код `null`; `"not_loaded"` → «Нет в справочнике»;
  - [x] позиция КТРУ одна (`final_code`) → сегмент 3 `done`, значение — имя кандидата, код — `final_code`;
  - [x] вопрос КТРУ с 2 кандидатами → сегмент 3 `current`, «Подходят две»; 4 → «Подходят четыре»; 7 → «Подходят 7»;
  - [x] сегмент 4: `ready = { done: 7, total: 10 }` → «7 из 10»; `ready = null` → значение пустое;
  - [x] `pending = 'autofill'` → сегменты 2 и 4 `busy`: «ищем категорию», «заполняем характеристики»;
    `pending = 'answer'` → текущий сегмент `busy`: «проверяем ответ».
- [x] Тесты `optionsOf`:
  - [x] категория: значение варианта — код кандидата, заголовок — его имя, `codes = [код]`; `similar_count = 14`,
    `similar_total = 30` → `share = "14 из 30 похожих"`; без счёта → `share = null`, без примеров →
    `examples = []` (Review Focus 2);
  - [x] КТРУ: заголовок через `optionLabel`, `codes` из `Option.codes`; эффект: `remaining` 1 → «останется 1 позиция»,
    3 → «останется 3 позиции», 5 → «останется 5 позиций».
- [x] Тест `readiness`: поля «Плотность» (обязательное, заполнено), «Формат» (обязательное, заполнено), «Белизна»
  (обязательное, пусто), «Марка» (необязательное, заполнено), «Производитель» (`group: 'general'`, заполнено) →
  `{ done: 2, total: 3, left: ["Белизна"] }`.
- [x] Тесты `shouldHandleKey` (поле ввода → `false`, кнопка и `null` → `true`; Review Focus 5), `answerError`
  (404 → `expired`, 500 и `TypeError` → `failed`; Review Focus 4), `reanswer` (2 → `restart`, 3 → `answer`).
- [x] `WizardPath`: сетка из пяти сегментов (уже 900 px — строка «Шаг N из 5 · <название>»), сегменты 2 и 3 в
  состоянии `done` — кнопки (повторный ответ по решению 6), текущий — фон `acc-soft` и полоса 3 px снизу, коды
  моноширинным, длинные значения переносятся.
- [x] `StepDescribe`: подпись «Вставьте текст», подсказка «Вставьте наименование и описание как есть: из прайса,
  счёта или карточки производителя», кнопка «Заполнить карточку» с подсказкой клавиш `Ctrl Enter`; справа «Что
  будет дальше», четыре строки: «Категорию найдём по похожим СТЕ портала и покажем, почему именно она»; «Позицию
  КТРУ сузим по характеристикам, а если их в тексте не хватит, спросим»; «Характеристики заполним из вашего текста
  и покажем, откуда взято каждое значение»; «Перед отправкой проверим карточку по правилам модерации портала».
- [x] `StepQuestion` (шаги 2 и 3): заголовок `questionTitle`; пояснение — «Определена по N из M похожих», если
  категорию модуль выбрал сам, иначе `state.steps`; варианты с радио, кодом, эффектом, именами похожих СТЕ
  (миниатюры — Task 8) и подсказкой клавиши `1`…`6`; кнопки шага 2 — «Выбрать категорию», «Ни одна не подходит»;
  шага 3 — «Выбрать и перейти к характеристикам», «Ни одна не подходит», «Вернуться к категории»; поле «Другое
  значение» (`__other__`) работает как сейчас; клавиши — только при `shouldHandleKey`.
- [x] Состояния: ошибка API — текст и «Повторить»; истёкшая сессия — «Заполнить заново» с сохранённым текстом;
  «не найдено» — шаг 2 с текстом `not_found_steps` и похожими позициями.
- [x] Скриншоты 390×844 и 1366×768 с наименованием на 200 символов (Review Focus 1): перенос, без горизонтальной
  прокрутки.
- [x] Ручная проверка на стенде: «Бумага офисная А4 80 г/м2 500 листов» → шаг 2 или сразу 3; «Зерно пшеницы
  продовольственное» → вопрос «Наименование типа» → ответ → шаг 4; повторный ответ на шаге 2 и на шаге 3.
- [x] Проверки фронта.
- [x] Коммит `feat(web): мастер создания СТЕ, шаги 1–3`

### Task 6: Мастер: шаги 4 и 5

**Files:**
- Create: `frontend/src/pages/create/wizard/StepCharacteristics.tsx`, `StepSubmit.tsx`
- Modify: `frontend/src/pages/create/wizard/groups.ts`, `groups.test.ts` (`groupFields`),
  `frontend/src/card/CardFields.tsx` (поле с провенансом и кнопкой исправления в новом стиле; `ChainFields`
  удаляется), `frontend/src/pages/create/formModel.ts` (`fieldHelper`), `formModel.test.ts`,
  `frontend/src/pages/create/CreatePage.tsx` (`answeredAt`)

**Interfaces:**
- Consumes: `FieldState.group`, `FieldState.unitName`, `ktruValue`, `ktruText` (Task 4); `readiness` (Task 5).
- Produces:
  - `groupFields(fields: FieldState[]) => { general: FieldState[]; required: FieldState[]; optional: FieldState[] }`
    (сначала `group === 'general'`, затем по `required`).
  - `fieldHelper(f, issue?, answeredStep?: 2 | 3)` — при `method === 'answer'` и известном шаге «Ваш ответ на
    шаге 3»; без шага — прежнее «По вашему ответу на вопрос».
  - `CreatePage` хранит `answeredAt: Record<string, 2 | 3>` (характеристика → шаг, на котором на неё ответили:
    `stage="category"` → 2, `stage="ktru"` → 3) и передаёт шаг в `fieldHelper`.

- [x] Тесты `groupFields`: поле `group: 'general'` → `general` (даже если `required`); обязательное → `required`;
  остальное → `optional`.
- [x] Тест `fieldHelper`: `answeredStep = 3` → «Ваш ответ на шаге 3»; прежние тесты `fieldHelper` зелёные.
- [x] `StepCharacteristics`: группы «Общие сведения», «Обязательные», «Необязательные: ещё N» (свёрнута);
  подсказка под полем (`fieldHelper`), замечание с кнопкой исправления (`Issue.fix`); справа «Осталось» со
  ссылками, клик ставит фокус в поле; нижняя полоса «Готово N из M» и «Проверить и отправить».
- [x] `StepSubmit`: цепочка кодов (КПГЗ, ОКПД2, КТРУ или «В КТРУ нет», СПГЗ); таблица «характеристика · значение
  (`ktruValue`) · единица (`unitName ?? unit`)»; итог `/check`; «Отправить на модерацию»; после отправки — «Заявка
  № N у модератора», «Открыть «Мои заявки»», «Создать ещё одну СТЕ»; исправление возвращённой заявки — `PUT`, как
  сейчас. Переключатель «Сводка / Как на портале» появляется в Task 9 (до него вкладки нет: мёртвых элементов
  не делаем).
- [x] Ручная проверка: до отправки, заявка видна в «Моих заявках» и у модератора.
- [x] Проверки фронта.
- [x] Коммит `feat(web): мастер, характеристики и отправка`

### Task 7: Живая карточка

**Files:**
- Create: `frontend/src/pages/create/wizard/liveCard.ts`, `liveCard.test.ts`, `LiveCard.tsx`
- Modify: `frontend/src/pages/create/CreatePage.tsx`, `frontend/src/card/SourceText.tsx` (вкладка «Ваш текст»)

**Interfaces:**
- Consumes: `readiness` (Task 5), `ktruText`, `ktruValue` (Task 4), `Chain.path` (Task 3).
- Produces:
  - `liveCard(out: AutofillOut, fields: FieldState[], current: string | null) => LiveCardView`,
    `LiveCardView = { path: string[]; name: string; codes: { kpgz: string | null; okpd2: string | null; ktru: { kind: 'code'; code: string } | { kind: 'pending' } | { kind: 'missing'; text: string } }; rows: { name: string; value: string | null; unit: string | null; fromText: boolean; state: 'filled' | 'empty' | 'asking' }[]; ready: { done: number; total: number } }`;
    строки — «Общие сведения», затем обязательные; `current` — характеристика текущего вопроса.
  - `changedRows(prev: LiveCardView | null, next: LiveCardView) => Set<string>`.

- [x] Тесты `liveCard`: порядок строк (общие, затем обязательные, необязательных нет); значение из текста без правки
  → `fromText: true`; пустое → `empty`; характеристика текущего вопроса → `asking`; вопрос КТРУ → `ktru.kind ===
  'pending'`; `ktru_missing` → `missing` с текстом из `ktruText`; `final_code` → `code`; `ready` равен
  `readiness(fields)`.
- [x] Тесты `changedRows`: только строки с изменившимся значением; `prev === null` → пустое множество.
- [x] `LiveCard`: колонка 340 px с шага 2 (от 900 px); вкладки «Карточка» и «Ваш текст»; «Готово N из M
  обязательных» и полоса; крошки `path` через « › » (больше трёх — первые сворачиваются в «…»); наименование;
  коды моноширинным, КТРУ до ответа — «ждёт ответа» кобальтом; значение из текста на жёлтом, «пусто», «выберите
  слева»; изменившиеся строки подсвечиваются 1,5 с (не при `prefers-reduced-motion: reduce`); «Ваш текст» —
  `SourceText` с фрагментами, фокус на поле обводит фрагмент, клик по фрагменту ведёт к полю. Уже 900 px —
  нижняя шторка «Карточка: N из M · Открыть». Ссылка «Открыть как на портале» появляется в Task 9.
- [x] Скриншоты 390×844 (шторка), 899×800 и 900×800 (порог), 1366×768, с наименованием на 200 символов
  (Review Focus 1).
- [x] Проверки фронта.
- [x] Коммит `feat(web): живая карточка рядом с мастером`

### Task 8: Картинки с портала через свой прокси

**Files:**
- Create: `backend/app/portal/images.py`, `backend/app/tools/warm_images.py`, `backend/tests/test_images.py`,
  `docs/demo-texts.txt` (тексты демо-сценариев, по одному в строке)
- Modify: `backend/app/api/v1.py` (два маршрута), `backend/app/resources.py` (клиент httpx и `ImageCache`,
  закрываются в lifespan), `backend/app/config.py` (`image_cache_dir`), `.env.example` (`IMAGE_CACHE_DIR=data/cache/images`),
  `frontend/src/api/v1.ts` (`skuImageUrl(id)`, `categoryImageUrl(portalId)`), `frontend/src/pages/create/wizard/StepQuestion.tsx`
  (миниатюры похожих СТЕ), контракт фронта

**Interfaces:**
- Consumes: `Position.portal_id`, `Position.image_id` (Task 3), `SimilarSku.id` (Task 2).
- Produces:
  - `ImageCache(root: Path, client: httpx.AsyncClient, timeout_s: float = 5.0)`; `async sku(sku_id: int) -> bytes | None`;
    `async category(image_id: int) -> bytes | None`.
  - `GET /api/v1/images/sku/{sku_id}`, `GET /api/v1/images/category/{portal_id}` → 200 `image/jpeg`
    (`Cache-Control: public, max-age=86400`) или 404.
  - Адреса портала (константы модуля):
    `https://zakupki.mos.ru/classifier/api/Sku/GetSku?id={id}&withRegionPriceStatistics=false`
    [проверено: `docs/research/zakupki-portal.md`] и `https://zakupki.mos.ru/newapi/api/Core/Thumbnail/{id}/300/300`
    [проверено: curl, ответ JPEG, 2026-10-07].
  - Фото СТЕ: `images[0].fileStorageId`, иначе `productionDirectory.defaultImageId`, иначе нет.
  - Кэш: `sku-<id>.jpg`, `category-<image_id>.jpg`; отметка «нет фото» `sku-<id>.none` — только на определённый
    ответ (GetSku 200 без картинок), никогда на 5xx и таймаут.
  - `warm_images --texts FILE`: по каждой строке — кандидаты (BM25 и похожие СТЕ, без LLM), картинки до 6
    категорий и их примеров в кэш.

- [x] Тесты на `httpx.MockTransport` и `tmp_path`:
  - [x] GetSku с `images[0].fileStorageId = 282710619` → запрос `Core/Thumbnail/282710619/300/300` → байты в кэше;
    второй вызов без запросов (счётчик вызовов транспорта);
  - [x] без `images`, но с `defaultImageId` → берётся он;
  - [x] без обоих → `None` и отметка; второй вызов без запросов;
  - [x] портал 502 → `None` без отметки; второй вызов снова идёт в сеть;
  - [x] ответ медленнее таймаута (в тесте `timeout_s=0.1`) → `None` не позже таймаута с запасом (Review Focus 3);
  - [x] маршрут категории: неизвестный `portal_id` → 404; позиция без `image_id` → 404.
- [x] Реализация; прогрев: `uv run python -m app.tools.warm_images --texts ../docs/demo-texts.txt`
  [вызов платформы не запускался: новый инструмент].
- [x] `StepQuestion`: миниатюра 40×40 у каждого похожего СТЕ (`alt` — наименование, ленивая загрузка, ошибка
  загрузки прячет картинку).
- [x] Контракт, проверки обеих сторон.
- [x] Коммит `feat(portal): картинки СТЕ и категорий через прокси с кэшем`

### Task 9: Предпросмотр «как на портале»

**Files:**
- Create: `frontend/src/pages/create/wizard/portalPreview.ts`, `portalPreview.test.ts`, `PortalPreview.tsx`
- Modify: `frontend/src/pages/create/wizard/StepSubmit.tsx` (переключатель «Сводка / Как на портале»),
  `frontend/src/pages/create/wizard/LiveCard.tsx` (ссылка «Открыть как на портале» открывает предпросмотр в
  диалоге)

**Interfaces:**
- Consumes: `categoryImageUrl` (Task 8), `ktruValue` (Task 4), `FieldState.group` (Task 4).
- Produces:
  - `portalPreview(input: { name: string; fields: FieldState[]; chain: Chain | null; photoUrl: string | null; ktruCode: string | null }) => PreviewView`,
    `PreviewView = { crumbs: string[]; title: string; photo: { src: string | null; kind: 'own' | 'category' | 'none'; caption: string | null }; facts: { label: string; value: string; missing: boolean }[]; rows: { name: string; value: string; unit: string | null }[]; extra: { label: string; value: string }[] }`.
  - Фото: своё (`photoUrl`) → `own`, без подписи; иначе `chain.portal_id` → `categoryImageUrl`, `category`, подпись
    «картинка категории, пока нет вашего фото»; иначе `none`.
  - Сведения: «ID СТЕ» — «после модерации»; «Производитель», «Модель», «Страна происхождения» из полей
    `group === 'general'`; пустые → «не указан, заполните на шаге 4», «не указана, заполните на шаге 4», «не
    указана, заполните на шаге 4», `missing: true`.
  - Строки — заполненные поля вне «Общих сведений»: имя, `ktruValue(value)`, `unitName ?? unit`.
  - «Доп. сведения»: ОКПД2, КПГЗ, КТРУ (код или «нет в КТРУ»), СПГЗ (через запятую или «нет»).

- [x] Тесты: своё фото важнее картинки категории; только `portal_id` → `category` с подписью; ничего → `none`;
  пустая страна → `missing` и текст; значение `">=80 ... <90"` в строках → `"≥ 80 и < 90"`; КТРУ нет → «нет в КТРУ».
- [x] `PortalPreview`: Open Sans; цвета портала — константы внутри компонента (синий `#264b82`, полоса вкладок
  `#e8eef6`, текст `#1a1a1a`, вторичный `#5b6577`, рамка `#cdd6e3`, «не указано» `#a14f00`; сняты с макета
  `preview-and-files.html`), в токены приложения не попадают; вкладки «Общие сведения», «Характеристики», «Доп.
  сведения»; подпись над превью «Так карточку увидят заказчики после модерации. Цена добавляется на портале»;
  ошибка загрузки картинки → заглушка без битой иконки (Review Focus 3).
- [x] Скриншоты предпросмотра 1366×768 и 390×844.
- [x] Проверки фронта.
- [x] Коммит `feat(web): предпросмотр карточки как на портале`

### Task 10: Фото поставщика

**Files:**
- Create: `backend/migrations/versions/*_submission_photo.py` (имя ревизии даст Alembic), `backend/app/demo/photos.py`,
  `backend/tests/test_photos.py`
- Modify: `backend/app/api/demo.py` (маршруты), `backend/app/schemas.py` (`CardIn.photo_id`, `StoredCard.photo_id`),
  `frontend/src/api/v1.ts` (`uploadPhoto(file)`, `photoUrl(id)`), `frontend/src/pages/create/wizard/StepCharacteristics.tsx`
  (поле «Фото товара» в «Общих сведениях»), `frontend/src/pages/create/CreatePage.tsx` (`photoUrl` в
  предпросмотр), `frontend/src/pages/mine/MinePage.tsx`, `frontend/src/pages/moderation/ModerationPage.tsx`
  (миниатюра), контракт фронта

**Interfaces:**
- Produces:
  - `POST /api/demo/photos` (multipart `file`) → 200 `{"photo_id": int}`; 415 — не JPEG, PNG или WebP по
    сигнатуре содержимого (расширение не в счёт: оно врёт); 413 — больше 5 МБ.
  - `GET /api/demo/photos/{photo_id}` → байты с сохранённым `Content-Type`; 404 — нет такого.
  - `CardIn.photo_id: int | None = None`, `StoredCard.photo_id: int | None = None`.
  - Таблица `submission_photo (id serial primary key, mime text not null, bytes bytea not null, created_at timestamptz not null default now())`.
  - При загрузке удаляются фото старше суток, на которые не ссылается ни одна заявка (`submission.card->>'photo_id'`).

- [x] Тесты: PNG 1×1 → 200 и `photo_id`; GET отдаёт те же байты и `image/png`; GIF → 415; текст с расширением
  `.jpg` → 415; JPEG на 6 МБ → 413; неизвестный id → 404.
- [x] Тесты заявки: `photo_id` сохраняется в `StoredCard`; `PUT` возвращённой заявки обновляет его; старая заявка
  без поля читается.
- [x] Тест очистки: старое фото без заявки удалено, старое с заявкой и свежее остались.
- [x] Миграция: новая ревизия поверх текущей головы Alembic `[вызов платформы не запускался: создание ревизии и
  проверка головы]`, применяется сервисом `migrate` в compose.
- [x] Фронт: загрузка сразу при выборе, превью, «Заменить», «Удалить»; ошибки под полем: 413 — «Файл больше 5 МБ»,
  415 — «Подойдёт JPEG, PNG или WebP».
- [x] Контракт, проверки обеих сторон.
- [x] Коммит `feat(demo): фото товара в заявке`

### Task 11: Текст из файла: Word, PDF, Excel

**Files:**
- Create: `backend/app/ingest/documents.py`, `backend/tests/test_documents.py`, фикстуры
  `backend/tests/fixtures/docs/` (`spec.docx`, `goods.docx`, `spec.pdf`, `scan.pdf`, `one.xlsx`),
  `frontend/src/pages/create/wizard/FileDrop.tsx`, `frontend/src/lib/pendingFile.ts`, `pendingFile.test.ts`
- Modify: `backend/app/api/v1.py` (`POST /extract`), `backend/app/ingest/readers.py` (таблица товаров в docx для
  пакета), `backend/pyproject.toml` и `backend/uv.lock` (PDF), `frontend/src/pages/create/wizard/StepDescribe.tsx`
  (зона файла), `frontend/src/pages/batch/BatchPage.tsx` (принимает файл из мастера), контракт фронта

**Interfaces:**
- Produces:
  - `POST /api/v1/extract` (multipart `file`) → `{"text": str, "items": int, "kind": "docx" | "pdf" | "table" | "text"}`;
    413 — больше 10 МБ; 415 — неизвестный тип; 422 «В PDF нет текстового слоя» — скан.
  - `docx_text(data: bytes) -> tuple[str, list[list[list[str]]]]` — текст абзацев и таблицы (строки ячеек) через
    zip и `word/document.xml`, без новых зависимостей.
  - `pdf_text(data: bytes) -> str` — через `pypdf`.
  - `goods_table(tables) -> list[list[str]] | None` — таблица, в заголовке которой есть ячейка «Наименование»
    (без учёта регистра).
  - `row_text(row) -> str` — наименование первой строкой, затем строки «Имя: значение единица».
  - Фронт: `pendingFile.put(file: File)`, `pendingFile.take() => File | null` (отдаёт один раз).

- [x] Тесты:
  - [x] docx с абзацем «Бумага офисная А4» и таблицей `[["Плотность", "80 г/м2"]]` → текст содержит оба, `items == 1`,
    `kind == "docx"`;
  - [x] docx с таблицей «Наименование | Количество» и 6 строками товаров → `items == 6`;
  - [x] pdf с текстом → текст содержит «Бумага»; pdf без текстового слоя → 422;
  - [x] xlsx с одной строкой → `kind == "table"`, `items == 1`, текст `"Бумага офисная А4\nПлотность: 80 г/м2"`;
    с тремя строками → `items == 3`;
  - [x] 11 МБ → 413; `.exe` → 415;
  - [x] пакет из `goods.docx` (`ingest/readers.py`) → 6 строк;
  - [x] `pendingFile`: `put`, затем `take` → файл; второй `take` → `null`.
- [x] Фикстура PDF с кириллицей и текстовым слоем — печать HTML в PDF браузером или любым доступным генератором;
  в репозиторий кладётся сам файл не больше 20 КБ. `scan.pdf` — страница-картинка без текста.
- [x] Зависимость: `uv add pypdf` при остановленных серверах проекта `[вызов платформы не запускался]`, затем
  пересборка образов api и worker.
- [x] `FileDrop`: перетаскивание и «Выбрать файл»; один товар → текст в поле шага 1 для проверки (без
  автозапуска); несколько → блок «В файле N товаров» и «Открыть массовую загрузку» (`pendingFile` и переход на
  `batch`).
- [x] Контракт, проверки обеих сторон.
- [x] Коммит `feat(ingest): Word и PDF на входе мастера и пакета`

### Task 12: Общие сведения: производитель, модель, страна

**Files:**
- Modify: `backend/app/portal/standards.py` (счёт `manufacturer`, `model`, `country` по категории; три
  спецификации с `group="general"`), `backend/app/catalog/model.py` (`GENERAL_NAMES`), `backend/app/search/vectors.py`
  (`position_text`), `backend/app/search/candidates.py` (`build_bm25`), `backend/app/narrowing.py`
  (`best_question`), `backend/app/checks/detector.py` и место вызова правил модерации (счёт характеристик),
  `backend/app/portal/holdout.py` (`score_case`), `backend/app/tools/build_standard_catalog.py`, контракт фронта
- Test: `backend/tests/test_standards.py` и тесты потребителей ниже

**Interfaces:**
- Consumes: `CharacteristicSpec.group` (Task 4), `values.canonical_map`.
- Produces:
  - `GENERAL_NAMES = ("Производитель", "Модель", "Страна происхождения")`.
  - `maker_name(raw: str) -> str` — без организационно-правовой формы и кавычек, регистр исходный; ключ сравнения —
    `maker_name(raw).casefold()`.
  - `country_title(raw: str) -> str` — каждое слово с заглавной, союз «и» строчной.
  - Спецификации (все `required=False`, `group="general"`, `kind="qualitative"`): «Производитель» — частые
    производители категории, показ — самое частое написание (`canonical_map`); «Модель» — без перечня; «Страна
    происхождения» — страны категории.
- Второй производитель: позиции получают характеристики, которых раньше не было. Потребители и ветки:
  - `position_text` и документы BM25 — без «Общих сведений» (иначе перечни производителей сдвинут поиск);
  - `best_question` — никогда не спрашивает про «Общие сведения»;
  - правило модерации «не меньше 4 характеристик» — считает без них;
  - `score_case` отложенной выборки — без них;
  - извлечение правилами — находит производителя по перечню в наименовании (это и нужно).

- [x] Тесты `maker_name`: `'ООО "Туринский ЦБЗ"'` → `"Туринский ЦБЗ"`;
  `'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "СПРИНТ-ПЛАСТ"'` → `"СПРИНТ-ПЛАСТ"`; `'АО «Гознак»'` → `"Гознак"`;
  `"ИП Иванов Иван Иванович"` → `"Иванов Иван Иванович"`; `"Kimberly-Clark"` → `"Kimberly-Clark"`.
- [x] Тесты `country_title`: `"РОССИЯ"` → `"Россия"`; `"КОРЕЯ, РЕСПУБЛИКА"` → `"Корея, Республика"`;
  `"БОСНИЯ И ГЕРЦЕГОВИНА"` → `"Босния и Герцеговина"`.
- [x] Тест сборки: у позиции три спецификации `group="general"` с перечнями из СТЕ категории; у «Модели» перечня нет.
- [x] Тесты потребителей:
  - [x] производитель «Туринский ЦБЗ» не попадает в `position_text` и в документ BM25 позиции;
  - [x] `best_question` на позициях, различающихся только производителем, вопроса не задаёт;
  - [x] карточка из 3 обычных и 3 общих характеристик получает то же замечание о минимуме, что карточка из 3;
  - [x] `score_case` не считает общие;
  - [x] «Бумага Туринский ЦБЗ А4» → «Производитель» = «Туринский ЦБЗ».
- [x] Пересобрать справочник (команда Task 3); оценка из Task 2: первой, топ-5 и итоговый код без изменений;
  «из текста в поле» может сдвинуться только из-за новых полей — записать в журнал.
- [x] Контракт, проверки, перезапуск api и worker.
- [x] Коммит `feat(catalog): общие сведения карточки`

### Task 13: Сквозные сценарии, проверка, документы

**Files:**
- Modify: `frontend/e2e/flows.js`, `docs/demo-scenarios.md`, `docs/demo-texts.txt`, `CLAUDE.md` (карта:
  `pages/create/wizard/`, новые эндпоинты, `warm_images`, `fetch_standards --entity-only`, `entities.jsonl`,
  `data/cache/images`), `docs/plans/2026-10-07-wizard-redesign-design.md` (итог)

- [x] e2e под мастер на справочнике стандартов: текст → вопрос категории → вопрос КТРУ → характеристики → отправка
  → «Мои заявки» → модератор; docx с одним товаром; docx с несколькими → пакет; товар без КТРУ.
  Запуск: `cd frontend && npm run e2e` [вызов платформы не запускался в этой сессии].
- [x] Скриншоты каждого шага: 390×844, 899×800, 900×800, 1366×768, 1440×720, 1440×900; наименование на 200
  символов; предпросмотр.
- [x] `accessibility-scan` по шагам мастера; `web-design-guidelines` по изменённым файлам; найденное починить.
- [x] Delivery Gate `antislop` с поэлементным прокликом.
- [x] Полные проверки обеих сторон; CI на последнем SHA ветки (`gh run list -L1`, все джобы `success`)
  [проверено: 2026-10-06].
- [x] Коммит `docs: мастер в сценариях и карте проекта`

## Журнал решений
- Task 1: Ruling: `TOKENS` получил ключ `field` (`#cfd4db`, рамка полей и подсказок клавиш из макета) — один источник цвета вместо литерала в трёх местах — цена ошибки: лишний ключ в токенах.
- Task 1: Ruling: фон полей «из текста» в `CardFields` временно `TOKENS.mark` вместо прежнего бледного `MARK_FIELD` — в спецификации один жёлтый, вид поля переделывает Task 6 — цена ошибки: до Task 6 поле ярче прежнего.
- Task 1: Ruling: Open Sans поставлен, но импортируется в Task 9 внутри предпросмотра — шрифт нужен только там, в общий бандл не попадает — цена ошибки: нет.
- Task 1 (ревью): Ruling: `field` сменён с `#cfd4db` (спецификация) на `#8a919c` — рамка поля ввода по WCAG 1.4.11 должна давать 3:1 с фоном, у прежнего 1,49:1 — цена ошибки: рамки полей заметно темнее макета.
- Task 1 (ревью): Ruling: внешний блок (`Paper variant="outlined"`) получил радиус 10px, `Alert` 8px, линии ячеек таблиц — `line` (MUI осветлял `divider` до невидимости), `overline` без заглавных и разрядки, `info` — кобальт, выделение текста — светлый кобальт `#d6ddf8` вместо жёлтого — находки ревьюера, все в пределах DESIGN.md — цена ошибки: нет.
- Task 2 (ревью): Ruling: примеры и «N из M похожих» — у любой категории-кандидата, у которой есть соседи с id, а не только у прошедших хвост голоса; без примеров нет и счёта (Review Focus 2) — ревьюер нашёл, что честный счёт был скрыт у 484 вариантов на 2000 отложенных СТЕ — цена ошибки: у слабого варианта видна подпись «1 из 30», это правда.
- Task 2 (ревью): Ruling: порог `AUTO_SHARE` не менялся, хотя ревьюер нашёл 75 из 582 автоматических выборов категории меньшинством соседей (верных 90,7 % против 96,1 %) — правило поиска вне плана мастера, решается замером отдельно; мастер честно пишет «Определена по N из M похожих» — цена ошибки: часть автоматических категорий неверна чаще, человек видит слабое основание.
- Task 5: Ruling: справочник из позиций КТРУ (код вида 17.12.14.129-00000017, как демо и прошлые выгрузки организаторов) сам задаёт КТРУ: цепочка отдаёт его как определённый, иначе мастер писал бы «В КТРУ нет» — найдено при разборе шага 3, отдельный коммит fix(chain) — цена ошибки: на справочнике КПГЗ правило не срабатывает (у кода КПГЗ другой вид).
- Task 5: Ruling: код в заявке (`CardIn.ktru_code`) по-прежнему код категории справочника, а не позиции КТРУ второго этапа — `/check` и детектор ищут позицию в основном справочнике; перевод заявки на код КТРУ — отдельная задача вне плана — цена ошибки: модератор видит в заявке код категории вместо кода КТРУ.
- Task 3 (ревью): Ruling: картинка категории есть только у 1 из 2070 категорий (выгрузка entities.jsonl 07.10) — предпросмотр почти всегда покажет своё фото поставщика или заглушку, как и сам портал у СТЕ без фото; картинки похожих СТЕ в карточку не подставляются (спецификация) — цена ошибки: превью беднее макета.
- Task 4 (ревью): Ruling: у справочника из позиций КТРУ `ktru_linked == 0`, но `ktru` задан и `ktru_missing = None` — контракт «no_links: ktru_linked == 0» уточнён: no_links только когда сама позиция не код КТРУ — цена ошибки: нет, ветка проверена тестом.
- Task 4 (ревью): Ruling: если загружена часть позиций КТРУ категории, единственная подходящая из загруженных не ставится кодом сама — вопрос подтверждения (код КТРУ не выдумывается) — цена ошибки: лишний вопрос, пока выгрузка ЕИС не закончена.
- Task 6: «Осталось заполнить» на широком экране стоит в правой колонке над «Ваш текст» (липкая, не уезжает при прокрутке полей), на телефоне и планшете — над полями шага 4: справа на телефоне её не увидеть до конца страницы — цена ошибки: на узком экране список не там, где в спецификации.
- Task 6: таблица шага 5 с фиксированными ширинами столбцов, на телефоне столбца «Единица» нет — единица идёт после значения («35 Миллиметр»): три столбца на 390 px рвали слова посередине — цена ошибки: нет.
- Task 6: на шаге 5 перечислены и замечания к сведению (не только блокирующие), отправка ими не запрещается; после отправки «Изменить описание» скрыто — оно бы открыло ту же заявку заново — цена ошибки: нет.
- Task 7: `liveCard(out, fields, current, name)` — четвёртый параметр `name`: наименование живёт в форме (его правят на шаге 4), в ответе модуля его нет — цена ошибки: нет.
- Task 7: в `LiveCardView` добавлен `category` (есть цепочка кодов): до выбора категории КПГЗ, ОКПД2 и КТРУ «ждут ответа», после — отсутствующий код не показывается, а не «ждёт ответа» навсегда — цена ошибки: нет.
- Task 7: до выбора категории в карточке нет строк и «Готово N из M»: модуль заполняет поля ведущего варианта (на «Зерне пшеницы» это «Наборы продовольственные»), карточка показывала бы чужие обязательные — вместо них строка «Характеристики появятся, когда категория будет выбрана» — цена ошибки: на шаге 2 карточка беднее.
- Task 7: компонент в `LiveCardPanel.tsx`, а не `LiveCard.tsx`: имена `liveCard.ts` и `LiveCard.tsx` различаются только регистром, на Windows и macOS импорт `./wizard/LiveCard` находит модель — цена ошибки: нет.
- Task 7: на телефоне полоса шторки «Карточка: N из M · Открыть» на шагах 2, 3 и 5; на шаге 4 её заменяет кнопка «Карточка» в полосе «Готово N из M»: две закреплённые полосы внизу перекрывались бы — цена ошибки: на шаге 4 шторка открывается не с нижней полосы экрана.
- Task 7: строки подсвечиваются только после ответа модуля (новый объект ответа), не при наборе в поле: подсветка каждой буквы — шум — цена ошибки: правка поля на шаге 4 не мигает в карточке.
- Task 7: «Изменить описание» переехало во вкладку «Ваш текст» (оно про текст); фрагменты текста доступны с клавиатуры (Tab, Enter, пробел, видимая рамка фокуса); из шторки фрагмент ведёт к полю после её закрытия — цена ошибки: кнопка на одну вкладку дальше.
- Task 8: Ruling: прокси картинок не ждёт портал синхронно 5 с: с диска — сразу, иначе ждёт не дольше `IMAGE_WAIT_S` (1 с) и отвечает 404 с `Cache-Control: no-store`, а скачивание продолжается в фоне (до `IMAGE_TIMEOUT_S`, 30 с, не больше 4 одновременно, одна картинка — одна задача) и кладёт файл в кэш к следующему показу. Замер 07.10: GetSku отвечал 6,7 и 27,5 с на одном id, миниатюра — 0,5 с; синхронное ожидание занимало бы шесть соединений браузера с модулем, и ответ мастера стоял бы в очереди за картинками — цена ошибки: у нового товара миниатюры появляются только при следующем показе вопроса.
- Task 8: прогрев `warm_images --texts ../docs/demo-texts.txt` 07.10: в кэше 24 картинки, 20 — портал не ответил за 10 с; перед показом прогнать ещё раз (готовые не качаются заново) — цена ошибки: у части вариантов демо нет миниатюр.
- Task 9: компонент в `PortalPreviewPanel.tsx`, а не `PortalPreview.tsx`: `portalPreview.ts` и `PortalPreview.tsx` различаются только регистром (как в Task 7) — цена ошибки: нет.
- Task 9: сведение «Общих сведений», которого у позиции нет вовсе, — «не указан(а)» без «заполните на шаге 4»: на шаге 4 такого поля нет, отсылка была бы неправдой — цена ошибки: нет.
- Task 9: КТРУ в «Доп. сведениях»: код; «уточняется на шаге 3», пока категория не выбрана или позицию КТРУ уточняют; «не определён», если позиции КТРУ у категории есть, но в справочник модуля не загружены (`not_loaded`); «нет в КТРУ» — только когда их нет или ни одна не подошла — цена ошибки: нет.
- Task 9: фото, заголовок и сведения видны на всех вкладках превью (как на макете), вкладка по умолчанию — «Характеристики»; «Общие сведения» — таблица: наименование, категория, модель, производитель, страна; строку адреса из макета («zakupki.mos.ru/sku/view/…») не рисуем: превью по образцу, а не страница портала — цена ошибки: нет.
- Task 10: префилл несёт `photoId`: «Исправить» возвращённую заявку открывает мастер с её фото, и исправленная заявка (PUT) ссылается на него же — без этого фото терялось бы при исправлении — цена ошибки: нет.
- Task 10: до загрузки фронт сам отвечает на файл больше 5 МБ и на явно чужой тип (`image/gif`, PDF); файл без типа решает сервер по сигнатуре — цена ошибки: нет.
- Task 10: раздел «Общие сведения» на шаге 4 есть всегда — в нём поле «Фото товара», даже когда у позиции нет полей производителя, модели и страны; «Удалить» только отвязывает фото от карточки, файл уберёт суточная очистка фото без заявок — цена ошибки: нет.
- Task 10: вне плана починено в `ModerationPage`: клик по уже открытой заявке очереди навсегда оставлял скелетон вместо карточки (вид сбрасывался, а ключ эффекта не менялся); тестов компонентов в проекте нет — проверено в браузере — цена ошибки: нет.
- Task 11: файл из мастера («Открыть массовую загрузку») массовая загрузка сразу отправляет в работу, а не только подставляет имя: человек уже выбрал действие; повторный заход на страницу файл не повторяет (`pendingFile.take` отдаёт его один раз) — цена ошибки: нет.
- Task 11: выбор файла в массовой загрузке принимает и `.docx` (таблица товаров читается модулем с Task 11, бэкенд); текст одного товара из файла заменяет содержимое поля шага 1, заполнение — по кнопке человека — цена ошибки: набранный до файла текст теряется (человек сам выбрал файл).
- Ревью 5–7: Ruling: исправлены Critical 1 (бесконечная загрузка после «Изменить описание» с префиллом — экран загрузки только до первого автозаполнения), Important 2 (поколение запросов: ответ после «Начать заново» или нового заполнения отбрасывается, «Начать заново» снимает «занято»), 3 (клавиши мастера — только из формы вопроса или со страницы, уже обработанное событие не трогается), 4 (сбой `/check` — «Проверить карточку не удалось» и «Повторить проверку», отправка выключена), 5 (повторный ответ на пройденном шаге запоминается — `rememberAsked`), 6 (фрагмент «Ваш текст» раскрывает свёрнутые необязательные и ставит фокус; на шагах без полей фрагменты не кнопки; поле с замечанием видно и в свёрнутой группе) и Minor 9, 10, 11, 12, 13, 16 — проверка: `frontend/e2e/wizard.js`, 11 сценариев PASS на dev-сервере, сценарии 1 и 11 падают при откате своей правки — цена ошибки: нет.
- Ревью 5–7: вне находок починено: префилл читался в начальном состоянии с удалением ключа — в StrictMode второй вызов получал пустоту, и полная перезагрузка с префиллом открывала пустую форму; теперь чтение чистое, ключ удаляет эффект — цена ошибки: нет.
- Ревью 5–7: Ruling: `pendingFile.peek()` добавлен к контракту Task 11 (`put`/`take`): массовая загрузка берёт файл в начальное состояние без побочного эффекта (StrictMode), а забирает в эффекте — линтер больше не ругается на setState в эффекте — цена ошибки: нет.
- Task 12: Ruling: перечни «Производитель» и «Страна происхождения» — подсказки, а не ограничение: `fit_value` для `group="general"` оставляет значение как его написал поставщик (без «not_in_list», без «опечаток» в именах собственных и без перевода в самое частое написание портала — у смесителей это «grohe» строчными), детектор новый завод не блокирует, LLM перечень не показывается, на шаге 4 поле — ввод с подсказками (Autocomplete freeSolo), а не выпадающий список. По плану перечень значил бы «значение только из справочника»: новый производитель блокировал бы отправку — цена ошибки: модуль не поправит опечатку в названии завода.
- Task 12: известный из текста производитель не сужает категории (`_fits` пропускает «Общие сведения»): у категории в перечне только частые заводы, и «Туринский ЦБЗ» отсёк бы категорию, где он ещё не встречался — цена ошибки: нет.
- Task 12: если стандарт категории сам задаёт характеристику «Страна происхождения» (или «Производитель», «Модель»), она остаётся характеристикой категории, поле «Общих сведений» не дублируется (так у 51 из 2070 категорий); подсказки — не реже 2 СТЕ категории, до 50, по убыванию числа СТЕ — цена ошибки: нет.
- Task 12: пересборка 07.10: 2070 позиций, характеристики вне «Общих сведений» побайтно те же, подсказки производителей у 1374 категорий, стран — у 1472; evaluate_holdout --limit 2000 --no-llm --no-vectors: top1 73,85 %, top5 86,3 %, final 32,25 %, not_found 1,25 % — без изменений; given_recall 0,4327 (было 0,432), target_in_spec 0,5686 (было 0,5678); p50 70 мс против 57 — прогон шёл параллельно со сборкой образов, задержки не сравнимы.
- Ревью 8, 10, 11 (бэкенд): Ruling: исправлены Important 1 (у клиента к порталу свой таймаут `IMAGE_TIMEOUT_S` — умолчание httpx 5 с обрывало фоновое скачивание, прогрев 07.10 на деле ждал 5 с, а не 10), 2 (таблица характеристик одного товара — «Наименование показателя», колонка «Значение» — не список товаров), 3 (шифрованный PDF без пароля на открытие читается — `pypdf[crypto]`; с паролем — «PDF защищён паролем»; любая ошибка разбора PDF — 422, не 500), 4 (тесты на семафор, защиту от больших docx, очистку фото в Postgres в своей схеме, свежую сироту) и Minor 5 (очередь фоновых скачиваний — не больше 64, при остановке задачи отменяются), 6 (огромный id картинки — 422), 7 (фото без заявки живёт неделю; заявка с несуществующим фото — 422), 8 (поля шаблонов Word `w:sdt`), 11 (`.doc` — «сохраните как .docx») — проверка: тесты RED→GREEN, 7 порч кода убиты, `check.sh be` 735 passed — цена ошибки: нет.
- Ревью 8–11 (фронт): Ruling: исправлены Important 1 (загрузка фото ведётся страницей с поколением запросов: фото после «Начать заново» не цепляется к новой карточке; пока фото загружается, «Проверить и отправить» выключена) и Minor 2–13: стрелки, Home и End во вкладках превью, панель вкладок шага 5, контраст неактивных вкладок и «Фото нет» в превью (темнее цветов портала), одна формулировка «нет в справочнике», фокус после «Удалить» фото, `alt=""` у миниатюр похожих (наименования уже в тексте — отступление от буквы плана Task 8), своё фото не загрузилось — картинка категории, тот же файл выбирается снова, чтение файла выключает «Заполнить карточку» и поздний текст после ухода с шага не пишется, подсветка зоны файла не мигает, радиус миниатюр 6 px, фото в сводке шага 5; вне находок — у модератора поле кода подписано «Категория портала (КПГЗ)», если в заявке не позиция КТРУ — проверка: e2e/wizard.js 14 и 15 PASS, portalPreview — два теста убивают выжившие порчи ревьюера — цена ошибки: нет.
- Web Interface Guidelines (07.10): «Начать заново» с подтверждением, пока есть что терять; `scroll-padding` под закреплёнными шапкой и полосой «Готово N из M»; `overscroll-behavior: contain` у шторки; `touch-action: manipulation`; `autocomplete="off"` у полей карточки; размеры у картинки превью; заголовки разделов шага 4 — h2 (axe: heading-order) — цена ошибки: нет.
- Task 13: Ruling: CI на последнем SHA не проверен: прогон GitHub Actions идёт на push, а пушить пользователь не просил (правило: push только по просьбе) — локально `check.sh all` зелёный — цена ошибки: CI может упасть на окружении раннера.
- Финальное ревью (вся ветка): Important 1 fixed — «Общие сведения» по перечню только из наименования, целым словом или фразой, не метка «X:», не слово имени характеристики, не значение другой характеристики: test_general (5 отрицательных) RED→GREEN; коммит 2eb09ee
- Финальное ревью: метрика общих полей — app.tools.evaluate_general (test_evaluate_general, 6 порч убиты): производитель 2 424 / 2 927 (82,8 %; было 2 558 / 3 260 = 78,5 %), страна 57 / 72 проверяемых (79,2 %); коммит после 381e96d
- Финальное ревью: Minor 1 (написание поставщика), 3 (общие по группе в детекторе и сужении), 4 (тесты _spec_issues, «Ромашка ООО», порядок и лимит 50) fixed — 2eb09ee; Minor 6 (золотой набор, точность заполнения, «Заполнено» пакета) fixed — 54d24ff, RED→GREEN; Minor 5 (фото) fixed — c9c53e9, e2e/wizard.js 19 FAIL→PASS; Minor 2 (превью своей страны) fixed — 381e96d, portalPreview.test RED→GREEN
- Финальное ревью: Ruling: совет «пропускать значение, равное слову наименования позиции» не внедрён — целое слово по fold уже отсекает «Насос»→«Насосы», «Флаг»→«Флаги»; цена ошибки: завод, чьё имя целиком совпадает с названием товара
- Финальное ревью: minor (deferred): holdout.score_case исключает общие поля по имени (своя «Страна происхождения» 46 категорий не идёт в заполнение выборки); отчёт build_standard_catalog считает общие поля характеристиками; порча пропуска в _described эквивалентна
- Порог автовыбора категории 70 % (решение пользователя 07.10): Ruling: «скрывать категории ниже 70 %» буквально не сделано — доли голоса в сумме 100 %, выше порога бывает одна категория, и в 46 % случаев список был бы пуст; ниже порога остаётся выбор из нескольких с процентом. evaluate_holdout --limit 2000 --no-llm --no-vectors: top1 73,9 % (было 73,85), top5 84,2 % (86,3: при неверном автовыборе других вариантов нет), код без вопросов 49,2 % (32,25), not_found 1,25 %, p50 53 мс — цена ошибки: 5,1 % случаев получают неверную категорию без вопроса, исправляется кнопкой «изменить» на шаге 2
- Справочник 8 569 категорий (08.10): Ruling: на стенде — большой справочник. CategoryList не отдаёт детей 443 веток (там бумага для офисной техники, перчатки): их СТЕ — поиском по пути ветки (3,68 млн СТЕ, 8 569 стандартов из 8 594, 25 — 404 портала). Честный замер на отложенной выборке из всех категорий (2000 СТЕ): у 2 070 категорий 75 % товаров вне справочника, первой 16,6 %; у 8 569 — первой 64,3 %, среди пяти 78,6 %; на общих 494 СТЕ больший справочник хуже (62,3 против 67,0 %) — цена ошибки: соседние категории («Зерно пшеницы» уходит в «Корма для парнокопытных» без вопроса), демо-текст пшеницы — с «ГОСТ 9353». Лимиты ввода подняты (имя 500, значение 4 000): имена характеристик стандартов до 400 знаков роняли карточку. Позиции КТРУ загружены у 708 из 3 123 категорий со ссылками (не хватает 26 тыс. карточек ЕИС)
- Аудит критериев 08.10: закрыты поля уровня «Виды …» (не характеристики, не обязательны), «500 листов» → «Листовая», «гр/м2», подтверждение единственной категории ниже 70 %, конфликт чужой единицы и выдуманная единица модели, отрицательные значения, «17 л л», «Метрики» с отложенной выборкой (первой 63,9 %, без вопроса 41,2 % с точностью 85,3 %, уверенных ошибок 6,1 %), BPMN (пакет, «дополнить описание», типы задач), найденные значения на виду, нагрузка на 8 569 категориях (≈1,9 ГБ на процесс). `catalog_from_table` на пермской выгрузке: 244 492 строки → 5 311 категорий → 1 658 позиций (от 20 СТЕ) за ~24 мин (разбор xlsx); мешки, бумага, перчатки — своя категория с долей голоса 89–92 %. Ruling: справочник для жюри и доступ к репозиторию — позже, решение пользователя 08.10
- КТРУ 08.10: Ruling: докачка тремя потоками с паузой 3 с (robots ЕИС просит 60 с; пользователь просил ускорить при 20 ч до старта) — 32 138 карточек за ~9 ч, отказов ЕИС не было; справочник КТРУ — 32 137 позиций (одна без характеристик), у всех 3 121 категорий со ссылками позиции загружены целиком (было 708). Цена ошибки: блок адреса ЕИС — повторная докачка стала бы невозможной
