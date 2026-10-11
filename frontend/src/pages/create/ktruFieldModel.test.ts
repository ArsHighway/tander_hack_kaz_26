import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { Issue } from '../../api/v1.ts'
import { acceptHints, fromText } from './formModel.ts'
import { allowedKtru, fieldStatus, ktruRelation, numericField, valueSource } from './ktruFieldModel.ts'
import { field } from './wizard/testing.ts'

const span = { field: 'description' as const, start: 0, end: 5, text: 'глина' }
const warning: Issue = { field: 'characteristics.Материал', kind: 'not_in_list', severity: 'warn', message: 'Нет в КТРУ', why: '', rule: '', fix: null }

test('корзины требуют числа, а качественный перечень остаётся выбором даже с числовыми вариантами', () => {
  for (const options of [['>=400 ... <=500'], ['600–800'], ['600-800'], ['[600;800]'], ['не более 800'], ['600', '800']]) {
    assert.equal(numericField(field({ origin: 'ktru', options })), true, options.join('; '))
  }
  assert.equal(numericField(field({ kind: 'quantitative' })), true)
  assert.equal(numericField(field({ kind: 'qualitative', options: ['42', '44'] })), false)
  assert.equal(numericField(field({ options: ['Глиняный', 'Металлический'] })), false)
})

test('ответ корзиной предлагает диапазон, не подставляет его как точное значение товара', () => {
  const f = field({ origin: 'ktru', kind: 'quantitative', unit: 'мм', method: 'answer', ktruMatch: '>=400 ... <=500' })
  assert.equal(f.value, '')
  assert.equal(fieldStatus(f), 'предложено')
  assert.equal(ktruRelation(f)?.text, 'КТРУ: ≥ 400 и ≤ 500 мм. Укажите точное значение')
})

test('пустое обязательное поле с замечанием остаётся незаполненным; варианты и подсказки — предложения', () => {
  assert.equal(fieldStatus(field({ required: true }), [warning]), 'не заполнено')
  assert.equal(fieldStatus(field({ origin: 'ktru', options: ['А3', 'А4'] }), [warning]), 'предложено')
  const hint = field({ hint: '750', method: 'similar' })
  assert.equal(fieldStatus(hint), 'предложено')
  assert.equal(fieldStatus(acceptHints([hint])[0]), 'подтверждено')
  assert.equal(fieldStatus(field({ origin: 'new' })), 'не заполнено')
})

test('сопоставление вне КТРУ требует проверки даже без отдельного замечания', () => {
  for (const ktruFit of ['not_in_list', 'out_of_range'] as const) {
    assert.equal(fieldStatus(field({ value: 'глина', initial: 'глина', source: span, status: 'filled', ktruFit })), 'требует проверки')
  }
  assert.equal(fieldStatus(field({ value: '80', initial: '80', method: 'llm', status: 'filled' })), 'требует проверки')
  assert.equal(fieldStatus(field({ value: '80', initial: '80', source: span }), [warning]), 'требует проверки')
  assert.equal(fieldStatus(field({ value: '80', initial: '80', source: span }), [{ ...warning, severity: 'info' }]), 'подтверждено')
})

test('в заявке ручное заполненное значение без фрагмента текста сохраняет подтверждённый статус', () => {
  assert.equal(fieldStatus(field({ value: '80', initial: '80', status: 'filled' })), 'подтверждено')
  assert.equal(fieldStatus(field({ origin: 'new', value: 'да', initial: 'да', status: 'filled' })), 'предложено')
})

test('нормализация и попадание в диапазон объясняются отдельно от источника', () => {
  const normalized = field({ origin: 'ktru', value: 'Глиняный', initial: 'Глиняный', normalizedFrom: 'глина', source: span, ktruFit: 'normalized', ktruMatch: 'Глиняный' })
  assert.equal(ktruRelation(normalized)?.text, 'Приведено к формулировке КТРУ: «глина» → «Глиняный»')
  assert.equal(valueSource(normalized), 'Из текста: «глина»')
  assert.equal(ktruRelation(field({ origin: 'ktru', value: '450', initial: '450', unit: 'мм', ktruFit: 'in_range', ktruMatch: '>=400 ... <=500' }))?.text, 'КТРУ: ≥ 400 и ≤ 500 мм, подходит')
})

test('явный выбор того же значения снимает подсветку источника и прежний вердикт КТРУ', () => {
  const f = field({ origin: 'ktru', value: 'А4', initial: 'А4', source: span, status: 'conflict', ktruFit: 'not_in_list', options: ['А3', 'А4'], edited: true })
  assert.equal(fromText(f), false)
  assert.equal(fieldStatus(f), 'подтверждено')
  assert.equal(valueSource(f), 'Выбрано вами или введено вручную')
  assert.match(ktruRelation(f)?.text ?? '', /соответствие сверяется при проверке/)
  assert.equal(fieldStatus(f, [warning]), 'требует проверки')
})

test('после ручной правки не показывается корзина от прежнего значения', () => {
  const f = field({ origin: 'ktru', kind: 'quantitative', value: '900', initial: '450', ktruMatch: '>=400 ... <=500', options: ['400–500', '800–1000'], unit: 'мм' })
  assert.equal(allowedKtru(f), '400–500; 800–1000 мм')
  assert.doesNotMatch(ktruRelation(f)?.text ?? '', /подходит/)
})

test('много корзин КТРУ — размах, а не список', () => {
  const buckets = ['<500', '>=500 ... <600', '>=600 ... <700', '>=700 ... <800', '>=800 ... <900', '>=3300']
  assert.equal(allowedKtru(field({ origin: 'ktru', unit: 'мм', options: buckets })), 'диапазоны от 500 до 3300 мм')
})
