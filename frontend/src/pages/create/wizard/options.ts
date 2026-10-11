// Варианты ответа шагов 2 и 3: подпись, код, что останется, похожие СТЕ.
import type { Issue, KtruState, Position, TwinOut } from '../../../api/v1.ts'
import { plural } from '../../../lib/format.ts'
import { CONFIRM, NOTHING_FITS, OTHER, optionLabel } from '../ktruView.ts'

export type Variant = {
  value: string
  title: string
  hint: string | null
  codes: string[]
  effect: string
  reasons: { label: string; values: string[] }[]
}

const left = (n: number) => `останется ${n} ${plural(n, 'позиция', 'позиции', 'позиций')}`

/** Больше вариантов в шаге человек не разберёт: клавиши 1…6 и спецификация — до шести. */
const MAX_VALUES = 6
const SERVICE = new Set([NOTHING_FITS, OTHER, ''])

/**
 * Шаг 2 выбирает категорию. Вопрос о характеристике с длинным перечнем (значения одной категории
 * из нескольких) заменяется выбором из самих кандидатов: ответ «Позиция КТРУ» = код модуль принимает.
 */
export function categoryQuestion(state: KtruState): KtruState {
  const q = state.question
  if (!q || q.characteristic === CONFIRM || state.candidates.length < 2) return state
  if (q.options.filter((o) => !SERVICE.has(o.value)).length <= MAX_VALUES) return state
  const n = state.candidates.length
  return {
    ...state,
    question: {
      characteristic: CONFIRM,
      options: [
        ...state.candidates.map((c) => ({ value: c.code, remaining: 1, cuts: n - 1, codes: [c.code] })),
        { value: NOTHING_FITS, remaining: 0, cuts: n, codes: [] },
      ],
    },
  }
}

/** Заголовок и пояснение вопроса: шаг 2 выбирает категорию, шаг 3 — позицию КТРУ внутри неё. */
export function questionText(state: KtruState, stage: 'category' | 'ktru'): { title: string; why: string } {
  const q = state.question
  if (!q) return { title: '', why: '' }
  const n = state.remaining
  const steps = state.steps.at(-1) ?? null
  if (q.characteristic === CONFIRM) {
    const many = q.options.filter((o) => o.value !== NOTHING_FITS).length > 1
    if (stage === 'category') {
      return {
        title: many ? 'Какой это товар по КТРУ?' : 'Подтвердите тип товара',
        why: steps ?? 'Выберите общую версию вашего товара в справочнике. Затем уточним позицию КТРУ.',
      }
    }
    return {
      title: many ? 'Выберите позицию КТРУ' : 'Подтвердите позицию КТРУ',
      why: steps ?? 'Позиции КТРУ этой категории не различаются характеристиками из текста.',
    }
  }
  // Вопрос, а не команда: «Регулировка по высоте: да или нет?» вместо «Уточните «Регулировка по высоте»».
  const title = yesNo(q) ? `${q.characteristic}: да или нет?` : `${q.characteristic}: что подходит вашему товару?`
  if (stage === 'ktru') {
    return { title, why: `От ответа зависит позиция КТРУ: сейчас подходят ${n} ${plural(n, 'позиция', 'позиции', 'позиций')}.` }
  }
  return { title, why: `От ответа зависит тип товара: сейчас подходят ${n} ${plural(n, 'тип', 'типа', 'типов')}.` }
}

const YES_NO = new Set(['да', 'нет'])

/** Вопрос «да или нет»: два настоящих варианта, «Да» и «Нет» (служебные «нет такой» и «другое» не в счёт). */
export function yesNo(q: { options: { value: string }[] }): boolean {
  const real = q.options.filter((o) => !SERVICE.has(o.value)).map((o) => o.value.trim().toLowerCase())
  return real.length === 2 && real.every((v) => YES_NO.has(v))
}

type Matched = Pick<KtruState['candidates'][number], 'similar' | 'similar_count' | 'similar_total' | 'why'>

/** Доля голоса похожих СТЕ портала в процентах, вниз: 69,96 % — ещё не порог 70 %, после
 *  которого модуль выбирает категорию сам. Нет голоса (категория найдена по словам) — нет и числа. */
