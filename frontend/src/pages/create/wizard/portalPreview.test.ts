import assert from 'node:assert/strict'
import { test } from 'node:test'
import { portalPreview } from './portalPreview.ts'
import { chain, field, state } from './testing.ts'

const general = (name: string, value = '') => field({ name, value, group: 'general' })

const base = {
  name: 'Бумага офисная Туринский ЦБЗ А4 500 л',
  fields: [
    general('Производитель', 'Туринский ЦБЗ'),
    general('Модель', 'Бумага офисная'),
    general('Страна происхождения'),
    field({ name: 'Формат', value: 'A4', required: true }),
    field({ name: 'Масса бумаги площадью 1 м²', value: '>=80 ... <90', unit: 'г/м2', unitName: 'Грамм на квадратный метр' }),
    field({ name: 'Количество листов в пачке', value: '500', unit: 'шт' }),
    field({ name: 'Марка', value: '  ' }),
  ],
  chain: chain({ portal_id: 793362891, path: ['Канцелярские товары', 'Бумага', 'Бумага для офисной техники'], spgz: ['83958722', '83958723'] }),
  photoUrl: null,
  ktruCode: null,
}

test('своё фото важнее картинки категории, без подписи', () => {
  const view = portalPreview({ ...base, photoUrl: '/api/demo/photos/p1' })
  // Своё фото не загрузилось (удалено очисткой) — превью переходит к картинке категории.
  assert.deepEqual(view.photo, { src: '/api/demo/photos/p1', kind: 'own', caption: null, fallback: '/api/v1/images/category/793362891' })
})

test('без своего фото — картинка категории портала с подписью', () => {
  assert.deepEqual(portalPreview(base).photo, {
    src: '/api/v1/images/category/793362891',
    kind: 'category',
    caption: 'картинка категории, пока нет вашего фото',
    fallback: null,
  })
})

test('ни фото, ни категории — заглушка', () => {
  assert.deepEqual(portalPreview({ ...base, chain: null }).photo, { src: null, kind: 'none', caption: null, fallback: null })
  assert.deepEqual(portalPreview({ ...base, chain: chain({ portal_id: null }) }).photo, { src: null, kind: 'none', caption: null, fallback: null })
})

test('сведения: ID после модерации, модель, производитель, страна; пустая — подсказка про шаг 4', () => {
  assert.deepEqual(portalPreview(base).facts, [
    { label: 'ID СТЕ', value: 'после модерации', missing: false },
    { label: 'Модель', value: 'Бумага офисная', missing: false },
    { label: 'Производитель', value: 'Туринский ЦБЗ', missing: false },
    { label: 'Страна происхождения', value: 'не указана, заполните на шаге 4', missing: true },
  ])
})

test('поля «Общих сведений» нет у позиции — «не указан» без отсылки к шагу 4', () => {
  const facts = portalPreview({ ...base, fields: base.fields.filter((f) => f.group !== 'general') }).facts
  assert.deepEqual(
    facts.slice(1).map((f) => [f.value, f.missing]),
    [
      ['не указана', true],
      ['не указан', true],
      ['не указана', true],
    ],
  )
})

test('характеристики: заполненные вне «Общих сведений», значение в формате КТРУ, единица по ОКЕИ', () => {
  assert.deepEqual(portalPreview(base).rows, [
    { name: 'Формат', value: 'A4', unit: null },
    { name: 'Масса бумаги площадью 1 м²', value: '≥ 80 и < 90', unit: 'Грамм на квадратный метр' },
    { name: 'Количество листов в пачке', value: '500', unit: 'шт' },
  ])
})

test('крошки — полный путь категории, заголовок — наименование', () => {
  const view = portalPreview(base)
  assert.deepEqual(view.crumbs, ['Канцелярские товары', 'Бумага', 'Бумага для офисной техники'])
  assert.equal(view.title, 'Бумага офисная Туринский ЦБЗ А4 500 л')
})

