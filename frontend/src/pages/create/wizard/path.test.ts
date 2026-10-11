import assert from 'node:assert/strict'
import { test } from 'node:test'
import { pathSegments } from './path.ts'
import { candidate, chain, out, state } from './testing.ts'

const paper = candidate('01.15.02.01', 'Бумага для офисной техники')
const decided = state({ final_code: paper.code, candidates: [paper], remaining: 1 })
const seg = (segments: ReturnType<typeof pathSegments>, step: number) => segments.find((s) => s.step === step)!

test('пять сегментов с названиями шагов', () => {
  const s = pathSegments(null, 1, null, null)
  assert.deepEqual(
    s.map((x) => x.label),
    ['Описание', 'Тип товара', 'Позиция КТРУ', 'Характеристики', 'Отправка'],
  )
  assert.equal(seg(s, 1).state, 'current')
  assert.equal(seg(s, 2).state, 'todo')
})

test('категория определена сама: имя и код КПГЗ', () => {
  const s = pathSegments(out(decided, { chain: chain({ ktru_missing: 'no_links', ktru_linked: 0 }) }), 4, null, null)
  assert.deepEqual([seg(s, 2).state, seg(s, 2).value, seg(s, 2).code], ['done', 'Бумага для офисной техники', '01.15.02.01'])
  assert.equal(seg(s, 1).state, 'done')
})

test('в КТРУ нет и нет в справочнике', () => {
  const none = pathSegments(out(decided, { chain: chain({ ktru_missing: 'no_links', ktru_linked: 0 }) }), 4, null, null)
  assert.deepEqual([seg(none, 3).state, seg(none, 3).value, seg(none, 3).code], ['done', 'В КТРУ нет', null])
  const missing = pathSegments(out(decided, { chain: chain({ ktru_missing: 'not_loaded' }) }), 4, null, null)
  assert.equal(seg(missing, 3).value, 'Нет в справочнике')
})

test('ни одна позиция КТРУ не подошла — шаг 3 пройден, его можно открыть снова', () => {
  const noneFit = chain({ ktru: state({ candidates: [], steps: ['Ни одна не подходит'] }) })
  const s = pathSegments(out(decided, { chain: noneFit }), 4, null, null)
  assert.deepEqual([seg(s, 3).state, seg(s, 3).value], ['done', 'Не подходит ни одна'])
})

test('позиция КТРУ одна: её имя и код', () => {
  const white = candidate('17.12.14.129-00000031', 'Бумага для офисной техники белая')
  const ktru = state({ final_code: white.code, candidates: [white], remaining: 1 })
  const s = pathSegments(out(decided, { chain: chain({ ktru }) }), 4, null, null)
  assert.deepEqual([seg(s, 3).state, seg(s, 3).value, seg(s, 3).code], ['done', white.name, white.code])
})

test('вопрос о КТРУ: «подходят две», «четыре», «7»', () => {
  const ask = (n: number) =>
    seg(pathSegments(out(decided, { chain: chain({ ktru: state({ question: { characteristic: 'Цветность', options: [] }, remaining: n }) }) }), 3, null, null), 3)
  assert.deepEqual([ask(2).state, ask(2).value], ['current', 'Подходят две'])
  assert.equal(ask(4).value, 'Подходят четыре')
  assert.equal(ask(7).value, 'Подходят 7')
})

test('шаг 4 показывает готовность', () => {
  const s = pathSegments(out(decided), 4, null, { done: 7, total: 10 })
  assert.equal(seg(s, 4).value, 'КТРУ: заполнено 7 из 10')
  assert.equal(seg(pathSegments(out(decided), 4, null, { done: 0, total: 0 }), 4).value, 'КТРУ: заполнено 0 из 0')
  assert.equal(seg(s, 4).state, 'current')
  assert.equal(seg(pathSegments(out(decided), 4, null, null), 4).value, '')
  // До выбора категории характеристики не её: готовность не показывается.
  const asking = state({ question: { characteristic: 'Позиция КТРУ', options: [] }, remaining: 2 })
  assert.equal(seg(pathSegments(out(asking), 2, null, { done: 0, total: 2 }), 4).value, '')
})

test('ожидание ответа модуля', () => {
  const filling = pathSegments(null, 1, 'autofill', null)
  assert.deepEqual([seg(filling, 2).state, seg(filling, 2).value], ['busy', 'ищем категорию'])
  assert.deepEqual([seg(filling, 4).state, seg(filling, 4).value], ['busy', 'заполняем характеристики'])
  const asking = state({ question: { characteristic: 'Позиция КТРУ', options: [] }, remaining: 2 })
  const answering = pathSegments(out(asking), 2, 'answer', null)
  assert.deepEqual([seg(answering, 2).state, seg(answering, 2).value], ['busy', 'проверяем ответ'])
})
