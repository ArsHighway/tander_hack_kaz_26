import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { CharacteristicOut, Issue, Position } from '../../api/v1.ts'
import {
  cardCodes,
  categoryPath,
  codesText,
  codeWithName,
  acceptedFrom,
  acceptHints,
  acceptLabel,
  blocking,
  buildFields,
  cardFrom,
  fieldHelper,
  fieldsOf,
  issuesByField,
  joinPasted,
  mergeFields,
  pendingHints,
  splitPasted,
  submissionFields,
  withOwn,
  type FieldState,
} from './formModel.ts'

const char = (over: Partial<CharacteristicOut>): CharacteristicOut => ({
  name: 'Формат',
  value: 'А4',
  unit: null,
  source: null,
  status: 'filled',
  required: false,
  origin: 'portal',
  ...over,
})

const spec = (over: Partial<Position['characteristics'][number]>): Position['characteristics'][number] => ({
  name: 'Формат',
  kind: 'qualitative',
  unit: null,
  values: [],
  required: false,
  level_define: false,
  synonyms: [],
  ktru: [],
  spgz: [],
  ...over,
})

const position = (characteristics: Position['characteristics']): Position => ({
  code: '17.12.14.129-00000017',
  name: 'Бумага для офисной техники цветная',
  path: [],
  okpd2: '17.12.14.129',
  characteristics,
  source: 'demo',
})

const field = (over: Partial<FieldState>): FieldState => ({
  name: 'Формат',
  value: 'А4',
  initial: 'А4',
  unit: null,
  required: false,
  options: [],
  source: null,
  status: 'filled',
  normalizedFrom: null,
  method: null,
  note: null,
  own: null,
  group: null,
  unitName: null,
  compact: null,
  origin: 'portal',
  extra: false,
  hint: null,
  ...over,
})

const issue =(over: Partial<Issue>): Issue => ({
  field: 'characteristics.Формат',
  kind: 'not_in_list',
  severity: 'block',
  message: 'Значения «Белый» нет в перечне позиции.',
  why: 'w',
  rule: 'r',
  fix: null,
  ...over,
})

const span = { field: 'name' as const, start: 0, end: 7, text: 'цветная' }

test('перечень значений берётся из позиции по имени без учёта регистра', () => {
  const [f] = buildFields(
    [char({ name: 'Цветность', value: 'Цветная', source: span })],
    position([spec({ name: 'цветность', values: ['Белая', 'Цветная'], required: true })]),
  )
  assert.deepEqual(f.options, ['Белая', 'Цветная'])
  assert.equal(f.value, 'Цветная')
  assert.equal(f.initial, 'Цветная')
  assert.equal(f.required, true)
  assert.deepEqual(f.source, span)
})

test('у числовой характеристики нет перечня, единица берётся из позиции', () => {
  const [f] = buildFields(
    [char({ name: 'Количество листов', value: '500' })],
    position([spec({ name: 'Количество листов', kind: 'quantitative', unit: 'шт' })]),
  )
  assert.deepEqual(f.options, [])
  assert.equal(f.unit, 'шт')
})

test('группа и полное название единицы приходят из ответа модуля', () => {
  const [mass, maker] = buildFields(
    [
      char({ name: 'Масса', value: '80', unit: 'г/м2', unit_name: 'Грамм на квадратный метр' }),
      char({ name: 'Производитель', value: 'Туринский ЦБЗ', group: 'general' }),
    ],
    null,
  )
  assert.equal(mass.unitName, 'Грамм на квадратный метр')
  assert.equal(mass.group, null)
  assert.equal(maker.group, 'general')
  assert.equal(maker.unitName, null)
})

test('группа берётся из позиции, если модуль её не прислал', () => {
  const [f] = buildFields(
    [char({ name: 'Страна происхождения', value: 'Россия' })],
    position([spec({ name: 'Страна происхождения', group: 'general' })]),
  )
  assert.equal(f.group, 'general')
})

test('без позиции поле без перечня, ненайденное значение — пустая строка', () => {
  const [f] = buildFields([char({ value: null, status: 'not_found', required: true })], null)
  assert.deepEqual(f.options, [])
  assert.equal(f.value, '')
  assert.equal(f.initial, '')
  assert.equal(f.required, true)
})

