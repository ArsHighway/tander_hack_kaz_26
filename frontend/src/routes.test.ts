import assert from 'node:assert/strict'
import { test } from 'node:test'
import { NAV_ASIDE, ROLES, navHref, resolveRoute, roleOf } from './routes.ts'

test('корень — форма создания СТЕ', () => {
  assert.equal(resolveRoute('/'), 'create')
})

test('хвостовой слэш не меняет экран', () => {
  assert.equal(resolveRoute('/batch/'), 'batch')
  assert.equal(resolveRoute('/moderation/'), 'moderation')
})

test('каждый экран кейса открывается своим путём', () => {
  assert.equal(resolveRoute('/batch'), 'batch')
  assert.equal(resolveRoute('/my'), 'mine')
  assert.equal(resolveRoute('/moderation'), 'moderation')
  assert.equal(resolveRoute('/metrics'), 'metrics')
  assert.equal(resolveRoute('/developers'), 'developers')
})

test('служебных страниц и прототипа больше нет', () => {
  assert.equal(resolveRoute('/status'), 'not-found')
  assert.equal(resolveRoute('/ui'), 'not-found')
  assert.equal(resolveRoute('/preview'), 'not-found')
})

test('неизвестный путь — not-found, а не форма', () => {
  assert.equal(resolveRoute('/nope'), 'not-found')
})

test('роли и их разделы: поставщик заводит карточки, модератор проверяет', () => {
  assert.deepEqual(
    ROLES.map((r) => [r.label, r.items.map((i) => i.href)]),
    [
      ['Поставщик', ['/', '/batch', '/my']],
      ['Модератор', ['/moderation']],
    ],
  )
  assert.deepEqual(
    NAV_ASIDE.map((i) => i.href),
    ['/metrics', '/developers'],
  )
})

test('роль экрана следует из пути, на общих страницах остаётся прежней', () => {
  assert.equal(roleOf('create', 'moderator'), 'supplier')
  assert.equal(roleOf('batch', 'moderator'), 'supplier')
  assert.equal(roleOf('mine', 'moderator'), 'supplier')
  assert.equal(roleOf('moderation', 'supplier'), 'moderator')
  assert.equal(roleOf('metrics', 'moderator'), 'moderator')
  assert.equal(roleOf('not-found', 'supplier'), 'supplier')
})

test('подсвечивается раздел текущего экрана, у 404 — никакой', () => {
  assert.equal(navHref('batch'), '/batch')
  assert.equal(navHref('not-found'), null)
})
