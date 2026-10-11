import assert from 'node:assert/strict'
import { test } from 'node:test'
import { pendingFile } from './pendingFile.ts'

test('файл из мастера передаётся массовой загрузке один раз', () => {
  const file = new File(['Наименование;Количество'], 'goods.docx')
  pendingFile.put(file)
  assert.equal(pendingFile.take(), file)
  assert.equal(pendingFile.take(), null)
})

test('посмотреть файл можно сколько угодно раз, забрать — один', () => {
  const file = new File(['x'], 'goods.xlsx')
  pendingFile.put(file)
  assert.equal(pendingFile.peek(), file)
  assert.equal(pendingFile.peek(), file)
  assert.equal(pendingFile.take(), file)
  assert.equal(pendingFile.peek(), null)
})

test('без файла — null', () => {
  assert.equal(pendingFile.take(), null)
})
