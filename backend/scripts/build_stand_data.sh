#!/usr/bin/env bash
# Данные стенда с нуля: справочник категорий портала, КТРУ из XML организаторов, похожие СТЕ с синтетикой
# и векторами, двойники, пределы категорий. Запуск из backend/: bash scripts/build_stand_data.sh
#
# Выгрузка с портала идёт часами и нужна один раз: FETCH=1 bash scripts/build_stand_data.sh.
# Без неё скрипт берёт уже скачанное в data/ste_full, data/standards, data/ste_root2, data/ste_root3.
# Синтетика редких категорий (data/synthetic/raw) пишется агентами по docs/synthetic-prompt.md — здесь
# только проверяется и сливается. Векторы считает bge-m3 в сервисе `embed` (llama.cpp server; EMBED_BASE_URL, по умолчанию 127.0.0.1:8082).
# Память: сборка справочника ~4,5 ГБ, векторы похожих ~2 ГБ. Итог — пути из .env.example.
set -euo pipefail
export PYTHONIOENCODING=utf-8
# Сервер эмбеддингов (compose, сервис embed) с хоста: опубликованный порт; localhost теряет 2 с — 127.0.0.1.
export EMBED_BASE_URL="${EMBED_BASE_URL:-http://127.0.0.1:8082/v1}"
D=../data
C=$D/catalog
KTRU_XML="${KTRU_XML:-$D/organizers/Казань 2026/КТРУ/КТРУ xml.zip}"
OUTSIDE="--outside $D/ste_root2/ste.jsonl:works --outside $D/ste_root3/ste.jsonl:services"
step() { echo "$(date +%T) $*"; }
run() { uv run python -m "$@"; }

if [ "${FETCH:-0}" = 1 ]; then
  step "выгрузка СТЕ ветки «Товары» (часы)"
  run app.tools.fetch_ste --out $D/ste_full --per-category 0 --exclude ""
  step "СТЕ работ и услуг — классы «не товар»"
  run app.tools.fetch_ste --root 2 --out $D/ste_root2 --per-category 0 --exclude ""
  run app.tools.fetch_ste --root 3 --out $D/ste_root3 --per-category 0 --exclude ""
  step "стандарты категорий"
  run app.tools.fetch_standards --out $D/standards
fi

step "КТРУ из XML ЕИС организаторов (только actual)"
run app.tools.build_ktru_catalog --xml "$KTRU_XML" --out $C/ktru_xml.json

step "справочник категорий портала (~20 мин)"
run app.tools.build_standard_catalog --out $C/standards.json

step "похожие СТЕ и их характеристики"
# shellcheck disable=SC2086
run app.tools.build_exemplars --out $C/exemplars.jsonl $OUTSIDE
run app.tools.build_exemplar_values $C/exemplars.jsonl

step "синтетика редких категорий"
run app.tools.synthetic_exemplars merge --base $C/exemplars.jsonl --out $C/exemplars_syn.jsonl
# У синтетики нет id СТЕ: характеристики похожих — те же, что у живого индекса.
ln -f $C/exemplars.values.sqlite $C/exemplars_syn.values.sqlite

step "векторы похожих СТЕ (bge-m3, ~10 мин)"
run app.tools.build_exemplar_vectors --exemplars $C/exemplars_syn.jsonl --out $C/exemplars_syn.vectors.npz

step "двойники «такая СТЕ уже есть» (~8 мин)"
run app.tools.build_twins --out $C/twins.sqlite

step "пределы числовых характеристик по СТЕ категории"
run app.tools.plausible_ranges $C/standards.json --exemplars $C/exemplars_syn.jsonl

step "готово. Дальше: docker compose up -d --build && docker compose exec api python -m app.tools.index_catalog"
