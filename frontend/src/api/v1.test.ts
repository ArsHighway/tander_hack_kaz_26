import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ApiError, api, categoryImageUrl, photoUrl, skuImageUrl } from './v1.ts'

test('картинка похожей СТЕ — через свой прокси, по id СТЕ портала', () => {
  assert.equal(skuImageUrl(282710619), '/api/v1/images/sku/282710619')
})

test('фото поставщика — с модуля по номеру фото заявки', () => {
  assert.equal(photoUrl(12), '/api/demo/photos/12')
})

test('картинка категории — через свой прокси, по id категории портала', () => {
  assert.equal(categoryImageUrl(793371198), '/api/v1/images/category/793371198')
})

test('отмена отправляет DELETE для выбранного пакета и возвращает состояние', async (t) => {
  const state = { job_id: 'j/1', status: 'cancelled', processed: 2, total: 10, rows: [], counts: {} }
  t.mock.method(globalThis, 'fetch', async (path: string, init: RequestInit) => {
    assert.equal(path, '/api/v1/batch/j%2F1')
    assert.equal(init.method, 'DELETE')
    assert.equal(init.body, undefined)
    return new Response(JSON.stringify(state), { status: 200 })
  })
  assert.deepEqual(await api.cancelBatch('j/1'), state)
})

test('сбой отмены возвращает понятную ошибку, а не успех', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({ detail: 'пакет устарел' }), { status: 404 }))
  await assert.rejects(api.cancelBatch('missing'), (e: unknown) => e instanceof ApiError && e.status === 404 && e.detail === 'пакет устарел')
})

test('фильтр запрашивает страницу по всему пакету с курсором', async (t) => {
  const result = { rows: [], matched: 150, next_after_row: 5000 }
  t.mock.method(globalThis, 'fetch', async (path: string) => {
    assert.equal(path, '/api/v1/batch/j%2F1/results?limit=50&after_row=1500&status=failed')
    return new Response(JSON.stringify(result))
  })
  assert.deepEqual(await api.batchResults('j/1', 'failed', 1500), result)
})

test('Все запрашивает результаты без ограничения по статусу', async (t) => {
  t.mock.method(globalThis, 'fetch', async (path: string) => {
    assert.equal(path, '/api/v1/batch/j/results?limit=50&after_row=0')
    return new Response(JSON.stringify({ rows: [], matched: 0, next_after_row: null }))
  })
  assert.equal((await api.batchResults('j')).matched, 0)
})
