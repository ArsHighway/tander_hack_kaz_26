from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["openai"]
# Рассуждения: False — выключить, None — не передавать, уровень — для моделей, которые
# не выключают их совсем (gpt-oss: с low ~15 символов рассуждения вместо ~900); gemma — False.
Think = bool | Literal["low", "medium", "high"] | None

# Демо-справочник в репозитории: синтетика с именами характеристик портала (app/catalog/demo).
DEMO_CATALOG = Path(__file__).parent / "catalog" / "demo" / "demo.json"


class Settings(BaseSettings):
    """Настройки из переменных окружения (в docker compose их задаёт compose-файл).

    Значения по умолчанию подходят для локального запуска против инфраструктуры из compose.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://tenderhack:tenderhack@localhost:5432/tenderhack"
    redis_url: str = "redis://localhost:6379/0"
    rabbitmq_url: str = "amqp://tenderhack:tenderhack@localhost:5672/"

    # LLM — локальная gemma-4-31b-it-Q8_0.gguf в llama.cpp server (сервис `llm` в docker compose,
    # `--alias` = llm_chat_model); протокол — OpenAI-совместимый API (`openai`), внешние облачные
    # API не используются. Из контейнеров адрес — http://llm:8080/v1, с хоста — порт LLM_PORT.
    llm_provider: Provider = "openai"
    llm_base_url: str = "http://127.0.0.1:8081/v1"
    llm_api_key: str = ""
    llm_chat_model: str = "gemma-4-31b-it-Q8_0"
    llm_timeout_s: float = 120.0
    # Сколько ждать модель в шаге мастера и строке пакета: не ответила — карточка без неё (ответ
    # шага — до 7 с). Пусто — без предела (оценка, инструменты).
    llm_budget_s: float | None = 15.0
    # Модель при старте включена; дальше её включает и выключает модератор (`PUT /api/v1/settings`).
    llm_enabled: bool = True
    # Модель ставит первым самый вероятный тип (`extract.llm_fill.suggest_type_order`): на эталоне
    # hard.csv верный тип первым 53 % → 74 %, p95 описания 8 → 13 с.
    llm_rerank_types: bool = True
    # Модель отвечает по тексту на вопрос сужения до позиции КТРУ («аккумуляторная» → «Тип
    # питания: От аккумуляторных батарей», `extract.llm_fill.pick_option`): вариант из списка с
    # цитатой, в поле — с пометкой «проверьте».
    llm_pick_options: bool = True
    # Модель называет значения из текста, которых у такого товара не бывает («Материал: глина» у
    # тумбочки, `extract.llm_fill.find_oddities`): замечание поставщику, значение не меняется.
    llm_sanity: bool = True
    # Первым шагом модель делит весь текст поставщика на пары «название: значение»
    # (`extract.llm_pairs`); раскладывает их код: КТРУ, портал, новая характеристика.
    llm_structure: bool = True
    # Второй слой (`extract.llm_review`): перепроверка неподтверждённых значений модели и место для
    # непонятых кусков текста; пусто — выключен.
    llm_review_model: str = "gemma-4-31b-it-Q8_0"
    llm_review_budget_s: float = 12.0
    llm_max_retries: int = 2
    # 429 (лимит облачного тарифа или сервера) — повтор с паузой (Retry-After или 1, 2, 4 … 30 с),
    # свой бюджет: 6 повторов ≈ до минуты ожидания, потом строка идёт без LLM (degraded «llm»).
    llm_rate_limit_retries: int = 6
    # Одновременных запросов к чат-модели на процесс (API или воркер); 0 — без лимита.
    # Всего к модели: (API + WORKER_REPLICAS) × это число — под тариф или GPU-сервер.
    llm_max_concurrency: int = 4
    # Рассуждения (think) у части моделей по умолчанию включены и замедляют ответ в разы
    # (0,35 с против 3 с на коротком вопросе). None — не передавать параметр.
    llm_think: Think = False
    # Сервер модели может быть не поднят при разработке: readiness без него не падает.
    llm_required: bool = False

    # Эмбеддинги — отдельный llama.cpp server с bge-m3 (сервис `embed`, порт EMBED_PORT); пустые
    # значения = как у LLM.
    embed_provider: Provider | None = None
    embed_base_url: str = "http://127.0.0.1:8082/v1"
    embed_api_key: str = ""
    embed_model: str = "bge-m3"

    health_timeout_s: float = 2.0
    job_ttl_s: int = 3600
    redis_socket_timeout_s: float = Field(default=30.0, gt=0)

    # Массовая загрузка: строки уходят в очередь jobs.batch порциями по BATCH_CHUNK_ROWS — порции
    # разбирают все воркеры. WORKER_CONCURRENCY — строк одновременно в одном воркере (ожидание
    # эмбеддингов и LLM перекрывается; разбор и BM25 — CPU, их ускоряют только реплики воркера).
    batch_chunk_rows: int = Field(default=20, ge=1)
    # Срок ожидания и обработки пакета.
    batch_pending_ttl_s: int = Field(default=86400, ge=1)
    # Сколько хранится готовый пакет: «Мои заявки → Пакеты» показывают его и результат неделю.
    batch_ttl_s: int = Field(default=604800, ge=1)
    worker_concurrency: int = Field(default=4, ge=1)

    # Файлы справочника через запятую (.json или .csv), пути от рабочего каталога процесса.
    # Пусто — демо-справочник из репозитория (app/catalog/demo/demo.json, синтетика): чистый клон
    # стартует сам. Справочник организаторов кладут в ./data и указывают здесь.
    catalog_paths: str = ""
    # Наименования живых СТЕ для поиска категории по похожим (`build_exemplars`); пусто — выключен.
    exemplars_path: str = ""
    # Векторы тех же наименований (`build_exemplar_vectors`, npz): второй голос категорий к BM25;
    # пусто — голосует только BM25.
    exemplar_vectors_path: str = ""
    # Справочник КТРУ (build_ktru_catalog): второй этап — позиция КТРУ внутри категории.
    ktru_paths: str = ""
    # Двойники — такая СТЕ уже есть на портале (`build_twins`, sqlite); пусто — выключены.
    twins_path: str = ""
    # Кэш картинок портала (фото СТЕ, картинки категорий). Браузер ждёт портал не дольше
    # image_wait_s, скачивание в фоне — до image_timeout_s (GetSku отвечал 6–27 с, 07.10.2026), с.
    image_cache_dir: str = "data/cache/images"
    image_timeout_s: float = 30.0
    image_wait_s: float = 1.0

    # Отсечка «товар вне справочника»: нормированная оценка BM25 лучшего кандидата (0..1).
    # Калибруется скриптом оценки на золотом наборе (app.tools.evaluate).
    search_min_score: float = 0.2
    # Результат последнего прогона оценки: его отдаёт GET /api/v1/metrics.
    metrics_path: str = "data/metrics/latest.json"
    # Отчёт по живым карточкам портала (app.tools.portal_report): GET /api/v1/metrics/portal.
    portal_report_path: str = "data/metrics/portal.json"
    holdout_report_path: str = "data/metrics/holdout.json"
    # Кэш ответов модели в Redis (тот же текст — без второго вызова LLM), секунды; 0 — выключен.
    llm_cache_ttl_s: int = 86400

    def catalog_files(self) -> list[Path]:
        files = [Path(p.strip()) for p in self.catalog_paths.split(",") if p.strip()]
        return files or [DEMO_CATALOG]

    @field_validator("embed_provider", "llm_budget_s", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        # EMBED_PROVIDER= в .env — «как у LLM»; LLM_BUDGET_S= — без предела
        return value or None


@lru_cache
def get_settings() -> Settings:
    return Settings()
