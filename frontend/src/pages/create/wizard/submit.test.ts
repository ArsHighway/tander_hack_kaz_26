import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { Issue } from '../../../api/v1.ts'
import { fieldId } from '../../../card/fieldId.ts'
import { fieldLabel } from '../createModel.ts'
import { PHOTO, PHOTO_FIELD_ID, firstProblem, missingRequired, notSentText, submitState } from './submit.ts'
import { field } from './testing.ts'

const issue = (severity: Issue['severity'], over: Partial<Issue> = {}): Issue =>
  ({ field: 'name', rule: 'r', message: 'm', severity, kind: 'mismatch', why: '', ...over }) as Issue

// «Российский размер» обязателен и пуст, сервер сказал о нём только warn (КТРУ).
const jacket = [
  field({ name: 'Цвет', value: 'черный' }),
  field({ name: 'Половой признак', value: 'Мужская', required: true }),
  field({ name: 'Российский размер', required: true }),
  field({ name: 'Назначение', value: '  ', required: true }),
]
const ktruRequired = issue('warn', { field: 'characteristics.Российский размер', kind: 'moderation_rule', message: 'Обязательная характеристика позиции КТРУ «Российский размер» не заполнена.' })
const colorConflict = issue('warn', { field: 'characteristics.Цвет', message: '«Цвет: черный» противоречит описанию: там «синяя».' })
const photoWarn = issue('warn', { field: 'photo', rule: 'у товара есть изображение', kind: 'moderation_rule', message: 'Нет фото товара: на портале изображение у товара обязательно.' })
const done = jacket.map((f) => ({ ...f, value: f.value.trim() || '52' }))

test('проверка идёт — ждём; не удалась — не «пройдены», а повторить', () => {
  assert.equal(submitState(null, []).state, 'checking')
  assert.equal(submitState('failed', []).state, 'failed')
})

test('блокирующее замечание запрещает отправку; без замечаний — пройдены', () => {
  assert.equal(submitState([issue('block')], []).state, 'blocked')
  assert.equal(submitState([], []).state, 'ok')
})

test('пустые обязательные поля — «Заполните», даже если сервер сказал о них warn или промолчал', () => {
  const r = submitState([ktruRequired], jacket)
  assert.equal(r.state, 'blocked')
  assert.deepEqual(r.missing, ['Российский размер', 'Назначение'])
  // warn о том же пустом поле второй раз не повторяется
  assert.deepEqual(r.warns, [])
  assert.equal(submitState([], jacket).state, 'blocked')
})

test('предупреждение — «Проверьте перед отправкой», а не «пройдены»; сведения отправке не мешают', () => {
  const note = issue('info', { field: 'characteristics.Цвет', kind: 'not_in_list' })
  const r = submitState([colorConflict, note], done)
  assert.equal(r.state, 'warn')
  assert.deepEqual(r.warns, [colorConflict])
  assert.deepEqual(r.notes, [note])
  assert.equal(submitState([note], done).state, 'ok')
})

test('пусто и предупреждения сразу: отправить нельзя, предупреждения не теряются', () => {
  const r = submitState([colorConflict, ktruRequired], jacket)
  assert.equal(r.state, 'blocked')
  assert.deepEqual(r.warns, [colorConflict])
})

test('«Отправить» ведёт к первому пустому обязательному, иначе к полю с блоком, иначе остаёмся', () => {
  assert.equal(firstProblem(submitState([], jacket), jacket), 'Российский размер')
  const block = issue('block', { field: 'characteristics.Цвет', kind: 'not_in_list' })
  assert.equal(firstProblem(submitState([issue('block'), block], done), done), 'Цвет')
  assert.equal(firstProblem(submitState([issue('block')], done), done), null)
  // поле, которого на экране нет, — не цель перехода
  assert.equal(firstProblem(submitState([issue('block', { field: 'characteristics.Нет такого' })], done), done), null)
})

test('строка «Не отправлено» у кнопки шага 4 исчезает, когда мешать перестало', () => {
  assert.equal(notSentText([], jacket), 'Не отправлено: заполните «Российский размер», «Назначение»')
  const many = ['А', 'Б', 'В', 'Г', 'Д'].map((name) => field({ name, required: true }))
  assert.equal(notSentText([], many), 'Не отправлено: заполните «А», «Б», «В» и ещё 2')
  assert.equal(notSentText([issue('block', { field: 'characteristics.Цвет' })], done), 'Не отправлено: исправьте «Цвет»')
  assert.equal(notSentText([colorConflict], done), null)
})

test('обязательное с пробелами — пустое', () => {
  assert.deepEqual(missingRequired(jacket), ['Российский размер', 'Назначение'])
})

test('без фото отправка разрешена: устаревшее предупреждение не создаёт пустое обязательное поле', () => {
  const r = submitState([photoWarn], done)
  assert.equal(r.state, 'ok')
  assert.deepEqual(r.missing, [])
  assert.deepEqual(r.warns, [])
  assert.equal(firstProblem(r, done), null)
  assert.equal(notSentText([photoWarn], done), null)
  assert.equal(submitState([], done).state, 'ok')
  assert.equal(fieldId(PHOTO), PHOTO_FIELD_ID)
})

test('необязательное фото не отменяет пустые обязательные характеристики и другие предупреждения', () => {
  const r = submitState([photoWarn, colorConflict, ktruRequired], jacket)
  assert.equal(r.state, 'blocked')
  assert.deepEqual(r.missing, ['Российский размер', 'Назначение'])
  assert.deepEqual(r.warns, [colorConflict])
  assert.equal(firstProblem(r, jacket), 'Российский размер')
})

test('блокирующая ошибка фотографии не скрывается вместе с предупреждением о её отсутствии', () => {
  const block = issue('block', { field: 'photo', message: 'Файл фотографии повреждён.' })
  const r = submitState([photoWarn, block], done)
  assert.equal(r.state, 'blocked')
  assert.deepEqual(r.blocks, [block])
})

test('другие предупреждения о фото сохраняются: необязательность не скрывает ошибки файла', () => {
  const warning = issue('warn', { field: 'photo', rule: 'формат изображения', message: 'Проверьте формат файла.' })
  const r = submitState([photoWarn, warning], done)
  assert.equal(r.state, 'warn')
  assert.deepEqual(r.warns, [warning])
})

test('замечание о фото подписано по-русски, как поле карточки', () => {
  assert.equal(fieldLabel('photo'), PHOTO)
})
