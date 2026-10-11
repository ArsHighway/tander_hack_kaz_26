// Черновик мастера в sessionStorage вкладки: ушёл на «Мои заявки» или «Метрики» и вернулся — карточка на
// месте. Сессия модуля живёт в Redis своё время; истёкшую мастер уже умеет показать (`history.ts`).
import type { AutofillOut, Issue, Position, Submission } from '../../api/v1.ts'
import type { FieldState } from './formModel.ts'
import type { Prefill } from './createModel.ts'
import type { AskedSteps } from './wizard/history.ts'
import type { Step } from './wizard/stepOf.ts'

export const DRAFT_KEY = 'create:draft:v1'

export type CreateDraft = {
  text: string
  name: string
  description: string
  carry: Prefill | null
  carried: string[]
  carryAnswersDisabled?: boolean
  out: AutofillOut | null
  position: Position | null
  fields: FieldState[]
  issues: Issue[]
  edits: number
  visit: Step | null
  asked: AskedSteps
  answeredAt: Record<string, 2 | 3>
  reviewing: boolean
  checked: Issue[] | null | 'failed'
  photoId: number | null
  submitted: Submission | null
}

/** Нечего хранить: пустой шаг 1 без фото и без отправленной заявки. */
export function draftIsEmpty(d: CreateDraft): boolean {
  return !d.text.trim() && !d.out && !d.submitted && d.photoId === null && !d.carry
}

type Box = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

function box(storage?: Box): Box | null {
  if (storage) return storage
  try {
    return globalThis.sessionStorage ?? null
  } catch {
    return null // хранилище закрыто настройками браузера: черновик живёт, пока открыта страница
  }
}

/** Черновик или null: чужой, битый или старого формата — форма открывается пустой. */
export function loadDraft(storage?: Box): CreateDraft | null {
  try {
    const raw = box(storage)?.getItem(DRAFT_KEY)
    if (!raw) return null
    const d = JSON.parse(raw) as Partial<CreateDraft>
    if (typeof d?.text !== 'string' || !Array.isArray(d.fields) || !Array.isArray(d.issues)) return null
    // Поля старого формата держали подсказку значением: такой черновик отдал бы её карточке.
    if (!d.fields.every((f) => typeof f === 'object' && f !== null && 'hint' in f)) return null
    return d as CreateDraft
  } catch {
    return null
  }
}

export function saveDraft(d: CreateDraft, storage?: Box): void {
  const b = box(storage)
  if (!b) return
  try {
    if (draftIsEmpty(d)) b.removeItem(DRAFT_KEY)
    else b.setItem(DRAFT_KEY, JSON.stringify(d))
  } catch {
    // переполнено или закрыто — не повод ломать мастер
  }
}
