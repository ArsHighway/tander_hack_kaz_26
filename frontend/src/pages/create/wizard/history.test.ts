import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ApiError } from '../../../api/http.ts'
import { rememberAsked, answerError, reanswer } from './history.ts'

test('истёкшая сессия отличается от сбоя', () => {
  assert.equal(answerError(new ApiError(404, 'сессия не найдена')), 'expired')
  assert.equal(answerError(new ApiError(500, 'ошибка')), 'failed')
  assert.equal(answerError(new TypeError('fetch failed')), 'failed')
})

test('повторный ответ о категории начинает заново, о КТРУ — отвечает', () => {
  assert.equal(reanswer(2), 'restart')
  assert.equal(reanswer(3), 'answer')
})

const q = (characteristic: string, values: string[]) =>
  ({ question: { characteristic, text: '', options: values.map((v) => ({ value: v, label: v })) } }) as never

test('ответ на вопрос запоминается с самим вопросом — чтобы открыть шаг снова', () => {
  const shown = q('Наименование типа', ['Озимая', 'Яровая'])
  assert.deepEqual(rememberAsked({}, 3, shown, 'Озимая'), { 3: { state: shown, chosen: 'Озимая' } })
})

test('повторный ответ на пройденном шаге меняет выбор, вопрос остаётся прежним', () => {
  const shown = q('Наименование типа', ['Озимая', 'Яровая'])
  const asked = { 3: { state: shown, chosen: 'Озимая' } }
  const answeredState = { question: null } as never
  assert.deepEqual(rememberAsked(asked, 3, answeredState, 'Яровая'), { 3: { state: shown, chosen: 'Яровая' } })
})

test('после «Ни одна не подходит» вопрос можно открыть снова, прежний выбор не отмечен', () => {
  const shown = q('Наименование типа', ['Озимая', 'Яровая'])
  const asked = { 3: { state: shown, chosen: 'Озимая' } }
  assert.deepEqual(rememberAsked(asked, 3, shown, '__nothing__'), { 3: { state: shown, chosen: '__nothing__' } })
  assert.deepEqual(rememberAsked({}, 3, shown, '__nothing__'), { 3: { state: shown, chosen: '__nothing__' } })
})
