import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { BatchRowResult, BatchState } from '../../api/v1.ts'
import { STATUS_TEXT, canCancel, isFinished, reconcileBatchState, summarize } from './batchView.ts'

const row = (status: BatchRowResult['status'], n: number): BatchRowResult => ({
  row: n,
  name: `строка ${n}`,
  status,
  ktru_code: null,
  filled: 0,
  hinted: 0,
  issues: [],
  degraded: [],
})

const state = (rows: BatchRowResult[], status: BatchState['status'] = 'done'): BatchState => ({
  job_id: 'j',
  status,
  filename: 'f.xlsx',
  total: rows.length,
  processed: rows.length,
  counts: {},
  rows,
})

test('сводка считает строки по статусам', () => {
  const s = summarize(state([row('ready', 1), row('ready', 2), row('needs_answer', 3), row('needs_fix', 4), row('skipped', 5), row('failed', 6), row('not_found', 7)]))
  assert.deepEqual(s, { ready: 2, needs_answer: 1, needs_fix: 1, not_found: 1, skipped: 1, failed: 1 })
})

test('у каждого статуса есть слово для человека', () => {
  for (const key of ['ready', 'needs_answer', 'needs_fix', 'not_found', 'skipped', 'failed'] as const) assert.ok(STATUS_TEXT[key]?.length > 0)
})

test('опрос останавливается на done и failed', () => {
  assert.equal(isFinished(state([], 'running')), false)
  assert.equal(isFinished(state([], 'queued')), false)
  assert.equal(isFinished(state([], 'done')), true)
  assert.equal(isFinished(state([], 'failed')), true)
  assert.equal(isFinished(state([], 'cancelled')), true)
})

test('прервать можно только ожидающий или обрабатываемый пакет', () => {
  assert.equal(canCancel(state([], 'queued')), true)
  assert.equal(canCancel(state([], 'running')), true)
  for (const status of ['done', 'failed', 'cancelled'] as const) assert.equal(canCancel(state([], status)), false)
})

test('ответ опроса до отмены не возвращает пакет в обработку', () => {
  const cancelled = state([row('ready', 1)], 'cancelled')
  assert.equal(reconcileBatchState(cancelled, state([], 'running')), cancelled)
  assert.equal(reconcileBatchState(cancelled, state([row('ready', 1)], 'done')), cancelled)
})

test('отмена без строк сохраняет таблицу, обновление добавляет завершённые строки', () => {
  const current = state([row('ready', 1)], 'running')
  const cancelled = { ...current, status: 'cancelled' as const, rows: [] }
  const next = reconcileBatchState(current, cancelled)
  assert.deepEqual(next.rows, current.rows)
  const refreshed = reconcileBatchState(next, { ...cancelled, processed: 2, rows: [row('ready', 2)] })
  assert.deepEqual(refreshed.rows.map((r) => r.row), [1, 2])
  assert.equal(refreshed.status, 'cancelled')
})

test('запоздалый ответ не уменьшает прогресс, новый пакет заменяет старый', () => {
  const current = state([row('ready', 1), row('ready', 2)], 'cancelled')
  assert.equal(reconcileBatchState(current, state([row('ready', 1)], 'cancelled')), current)
  const other = { ...state([], 'queued'), job_id: 'other' }
  assert.equal(reconcileBatchState(current, other), other)
})

test('ответ отмены сохраняет статус, даже если более свежий опрос уже увеличил прогресс', () => {
  const current = state([row('ready', 1), row('ready', 2)], 'running')
  const next = reconcileBatchState(current, { ...state([], 'cancelled'), total: 4 })
  assert.equal(next.status, 'cancelled')
  assert.equal(next.processed, 2)
  assert.deepEqual(next.rows, current.rows)
})

test('у большого пакета сводка — по счётчикам сервера, строк может не быть', () => {
  const big: BatchState = { ...state([], 'running'), total: 100000, processed: 7, counts: { ready: 5, failed: 2 } }
  assert.deepEqual(summarize(big), { ready: 5, needs_answer: 0, needs_fix: 0, not_found: 0, skipped: 0, failed: 2 })
})
