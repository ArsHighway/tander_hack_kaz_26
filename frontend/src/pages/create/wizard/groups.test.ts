import assert from 'node:assert/strict'
import { test } from 'node:test'
import { groupFields, readiness, visibleOptional } from './groups.ts'
import { field } from './testing.ts'

test('готовность считает только обязательные характеристики КТРУ', () => {
  const fields = [
    field({ name: 'Плотность', value: '80', required: true, origin: 'ktru' }),
    field({ name: 'Формат', value: 'A4', required: true, origin: 'ktru' }),
    field({ name: 'Белизна', value: '', required: true, origin: 'ktru' }),
    field({ name: 'Цвет', required: true, origin: 'portal' }),
    field({ name: 'Марка', value: 'C', required: false }),
    field({ name: 'Производитель', value: 'Туринский ЦБЗ', group: 'general' }),
  ]
  assert.deepEqual(readiness(fields), { done: 2, total: 3, left: ['Белизна'] })
})

test('пробелы — не значение', () => {
  assert.deepEqual(readiness([field({ name: 'Формат', value: '  ', required: true, origin: 'ktru' })]), { done: 0, total: 1, left: ['Формат'] })
})

test('обязательное поле из КТРУ учитывается и в общих сведениях; портальное поле не учитывается', () => {
  assert.deepEqual(readiness([
    field({ name: 'Страна', group: 'general', origin: 'ktru', required: true }),
    field({ name: 'Производитель', group: 'general', origin: 'portal', required: true }),
  ]), { done: 0, total: 1, left: ['Страна'] })
})

test('группы шага 4 определяются происхождением, включая свойства extra', () => {
  const maker = field({ name: 'Производитель', group: 'general', required: true })
  const format = field({ name: 'Формат', required: true, origin: 'ktru' })
  const mark = field({ name: 'Марка' })
  const loose = field({ name: 'Поле без спецификации', origin: 'new', extra: true })
  const groups = groupFields([format, maker, mark, loose])
  assert.deepEqual(groups.general.map((f) => f.name), ['Производитель'])
  assert.deepEqual(groups.ktru.map((f) => f.name), ['Формат'])
  assert.deepEqual(groups.portal.map((f) => f.name), ['Марка'])
  assert.deepEqual(groups.new.map((f) => f.name), ['Поле без спецификации'])
})

test('свёрнутые необязательные: поле с замечанием видно всегда, остальные — за «Показать ещё»', () => {
  const optional = [field({ name: 'Марка' }), field({ name: 'Цвет' }), field({ name: 'Вес' })]
  assert.deepEqual(visibleOptional(optional, new Set(['Цвет']), false), { shown: ['Цвет'], hidden: 2 })
  assert.deepEqual(visibleOptional(optional, new Set(['Цвет']), true), { shown: ['Марка', 'Цвет', 'Вес'], hidden: 0 })
  assert.deepEqual(visibleOptional(optional, new Set(), false), { shown: [], hidden: 3 })
})

test('необязательное с найденным значением не прячется за «Показать ещё»', () => {
  // значения, найденные в тексте, уходили в свёрнутые — человек их не видел
  const optional = [field({ name: 'Марка' }), field({ name: 'Плотность', value: '80' }), field({ name: 'Вес' })]
  assert.deepEqual(visibleOptional(optional, new Set(), false), { shown: ['Плотность'], hidden: 2 })
})

test('необязательное с подсказкой видно: её принимают или нет глазами', () => {
  const optional = [field({ name: 'Марка' }), field({ name: 'Вес', hint: '0,383', method: 'twin' })]
  assert.deepEqual(visibleOptional(optional, new Set(), false), { shown: ['Вес'], hidden: 1 })
})

test('поле со значением от модуля не исчезает, пока человек его стирает', () => {
  // стёр найденное значение, чтобы ввести своё, — поле пропадало вместе с фокусом
  const optional = [field({ name: 'Тип управления', value: '', initial: 'Керамический картридж 35 мм' }), field({ name: 'Вес' })]
  assert.deepEqual(visibleOptional(optional, new Set(), false), { shown: ['Тип управления'], hidden: 1 })
})
