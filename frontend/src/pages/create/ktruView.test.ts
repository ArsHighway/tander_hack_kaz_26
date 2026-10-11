import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { KtruState } from '../../api/v1.ts'
import { CONFIRM, NOTHING_FITS, OTHER, optionLabel, questionTitle } from './ktruView.ts'

const state = (over: Partial<KtruState>): KtruState => ({
  candidates: [{ code: 'K-1', name: 'Батарейка щелочная', score: 1, why: [], examples: [], matched: [], missing: [], conflicts: [] }],
  remaining: 1,
  question: null,
  final_code: null,
  steps: [],
  trail: [],
  ...over,
})

test('вариант «ничего не подходит» назван словами, а не служебным значением', () => {
  const s = state({})
  assert.deepEqual(optionLabel(s, CONFIRM, { value: NOTHING_FITS, remaining: 0, cuts: 1, codes: [] }), { text: 'Ни одна не подходит', hint: null })
  assert.deepEqual(optionLabel(s, 'Цвет', { value: NOTHING_FITS, remaining: 0, cuts: 3, codes: [] }), { text: 'Ни одна не подходит', hint: null })
})

test('вопрос подтверждения показывает название позиции и код', () => {
  const s = state({ question: { characteristic: CONFIRM, options: [] } })
  assert.equal(questionTitle(CONFIRM), 'Подтвердите позицию КТРУ')
  assert.deepEqual(optionLabel(s, CONFIRM, { value: 'K-1', remaining: 1, cuts: 0, codes: [] }), { text: 'Батарейка щелочная', hint: 'K-1' })
})

test('«другое значение» — словами и без счёта отсечённых', () => {
  const s = state({ remaining: 2 })
  assert.deepEqual(optionLabel(s, 'Цвет', { value: OTHER, remaining: 2, cuts: 0, codes: [] }), { text: 'Другое значение', hint: 'выбор не сузит, модуль спросит другое' })
})

test('обычный вариант: значение и сколько позиций останется', () => {
  const s = state({})
  assert.equal(questionTitle('Цвет'), 'Уточните «Цвет»')
  assert.deepEqual(optionLabel(s, 'Цвет', { value: 'Белый', remaining: 2, cuts: 1, codes: [] }), { text: 'Белый', hint: 'останется 2, отсечёт 1' })
  assert.deepEqual(optionLabel(s, 'Цвет', { value: '', remaining: 1, cuts: 2, codes: [] }), { text: 'Нет такой характеристики', hint: 'останется 1, отсечёт 2' })
})

test('вопрос второго этапа подписан как вопрос о позиции КТРУ', () => {
  assert.equal(questionTitle('Цветность', 'ktru'), 'Позиция КТРУ: уточните «Цветность»')
  assert.equal(questionTitle('Цветность'), 'Уточните «Цветность»')
})
