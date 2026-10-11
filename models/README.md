# Веса моделей

Сюда кладутся GGUF-файлы, которые читают сервисы `llm` и `embed` из `docker-compose.yml` (том `./models`,
только чтение). Сами файлы в git не идут (`*.gguf` в `.gitignore`).

| Файл | Зачем | Сервис | Переменная |
|---|---|---|---|
| `gemma-4-31b-it-Q8_0.gguf` | чат-модель: дозаполнение характеристик, второй слой проверки, порядок типов | `llm`, порт 8081 | `LLM_MODEL_FILE`, `LLM_CHAT_MODEL` |
| `bge-m3-Q8_0.gguf` | эмбеддинги поиска категорий и позиций КТРУ (1024 измерения) | `embed`, порт 8082 | `EMBED_MODEL_FILE`, `EMBED_MODEL` |

Запуск: `docker compose --profile llm up -d` (NVIDIA GPU и NVIDIA Container Toolkit; веса `gemma-4-31b-it-Q8_0.gguf` — около
33 ГБ, сверху KV-кэш: контекст `LLM_CTX` делится между `LLM_PARALLEL` слотами).

Без файлов и без профиля `llm` решение работает на правилах и поиске по справочнику: ответ помечается
`degraded: ["llm"]` и `degraded: ["embeddings"]`.

Проверка после запуска: `cd backend && uv run python -m app.tools.llm_smoke`, готовность — `GET /api/health/ready`.
