import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ApiError } from '../../../api/http.ts'
import { checkPhoto, MAX_PHOTO_BYTES, photoError } from './photo.ts'

test('фото больше 5 МБ не отправляется: ответ сразу', () => {
  assert.equal(checkPhoto({ size: MAX_PHOTO_BYTES + 1, type: 'image/jpeg' }), 'Файл больше 5 МБ')
  assert.equal(checkPhoto({ size: MAX_PHOTO_BYTES, type: 'image/jpeg' }), null)
})

test('явно не картинка нужного вида — сразу; без типа решает сервер по содержимому', () => {
  assert.equal(checkPhoto({ size: 10, type: 'image/gif' }), 'Подойдёт JPEG, PNG или WebP')
  assert.equal(checkPhoto({ size: 10, type: 'application/pdf' }), 'Подойдёт JPEG, PNG или WebP')
  for (const type of ['image/jpeg', 'image/png', 'image/webp', '']) assert.equal(checkPhoto({ size: 10, type }), null)
})

test('ответ сервера переводится в подсказку под полем', () => {
  assert.equal(photoError(new ApiError(413, 'too large')), 'Файл больше 5 МБ')
  assert.equal(photoError(new ApiError(415, 'nope')), 'Подойдёт JPEG, PNG или WebP')
  assert.equal(photoError(new ApiError(500, 'boom')), 'Не удалось загрузить фото, попробуйте ещё раз')
  assert.equal(photoError(new TypeError('Failed to fetch')), 'Не удалось загрузить фото, попробуйте ещё раз')
})
