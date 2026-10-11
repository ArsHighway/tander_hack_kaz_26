import assert from 'node:assert/strict'
import { test } from 'node:test'
import { DRAFT_KEY, loadDraft, saveDraft, type CreateDraft } from './createDraft.ts'

// Своё хранилище в тесте: в Node 24 глобального sessionStorage нет.
function memory() {
  const m = new Map<string, string>()
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
    m,
  }
}

const draft = (over: Partial<CreateDraft> = {}): CreateDraft => ({
  text: 'Бумага А4',
  name: '',
  description: '',
  carry: null,
  carried: [],
  out: null,
  position: null,
  fields: [],
  issues: [],
  edits: 0,
  visit: null,
  asked: {},
  answeredAt: {},
  reviewing: false,
  checked: null,
  photoId: null,
  submitted: null,
  ...over,
})

test('черновик переживает уход со страницы и читается тем же', () => {
  const s = memory()
  saveDraft(draft({ photoId: 7, answeredAt: { Цвет: 2 } }), s)
  const back = loadDraft(s)
  assert.equal(back?.text, 'Бумага А4')
  assert.equal(back?.photoId, 7)
  assert.deepEqual(back?.answeredAt, { Цвет: 2 })
})

test('пустой шаг 1 не хранится, «Начать заново» стирает черновик', () => {
  const s = memory()
  saveDraft(draft(), s)
  saveDraft(draft({ text: '  ' }), s)
  assert.equal(s.m.has(DRAFT_KEY), false)
  assert.equal(loadDraft(s), null)
})

test('черновик со старыми полями (подсказка стояла значением) не восстанавливается', () => {
  // подсказка — `hint`, а не `value`; старый черновик отдал бы её карточке
  const s = memory()
  s.setItem(DRAFT_KEY, JSON.stringify(draft({ fields: [{ name: 'Вес', value: '0,383', method: 'twin' } as never] })))
  assert.equal(loadDraft(s), null)
  s.setItem(DRAFT_KEY, JSON.stringify(draft({ fields: [{ name: 'Вес', value: '', method: 'twin', hint: '0,383' } as never] })))
  assert.equal(loadDraft(s)?.fields[0].hint, '0,383')
})

test('битый или чужой черновик — пустая форма, а не падение', () => {
  const s = memory()
  s.setItem(DRAFT_KEY, '{не json')
  assert.equal(loadDraft(s), null)
  s.setItem(DRAFT_KEY, JSON.stringify({ text: 1 }))
  assert.equal(loadDraft(s), null)
  const closed = { getItem: () => { throw new Error('closed') }, setItem: () => { throw new Error('closed') }, removeItem: () => {} }
  assert.equal(loadDraft(closed), null)
  saveDraft(draft(), closed) // не бросает
})
