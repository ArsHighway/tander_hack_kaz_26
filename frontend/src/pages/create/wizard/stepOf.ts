// Какой шаг мастера открыть по ответу модуля: первый, где нужен человек.
import type { AutofillOut, Issue } from '../../../api/v1.ts'
import { blocking, type FieldState } from '../formModel.ts'

export type Step = 1 | 2 | 3 | 4 | 5

/** Категория не найдена: ни кода, ни вопроса (то же условие, что было в форме создания). */
export const notFound = (out: AutofillOut): boolean => !out.ktru.final_code && !out.ktru.question

/** Шаги, которые модуль решил сам, пропускаются: открывается первый, где нужен человек. */
export function stepOf(out: AutofillOut | null, ui: { reviewing: boolean }): Step {
  if (!out) return 1
  if (ui.reviewing) return 5
  if (out.ktru.question || notFound(out)) return 2
  if (out.chain?.ktru?.question) return 3
  return 4
}

/**
 * Можно сразу к проверке (шаг 5), без шага 4: вопросов нет, обязательные заполнены, блокирующих
 * замечаний нет. Подсказки СТЕ портала (`method: twin`, `similar`) — не из текста поставщика, их
 * принимает человек: тогда открывается шаг 4.
 */
export function readyToReview(out: AutofillOut | null, fields: FieldState[], issues: Issue[]): boolean {
  if (stepOf(out, { reviewing: false }) !== 4) return false
  if (blocking(issues, fields) > 0) return false
  return !fields.some((f) => f.hint != null)
}
