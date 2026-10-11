import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { Issue } from '../../api/v1.ts'
import type { FieldState } from '../create/formModel.ts'
import {
  LLM_EXTRA_S,
  cleanPending,
  issueText,
  llmHint,
  llmLabel,
  returnNote,
  selectedId,
  splitOutsideKtru,
} from './moderationModel.ts'

const issue = (over: Partial<Issue>): Issue => ({
  field: 'characteristics.Интенсивность цвета',
  kind: 'not_in_list',
  severity: 'block',
  message: 'Значения «Белый» нет в перечне позиции.',
  why: 'w',
  rule: 'r',
  fix: null,
  ...over,
})

test('текст возврата собирается из блокирующих замечаний, по номерам', () => {
  const note = returnNote([issue({}), issue({ severity: 'warn', message: 'не блокирует' }), issue({ field: 'name', message: 'В наименовании «500л».' })])
  assert.equal(note, '1. Интенсивность цвета: Значения «Белый» нет в перечне позиции.\n2. Наименование: В наименовании «500л».')
})

test('без блокирующих замечаний текст пустой', () => {
  assert.equal(returnNote([issue({ severity: 'warn' })]), '')
})

test('в один клик — только ждущие решения и чистые, со списком для подтверждения', () => {
  const clean = { clean: true, reasons: [], hinted: 0 }
  const dirty = { clean: false, reasons: ['«Вид материала каркаса: Дерево» не соответствует позиции КТРУ 31.01.10.000-00000005.'], hinted: 1 }
  const queue = [
    { id: 3, status: 'pending' as const, triage: clean, card: { name: 'Перчатки смотровые нитриловые' } },
    { id: 2, status: 'pending' as const, triage: dirty, card: { name: 'Болгарка Bosch круг 125' } },
    { id: 1, status: 'accepted' as const, triage: clean, card: { name: 'Бумага А4' } },
  ]
  assert.deepEqual(cleanPending(queue), [{ id: 3, name: 'Перчатки смотровые нитриловые' }])
})
test('замечание вне полей: подпись поля, если сообщение само его не называет', () => {
  // «Фото товара: Нет фото товара: …» — дубль; остаётся одна строка.
  const photo = issue({ field: 'photo', severity: 'warn', message: 'Нет фото товара: на портале изображение у товара обязательно.' })
  assert.equal(issueText(photo), 'Нет фото товара: на портале изображение у товара обязательно.')
  assert.equal(issueText(issue({ field: 'category_code', message: 'Не выбрана позиция КТРУ.' })), 'Категория: Не выбрана позиция КТРУ.')
})

test('выключатель модели: состояние в подписи, пояснение с добавкой времени', () => {
  assert.equal(llmLabel(true), 'Модель (LLM): вкл')
  assert.equal(llmLabel(false), 'Модель (LLM): выкл')
  const hint = llmHint(7)
  assert.match(hint, /ответ дольше \(до 7\s+с на товар\)\./)
  assert.match(hint, /Выключено: только правила и справочник КТРУ\./)
  assert.match(llmHint(), new RegExp(`до ${LLM_EXTRA_S}\\s+с на товар`))
})

test('новые свойства — в блок «вне КТРУ», остальные остаются в карточке в прежнем порядке', () => {
  const field = (name: string, origin: FieldState['origin']): FieldState => ({
    name,
    value: 'да',
    initial: 'да',
    unit: null,
    required: false,
    options: [],
    source: null,
    status: 'filled',
    normalizedFrom: null,
    method: 'rule',
    note: null,
    own: null,
    group: null,
    unitName: null,
    compact: null,
    origin,
    extra: origin === 'new',
    hint: null,
  })
  const fields = [field('Материал изделия', 'ktru'), field('Ручка противоскользящая', 'new'), field('Цвет фасада', 'portal')]
  const split = splitOutsideKtru(fields)
  assert.deepEqual(split.inCard.map((f) => f.name), ['Материал изделия', 'Цвет фасада'])
  assert.deepEqual(split.outside.map((f) => f.name), ['Ручка противоскользящая'])
  assert.deepEqual(splitOutsideKtru([]), { inCard: [], outside: [] })
})

test('номер заявки из адреса', () => {
  assert.equal(selectedId('?id=12'), 12)
  assert.equal(selectedId('?id=abc'), null)
  assert.equal(selectedId(''), null)
})
