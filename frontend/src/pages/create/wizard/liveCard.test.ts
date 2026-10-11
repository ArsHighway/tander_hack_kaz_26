import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ktruText } from '../chainView.ts'
import { readiness } from './groups.ts'
import { changedRows, liveCard, type LiveCardView } from './liveCard.ts'
import { candidate, chain, field, out, state } from './testing.ts'

const span = { field: 'name' as const, start: 0, end: 2, text: 'А4' }
/** Категория определена: строки и готовность карточки считаются только у неё. */
const decided = out(state({ final_code: '01.15.02.01' }))
/** Категория ещё выбирается: модуль задал вопрос шага 2. */
const choosing = out(state({ question: { characteristic: 'Вид товара', text: '', options: [] } as never }))

const fields = [
  field({ name: 'Плотность', required: true, value: '80', initial: '80', unit: 'г/м2', unitName: 'Грамм на квадратный метр', source: span }),
  field({ name: 'Марка', value: 'C' }),
  field({ name: 'Производитель', group: 'general', value: 'Туринский ЦБЗ' }),
  field({ name: 'Формат', required: true }),
]

test('строки: «Общие сведения», обязательные, затем необязательные со значением; пустых необязательных нет', () => {
  // у товара без обязательных полей найденные значения в карточке не показывались
  const view = liveCard(decided, [...fields, field({ name: 'Вес' })], null, 'Бумага')
  assert.deepEqual(
    view.rows.map((r) => r.name),
    ['Производитель', 'Плотность', 'Формат', 'Марка'],
  )
})

test('значение из текста без правки — fromText; правка снимает отметку', () => {
  const view = liveCard(decided, fields, null, 'Бумага')
  assert.equal(view.rows.find((r) => r.name === 'Плотность')?.fromText, true)
  const edited = fields.map((f) => (f.name === 'Плотность' ? { ...f, value: '90' } : f))
  assert.equal(liveCard(decided, edited, null, 'Бумага').rows.find((r) => r.name === 'Плотность')?.fromText, false)
})

test('значение, единица и состояние строки', () => {
  const rows = liveCard(decided, fields, null, 'Бумага').rows
  assert.deepEqual(rows.find((r) => r.name === 'Плотность'), {
    name: 'Плотность',
    value: '80',
    unit: 'г/м2',
    fromText: true,
    origin: null,
    state: 'filled',
    status: 'подтверждено',
    provenance: 'Из текста: «А4»',
    relation: null,
  })
  assert.deepEqual(rows.find((r) => r.name === 'Формат'), { name: 'Формат', value: null, unit: null, fromText: false, origin: null, state: 'empty', status: 'не заполнено', provenance: '', relation: null })
})

test('непринятая подсказка в живую карточку не идёт, принятая — идёт', () => {
  // в карточке только принятое поставщиком
  const pending = field({ name: 'Белизна', required: true, hint: '146', method: 'similar' })
  const optional = field({ name: 'Вес', hint: '0,383', method: 'twin' })
  const rows = liveCard(decided, [pending, optional], null, 'Бумага').rows
  assert.deepEqual(rows.map((r) => [r.name, r.state]), [['Белизна', 'empty']])
  const accepted = liveCard(decided, [{ ...pending, value: '146' }, { ...optional, value: '0,383' }], null, 'Бумага').rows
  assert.deepEqual(accepted.map((r) => [r.name, r.value, r.fromText]), [['Белизна', '146', false], ['Вес', '0,383', false]])
})

test('пороги показываются в формате КТРУ', () => {
  const rows = liveCard(decided, [field({ name: 'Белизна', required: true, value: '>=146 ... <=150' })], null, 'Бумага').rows
  assert.equal(rows[0]?.value, '≥ 146 и ≤ 150')
})

test('характеристика текущего вопроса — «выберите слева», и пустая, и заполненная', () => {
  const rows = liveCard(decided, fields, 'Формат', 'Бумага').rows
  assert.equal(rows.find((r) => r.name === 'Формат')?.state, 'asking')
  assert.equal(rows.find((r) => r.name === 'Плотность')?.state, 'filled')
  assert.equal(liveCard(decided, fields, 'Плотность', 'Бумага').rows.find((r) => r.name === 'Плотность')?.state, 'asking')
})

test('у пустой строки нет единицы', () => {
  const rows = liveCard(decided, [field({ name: 'Плотность', required: true, unit: 'г/м2' })], null, 'Бумага').rows
  assert.deepEqual(rows[0] && { value: rows[0].value, unit: rows[0].unit }, { value: null, unit: null })
})

test('КТРУ: вопрос второго этапа — ждёт ответа', () => {
  const asking = state({ question: { characteristic: 'Цвет', text: 'Цвет?', options: [] } as never, remaining: 3 })
  const view = liveCard(out(state({ final_code: '01.15.02.01' }), { chain: chain({ ktru: asking }) }), fields, null, 'Бумага')
  assert.deepEqual(view.codes.ktru, { kind: 'pending' })
})

test('КТРУ: до определения категории — ждёт ответа', () => {
  assert.deepEqual(liveCard(choosing, fields, null, 'Бумага').codes.ktru, { kind: 'pending' })
})