test('после ответа на вопрос значение, изменённое человеком, остаётся', () => {
  const prev = [field({ name: 'Интенсивность', value: 'Неон', initial: 'Белый' }), field({ name: 'Формат', value: 'А4', initial: 'А4' })]
  const next = [field({ name: 'Интенсивность', value: 'Белый', initial: 'Белый' }), field({ name: 'Формат', value: 'А3', initial: 'А3' })]
  const merged = mergeFields(prev, next)
  assert.equal(merged[0].value, 'Неон')
  assert.equal(merged[0].initial, 'Белый')
  assert.equal(merged[1].value, 'А3')
})

test('в карточку идут только непустые значения', () => {
  const card = cardFrom('Бумага', 'описание', [field({ name: 'A', value: '1', unit: 'шт' }), field({ name: 'B', value: '  ' })], '17.12')
  assert.deepEqual(card, {
    name: 'Бумага',
    description: 'описание',
    category_code: '17.12',
    ktru_code: null,
    characteristics: [{ name: 'A', value: '1', unit: 'шт', origin: 'portal' }],
  })
})

test('свойство вне шаблона: свой блок, подпись источника и origin в карточке', () => {
  const [ktru, extra] = fieldsOf(
    {
      characteristics: [char({ name: 'Формат', origin: 'ktru' })],
      extra: [char({ name: 'Ручка противоскользящая', value: 'да', origin: 'new', method: 'rule', source: { field: 'description', start: 0, end: 27, text: 'Ручка противоскользящая: да' } })],
    },
    null,
  )
  assert.equal(ktru.extra, false)
  assert.equal(ktru.origin, 'ktru')
  assert.equal(extra.extra, true)
  assert.equal(fieldHelper(extra).text, 'Новая характеристика. Из описания: «Ручка противоскользящая: да»')
  const card = cardFrom('Молоток', '', [ktru, extra], '01.11')
  assert.deepEqual(
    card.characteristics?.map((c) => [c.name, c.origin]),
    [
      ['Формат', 'ktru'],
      ['Ручка противоскользящая', 'new'],
    ],
  )
})

test('изменённое человеком значение уходит без фрагмента текста', () => {
  const out = acceptedFrom([
    field({ name: 'A', value: 'Неон', initial: 'Белый', source: span, status: 'conflict', method: 'rule' }),
    field({ name: 'B', value: 'А4', initial: 'А4', source: span, method: 'rule' }),
    field({ name: 'C', value: '' }),
  ])
  assert.equal(out.length, 2)
  assert.deepEqual(
    { source: out[0].source, status: out[0].status, method: out[0].method, value: out[0].value },
    { source: null, status: 'filled', method: null, value: 'Неон' },
  )
  assert.deepEqual(out[1].source, span)
  assert.equal(out[1].method, 'rule')
})

test('замечания раскладываются по полям', () => {
  const byField = issuesByField([issue({ field: 'name' }), issue({ field: 'characteristics.Формат' }), issue({ field: 'ktru_code' })])
  assert.equal(byField.name.length, 1)
  assert.equal(byField.fields.get('Формат')?.length, 1)
  assert.equal(byField.other.length, 1)
})

test('подпись под полем: замечание важнее всего остального', () => {
  const f = field({ value: 'Неон', initial: 'Белый', source: span })
  assert.deepEqual(fieldHelper(f, issue({ severity: 'block' })), { text: 'Значения «Белый» нет в перечне позиции.', tone: 'error' })
  assert.equal(fieldHelper(f, issue({ severity: 'warn' })).tone, 'warn')
})

test('подпись под полем без замечания', () => {
  assert.deepEqual(fieldHelper(field({ value: 'Неон', initial: 'Белый' })), { text: 'Изменено вами', tone: 'plain' })
  assert.deepEqual(fieldHelper(field({ value: '', initial: '', required: true })), {
    text: 'Обязательная характеристика: в описании не нашли',
    tone: 'error',
  })
  assert.deepEqual(fieldHelper(field({ value: '', initial: '' })), { text: 'В описании не нашли, можно оставить пустым', tone: 'plain' })
  assert.deepEqual(fieldHelper(field({ normalizedFrom: '80', source: span, status: 'normalized' })), {
    text: 'Из «80» в описании, приведено к значению справочника',
    tone: 'source',
  })
  assert.deepEqual(fieldHelper(field({ method: 'answer' })), { text: 'Из вашего ответа на вопрос', tone: 'plain' })
  assert.deepEqual(fieldHelper(field({ source: span })), { text: 'Из описания: «цветная»', tone: 'source' })
})

