const NBSP = String.fromCharCode(160)
const num = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 })

/** «302 мс» с неразрывным пробелом: единица не отрывается от числа при переносе. */
export const ms = (value: number) => `${num.format(value)}${NBSP}мс`

/** Текст ошибки начинается со строчной («воркер не ответил…»): в интерфейсе это начало предложения. */
export const sentence = (text: string) => text.charAt(0).toUpperCase() + text.slice(1)

export { NBSP }

/** Форма слова по числу: 1 позиция, 2 позиции, 5 позиций. */
export function plural(n: number, one: string, few: string, many: string): string {
  const n10 = n % 10
  const n100 = n % 100
  if (n10 === 1 && n100 !== 11) return one
  if (n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14)) return few
  return many
}

/** Число по-русски: «0,875» → «0,88», целые без дробной части. */
export const decimal = (value: number, digits = 2) =>
  new Intl.NumberFormat('ru-RU', { maximumFractionDigits: digits }).format(value)
