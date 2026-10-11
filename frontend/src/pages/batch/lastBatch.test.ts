import assert from 'node:assert/strict'
import { test } from 'node:test'
import { LAST_BATCH_KEY, loadLastBatch, saveLastBatch } from './lastBatch.ts'

function memory() {
  const m = new Map<string, string>()
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k), m }
}

test('последний пакет вкладки запоминается и забывается', () => {
  const s = memory()
  assert.equal(loadLastBatch(s), null)
  saveLastBatch({ jobId: 'j1', filename: 'goods.xlsx' }, s)
  assert.deepEqual(loadLastBatch(s), { jobId: 'j1', filename: 'goods.xlsx' })
  saveLastBatch(null, s)
  assert.equal(s.m.has(LAST_BATCH_KEY), false)
})

test('битая запись — нет пакета', () => {
  const s = memory()
  s.setItem(LAST_BATCH_KEY, '{')
  assert.equal(loadLastBatch(s), null)
  s.setItem(LAST_BATCH_KEY, JSON.stringify({ jobId: '', filename: 'x' }))
  assert.equal(loadLastBatch(s), null)
})
