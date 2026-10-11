// Итог проверки модерации на шаге 5: что показать, можно ли отправлять и куда вести, если нельзя.
import type { Issue } from '../../../api/v1.ts'
import { fieldId } from '../../../card/fieldId.ts'
import { PHOTO, fieldLabel } from '../createModel.ts'
import type { FieldState } from '../formModel.ts'

export { PHOTO }

/** Кнопка загрузки необязательного фото на шаге 4. */
export const PHOTO_FIELD_ID = fieldId(PHOTO)

/** Предупреждение старого сервера об отсутствии фото: поле уже необязательно. */
export const missingPhotoWarning = (i: Issue): boolean =>
  i.field === 'photo' && i.severity === 'warn' && i.rule === 'у товара есть изображение'

/**
 * 'checking' — проверка идёт; 'failed' — модуль не ответил: это не «пройдены», отправка ждёт повторной проверки;
 * 'blocked' — пусты обязательные поля или есть блокирующее замечание; 'warn' — отправить можно, но есть что
 * проверить; 'ok' — правила модерации пройдены.
 */
export type SubmitState = 'checking' | 'failed' | 'blocked' | 'warn' | 'ok'

export type SubmitReport = {
  state: SubmitState
  /** Пустые обязательные поля по порядку карточки: «Заполните: …». */
  missing: string[]
  /** Блокирующие замечания, кроме тех, что о пустых полях из `missing`. */
  blocks: Issue[]
  /** Предупреждения («расходится с текстом», «необычно для категории», дубль): «Проверьте перед отправкой». */
  warns: Issue[]
  /** Сведения: отправке не мешают. */
  notes: Issue[]
}

const FIELD_PREFIX = 'characteristics.'

/** Красная плашка шага 5: на неё ставится фокус, когда отправке мешает не поле. */
export const BLOCKED_ID = 'submit-blocked'

/** Обязательные поля без значения. Сервер говорит о них по-разному (block у шаблона, warn у КТРУ) или молчит,
 *  а отправку не пускает `blocking` по полям: экран смотрит туда же, куда и отправка. */
export function missingRequired(fields: FieldState[]): string[] {
  return fields.filter((f) => f.required && !f.value.trim()).map((f) => f.name)
}

/** Фото необязательно; отправке мешают только обязательные поля и блокирующие замечания. */
export function submitState(checked: Issue[] | null | 'failed', fields: FieldState[]): SubmitReport {
  const missing = missingRequired(fields)
  if (checked === null || checked === 'failed') {
    return { state: checked === null ? 'checking' : 'failed', missing, blocks: [], warns: [], notes: [] }
  }
  // Пустые поля уже перечислены в «Заполните». Предупреждение об отсутствии фото от старого
  // сервера не показываем: поле необязательно. Блокирующую ошибку фото сохраняем.
  const empty = new Set(missing.map((n) => FIELD_PREFIX + n))
  const rest = checked.filter((i) => !empty.has(i.field) && !missingPhotoWarning(i))
  const blocks = rest.filter((i) => i.severity === 'block')
  const warns = rest.filter((i) => i.severity === 'warn')
  const notes = rest.filter((i) => i.severity === 'info')
  const state = missing.length || blocks.length ? 'blocked' : warns.length ? 'warn' : 'ok'
  return { state, missing, blocks, warns, notes }
}

/** Поле карточки, к которому ведёт «Отправить», когда отправить нельзя: первое пустое обязательное,
 *  иначе первое поле с блокирующим замечанием; null — мешает не поле (наименование,
 *  код), остаёмся на шаге 5. */
export function firstProblem(report: SubmitReport, fields: FieldState[]): string | null {
  if (report.missing.length) return report.missing[0]
  const names = new Set(fields.map((f) => f.name))
  const hit = report.blocks.find((i) => i.field.startsWith(FIELD_PREFIX) && names.has(i.field.slice(FIELD_PREFIX.length)))
  return hit ? hit.field.slice(FIELD_PREFIX.length) : null
}

const quoted = (names: string[], max = 3) => {
  const shown = names
    .slice(0, max)
    .map((n) => `«${n}»`)
    .join(', ')
  return names.length > max ? `${shown} и ещё ${names.length - max}` : shown
}

/** Строка у кнопки шага 4 после неудачной отправки: что ещё мешает; null — уже ничего. */
export function notSentText(issues: Issue[], fields: FieldState[]): string | null {
  const report = submitState(issues, fields)
  if (report.missing.length) return `Не отправлено: заполните ${quoted(report.missing)}`
  if (report.blocks.length) return `Не отправлено: исправьте ${quoted([...new Set(report.blocks.map((i) => fieldLabel(i.field)))])}`
  return null
}
