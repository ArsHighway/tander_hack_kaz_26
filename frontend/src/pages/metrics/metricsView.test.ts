import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { PortalReport } from '../../api/v1.ts'
import { CONTRADICTION_RULE, formatMetric, portalHeadline, splitRules } from './metricsView.ts'

const NB = String.fromCharCode(160)

test('доли — в процентах', () => {
  assert.equal(formatMetric('ktru_acc1', 0.875), `87,5${NB}%`)
  assert.equal(formatMetric('detector_typo', 1), `100${NB}%`)
})

test('задержки — в миллисекундах', () => {
  assert.equal(formatMetric('latency_p95', 5.7), `5,7${NB}мс`)
})

test('среднее число вопросов и порог — числом', () => {
  assert.equal(formatMetric('questions', 0.0333), '0,03')
  assert.equal(formatMetric('questions_real', 0.273), '0,27')
  assert.equal(formatMetric('fill_recall_real', 0.871), `87,1${NB}%`)
  assert.equal(formatMetric('min_score', 0.2), '0,2')
})

test('обязательных вручную на СТЕ — числом, а не процентом', () => {
  assert.equal(formatMetric('manual_required', 2.14), '2,1')
  assert.equal(formatMetric('manual_fields', 3.46), '3,5')
  assert.equal(formatMetric('zero_touch', 0.12), `12${NB}%`)
})

const report = (rules: { rule: string; cards: number }[]): PortalReport => ({
  created_at: '2026-10-02T10:00:00Z',
  cards: 8520,
  categories: 554,
  source: 'skus.jsonl, categories.jsonl',
  audit: {
    cards: 8520,
    with_issues: 5108,
    share: 5108 / 8520,
    rules: rules.map((r) => ({
      ...r,
      share: r.cards / 8520,
      examples: [{ id: 1, url: 'https://zakupki.mos.ru/sku/view/1', name: 'n', field: 'name', message: 'm', fix: null }],
    })),
  },
  fill: [],
})

test('итог аудита — доля и число карточек словами', () => {
  assert.equal(portalHeadline(report([])), `5${NB}108 из 8${NB}520 карточек нарушают хотя бы одно из правил модерации, которые проверяет модуль`)
})

test('ошибки ввода — отдельно от остальных правил', () => {
  const r = report([
    { rule: 'в наименовании нет значения и единицы', cards: 5043 },
    { rule: CONTRADICTION_RULE, cards: 11 },
  ])
  assert.deepEqual(
    splitRules(r).other.map((x) => x.rule),
    ['в наименовании нет значения и единицы'],
  )
  assert.equal(splitRules(r).slips?.cards, 11)
  assert.equal(splitRules(report([])).slips, null)
})
