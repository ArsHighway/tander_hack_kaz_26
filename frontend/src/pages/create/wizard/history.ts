// Ошибки ответа и повторный ответ на пройденный шаг.
import { ApiError } from '../../../api/http.ts'
import type { KtruState } from '../../../api/v1.ts'

/** 404 на ответ — сессия заполнения истекла: текст сохранён, заполнить можно заново. */
export function answerError(e: unknown): 'expired' | 'failed' {
  return e instanceof ApiError && e.status === 404 ? 'expired' : 'failed'
}

/**
 * Повторный ответ на пройденный шаг. Категория — заново (новая сессия): ответы шага 3 в сессии
 * относятся к позициям прежней категории. Позиция КТРУ — обычный ответ, поле сессии перезаписывается.
 */
export function reanswer(step: 2 | 3): 'restart' | 'answer' {
  return step === 2 ? 'restart' : 'answer'
}

/** Вопрос, на который человек уже ответил: шаг можно открыть и ответить заново. */
export type Asked = { state: KtruState; chosen: string }
export type AskedSteps = Partial<Record<2 | 3, Asked>>

/**
 * Запомнить ответ на шаге `at`: с показанным вопросом, а при повторном ответе на пройденном шаге
 * (вопроса в текущем ответе модуля уже нет) — новый выбор при прежнем вопросе. «Ни одна не подходит»
 * тоже запоминается: вопрос можно открыть снова, а прежний вариант больше не отмечен.
 */
export function rememberAsked(asked: AskedSteps, at: 2 | 3, shown: KtruState | null | undefined, value: string): AskedSteps {
  if (shown?.question) return { ...asked, [at]: { state: shown, chosen: value } }
  const prev = asked[at]
  return prev ? { ...asked, [at]: { ...prev, chosen: value } } : asked
}
