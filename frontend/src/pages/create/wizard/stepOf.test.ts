import assert from 'node:assert/strict'
import { test } from 'node:test'
import { stepOf } from './stepOf.ts'
import { candidate, chain, out, state } from './testing.ts'

const question = { characteristic: 'Позиция КТРУ', options: [] }
const paper = candidate('01.15.02.01', 'Бумага для офисной техники')

test('без ответа модуля — шаг 1', () => {
  assert.equal(stepOf(null, { reviewing: false }), 1)
})

test('вопрос о категории — шаг 2', () => {
  assert.equal(stepOf(out(state({ question, remaining: 2 })), { reviewing: false }), 2)
})

test('категория не найдена — шаг 2', () => {
  assert.equal(stepOf(out(state()), { reviewing: false }), 2)
})

test('вопрос о позиции КТРУ — шаг 3', () => {
  const ktru = state({ question: { characteristic: 'Цветность', options: [] }, remaining: 2 })
  const o = out(state({ final_code: paper.code, candidates: [paper], remaining: 1 }), { chain: chain({ ktru }) })
  assert.equal(stepOf(o, { reviewing: false }), 3)
})

test('вопросов нет — шаг 4', () => {
  const o = out(state({ final_code: paper.code, candidates: [paper], remaining: 1 }), { chain: chain({ ktru_missing: 'no_links', ktru_linked: 0 }) })
  assert.equal(stepOf(o, { reviewing: false }), 4)
})

test('проверка перед отправкой — шаг 5', () => {
  const o = out(state({ final_code: paper.code, candidates: [paper], remaining: 1 }))
  assert.equal(stepOf(o, { reviewing: true }), 5)
})

test('всё решено и заполнено из текста — сразу к проверке; подсказки похожих СТЕ — сначала глазами', async () => {
  const { readyToReview } = await import('./stepOf.ts')
  const { field } = await import('./testing.ts')
  const done = out(state({ final_code: paper.code, candidates: [paper], remaining: 1 }), { chain: chain({ ktru_missing: 'no_links', ktru_linked: 0 }) })
  const fromText = field({ name: 'Формат', value: 'A4', initial: 'A4', required: true, status: 'filled', method: 'rule' })
  // подсказка — пустое поле с предложением
  const hint = field({ name: 'Белизна', hint: '146', status: 'filled', method: 'similar' })
  const twin = field({ name: 'Белизна', hint: '146', status: 'filled', method: 'twin' })
  const empty = field({ name: 'Плотность', value: '', initial: '', required: true })
  assert.equal(readyToReview(done, [fromText], []), true)
  assert.equal(readyToReview(done, [fromText, hint], []), false)
  assert.equal(readyToReview(done, [fromText, twin], []), false)
  assert.equal(readyToReview(done, [fromText, empty], []), false)
  const block = { field: 'characteristics.Формат', severity: 'block', message: 'нет в перечне', rule: 'x' }
  assert.equal(readyToReview(done, [fromText], [block as never]), false)
  assert.equal(readyToReview(out(state({ question, remaining: 2 })), [fromText], []), false)
})
