// Живая карточка рядом с мастером: что уже заполнено, что пусто и о чём спрашивают сейчас.
import type { AutofillOut, Issue } from '../../../api/v1.ts'
import { ktruText } from '../chainView.ts'
import { fromText, issuesByField, type FieldState, type Origin } from '../formModel.ts'
import { fieldStatus, ktruRelation, valueSource, type FieldStatus } from '../ktruFieldModel.ts'
import { groupFields, readiness } from './groups.ts'
import { ktruValue } from './ktruFormat.ts'
import { notFound } from './stepOf.ts'
import { trailOf, type Trail } from './trail.ts'

export type KtruCode = { kind: 'code'; code: string } | { kind: 'pending' } | { kind: 'missing'; text: string }

export type LiveRow = {
  name: string
  value: string | null
  unit: string | null
  fromText: boolean
  /** Пометка источника: КТРУ у полей шаблона, «портал» и «новая» — у свойств вне шаблона; null — шаблон портала. */
  origin: Origin | null
  /** asking — о характеристике спрашивают на текущем шаге: «выберите слева». */
  state: 'filled' | 'empty' | 'asking'
  status?: FieldStatus
  provenance?: string
  relation?: string | null
}

export type LiveCardView = {
  trail?: Trail
  /** Категория определена (есть цепочка кодов); до неё коды «ждут ответа». */
  category: boolean
  /** Категория не найдена: вопроса нет, коды не «ждут ответа», а отсутствуют. */
  notFound: boolean
  path: string[]
  name: string
  codes: { kpgz: string | null; okpd2: string | null; ktru: KtruCode }
  rows: LiveRow[]
  ready: { done: number; total: number }
}

/** КТРУ карточки: код, «ждёт ответа» (категория или позиция ещё уточняются) или причина, по которой кода нет. */
function ktruCode(out: AutofillOut): KtruCode {
  const chain = out.chain
  if (!chain) return notFound(out) ? { kind: 'missing', text: 'нет' } : { kind: 'pending' }
  const state = chain.ktru
  if (state?.final_code) return { kind: 'code', code: state.final_code }
  if (state?.question) return { kind: 'pending' }
  return { kind: 'missing', text: ktruText(chain).value }
}

/** Карточка по ответу модуля и полям формы; `current` — характеристика текущего вопроса, `name` — наименование из формы. */
export function liveCard(out: AutofillOut, fields: FieldState[], current: string | null, name: string, issues: Issue[] = out.issues, answeredAt: Record<string, 2 | 3> = {}): LiveCardView {
  // До выбора категории модуль заполняет поля ведущего варианта: строки и готовность чужой категории не показываются.
  const decided = out.ktru.final_code != null
  const groups = groupFields(decided ? fields : [])
  const byField = issuesByField(issues).fields
  const found = groups.portal.filter((f) => f.required || f.value.trim())
  const extra = groups.new.filter((f) => f.value.trim())
  const rows = [...groups.general, ...groups.ktru, ...found, ...extra].map((f): LiveRow => {
    const value = f.value.trim() ? ktruValue(f.value.trim()) : null
    return {
      name: f.name,
      value,
      unit: value ? (f.unit ?? f.unitName) : null,
      fromText: fromText(f),
      origin: f.extra || f.origin === 'ktru' ? f.origin : null,
      state: f.name === current ? 'asking' : value ? 'filled' : 'empty',
      status: fieldStatus(f, byField.get(f.name)),
      provenance: valueSource(f, answeredAt[f.name]),
      relation: ktruRelation(f)?.text ?? (f.origin === 'new' ? 'Предложение: вне КТРУ, подтверждает модератор' : null),
    }
  })
  const { done, total } = readiness(decided ? fields : [])
  return {
    trail: trailOf(out),
    category: out.chain != null,
    notFound: out.chain == null && notFound(out),
    path: out.chain?.path ?? [],
    name,
    codes: { kpgz: out.chain?.kpgz ?? null, okpd2: out.chain?.okpd2 ?? null, ktru: ktruCode(out) },
    rows,
    ready: { done, total },
  }
}

/** Строки, значение которых изменилось с прошлой карточки (новая строка со значением — тоже); первая карточка — ни одной. */
export function changedRows(prev: LiveCardView | null, next: LiveCardView): Set<string> {
  if (!prev) return new Set()
  const before = new Map(prev.rows.map((r) => [r.name, r.value]))
  return new Set(next.rows.filter((r) => (before.has(r.name) ? before.get(r.name) !== r.value : r.value !== null)).map((r) => r.name))
}
