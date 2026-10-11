import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ApiError, toApiError } from './http.ts'

test('detail из ответа FastAPI становится текстом ошибки', () => {
  const err = toApiError(404, '{"detail":"сессия заполнения не найдена или истекла"}')
  assert.ok(err instanceof ApiError)
  assert.equal(err.status, 404)
  assert.equal(err.detail, 'сессия заполнения не найдена или истекла')
})

test('ошибка проверки полей собирается в одну строку', () => {
  const body = JSON.stringify({ detail: [{ loc: ['body', 'name'], msg: 'String should have at least 1 character' }] })
  assert.equal(toApiError(422, body).detail, 'name: String should have at least 1 character')
})

test('не JSON — показываем статус, а не пустоту', () => {
  assert.equal(toApiError(502, '<html>Bad Gateway</html>').detail, 'сервис ответил ошибкой 502')
})

test('сетевой сбой — статус 0 и понятный текст', () => {
  const err = toApiError(0, '')
  assert.equal(err.status, 0)
  assert.equal(err.detail, 'сервис недоступен')
})