// Подсказка похожих СТЕ и двойника — предложение, а не значение.
const hinted = () =>
  fieldsOf(
    {
      characteristics: [
        char({ name: 'Формат', value: 'А4', method: 'rule', source: span }),
        char({ name: 'Высота', value: '750', unit: 'мм', method: 'similar', note: 'как у 7 из 10 похожих СТЕ портала', required: true }),
        char({ name: 'Вес', value: '0,383', unit: 'кг', method: 'twin', note: 'как у такой же СТЕ №5001 на портале' }),
      ],
    },
    null,
  )

test('подсказка похожих СТЕ и двойника приходит пустым полем с предложением', () => {
  const [format, height, weight] = hinted()
  assert.deepEqual([format.value, format.hint], ['А4', null])
  assert.deepEqual([height.value, height.initial, height.hint, height.method], ['', '', '750', 'similar'])
  assert.deepEqual([weight.value, weight.hint], ['', '0,383'])
  assert.deepEqual(fieldHelper(height), { text: 'Подсказка: как у 7 из 10 похожих СТЕ портала', tone: 'plain' })
  assert.deepEqual(fieldHelper(weight), { text: 'Подсказка: как у такой же СТЕ №5001 на портале', tone: 'plain' })
  assert.equal(acceptLabel(height), 'Принять «750 мм»')
  assert.deepEqual(pendingHints(hinted()).map((f) => f.name), ['Высота', 'Вес'])
})

test('непринятая подсказка не идёт ни в карточку, ни в заявку, а обязательное поле с ней — пустое', () => {
  const fields = hinted()
  assert.deepEqual(cardFrom('Стол', '', fields, '01.16').characteristics?.map((c) => c.name), ['Формат'])
  assert.deepEqual(acceptedFrom(fields).map((c) => c.name), ['Формат'])
  assert.equal(blocking([], fields), 1)
})

test('«Принять» кладёт значение подсказки, оно уходит в карточку с пометкой источника', () => {
  const one = acceptHints(hinted(), ['Высота'])
  assert.deepEqual(one.map((f) => f.value), ['А4', '750', ''])
  assert.deepEqual(fieldHelper(one[1]), { text: 'Принято из подсказки: как у 7 из 10 похожих СТЕ портала. Проверьте', tone: 'plain' })
  assert.deepEqual(pendingHints(one).map((f) => f.name), ['Вес'])
  const all = acceptHints(hinted())
  assert.deepEqual(cardFrom('Стол', '', all, '01.16').characteristics?.map((c) => [c.name, c.value]), [
    ['Формат', 'А4'],
    ['Высота', '750'],
    ['Вес', '0,383'],
  ])
  const stored = acceptedFrom(all)
  assert.deepEqual(stored.map((c) => c.method), ['rule', 'similar', 'twin'])
  assert.equal(stored[1].note, 'как у 7 из 10 похожих СТЕ портала')
  // своё значение поверх подсказки — «Изменено вами», «Принять» к пустому полю не возвращает
  const own = all.map((f) => (f.name === 'Высота' ? { ...f, value: '760' } : f))
  assert.equal(fieldHelper(own[1]).text, 'Изменено вами')
  assert.equal(acceptedFrom(own)[1].method, null)
  assert.deepEqual(acceptHints(own, ['Высота'])[1].value, '760')
})

test('принятая подсказка переживает ответ на следующий вопрос', () => {
  const accepted = acceptHints(hinted(), ['Высота'])
  const merged = mergeFields(accepted, hinted())
  assert.deepEqual(merged.map((f) => f.value), ['А4', '750', ''])
})

test('значение поставщика из заявки или файла — не подсказка', () => {
  const label = 'Из вашей заявки'
  const [, height] = withOwn(hinted(), [{ name: 'Высота', value: '760', unit: 'мм' }], null, label)
  assert.deepEqual([height.value, height.hint, height.own], ['760', null, label])
})

test('замечание у поля не прячет, что значение принято из подсказки', () => {
  // у модератора «принял: 3 значения», а подпись видна у одного поля из трёх
  const [, height] = acceptHints(hinted(), ['Высота'])
  const info = issue({ field: 'characteristics.Высота', severity: 'info', message: 'В позиции КТРУ это записано иначе: «740».' })
  assert.deepEqual(fieldHelper(height, info), {
    text: 'В позиции КТРУ это записано иначе: «740». Принято из подсказки: как у 7 из 10 похожих СТЕ портала',
    tone: 'warn',
  })
  // своё значение и значение из текста — без пометки подсказки
  assert.equal(fieldHelper(field({ value: 'Неон', initial: 'Белый' }), info).text, info.message)
})

