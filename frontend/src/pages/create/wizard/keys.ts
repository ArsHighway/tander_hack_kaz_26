// Клавиши мастера: 1…6 — вариант, Enter — подтвердить. В поле ввода цифры — это текст.

const TYPING = new Set(['INPUT', 'TEXTAREA', 'SELECT'])
// Поля, в которые не печатают: на них клавиши мастера работают (радиокнопка варианта — главный случай).
const NOT_TEXT = new Set(['radio', 'checkbox', 'button', 'submit', 'reset'])

/**
 * Отвечают ли клавиши 1…6 и Enter за вопрос. `scope` — форма вопроса: вне неё (живая карточка, текст
 * поставщика, шапка) у Enter свой смысл, клавиши мастера работают только в форме или со страницы.
 */
export function shouldHandleKey(target: EventTarget | null, scope?: { contains(node: Node | null): boolean } | null): boolean {
  const el = target as { tagName?: string; isContentEditable?: boolean; type?: string } | null
  if (!el) return true
  if (el.isContentEditable) return false
  const tag = (el.tagName ?? '').toUpperCase()
  if (scope && tag !== 'BODY' && !scope.contains(target as Node)) return false
  if (tag === 'INPUT' && NOT_TEXT.has((el.type ?? '').toLowerCase())) return true
  return !TYPING.has(tag)
}

/** Номер варианта по клавише «1»…«6»; другие клавиши и номера за списком — null. */
export function optionIndex(key: string, count: number): number | null {
  const n = Number(key)
  return Number.isInteger(n) && n >= 1 && n <= Math.min(count, 6) ? n - 1 : null
}
