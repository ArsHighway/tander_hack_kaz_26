// Значения в формате карточек КТРУ ЕИС: пороги знаками со словом «и», а не служебной записью корзины.

const SIGN: Record<string, string> = { '>=': '≥', '<=': '≤', '>': '>', '<': '<' }
const BOUND = /^(>=|<=|>|<)\s*(-?\d[\d\s.,]*)$/

const bound = (part: string): string | null => {
  const m = BOUND.exec(part.trim())
  return m ? `${SIGN[m[1]]} ${m[2].trim()}` : null
}

/** «>=80 ... <90» → «≥ 80 и < 90», «>=80» → «≥ 80»; остальное без изменений. */
export function ktruValue(value: string): string {
  const parts = value.split(/\s*(?:\.\.\.|…)\s*/)
  if (parts.length > 2) return value
  const bounds = parts.map(bound)
  return bounds.every((b) => b !== null) ? bounds.join(' и ') : value
}
