// Готовность карточки и группы полей шага 4.
import type { FieldState } from '../formModel.ts'

export type Readiness = { done: number; total: number; left: string[] }

/** Счётчик обязательных характеристик КТРУ; портальные требования проверяются отдельно при отправке. */
export function readiness(fields: FieldState[]): Readiness {
  const counted = fields.filter((f) => f.required && f.origin === 'ktru')
  const left = counted.filter((f) => !f.value.trim()).map((f) => f.name)
  return { done: counted.length - left.length, total: counted.length, left }
}

export const readinessText = ({ done, total }: Pick<Readiness, 'done' | 'total'>): string =>
  `Обязательные характеристики КТРУ: заполнено ${done} из ${total}`

/** Необязательные на шаге 4: пустые свёрнуты; поле с замечанием, со значением (из текста, заявки, файла) или
 *  с подсказкой показывается всегда; `hidden` — сколько за «Показать ещё». */
export function visibleOptional(optional: FieldState[], flagged: Set<string>, open: boolean): { shown: string[]; hidden: number } {
  // `initial` — значение модуля: стёртое ради своего поле не пропадает из-под курсора
  const shown = optional.filter((f) => open || f.required || flagged.has(f.name) || f.value.trim() || f.initial.trim() || f.hint != null).map((f) => f.name)
  return { shown, hidden: optional.length - shown.length }
}

export type Groups = { general: FieldState[]; ktru: FieldState[]; portal: FieldState[]; new: FieldState[] }

/** Происхождение поля определяет раздел, в том числе для характеристик из `extra`. */
export function groupFields(fields: FieldState[]): Groups {
  const general = fields.filter((f) => f.group === 'general' && f.origin !== 'new')
  const rest = fields.filter((f) => !general.includes(f))
  const requiredFirst = (list: FieldState[]) => [...list.filter((f) => f.required), ...list.filter((f) => !f.required)]
  return {
    general,
    ktru: requiredFirst(rest.filter((f) => f.origin === 'ktru')),
    portal: requiredFirst(rest.filter((f) => f.origin === 'portal')),
    new: rest.filter((f) => f.origin === 'new'),
  }
}
