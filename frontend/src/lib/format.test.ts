import assert from 'node:assert/strict'
import { test } from 'node:test'
import { plural } from './format.ts'

test('склонение по числу', () => {
  const forms = [1, 2, 5, 11, 21, 22, 112].map((n) => plural(n, 'позиция', 'позиции', 'позиций'))
  assert.deepEqual(forms, ['позиция', 'позиции', 'позиций', 'позиций', 'позиция', 'позиции', 'позиций'])
})
