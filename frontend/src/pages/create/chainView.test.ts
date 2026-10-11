import assert from 'node:assert/strict'
import { test } from 'node:test'
import { categoryLabel, ktruText, type Chain } from './chainView.ts'

const base: Chain = {
  category: '01.15.02.01',
  category_name: 'Бумага для офисной техники',
  kpgz: '01.15.02.01',
  okpd2: '17.12.14.129',
  spgz: [],
  ktru_linked: 2,
  ktru: null,
  path: [],
  ktru_merged: [],
}

const state = {
  candidates: [
    { code: 'K1', name: 'Бумага белая', score: 0, why: [], similar: null, examples: [], matched: [], missing: [], conflicts: [] },
    { code: 'K2', name: 'Бумага цветная', score: 0, why: [], similar: null, examples: [], matched: [], missing: [], conflicts: [] },
  ],
  remaining: 2,
  final_code: null,
  question: null,
  steps: [],
  trail: [],
}

test('подпись категории зависит от справочника', () => {
  assert.equal(categoryLabel(base), 'Категория портала (КПГЗ)')
  assert.equal(categoryLabel({ ...base, kpgz: null }), 'Позиция КТРУ')
  assert.equal(categoryLabel(null), 'Позиция КТРУ')
})

test('поле КТРУ: определена, уточняется, не загружен, нет у категории', () => {
  assert.equal(ktruText({ ...base, ktru: { ...state, final_code: 'K2', remaining: 1 } }).value, 'K2  Бумага цветная')
  const asking = ktruText({ ...base, ktru: { ...state, question: { characteristic: 'Цветность', options: [] } } })
  assert.equal(asking.value, 'Уточняется: подходят 2')
})

test('поле КТРУ: у категории нет ссылок на КТРУ', () => {
  const none = ktruText({ ...base, ktru_linked: 0, ktru_missing: 'no_links' })
  assert.equal(none.value, 'В КТРУ нет')
  assert.equal(
    none.helper,
    'Позиции КТРУ для этого товара нет. Код КТРУ останется пустым, характеристики оформлены в формате КТРУ',
  )
})

test('поле КТРУ: привязанных позиций нет в справочнике', () => {
  const missing = (n: number) => ktruText({ ...base, ktru_linked: n, ktru_missing: 'not_loaded' })
  assert.equal(missing(3).value, 'Нет в справочнике')
  assert.equal(missing(3).helper, 'Справочник КТРУ не содержит 3 привязанных позиций')
  assert.equal(missing(1).helper, 'Справочник КТРУ не содержит 1 привязанной позиции')
  assert.equal(missing(21).helper, 'Справочник КТРУ не содержит 21 привязанной позиции')
  assert.equal(missing(11).helper, 'Справочник КТРУ не содержит 11 привязанных позиций')
})
