import assert from 'node:assert/strict'
import { test } from 'node:test'
import { allowedRange, rangeLabel } from './allowedValues.ts'

test('диапазон читается, пустой и перепутанный — нет', () => {
  assert.deepEqual(allowedRange({ min: 90, max: 175 }), { min: 90, max: 175 })
  assert.equal(allowedRange({ min: null, max: null }), null)
  assert.equal(allowedRange(undefined), null)
  assert.equal(allowedRange({ min: 10, max: 1 }), null)
  assert.deepEqual(allowedRange({ max: 120 }), { min: null, max: 120 })
})

test('подписи диапазона', () => {
  assert.equal(rangeLabel({ min: 90, max: 175 }, '%'), '90–175 %')
  assert.equal(rangeLabel({ min: 8 }, 'мкм'), 'от 8 мкм')
  assert.equal(rangeLabel({ max: 120 }, 'мкм'), 'до 120 мкм')
  assert.equal(rangeLabel(null, '%'), null)
})
