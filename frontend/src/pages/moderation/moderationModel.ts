import type { Issue, QueuedSubmission } from '../../api/v1.ts'
import { NBSP } from '../../lib/format.ts'
import { fieldLabel } from '../create/createModel.ts'
import type { FieldState } from '../create/formModel.ts'

/** Текст возврата поставщику: блокирующие замечания модуля по номерам; модератор его правит. */
export function returnNote(issues: Issue[]): string {
  return issues
    .filter((i) => i.severity === 'block')
    .map((i, n) => `${n + 1}. ${fieldLabel(i.field)}: ${i.message}`)
    .join('\n')
}

/** Замечание вне полей карточки (фото, коды) одной строкой: подпись поля, если сообщение само его не называет —
 *  «Нет фото товара: …», а не «Фото товара: Нет фото товара: …». */
export function issueText(i: Issue): string {
  const label = fieldLabel(i.field)
  return i.message.toLocaleLowerCase('ru').includes(label.toLocaleLowerCase('ru')) ? i.message : `${label}: ${i.message}`
}

type Queued = Pick<QueuedSubmission, 'id' | 'status' | 'triage'> & { card: Pick<QueuedSubmission['card'], 'name'> }

/**
 * Ждущие заявки, которые можно одобрить одной кнопкой (без блокирующих замечаний и предупреждений,
 * в том числе по позиции КТРУ): номер и наименование — для списка в диалоге подтверждения.
 */
export function cleanPending(queue: Queued[]): { id: number; name: string }[] {
  return queue.filter((s) => s.status === 'pending' && s.triage?.clean).map((s) => ({ id: s.id, name: s.card.name }))
}

/** Новые свойства (`origin: new`) — в свой блок «Вне КТРУ — подтвердите»; остальное — как в карточке. */
export function splitOutsideKtru(fields: FieldState[]): { inCard: FieldState[]; outside: FieldState[] } {
  return { inCard: fields.filter((f) => f.origin !== 'new'), outside: fields.filter((f) => f.origin === 'new') }
}

/** Сколько секунд модель добавляет к ответу на товар: на эталоне hard.csv (11.10) p50 описания 3,5 с с моделью,
 *  предел ожидания модели — 15 с (`LLM_BUDGET_S`). */
export const LLM_EXTRA_S = 15

/** Подпись выключателя: текущее состояние видно в самой подписи, а не только по положению ползунка. */
export function llmLabel(enabled: boolean): string {
  return `Модель (LLM): ${enabled ? 'вкл' : 'выкл'}`
}

/** Пояснение под выключателем: когда включать и что даёт выключение. */
export function llmHint(extraS: number = LLM_EXTRA_S): string {
  return (
    `Включено: модель находит в тексте ещё треть значений и ставит первым вероятный тип, ответ дольше (до ${extraS}${NBSP}с на товар). ` +
    'Выключено: только правила и справочник КТРУ. Настройка общая для всех поставщиков и пакетов.'
  )
}

/** Номер заявки из адреса «/moderation?id=12»; иначе null. */
export function selectedId(search: string): number | null {
  const raw = new URLSearchParams(search).get('id')
  return raw && /^\d+$/.test(raw) ? Number(raw) : null
}
