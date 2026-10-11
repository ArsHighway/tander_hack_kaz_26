import assert from 'node:assert/strict'
import { test } from 'node:test'
import { carriedAnswer, fieldLabel, highlightSegments, parsePrefill, revisePrefill } from './createModel.ts'
import { chain, out, state } from './wizard/testing.ts'

test('фрагмент значения выделяется маркером, остальное — как есть', () => {
  assert.deepEqual(highlightSegments('Масса 80 г', [{ start: 6, end: 8, key: 'm' }]), [
    { text: 'Масса ', mark: false, key: null },
    { text: '80', mark: true, key: 'm' },
    { text: ' г', mark: false, key: null },
  ])
})

test('пересекающийся фрагмент не ломает текст: остаётся первый', () => {
  const segs = highlightSegments('абвгдеёжз', [
    { start: 0, end: 5, key: 'a' },
    { start: 3, end: 8, key: 'b' },
  ])
  assert.equal(segs.map((s) => s.text).join(''), 'абвгдеёжз')
  assert.deepEqual(segs.filter((s) => s.mark).map((s) => s.key), ['a'])
})

test('соседние фрагменты — два отдельных выделения', () => {
  const segs = highlightSegments('A4B5', [
    { start: 0, end: 2, key: 'a' },
    { start: 2, end: 4, key: 'b' },
  ])
  assert.deepEqual(segs, [
    { text: 'A4', mark: true, key: 'a' },
    { text: 'B5', mark: true, key: 'b' },
  ])
})

test('фрагмент за границей текста отбрасывается', () => {
  assert.deepEqual(highlightSegments('abc', [{ start: 5, end: 9, key: 'x' }]), [{ text: 'abc', mark: false, key: null }])
})

test('поле кода КТРУ подписано словами', () => {
  assert.equal(fieldLabel('ktru_code'), 'Код КТРУ')
  assert.equal(fieldLabel('characteristics.Формат'), 'Формат')
})

test('префилл из массовой загрузки: описание и характеристики файла', () => {
  const raw = JSON.stringify({ name: 'Бумага', description: 'Неон', characteristics: [{ name: 'Формат', value: 'А4', unit: null }] })
  assert.deepEqual(parsePrefill(raw), {
    name: 'Бумага',
    description: 'Неон',
    characteristics: [{ name: 'Формат', value: 'А4', unit: null }],
    revise: null,
    photoId: null,
  })
})

test('префилл исправления заявки несёт её номер и текст возврата', () => {
  const raw = JSON.stringify({ name: 'Бумага', description: '', characteristics: [], revise: { id: 7, note: 'Уточните цвет.' } })
  assert.deepEqual(parsePrefill(raw)?.revise, { id: 7, note: 'Уточните цвет.' })
})

test('префилл старого вида и мусор: только наименование или ничего', () => {
  assert.deepEqual(parsePrefill(JSON.stringify({ name: 'Бумага', description: '' })), {
    name: 'Бумага',
    description: '',
    characteristics: [],
    revise: null,
    photoId: null,
  })
  assert.equal(parsePrefill(null), null)
  assert.equal(parsePrefill('не json'), null)
  assert.equal(parsePrefill(JSON.stringify({ description: 'без наименования' })), null)
  assert.deepEqual(parsePrefill(JSON.stringify({ name: 'Бумага', characteristics: [{ name: 'Формат' }, 5] }))?.characteristics, [])
})

test('возвращённая заявка переходит в форму через тот же префилл без потерь', () => {
  const sub = {
    id: 3,
    created_at: '2026-10-05T10:00:00Z',
    card: { name: 'Бумага', description: 'Неон', ktru_code: '17.12', characteristics: [{ name: 'Формат', value: 'А4' }] },
    accepted: [],
    status: 'returned' as const,
    decision_note: 'Уточните цвет.',
  }
  const p = revisePrefill(sub)
  assert.deepEqual(parsePrefill(JSON.stringify(p)), {
    name: 'Бумага',
    description: 'Неон',
    characteristics: [{ name: 'Формат', value: 'А4', unit: null }],
    revise: { id: 3, note: 'Уточните цвет.' },
    photoId: null,
  })
})

test('фото возвращённой заявки переходит в форму: исправленная заявка ссылается на то же фото', () => {
  const sub = {
    id: 4,
    created_at: '2026-10-07T10:00:00Z',
    card: { name: 'Бумага', description: '', ktru_code: null, characteristics: [], photo_id: 12 },
    accepted: [],
    status: 'returned' as const,
    decision_note: null,
  }
  assert.equal(parsePrefill(JSON.stringify(revisePrefill(sub)))?.photoId, 12)
})

test('номер фото в префилле только числом', () => {
  assert.equal(parsePrefill(JSON.stringify({ name: 'Бумага', photoId: '12' }))?.photoId, null)
})

const question = (characteristic: string, values: string[]) => ({
  characteristic,
  options: [...values, '__nothing__'].map((value) => ({ value, remaining: 1, cuts: 1, codes: [] })),
})

test('заявка уже отвечала на вопрос КТРУ: тот же вариант, без учёта регистра', () => {
  const o = out(state({ final_code: '01.11.01.04.05.01' }), {
    chain: chain({ ktru: state({ remaining: 3, question: question('Тип управления', ['Сенсорный', 'Рычажный']) }) }),
  })
  const chars = [{ name: 'тип управления', value: 'рычажный', unit: null }]
  assert.deepEqual(carriedAnswer(o, chars, new Set()), { characteristic: 'Тип управления', value: 'Рычажный', stage: 'ktru' })
  // уже пробовали — второй раз не отвечаем: человек мог сам выбрать другое
  assert.equal(carriedAnswer(o, chars, new Set(['ktru:Тип управления'])), null)
})

test('вопрос категории — тоже из заявки; подтверждение позиции и чужое значение — нет', () => {
  const cat = out(state({ remaining: 2, question: question('Формат', ['A4', 'A3']) }))
  assert.deepEqual(carriedAnswer(cat, [{ name: 'Формат', value: 'A4', unit: null }], new Set()), {
    characteristic: 'Формат',
    value: 'A4',
    stage: 'category',
  })
  assert.equal(carriedAnswer(cat, [{ name: 'Формат', value: 'A5', unit: null }], new Set()), null)
  const confirm = out(state({ remaining: 2, question: question('Позиция КТРУ', ['01.15.02.01']) }))
  assert.equal(carriedAnswer(confirm, [{ name: 'Позиция КТРУ', value: '01.15.02.01', unit: null }], new Set()), null)
})

test('префилл строки пакета помнит пакет и строку: заявка попадёт в «Массовую загрузку» в «Моих заявках»', () => {
  const raw = JSON.stringify({ name: 'Тумба', description: '', characteristics: [], revise: null, photoId: null, batch: { job_id: 'j1', row: 4, filename: 'товары.xlsx' } })
  assert.deepEqual(parsePrefill(raw)?.batch, { job_id: 'j1', row: 4, filename: 'товары.xlsx' })
  const broken = JSON.stringify({ name: 'Тумба', batch: { job_id: 7 } })
  assert.equal(parsePrefill(broken)?.batch, undefined)
})