test('доп. сведения: ОКПД2, КПГЗ, КТРУ кодом, СПГЗ через запятую', () => {
  assert.deepEqual(portalPreview({ ...base, ktruCode: '17.12.14.129-00000017' }).extra, [
    { label: 'КТРУ', value: '17.12.14.129-00000017' },
    { label: 'КПГЗ', value: '01.15.02.01 (источник связи не указан)' },
    { label: 'ОКПД2', value: '17.12.14.129' },
    { label: 'СПГЗ', value: '83958722, 83958723' },
  ])
})

test('КТРУ нет — «нет в КТРУ»; СПГЗ нет — «нет»', () => {
  const extra = portalPreview({ ...base, chain: chain({ spgz: [], ktru_missing: 'no_links', ktru_linked: 0 }) }).extra
  assert.equal(extra.find((e) => e.label === 'КТРУ')?.value, 'нет в КТРУ')
  assert.equal(extra.find((e) => e.label === 'СПГЗ')?.value, 'нет')
})

test('позиции КТРУ категории не загружены в справочник модуля — как в живой карточке, а не «нет в КТРУ»', () => {
  const notLoaded = chain({ ktru: null, ktru_missing: 'not_loaded', ktru_linked: 3 })
  assert.equal(portalPreview({ ...base, chain: notLoaded }).extra.find((e) => e.label === 'КТРУ')?.value, 'нет в справочнике')
  const noLinks = chain({ ktru: null, ktru_missing: 'no_links', ktru_linked: 0 })
  assert.equal(portalPreview({ ...base, chain: noLinks }).extra.find((e) => e.label === 'КТРУ')?.value, 'нет в КТРУ')
})

test('позиция КТРУ ещё уточняется — так и написано, а не «нет в КТРУ»', () => {
  const asking = chain({ ktru: state({ question: { characteristic: 'Цвет', text: 'Цвет?', options: [] } as never, remaining: 3 }) })
  assert.equal(portalPreview({ ...base, chain: asking }).extra.find((e) => e.label === 'КТРУ')?.value, 'уточняется на шаге 3')
})

test('категория не выбрана — коды «не определён»', () => {
  const extra = portalPreview({ ...base, chain: null }).extra
  assert.deepEqual(
    extra.map((e) => e.value),
    ['уточняется на шаге 3', 'связи нет', 'не определён', 'нет'],
  )
})

test('позиции КТРУ подбирались, но ни одна не подошла — «нет в КТРУ», даже при неполном справочнике', () => {
  const noneFit = chain({ ktru: state({ candidates: [], steps: ['Ни одна не подходит'] }), ktru_missing: 'not_loaded', ktru_linked: 3 })
  assert.equal(portalPreview({ ...base, chain: noneFit }).extra.find((e) => e.label === 'КТРУ')?.value, 'нет в КТРУ')
})

test('сведение из одних пробелов — не указано', () => {
  const fields = [general('Производитель', '   '), ...base.fields.filter((f) => f.name !== 'Производитель')]
  assert.deepEqual(portalPreview({ ...base, fields }).facts.find((f) => f.label === 'Производитель'), {
    label: 'Производитель',
    value: 'не указан, заполните на шаге 4',
    missing: true,
  })
})

test('своя характеристика «Страна происхождения» у категории — тоже сведение превью', () => {
  // У 46 категорий страна — характеристика стандарта, а не поле «Общих сведений».
  const own = field({ name: 'Страна происхождения', value: 'Россия' })
  const fields = [...base.fields.filter((f) => f.name !== 'Страна происхождения'), own]
  assert.deepEqual(portalPreview({ ...base, fields }).facts.find((f) => f.label === 'Страна происхождения'), {
    label: 'Страна происхождения',
    value: 'Россия',
    missing: false,
  })
  // и не повторяется строкой в таблице характеристик
  assert.equal(portalPreview({ ...base, fields }).rows.some((r) => r.name === 'Страна происхождения'), false)
})