function percent(c: Matched): number | null {
  return c.similar == null ? null : Math.floor(c.similar * 100)
}

/** Почему категория определена — подпись на шаге 2 после выбора. */
/**
 * Такие СТЕ для блока «уже есть на портале»: то же наименование в определённой категории — и только пока
 * последняя проверка модуля видит дубль (после правки наименования замечание и блок уходят вместе).
 */
export function shownTwins(twins: TwinOut[], issues: Pick<Issue, 'kind'>[]): TwinOut[] {
  if (!issues.some((i) => i.kind === 'duplicate')) return []
  return twins.filter((t) => t.same_category && t.by === 'name')
}

/** Категорию назвала такая же СТЕ портала (то же наименование в этой категории): подпись с её номером. */
export function twinNote(twins: Pick<TwinOut, 'id' | 'by' | 'same_category'>[]): string | null {
  const twin = twins.find((t) => t.same_category && t.by === 'name')
  return twin ? `Такая же СТЕ есть на портале: №${twin.id}` : null
}

export function categoryNote(c: Matched): string | null {
  const p = percent(c)
  if (p !== null) return `Совпадение ${p} % с похожими СТЕ портала`
  return c.why.length ? `Определена по словам: ${c.why.join(', ')}` : null
}

/** Варианты вопроса без «Ни одна не подходит»: он — отдельная кнопка под списком. */
export function optionsOf(state: KtruState, stage: 'category' | 'ktru', positions: Record<string, Position> = {}): Variant[] {
  const question = state.question
  if (!question) return []
  const choosing = question.characteristic === CONFIRM
  return question.options
    .filter((o) => o.value !== NOTHING_FITS)
    .map((o) => {
      const label = optionLabel(state, question.characteristic, o)
      const c = choosing ? state.candidates.find((x) => x.code === o.value) : undefined
      const position = positions[o.value]
      const linked = new Set(position?.characteristics.flatMap((s) => s.ktru) ?? [])
      const count = position ? (/^\d{2}\.\d{2}\.\d{2}\.\d{3}-\d+$/.test(position.code) ? 1 : linked.size) : null
      const reasons = c ? [
        { label: 'Совпало', values: c.matched ?? [] },
        { label: 'Нет в тексте', values: c.missing ?? [] },
        { label: 'Противоречит', values: c.conflicts ?? [] },
      ] : []
      if (c && !reasons.some((r) => r.values.length) && c.why.length) reasons.push({ label: 'Основание подбора', values: c.why })
      return {
        value: o.value,
        title: label.text,
        hint: o.value === OTHER ? label.hint : null,
        codes: choosing && stage === 'category' ? [position?.okpd2 ? `ОКПД2 ${position.okpd2}` : 'ОКПД2: сведения не загружены'] : o.codes,
        effect: choosing && stage === 'category'
          ? ktruEffect(c, count)
          : choosing || o.value === OTHER ? '' : left(o.remaining),
        reasons: reasons.filter((r) => r.values.length),
      }
    })
}

/** «2 позиции КТРУ» — только если их правда две: шаблон и позиция из него с одинаковыми
 * характеристиками модуль объединяет, и это сказано здесь же, до выбора. */
export function ktruEffect(c: { ktru_total?: number | null; ktru_distinct?: number | null } | undefined, count: number | null): string {
  const total = c?.ktru_total ?? count
  if (total === null || total === undefined) return 'Число позиций не загружено'
  const distinct = c?.ktru_distinct ?? total
  if (total === 0) return 'Нет в КТРУ: карточка пойдёт с категорией портала'
  const n = (k: number) => `${k} ${plural(k, 'позиция', 'позиции', 'позиций')} КТРУ`
  if (distinct >= total) return n(total)
  return `${n(distinct)}: из ${total} одинаковые объединены`
}

/** Первый вариант, если модуль назвал его самым вероятным (шаг «Вероятнее всего это …»). */
export function likelyOption(state: KtruState): string | null {
  const q = state.question
  if (!q || q.characteristic !== CONFIRM || !state.steps.some((s) => s.startsWith('Вероятнее всего'))) return null
  return q.options[0]?.value ?? null
}
