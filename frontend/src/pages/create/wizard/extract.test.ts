import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ApiError } from '../../../api/http.ts'
import { checkExtract, extractError, fitText, goodsCount, MAX_EXTRACT_BYTES } from './extract.ts'

test('файл больше 10 МБ не отправляется', () => {
  assert.equal(checkExtract({ size: MAX_EXTRACT_BYTES + 1 }), 'Файл больше 10 МБ')
  assert.equal(checkExtract({ size: MAX_EXTRACT_BYTES }), null)
})

test('ответ сервера переводится в подсказку', () => {
  assert.equal(extractError(new ApiError(413, 'too large')), 'Файл больше 10 МБ')
  assert.equal(
    extractError(new ApiError(415, 'unsupported')),
    'Такой файл не прочитать. Подойдут Word (.docx), PDF с текстом, Excel, CSV и обычный текст',
  )
  assert.equal(extractError(new ApiError(422, 'В PDF нет текстового слоя: это скан')), 'В PDF нет текстового слоя: это скан')
  assert.equal(extractError(new TypeError('Failed to fetch')), 'Не удалось прочитать файл, попробуйте ещё раз')
})

// Перевод строки в тексте поставщика.
const NL = String.fromCharCode(10)

test('текст из файла в пределах модуля не меняется', () => {
  const text = ['Бумага офисная А4', 'Плотность: 80 г/м2'].join(NL)
  assert.deepEqual(fitText(text), { text, cut: false })
})

test('длинный текст из файла обрезается до пределов модуля: наименование 1000, описание 20 000 знаков', () => {
  const long = fitText(['Б'.repeat(1500), 'о'.repeat(25_000)].join(NL))
  const [name, description] = long.text.split(NL)
  assert.deepEqual([name.length, description.length, long.cut], [1000, 20_000, true])
})

test('товаров в файле: счёт модуля, а за пределом чтения — «больше 10 000»', () => {
  assert.equal(goodsCount(3), '3 товара')
  assert.equal(goodsCount(10_000), '10000 товаров')
  assert.equal(goodsCount(10_001), 'больше 10 000 товаров')
})
