#!/usr/bin/env bash
# Честный замер на отложенной выборке: те же сборки, что у стенда, но без СТЕ выборки и их двойников,
# затем два прогона — вся выборка портала (страница «Метрики») и категории хакатона (одежда, мебель,
# инструменты). Запуск из backend/: bash scripts/eval_holdout.sh
#
# Нужны данные стенда (scripts/build_stand_data.sh): data/ste_full, data/standards, ste_root2/3,
# data/catalog/ktru_xml.json, twins.sqlite, data/synthetic/raw. Выборка data/holdout_full строится один раз
# (seed 7) и дальше не меняется: новая выборка — новые числа, сравнивать с прошлыми нельзя.
# Без LLM — нижняя оценка. VECTORS=1 — с векторным поиском по справочнику (нужен index_catalog справочника
# оценки в Postgres), иначе --no-vectors.
set -euo pipefail
export PYTHONIOENCODING=utf-8
# Сервер эмбеддингов (compose, сервис embed) с хоста: опубликованный порт; localhost теряет 2 с — 127.0.0.1.
export EMBED_BASE_URL="${EMBED_BASE_URL:-http://127.0.0.1:8082/v1}"
D=../data
L=$D/catalog/eval
EX=$D/holdout_full/exclude.txt
OUTSIDE="--outside $D/ste_root2/ste.jsonl:works --outside $D/ste_root3/ste.jsonl:services"
step() { echo "$(date +%T) $*"; }
run() { uv run python -m "$@"; }

if [ ! -f $D/holdout_full/holdout.jsonl ]; then
  step "отложенная выборка (один раз)"
  run app.tools.build_holdout --out $D/holdout_full
fi
step "выборка категорий хакатона"
run app.tools.hackathon_holdout --holdout $D/holdout_full/holdout.jsonl --out $D/holdout_kazan

step "справочник и похожие СТЕ без выборки (~25 мин)"
run app.tools.build_standard_catalog --exclude $EX --out $L/standards_new.json
# shellcheck disable=SC2086
run app.tools.build_exemplars --exclude $EX --out $L/exemplars_new.jsonl $OUTSIDE
run app.tools.build_exemplar_values $L/exemplars_new.jsonl
# Синтетика проверяется против выборки (--holdout): наименование из выборки в индекс не попадёт.
run app.tools.synthetic_exemplars merge --base $L/exemplars_new.jsonl --out $L/exemplars_syn.jsonl \
  --holdout $D/holdout_full/holdout.jsonl
ln -f $L/exemplars_new.values.sqlite $L/exemplars_syn.values.sqlite
mkdir -p $D/vectors/exemplars
run app.tools.build_exemplar_vectors --exemplars $L/exemplars_syn.jsonl \
  --out $D/vectors/exemplars/eval_syn_10.npz
run app.tools.plausible_ranges $L/standards_new.json --exemplars $L/exemplars_new.jsonl

VEC=--no-vectors
[ "${VECTORS:-0}" = 1 ] && VEC=
COMMON="--limit 2000 --no-llm $VEC --exemplars $L/exemplars_syn.jsonl \
  --exemplar-vectors $D/vectors/exemplars/eval_syn_10.npz --ktru $D/catalog/ktru_xml.json \
  --twins $D/catalog/twins.sqlite"
step "замер: вся выборка портала → data/metrics/holdout.json («Метрики»)"
# shellcheck disable=SC2086
CATALOG_PATHS=$L/standards_new.json run app.tools.evaluate_holdout \
  --holdout $D/holdout_full/holdout.jsonl --out $D/metrics/holdout.json $COMMON
step "замер: категории хакатона → data/metrics/holdout_kazan.json"
# shellcheck disable=SC2086
CATALOG_PATHS=$L/standards_new.json run app.tools.evaluate_holdout \
  --holdout $D/holdout_kazan/holdout.jsonl --out $D/metrics/holdout_kazan.json $COMMON
step "готово"
