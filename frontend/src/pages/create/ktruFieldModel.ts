// Представление данных backend: исходное значение, допустимые варианты и источник не смешиваются.
import type { Issue } from '../../api/v1.ts'
import { rangeLabel } from './allowedValues.ts'
import { accepted, changed, type FieldState, type Helper } from './formModel.ts'
import { ktruValue } from './wizard/ktruFormat.ts'

export type FieldStatus = 'подтверждено' | 'предложено' | 'не заполнено' | 'требует проверки'

const bucket = (value: string) => /(?:>=|<=|[<>≥≤]|\.\.\.|…|(?:более|менее|свыше|^до\s|^от\s))/i.test(value) ||
  /^[([]?\s*-?\d+(?:[.,]\d+)?\s*(?:[–—-]|;)\s*-?\d+(?:[.,]\d+)?\s*[\])]?\s*$/.test(value)
const number = (value: string) => /^-?\d+(?:[.,]\d+)?$/.test(value.trim())

/** Корзина чисел не становится списком значений товара: требуется точное число. */
export function numericField(f: FieldState): boolean {
  if (f.kind === 'qualitative') return false
  return f.kind === 'quantitative' || Boolean(f.range) || Boolean(f.ktruMatch && bucket(f.ktruMatch)) ||
    (f.options.length > 0 && f.options.every((o) => bucket(o) || number(o)))
}

export function fieldStatus(f: FieldState, issues: Issue[] = []): FieldStatus {
  if (!f.value.trim()) return f.hint != null || (f.origin === 'ktru' && (f.options.length > 0 || f.ktruMatch)) ? 'предложено' : 'не заполнено'
  if (issues.some((i) => i.severity !== 'info') || (!changed(f) && (f.status === 'conflict' || f.ktruFit === 'not_in_list' || f.ktruFit === 'out_of_range'))) return 'требует проверки'
  if (f.origin === 'new') return 'предложено'
  if (changed(f) || f.own || f.confirmed || f.method === 'answer' || accepted(f)) return 'подтверждено'
  if (f.method === 'llm' && !f.checked) return 'требует проверки'
  return f.source || f.status === 'filled' || f.status === 'normalized' ? 'подтверждено' : 'требует проверки'
}

export function valueSource(f: FieldState, answeredStep?: 2 | 3): string {
  if (changed(f)) return 'Выбрано вами или введено вручную'
  if (f.own) return f.own
  if (accepted(f) || (f.value.trim() && (f.method === 'similar' || f.method === 'twin'))) return 'Принято вами из подсказки СТЕ портала'
  if (f.method === 'answer') return f.note?.includes('выбранного типа') ? 'Из выбранного вами типа КТРУ' : answeredStep ? `Из вашего ответа на шаге ${answeredStep}` : 'Из вашего ответа на вопрос'
  if (f.confirmed) return 'Подтверждено вами'
  if (f.method === 'llm' && f.checked && f.source) return `Модель нашла в тексте «${f.source.text}»: подпись и значение совпали`
  if (f.method === 'llm') return f.source ? `Модель по вашему тексту «${f.source.text}»: проверьте` : 'Модель: проверьте значение по вашему тексту'
  if (f.source) return `Из текста: «${f.source.text}»`
  if (f.hint != null) return 'Подсказка СТЕ портала: подтвердите, если подходит'
  return ''
}

const NUMBER = /-?\d+(?:[.,]\d+)?/g
const fmt = (n: number) => n.toLocaleString('ru-RU', { maximumFractionDigits: 3, useGrouping: false })

/** Больше четырёх корзин — не список на десять строк, а их размах: «диапазоны от 500 до 3300 мм». */
function bucketSpan(values: string[], unit: string | null): string | null {
  if (values.length <= 4) return null
  const nums = values.flatMap((v) => [...v.matchAll(NUMBER)].map((m) => Number(m[0].replace(',', '.'))))
  if (!nums.length) return null
  return `диапазоны от ${fmt(Math.min(...nums))} до ${fmt(Math.max(...nums))}${unit ? ` ${unit}` : ''}`
}

export function allowedKtru(f: FieldState): string | null {
  const match = !changed(f) ? f.ktruMatch : null
  if (numericField(f)) {
    const values = match ? [match] : f.options
    if (!values.length) return rangeLabel(f.range, f.unit)
    return bucketSpan(values, f.unit) ?? values.map(ktruValue).join('; ') + (f.unit ? ` ${f.unit}` : '')
  }
  return f.options.length ? f.options.join(', ') : match ?? null
}

/** После правки старые fit/match не применяются к новому значению; проверка /check остаётся главным вердиктом. */
export function ktruRelation(f: FieldState): Helper | null {
  if (f.origin !== 'ktru') return null
  const allowed = allowedKtru(f)
  if (!f.value.trim()) return {
    text: f.ktruMatch && f.method === 'answer'
      ? `КТРУ: ${allowed ?? ktruValue(f.ktruMatch)}. Укажите точное значение`
      : allowed ? `КТРУ: ${allowed}. Нет в тексте: укажите значение` : 'Нет в тексте: укажите значение',
    tone: 'plain',
  }
  if (changed(f)) return { text: allowed ? `КТРУ: ${allowed}. Значение выбрано вами; соответствие сверяется при проверке` : 'Значение введено вами; соответствие сверяется при проверке', tone: 'plain' }
  if (f.ktruFit === 'normalized') return { text: `Приведено к формулировке КТРУ: «${f.normalizedFrom ?? f.source?.text ?? f.value}» → «${f.ktruMatch ?? f.value}»`, tone: 'plain' }
  if (f.ktruFit === 'in_range' || f.ktruFit === 'in_list') return { text: `КТРУ: ${f.ktruMatch ? ktruValue(f.ktruMatch) + (f.unit ? ` ${f.unit}` : '') : allowed ?? f.value}, подходит`, tone: 'plain' }
  if (f.ktruFit === 'not_in_list') return { text: `Нет в КТРУ${allowed ? `. Допустимые значения: ${allowed}` : ''}`, tone: 'error' }
  if (f.ktruFit === 'out_of_range') return { text: f.note ?? `Вне диапазонов КТРУ${allowed ? `: ${allowed}` : ''}`, tone: 'error' }
  return allowed ? { text: `КТРУ: ${allowed}`, tone: 'plain' } : null
}
