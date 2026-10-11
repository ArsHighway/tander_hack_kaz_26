// Цепочка кодов найденной категории (ответ модуля `chain`): что показать в полях только для чтения.
import type { AutofillOut } from '../../api/v1.ts'
import { plural } from '../../lib/format.ts'

export type Chain = NonNullable<AutofillOut['chain']>

/** Позиции КТРУ для товара нет: код остаётся пустым, характеристики — в формате КТРУ (вопрос организаторам, п. 8). */
export const NO_KTRU_HELPER =
  'Позиции КТРУ для этого товара нет. Код КТРУ останется пустым, характеристики оформлены в формате КТРУ'

const notLoaded = (n: number) =>
  `Справочник КТРУ не содержит ${n} ${plural(n, 'привязанной позиции', 'привязанных позиций', 'привязанных позиций')}`

/** Подпись поля категории: у справочника стандартов портала это КПГЗ, у справочника КТРУ — сама позиция. */
export function categoryLabel(chain: Chain | null | undefined): string {
  return chain?.kpgz ? 'Категория портала (КПГЗ)' : 'Позиция КТРУ'
}

/** Значение и подсказка поля «Позиция КТРУ» второго этапа. */
export function ktruText(chain: Chain): { value: string; helper: string | null } {
  const state = chain.ktru
  if (!state) {
    // Без второго этапа модуль называет причину (`ktru_missing`); без неё судим по числу ссылок.
    const missing = chain.ktru_missing ?? (chain.ktru_linked ? 'not_loaded' : 'no_links')
    return missing === 'not_loaded'
      ? { value: 'Нет в справочнике', helper: notLoaded(chain.ktru_linked) }
      : { value: 'В КТРУ нет', helper: NO_KTRU_HELPER }
  }
  const final = state.candidates.find((c) => c.code === state.final_code)
  if (final) return { value: `${final.code}  ${final.name}`, helper: 'Определена по характеристикам товара' }
  if (state.question) return { value: `Уточняется: подходят ${state.remaining}`, helper: 'Ответьте на вопрос выше' }
  return { value: 'Не подходит ни одна позиция КТРУ', helper: state.steps.at(-1) ?? null }
}
