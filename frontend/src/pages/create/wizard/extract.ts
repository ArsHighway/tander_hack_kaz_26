// Текст из файла на шаге 1: проверка до загрузки и подсказки по ответу модуля.
import { ApiError } from '../../../api/http.ts'
import { plural } from '../../../lib/format.ts'

/** Столько же принимает модуль (`POST /api/v1/extract`). */
export const MAX_EXTRACT_BYTES = 10 * 1024 * 1024

const TOO_BIG = 'Файл больше 10 МБ'

export function checkExtract(file: { size: number }): string | null {
  return file.size > MAX_EXTRACT_BYTES ? TOO_BIG : null
}

/** 413 и 415 — свои слова; 422 модуль объясняет сам (скан без текста, пустой файл). */
export function extractError(e: unknown): string {
  if (e instanceof ApiError && e.status === 413) return TOO_BIG
  if (e instanceof ApiError && e.status === 415) return 'Такой файл не прочитать. Подойдут Word (.docx), PDF с текстом, Excel, CSV и обычный текст'
  if (e instanceof ApiError && e.status === 422) return e.detail
  return 'Не удалось прочитать файл, попробуйте ещё раз'
}

// Пределы `/autofill` (`MAX_NAME`, `MAX_DESCRIPTION` в backend/app/schemas.py): длиннее модуль не примет.
const MAX_NAME = 1000
const MAX_DESCRIPTION = 20_000
const NL = String.fromCharCode(10)

/** Текст из файла в пределах модуля: первая строка — наименование, остальное — описание. */
export function fitText(text: string): { text: string; cut: boolean } {
  const [first = '', ...rest] = text.split(NL)
  const description = rest.join(NL)
  const name = first.slice(0, MAX_NAME)
  const body = description.slice(0, MAX_DESCRIPTION)
  const cut = name.length < first.length || body.length < description.length
  return { text: rest.length ? [name, body].join(NL) : name, cut }
}

/** Модуль читает таблицу не дальше 10 000 строк (`MAX_TABLE_ROWS`): 10 001 — «строк больше». */
const TABLE_ROWS = 10_000

export function goodsCount(n: number): string {
  if (n > TABLE_ROWS) return 'больше 10 000 товаров'
  return `${n} ${plural(n, 'товар', 'товара', 'товаров')}`
}