test('КТРУ: позиции нет — текст из ktruText', () => {
  for (const missing of ['no_links', 'not_loaded'] as const) {
    const c = chain({ ktru: null, ktru_missing: missing })
    const view = liveCard(out(state({ final_code: '01.15.02.01' }), { chain: c }), fields, null, 'Бумага')
    assert.deepEqual(view.codes.ktru, { kind: 'missing', text: ktruText(c).value })
  }
})

test('КТРУ: ни одна позиция не подошла — текст из ktruText', () => {
  const c = chain({ ktru: state({ candidates: [candidate('17.12.14.129-00000017', 'Бумага')], steps: ['Ни одна не подходит'] }) })
  const view = liveCard(out(state({ final_code: '01.15.02.01' }), { chain: c }), fields, null, 'Бумага')
  assert.deepEqual(view.codes.ktru, { kind: 'missing', text: ktruText(c).value })
})

test('КТРУ: определён — код; КПГЗ, ОКПД2, путь и наименование из ответа', () => {
  const c = chain({
    path: ['Канцелярские товары', 'Бумага', 'Бумага для офисной техники'],
    ktru: state({ final_code: '17.12.14.129-00000017', candidates: [candidate('17.12.14.129-00000017', 'Бумага')] }),
  })
  const view = liveCard(out(state({ final_code: '01.15.02.01' }), { chain: c }), fields, null, 'Бумага А4')
  assert.deepEqual(view.codes, { kpgz: '01.15.02.01', okpd2: '17.12.14.129', ktru: { kind: 'code', code: '17.12.14.129-00000017' } })
  assert.deepEqual(view.path, ['Канцелярские товары', 'Бумага', 'Бумага для офисной техники'])
  assert.equal(view.name, 'Бумага А4')
  assert.equal(view.category, true)
})

test('без цепочки кодов нет, путь пуст', () => {
  const view = liveCard(choosing, fields, null, 'Бумага')
  assert.deepEqual(view.codes, { kpgz: null, okpd2: null, ktru: { kind: 'pending' } })
  assert.deepEqual(view.path, [])
  assert.equal(view.category, false)
})

test('до выбора категории ни строк, ни готовности: поля ведущего варианта не показываются', () => {
  const view = liveCard(choosing, fields, null, 'Бумага')
  assert.deepEqual(view.rows, [])
  assert.deepEqual(view.ready, { done: 0, total: 0 })
})

test('категория не найдена: коды не «ждут ответа» — вопроса нет', () => {
  const view = liveCard(out(state({ steps: ['Похожие позиции: …'] })), fields, null, 'Ремонт автомобиля')
  assert.equal(view.notFound, true)
  assert.deepEqual(view.codes.ktru, { kind: 'missing', text: 'нет' })
  assert.equal(liveCard(out(state({ question: { characteristic: 'Вид', text: '', options: [] } as never })), fields, null, 'x').notFound, false)
})

test('«Готово N из M» равно readiness', () => {
  const r = readiness(fields)
  assert.deepEqual(liveCard(decided, fields, null, 'Бумага').ready, { done: r.done, total: r.total })
})

test('живая карточка показывает те же замечания, источник ответа и новую характеристику', () => {
  const view = liveCard(decided, [
    field({ name: 'Высота', origin: 'ktru', value: '900', initial: '900', required: true, unit: 'мм', method: 'answer', ktruFit: 'out_of_range' }),
    field({ name: 'Ручка', origin: 'new', value: 'да', initial: 'да', extra: true }),
  ], null, 'Стол', [], { Высота: 3 })
  assert.deepEqual(view.rows.map((r) => r.status), ['требует проверки', 'предложено'])
  assert.equal(view.rows[0].provenance, 'Из вашего ответа на шаге 3')
  assert.equal(view.rows[1].relation, 'Предложение: вне КТРУ, подтверждает модератор')
  assert.deepEqual(view.ready, { done: 1, total: 1 })
})

const view = (rows: [string, string | null][]): LiveCardView => ({
  category: true,
  notFound: false,
  path: [],
  name: '',
  codes: { kpgz: null, okpd2: null, ktru: { kind: 'pending' } },
  rows: rows.map(([name, value]) => ({ name, value, unit: null, fromText: false, origin: null, state: value ? 'filled' : 'empty' })),
  ready: { done: 0, total: 0 },
})

test('changedRows: первая карточка ничего не подсвечивает', () => {
  assert.deepEqual(changedRows(null, view([['Формат', 'A4']])), new Set())
})

test('changedRows: только строки с новым значением', () => {
  const prev = view([
    ['Формат', 'A4'],
    ['Плотность', null],
    ['Белизна', '146'],
  ])
  const next = view([
    ['Формат', 'A4'],
    ['Плотность', '80'],
    ['Белизна', '150'],
    ['Цвет', 'белый'],
    ['Марка', null],
  ])
  assert.deepEqual(changedRows(prev, next), new Set(['Плотность', 'Белизна', 'Цвет']))
})

test('changedRows: значение стёрто — строка тоже изменилась', () => {
  assert.deepEqual(changedRows(view([['Формат', 'A4']]), view([['Формат', null]])), new Set(['Формат']))
})