test('модератор видит принятую поставщиком подсказку с её источником', () => {
  const [f] = submissionFields(
    { name: 'Стол', description: '', characteristics: [{ name: 'Высота', value: '750', unit: 'мм' }] },
    [char({ name: 'Высота', value: '750', unit: 'мм', method: 'similar', note: 'как у 7 из 10 похожих СТЕ портала' })],
  )
  assert.equal(f.value, '750')
  assert.equal(fieldHelper(f).text, 'Принято из подсказки: как у 7 из 10 похожих СТЕ портала. Проверьте')
})

test('диапазон позиции — под пустым и исправленным полем, под значением из текста его нет', () => {
  const range = { min: 8, max: 120 }
  assert.deepEqual(fieldHelper(field({ value: '', initial: '', unit: 'мкм', range })), {
    text: 'В описании не нашли, можно оставить пустым. Допустимо: 8–120 мкм',
    tone: 'plain',
  })
  assert.equal(fieldHelper(field({ value: '30', initial: '20', unit: 'мкм', range })).text, 'Изменено вами. Допустимо: 8–120 мкм')
  assert.deepEqual(fieldHelper(field({ source: span, unit: 'мкм', range })), { text: 'Из описания: «цветная»', tone: 'source' })
})

test('диапазон приходит из ответа модуля, а без него — из позиции', () => {
  const pos = position([spec({ name: 'Толщина', kind: 'quantitative', unit: 'мкм', range: { min: 8, max: 120 } })])
  assert.deepEqual(buildFields([char({ name: 'Толщина', value: null })], pos)[0].range, { min: 8, max: 120 })
  const own = buildFields([char({ name: 'Толщина', value: null, range: { min: 10, max: 50 } })], pos)[0]
  assert.deepEqual(own.range, { min: 10, max: 50 })
})

test('мешает отправке: блокирующие замечания и пустые обязательные поля без замечания', () => {
  const fields = [
    field({ name: 'X', value: '', initial: '', required: true }),
    field({ name: 'Y', value: '', initial: '', required: true }),
    field({ name: 'Z', value: '1', initial: '1', required: true }),
  ]
  const issues = [issue({ field: 'characteristics.X', severity: 'block' }), issue({ field: 'name', severity: 'warn' })]
  assert.equal(blocking(issues, fields), 2)
})

test('вставленный текст: первая непустая строка — наименование, остальное — описание', () => {
  assert.deepEqual(splitPasted('  \nБумага А4\nМасса 80\n\nЛистов 500\n'), { name: 'Бумага А4', description: 'Масса 80\n\nЛистов 500' })
  assert.equal(joinPasted('Бумага', ''), 'Бумага')
  assert.equal(joinPasted('Бумага', 'Масса 80'), 'Бумага\nМасса 80')
})

test('невидимые символы из Word убираются, неразрывный пробел становится обычным', () => {
  const zwsp = String.fromCharCode(0x200b)
  const nbsp = String.fromCharCode(0xa0)
  assert.deepEqual(
    splitPasted(`Бу${zwsp}мага${nbsp}А4
опи${zwsp}сание`),
    { name: 'Бумага А4', description: 'описание' },
  )
})

test('модератор видит характеристики карточки, источник берётся из принятых только при совпадении', () => {
  const fields = submissionFields(
    {
      name: 'Бумага',
      description: '',
      characteristics: [
        { name: 'Формат', value: 'А4' },
        { name: 'Цветность', value: 'Белая' },
      ],
    },
    [char({ name: 'Формат', value: 'А4', source: span }), char({ name: 'ХАК', value: '<script>1</script>' })],
  )
  assert.deepEqual(
    fields.map((f) => [f.name, f.value, f.source !== null]),
    [
      ['Формат', 'А4', true],
      ['Цветность', 'Белая', false],
    ],
  )
})

const FILE = 'Из файла массовой загрузки'

test('значение поставщика из файла главнее найденного модулем, совпадение имени без учёта регистра', () => {
  const fields = [field({ name: 'Формат', value: 'А3', initial: 'А3', source: span, method: 'rule' }), field({ name: 'Цвет', value: '' , initial: '' })]
  const out = withOwn(fields, [{ name: 'формат', value: 'А4', unit: null }], null, FILE)
  assert.equal(out.length, 2)
  assert.equal(out[0].name, 'Формат')
  assert.equal(out[0].value, 'А4')
  assert.equal(out[0].initial, 'А4')
  assert.equal(out[0].source, null)
  assert.equal(out[0].method, null)
  assert.equal(out[0].own, FILE)
  assert.equal(out[1].own, null)
})

