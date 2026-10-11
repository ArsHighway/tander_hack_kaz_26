// Полоса шагов мастера: у каждого шага его результат с кодом, а не только название.
import type { AutofillOut, KtruState } from '../../../api/v1.ts'
import { notFound, type Step } from './stepOf.ts'

export type Pending = 'autofill' | 'answer' | null

export type Segment = {
  step: Step
  label: string
  value: string
  code: string | null
  state: 'done' | 'current' | 'todo' | 'busy'
}

export const STEP_LABELS: Record<Step, string> = {
  1: 'Описание',
  2: 'Тип товара',
  3: 'Позиция КТРУ',
  4: 'Характеристики',
  5: 'Отправка',
}

const WORDS: Record<number, string> = { 2: 'две', 3: 'три', 4: 'четыре' }

/** «Подходят две», «Подходят 7»: сколько кандидатов осталось до ответа. */
export const matchText = (n: number): string => (n <= 1 ? 'Подтвердите' : `Подходят ${WORDS[n] ?? n}`)

type Result = { value: string; code: string | null; done: boolean }

const named = (state: KtruState): Result | null => {
  const final = state.candidates.find((c) => c.code === state.final_code)
  return state.final_code ? { value: final?.name ?? state.final_code, code: state.final_code, done: true } : null
}

function category(out: AutofillOut): Result {
  if (out.ktru.final_code) {
    const final = out.ktru.candidates.find((c) => c.code === out.ktru.final_code)
    const name = out.chain?.category_name ?? final?.name ?? out.ktru.final_code
    return { value: name, code: out.chain?.kpgz ?? out.ktru.final_code, done: true }
  }
  if (notFound(out)) return { value: 'Не найдена', code: null, done: false }
  return { value: matchText(out.ktru.remaining), code: null, done: false }
}

function ktru(out: AutofillOut): Result | null {
  const chain = out.chain
  if (!out.ktru.final_code || !chain) return null
  if (chain.ktru_missing === 'no_links') return { value: 'В КТРУ нет', code: null, done: true }
  if (chain.ktru_missing === 'not_loaded') return { value: 'Нет в справочнике', code: null, done: true }
  if (!chain.ktru) return null
  const final = named(chain.ktru)
  if (final) return final
  if (chain.ktru.question) return { value: matchText(chain.ktru.remaining), code: null, done: false }
  // Ответ дан («Ни одна не подходит»): шаг пройден, и его можно открыть, чтобы ответить иначе.
  return { value: 'Не подходит ни одна', code: null, done: true }
}

/** Сегменты полосы шагов: результат каждого шага, текущий, ждущий ответа модуля. */
export function pathSegments(
  out: AutofillOut | null,
  step: Step,
  pending: Pending,
  ready: { done: number; total: number } | null,
): Segment[] {
  const filling = pending === 'autofill'
  const results: Record<Step, Result | null> = {
    1: out || filling ? { value: 'Ваш текст', code: null, done: true } : null,
    2: out && !filling ? category(out) : null,
    3: out && !filling ? ktru(out) : null,
    4: out && !filling ? { value: ready && out.ktru.final_code ? `КТРУ: заполнено ${ready.done} из ${ready.total}` : '', code: null, done: step === 5 } : null,
    5: null,
  }
  return ([1, 2, 3, 4, 5] as Step[]).map((s) => {
    const r = results[s]
    let state: Segment['state'] = s === step ? 'current' : r?.done ? 'done' : 'todo'
    let value = r?.value ?? ''
    if (filling && (s === 2 || s === 4)) {
      state = 'busy'
      value = s === 2 ? 'ищем категорию' : 'заполняем характеристики'
    } else if (pending === 'answer' && s === step) {
      state = 'busy'
      value = 'проверяем ответ'
    }
    return { step: s, label: STEP_LABELS[s], value, code: r?.code ?? null, state }
  })
}
