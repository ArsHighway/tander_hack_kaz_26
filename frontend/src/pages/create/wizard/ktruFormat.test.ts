import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ktruValue } from './ktruFormat.ts'

test('корзина «от и до» читается порогами', () => {
  assert.equal(ktruValue('>=80 ... <90'), '≥ 80 и < 90')
})

test('корзина с многоточием и лишними пробелами', () => {
  assert.equal(ktruValue('>= 80  …  < 90'), '≥ 80 и < 90')
})

test('один порог', () => {
  assert.equal(ktruValue('>=80'), '≥ 80')
  assert.equal(ktruValue('<90'), '< 90')
  assert.equal(ktruValue('<=5,5'), '≤ 5,5')
})

test('обычное значение и число не меняются', () => {
  assert.equal(ktruValue('A4'), 'A4')
  assert.equal(ktruValue('500'), '500')
  assert.equal(ktruValue('Белая'), 'Белая')
})

test('отрицательные пороги (температуры)', () => {
  assert.equal(ktruValue('>=-20 ... <-10'), '≥ -20 и < -10')
  assert.equal(ktruValue('<-30'), '< -30')
})
