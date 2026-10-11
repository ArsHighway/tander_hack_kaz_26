// Общее для экранов карточки: подсветка фрагментов текста, префилл из массовой загрузки, подписи полей замечаний.
import type { AutofillOut, CharIn, Submission, SubmissionBatch } from '../../api/v1.ts'
import { CONFIRM } from './ktruView.ts'

export type Segment = { text: string; mark: boolean; key: string | null }

/** Текст с выделенными фрагментами; пересекающийся фрагмент пропускается, чтобы текст не дублировался. */
export function highlightSegments(text: string, spans: { start: number; end: number; key: string }[]): Segment[] {
  const sorted = spans
    .filter((s) => s.start >= 0 && s.end <= text.length && s.start < s.end)
    .sort((a, b) => a.start - b.start || b.end - a.end)
  const out: Segment[] = []
  let at = 0
  for (const s of sorted) {
    if (s.start < at) continue
    if (s.start > at) out.push({ text: text.slice(at, s.start), mark: false, key: null })
    out.push({ text: text.slice(s.start, s.end), mark: true, key: s.key })
    at = s.end
  }
  if (at < text.length || out.length === 0) out.push({ text: text.slice(at), mark: false, key: null })
  return out
}

/** Ключ, под которым «Массовая загрузка» и «Мои заявки» передают карточку в форму. */
export const PREFILL_KEY = 'create:prefill'

/**
 * Что открывается в форме: текст поставщика, его характеристики (из файла или из возвращённой заявки)
 * и, при исправлении, номер заявки с текстом возврата.
 */
export type Prefill = {
  name: string
  description: string
  characteristics: CharIn[]
  revise: { id: number; note: string | null } | null
  /** Фото исправляемой заявки (`photo_id`): исправленная ссылается на то же фото. */
  photoId: number | null
  /** Строка пакета, открытая в форме: заявка запомнит пакет, «Мои заявки» покажут, откуда она. */
  batch?: SubmissionBatch | null
}

const isChar = (c: unknown): c is CharIn =>
  typeof c === 'object' && c !== null && typeof (c as CharIn).name === 'string' && typeof (c as CharIn).value === 'string'

/** Возвращённая заявка открывается в форме с теми же текстом и характеристиками. */
export function revisePrefill(s: Submission): Prefill {
  return {
    name: s.card.name,
    description: s.card.description,
    characteristics: s.card.characteristics.map((c) => ({ name: c.name, value: c.value, unit: c.unit ?? null })),
    revise: { id: s.id, note: s.decision_note ?? null },
    photoId: s.card.photo_id ?? null,
  }
}

/** Разбор префилла из sessionStorage; чужой или битый — null, форма открывается пустой. */
export function parsePrefill(raw: string | null): Prefill | null {
  if (!raw) return null
  try {
    const p = JSON.parse(raw) as Partial<Prefill>
    if (typeof p.name !== 'string' || !p.name.trim()) return null
    const revise = p.revise && typeof p.revise.id === 'number' ? { id: p.revise.id, note: p.revise.note ?? null } : null
    return {
      name: p.name,
      description: typeof p.description === 'string' ? p.description : '',
      characteristics: Array.isArray(p.characteristics) ? p.characteristics.filter(isChar) : [],
      revise,
      photoId: typeof p.photoId === 'number' ? p.photoId : null,
      // Ключ только у карточки из строки пакета: у остальных префилл прежнего вида.
      ...(p.batch && typeof p.batch.job_id === 'string' && typeof p.batch.row === 'number'
        ? { batch: { job_id: p.batch.job_id, row: p.batch.row, filename: typeof p.batch.filename === 'string' ? p.batch.filename : '' } }
        : {}),
    }
  } catch {
    return null
  }
}

const FIELD_PREFIX = 'characteristics.'

/** Название необязательного поля фото; `/check` говорит о нём на поле `photo`. */
export const PHOTO = 'Фото товара'

/** Подпись поля замечания: «characteristics.Формат» → «Формат». */
export function fieldLabel(field: string): string {
  if (field === 'name') return 'Наименование'
  if (field === 'photo') return PHOTO
  if (field === 'description') return 'Описание'
  if (field === 'characteristics') return 'Характеристики'
  if (field === 'ktru_code') return 'Код КТРУ'
  if (field === 'category_code') return 'Категория'
  return field.startsWith(FIELD_PREFIX) ? field.slice(FIELD_PREFIX.length) : field
}

export type CarriedAnswer = { characteristic: string; value: string; stage: 'category' | 'ktru' }

const same = (s: string) => s.trim().toLocaleLowerCase('ru').replaceAll('ё', 'е')

/** Вопрос мастера, на который уже ответила заявка или строка файла: тот же вариант из перечня.
 *  Каждый вопрос — один раз (`tried`: «этап:характеристика»): дальше решает человек. Подтверждение
 *  позиции («Позиция КТРУ») характеристикой не является и из заявки не берётся. */
export function carriedAnswer(out: AutofillOut, chars: CharIn[], tried: ReadonlySet<string>): CarriedAnswer | null {
  const asked = [
    { stage: 'ktru' as const, q: out.chain?.ktru?.question ?? null },
    { stage: 'category' as const, q: out.ktru.question },
  ]
  for (const { stage, q } of asked) {
    if (!q || q.characteristic === CONFIRM || tried.has(`${stage}:${q.characteristic}`)) continue
    const own = chars.find((c) => same(c.name) === same(q.characteristic))
    const option = own && q.options.find((o) => same(o.value) === same(own.value))
    if (option) return { characteristic: q.characteristic, value: option.value, stage }
  }
  return null
}
