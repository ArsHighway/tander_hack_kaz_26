import type { AutofillOut, KtruState } from '../../../api/v1.ts'

export type Trail = KtruState['trail']

/** Серверные шаги не заменяются предположениями о размере справочника. */
export function trailOf(out: AutofillOut): Trail {
  const trail = [...(out.ktru.trail ?? []), ...(out.chain?.ktru?.trail ?? [])]
  const code = out.chain?.ktru?.final_code
  if (code && !trail.some((s) => s.label.includes(code))) trail.push({ label: `Код КТРУ ${code}`, remaining: 1 })
  return trail.filter((s, i) => i === 0 || s.label !== trail[i - 1].label || s.remaining !== trail[i - 1].remaining)
}
