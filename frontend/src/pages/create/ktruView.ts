// Подписи уточняющего вопроса: служебные значения модуля (app/narrowing.py) — словами для человека.
import type { KtruState, Option } from '../../api/v1.ts'

/** Вариант «ни одна позиция не подходит» — честный отказ вместо выбора наугад. */
export const NOTHING_FITS = '__nothing__'
/** Вопрос-подтверждение позиции: в наименовании есть слова, которых нет у кандидатов. */
export const CONFIRM = 'Позиция КТРУ'
/** Вариант «у товара нет такой характеристики». */
const NONE = ''
/** Вариант «другое значение»: значения, общие для всех позиций, выбор не сужают. */
export const OTHER = '__other__'

export function questionTitle(characteristic: string, stage: 'category' | 'ktru' = 'category'): string {
  if (stage === 'ktru') return characteristic === CONFIRM ? 'Выберите позицию КТРУ' : `Позиция КТРУ: уточните «${characteristic}»`
  return characteristic === CONFIRM ? 'Подтвердите позицию КТРУ' : `Уточните «${characteristic}»`
}

export function optionLabel(state: KtruState, characteristic: string, o: Option): { text: string; hint: string | null } {
  if (o.value === NOTHING_FITS) return { text: 'Ни одна не подходит', hint: null }
  if (characteristic === CONFIRM) {
    const position = state.candidates.find((c) => c.code === o.value)
    return { text: o.label?.trim() || position?.name || o.value, hint: o.value }
  }
  if (o.value === OTHER) return { text: 'Другое значение', hint: 'выбор не сузит, модуль спросит другое' }
  return { text: o.value === NONE ? 'Нет такой характеристики' : o.label?.trim() || o.value, hint: `останется ${o.remaining}, отсечёт ${o.cuts}` }
}