test('характеристика из файла, которой нет в ответе модуля, добавляется полем; пустые пропускаются', () => {
  const out = withOwn(
    [field({ name: 'Формат' })],
    [
      { name: 'Плотность', value: '80', unit: 'г/м2' },
      { name: 'Цвет', value: '  ', unit: null },
    ],
    position([spec({ name: 'Плотность', kind: 'quantitative', unit: 'г/м2', required: true })]),
    FILE,
  )
  assert.deepEqual(
    out.map((f) => [f.name, f.value, f.unit, f.required, f.own]),
    [
      ['Формат', 'А4', null, false, null],
      ['Плотность', '80', 'г/м2', true, FILE],
    ],
  )
})

test('значение поставщика не подсвечивается как взятое из текста и уходит в заявку без фрагмента', () => {
  const [f] = withOwn([field({ source: span, method: 'rule' })], [{ name: 'Формат', value: 'А4' }], null, FILE)
  assert.deepEqual(fieldHelper(f), { text: FILE, tone: 'plain' })
  assert.equal(acceptedFrom([f])[0].source, null)
})

test('после ответа на вопрос значения поставщика остаются, даже если модуль их не вернул', () => {
  const prev = withOwn([field({ name: 'Формат', value: 'А3', initial: 'А3' })], [{ name: 'Формат', value: 'А4' }, { name: 'Плотность', value: '80' }], null, FILE)
  const next = [field({ name: 'Формат', value: 'А3', initial: 'А3', required: true, options: ['А3', 'А4'] })]
  const merged = mergeFields(prev, next)
  assert.deepEqual(
    merged.map((f) => [f.name, f.value, f.own]),
    [
      ['Формат', 'А4', FILE],
      ['Плотность', '80', FILE],
    ],
  )
  assert.equal(merged[0].required, true)
  assert.deepEqual(merged[0].options, ['А3', 'А4'])
})

test('повторный ответ не дублирует поле поставщика при другом регистре имени', () => {
  const prev = withOwn([], [{ name: ' Формат ', value: 'А4' }], null, FILE)
  const merged = mergeFields(prev, [field({ name: 'формат', value: 'А3', initial: 'А3' })])
  assert.equal(merged.length, 1)
  assert.equal(merged[0].value, 'А4')
})

test('смена позиции убирает старые ответы, подсказки и исчезнувшие поля', () => {
  const prev = [
    field({ name: 'Материал', value: 'Деревянный', initial: 'Деревянный', method: 'answer' }),
    field({ name: 'Высота', value: '750', initial: '', hint: '750', method: 'similar', unit: 'мм' }),
    field({ name: 'Цвет', value: 'Красный', initial: '', own: FILE }),
  ]
  const next = [
    field({ name: 'Материал', value: '', initial: '' }),
    field({ name: 'Высота', value: '', initial: '', unit: 'мм' }),
  ]
  const merged = mergeFields(prev, next, true)
  assert.deepEqual(merged.map((f) => [f.name, f.value, f.hint, f.method]), [
    ['Материал', '', null, null], ['Высота', '', null, null],
  ])
})

test('смена позиции сохраняет совместимую ручную правку, очищая прежние источники и fit', () => {
  const prev = [field({ name: ' Высота ', value: '760', initial: '750', unit: 'мм', source: span, method: 'rule', ktruFit: 'in_range', ktruMatch: '700–800' })]
  const next = [field({ name: 'высота', value: '800', initial: '800', unit: 'мм', required: true, origin: 'ktru', options: ['750–900'], source: span, ktruFit: 'in_range', ktruMatch: '750–900', compact: '0,8 м' })]
  const [f] = mergeFields(prev, next, true)
  assert.deepEqual([f.value, f.unit, f.required, f.origin, f.options], ['760', 'мм', true, 'ktru', ['750–900']])
  assert.deepEqual([f.edited, f.source, f.method, f.ktruFit, f.ktruMatch, f.compact], [true, null, null, null, null, null])
})

test('смена единицы у новой позиции не переносит число буквально', () => {
  const [f] = mergeFields(
    [field({ name: 'Высота', value: '760', initial: '750', unit: 'мм' })],
    [field({ name: 'Высота', value: '', initial: '', unit: 'м' })], true,
  )
  assert.equal(f.value, '')
  assert.equal(f.unit, 'м')
})

