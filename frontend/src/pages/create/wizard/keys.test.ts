import assert from 'node:assert/strict'
import { test } from 'node:test'
import { optionIndex, shouldHandleKey } from './keys.ts'

const el = (tagName: string, isContentEditable = false, type?: string) =>
  ({ tagName, isContentEditable, type }) as unknown as EventTarget

test('на радиокнопке варианта клавиши работают: это не ввод текста', () => {
  assert.equal(shouldHandleKey(el('INPUT', false, 'radio')), true)
  assert.equal(shouldHandleKey(el('INPUT', false, 'text')), false)
})

test('номер варианта по цифре: только 1…6 и только в пределах списка', () => {
  assert.equal(optionIndex('1', 3), 0)
  assert.equal(optionIndex('3', 3), 2)
  assert.equal(optionIndex('4', 3), null)
  assert.equal(optionIndex('7', 9), null)
  assert.equal(optionIndex('0', 3), null)
  assert.equal(optionIndex('a', 3), null)
})

test('в поле ввода цифры и Enter — текст, а не выбор варианта', () => {
  assert.equal(shouldHandleKey(el('INPUT')), false)
  assert.equal(shouldHandleKey(el('TEXTAREA')), false)
  assert.equal(shouldHandleKey(el('SELECT')), false)
  assert.equal(shouldHandleKey(el('DIV', true)), false)
})

test('на кнопке и странице клавиши выбирают вариант', () => {
  assert.equal(shouldHandleKey(el('BUTTON')), true)
  assert.equal(shouldHandleKey(el('BODY')), true)
  assert.equal(shouldHandleKey(null), true)
})

const scope = (...inside: EventTarget[]) => ({ contains: (node: unknown) => inside.includes(node as EventTarget) })

test('клавиши — только внутри вопроса или со страницы: в карточке и тексте у Enter свой смысл', () => {
  const radio = el('INPUT', false, 'radio')
  const mark = el('MARK')
  const body = el('BODY')
  assert.equal(shouldHandleKey(radio, scope(radio)), true)
  assert.equal(shouldHandleKey(mark, scope(radio)), false)
  assert.equal(shouldHandleKey(el('A'), scope(radio)), false)
  assert.equal(shouldHandleKey(body, scope(radio)), true)
})
