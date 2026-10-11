// Фото товара в заявке: проверка до загрузки и подсказки под полем.
import { ApiError } from '../../../api/http.ts'

/** Столько же принимает сервер (`MAX_PHOTO_BYTES` в backend/app/demo/photos.py). */
export const MAX_PHOTO_BYTES = 5 * 1024 * 1024

const TOO_BIG = 'Файл больше 5 МБ'
const WRONG_KIND = 'Подойдёт JPEG, PNG или WebP'
const ACCEPTED = new Set(['image/jpeg', 'image/png', 'image/webp'])

/** Что можно сказать до загрузки; пустой тип (браузер не знает) — решает сервер по содержимому файла. */
export function checkPhoto(file: { size: number; type: string }): string | null {
  if (file.size > MAX_PHOTO_BYTES) return TOO_BIG
  if (file.type && !ACCEPTED.has(file.type)) return WRONG_KIND
  return null
}

/** Ответ сервера на загрузку → подсказка под полем. */
export function photoError(e: unknown): string {
  if (e instanceof ApiError && e.status === 413) return TOO_BIG
  if (e instanceof ApiError && e.status === 415) return WRONG_KIND
  return 'Не удалось загрузить фото, попробуйте ещё раз'
}