test('после обычного ответа ручная единица сохраняется, новая подсказка не меняет происхождение', () => {
  const [f] = mergeFields(
    [field({ name: 'Высота', value: '0,75', initial: '700', unit: 'м', edited: true })],
    [field({ name: 'Высота', value: '', initial: '', unit: 'мм', unitName: 'Миллиметр', hint: '0,75', method: 'similar' })],
  )
  assert.deepEqual([f.value, f.unit, f.unitName, f.hint, f.method], ['0,75', 'м', null, null, null])
  assert.equal(fieldHelper(f).text, 'Изменено вами')
})

test('значение поставщика в другой единице не наследует название единицы модуля', () => {
  const module = field({ name: 'Толщина', value: '5', initial: '5', unit: 'мм', unitName: 'Миллиметр' })
  const [other] = withOwn([module], [{ name: 'Толщина', value: '0,5', unit: 'см' }], null, FILE)
  assert.deepEqual([other.unit, other.unitName], ['см', null])
  const [same] = withOwn([module], [{ name: 'Толщина', value: '6', unit: 'мм' }], null, FILE)
  assert.deepEqual([same.unit, same.unitName], ['мм', 'Миллиметр'])
})

test('у значения поставщика группа обновляется из новой позиции', () => {
  const own = withOwn([field({ name: 'Производитель', value: '', initial: '' })], [{ name: 'Производитель', value: 'Монди' }], null, FILE)
  const [merged] = mergeFields(own, [field({ name: 'Производитель', value: '', initial: '', group: 'general' })])
  assert.equal(merged.group, 'general')
  assert.equal(merged.value, 'Монди')
})

test('ответ на вопрос подписан шагом, на котором ответили', () => {
  const answered = field({ value: 'Белая', initial: 'Белая', method: 'answer' })
  assert.equal(fieldHelper(answered, undefined, 3).text, 'Из вашего ответа на шаге 3')
  assert.equal(fieldHelper(answered).text, 'Из вашего ответа на вопрос')
})

test('в карточку идут оба кода: категория этапа 1 и КТРУ этапа 2', () => {
  const card = cardFrom('Бумага', '', [field({ name: 'A', value: '1' })], '01.15.02.01', '17.12.14.129-00000031')
  assert.equal(card.category_code, '01.15.02.01')
  assert.equal(card.ktru_code, '17.12.14.129-00000031')
})

test('коды заявки: новые, старые с категорией в ktru_code, демо-справочник КТРУ', () => {
  assert.deepEqual(cardCodes({ category_code: '01.15.02.01', ktru_code: '17.12.14.129-00000031' }), { category: '01.15.02.01', ktru: '17.12.14.129-00000031' })
  assert.deepEqual(cardCodes({ ktru_code: '01.15.02.01' }), { category: '01.15.02.01', ktru: null })
  assert.deepEqual(cardCodes({ category_code: '17.12.14.129-00000031', ktru_code: '17.12.14.129-00000031' }), { category: null, ktru: '17.12.14.129-00000031' })
  assert.equal(codesText({ category_code: '01.15.02.01', ktru_code: null }), 'категория 01.15.02.01, позиция КТРУ не указана')
  assert.equal(codesText({ category_code: '01.15.02.01', ktru_code: '17.12.14.129-00000031' }), 'категория 01.15.02.01, КТРУ 17.12.14.129-00000031')
})

test('код с названием позиции: «код · название», без названия — один код', () => {
  assert.equal(codeWithName('31.01.10.000-00000005', 'Стол письменный'), '31.01.10.000-00000005 · Стол письменный')
  assert.equal(codeWithName('31.01.10.000-00000005', null), '31.01.10.000-00000005')
  assert.equal(codeWithName('31.01.10.000-00000005', '  '), '31.01.10.000-00000005')
})

test('путь категории для подписи: последний узел не повторяет название категории', () => {
  // Как в справочнике стандартов: путь кончается родителем, название категории — другое.
  const gloves = ['Товары медицинские', 'Медицинские расходные материалы', 'Перчатки медицинские']
  assert.equal(categoryPath(gloves, 'Перчатки смотровые/процедурные и хирургические'), 'Товары медицинские / Медицинские расходные материалы / Перчатки медицинские')
  assert.equal(categoryPath(['Мебель', 'Столы письменные'], 'Столы письменные'), 'Мебель')
  assert.equal(categoryPath(['Столы письменные'], 'столы письменные'), null)
  assert.equal(categoryPath([], 'Стол'), null)
  assert.equal(categoryPath(undefined), null)
})
