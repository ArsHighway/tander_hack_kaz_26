// Воспроизводимая проверка UI на контрактных ответах API. Это не проверка извлечения backend.
// playwright-cli --raw run-code --filename=e2e/wizard.js
async (page) => {
  const base = new URL(page.url()).origin
  const code = '31.01.12.150-00000001'
  const cat = '01.01.01'
  const logs = []
  const errors = []
  const onError = (e) => errors.push(String(e))
  page.on('pageerror', onError)
  const assert = (ok, message) => { if (!ok) throw new Error(message) }
  const candidate = (c, name) => ({ code: c, name, score: 1, why: [], matched: ['тумба'], missing: ['Высота'], conflicts: ['материал'], examples: [], similar: null })
  const state = (over = {}) => ({ candidates: [], remaining: 0, final_code: null, question: null, steps: [], trail: [], ...over })
  const spec = (name, over = {}) => ({ name, kind: 'qualitative', required: false, values: [], ktru: [code], spgz: [], ...over })
  const position = { code, name: 'Тумба офисная', okpd2: '31.01.12.150', source: 'ktru', characteristics: [spec('Материал', { required: true, values: ['Дерево', 'Металл'] }), spec('Высота', { kind: 'quantitative', required: true, range: { min: 400, max: 800 } })] }
  const char = (name, value, origin, over = {}) => ({ name, value, origin, unit: null, required: false, source: null, status: value ? 'filled' : 'not_found', ...over })
  const response = (question = false) => ({
    session_id: 'ui-contract', llm: 'off', degraded: [], issues: [], twins: [],
    ktru: state({ final_code: question ? null : cat, candidates: [candidate(cat, 'Тумба офисная')], remaining: 1,
      question: question ? { characteristic: 'Позиция КТРУ', options: [{ value: cat, label: 'Тумба по КТРУ', remaining: 1, cuts: 0, codes: [cat] }] } : null,
      trail: [{ label: 'Типы КТРУ', remaining: 12 }, { label: 'По описанию', remaining: 1 }] }),
    chain: question ? null : { category: cat, category_name: 'Тумба офисная', category_via: 'ktru_link', ktru_type: 'Тумба офисная', ktru_merged: [], kpgz: cat, okpd2: '31.01.12.150', spgz: [], path: [], ktru_linked: 1,
      ktru: state({ final_code: code, remaining: 1, candidates: [candidate(code, 'Тумба офисная')], trail: [{ label: 'Высота: 500 мм', remaining: 1 }] }) },
    characteristics: question ? [] : [char('Материал', 'глина', 'ktru', { required: true, ktru_fit: 'not_in_list', values: ['Дерево', 'Металл'] }), char('Высота', null, 'ktru', { required: true, unit: 'мм' }), char('Цвет', 'белый', 'portal')],
    extra: question ? [] : [char('Ручка противоскользящая', 'да', 'new')],
  })
  let checkFails = false
  let submissions = []
  const handler = async (route) => {
    const url = new URL(route.request().url())
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (url.pathname.endsWith('/autofill')) return json(response(true))
    if (url.pathname.endsWith('/answer')) return json(response())
    if (url.pathname.includes('/ktru/')) return json({ ...position, code: decodeURIComponent(url.pathname.split('/').pop()) })
    if (url.pathname.endsWith('/check')) {
      if (checkFails) return json({ detail: 'Проверка недоступна' }, 503)
      const body = route.request().postDataJSON()
      const clay = body.characteristics.some((c) => c.name === 'Материал' && c.value === 'глина')
      return json({ verdict: 'ready', issues: clay ? [{ field: 'characteristics.Материал', kind: 'not_in_list', severity: 'warn', message: 'Глина отсутствует в перечне КТРУ', why: 'Допустимы дерево и металл', rule: 'значение соответствует выбранной позиции КТРУ' }] : [] })
    }
    if (url.pathname.endsWith('/submissions') && route.request().method() === 'POST') {
      const body = route.request().postDataJSON()
      submissions.push(body)
      return json({ id: 9001, card: body.card ?? body, status: 'pending', issues: [], accepted: [], created_at: '2026-10-10T12:00:00Z' })
    }
    return route.continue()
  }
  const fresh = async () => {
    await page.goto(base)
    await page.evaluate(() => { sessionStorage.clear() })
    await page.reload()
    await page.locator('#describe-text').waitFor()
  }
  // Когда все обязательные заполнены, мастер сам переходит к проверке (после /check через 500 мс):
  // кнопку жмём, только если переход не случился.
  const toReview = async () => {
    await page.waitForTimeout(1500)
    const review = page.getByRole('button', { name: 'Проверить и отправить', exact: true })
    if (await review.isVisible()) await review.click()
  }
  const run = async (name, body) => {
    try { await body() } catch (e) { throw new Error([...logs, `FAIL ${name}: ${e.message}`].join(' | ')) }
    logs.push(`PASS ${name}`)
  }
  await page.route('**/api/**', handler)
  try {
    await run('пустой и пробельный ввод не отправляется', async () => {
      await fresh()
      await page.locator('#describe-text').fill('   \n  ')
      assert(await page.getByRole('button', { name: /^Заполнить карточку/ }).isDisabled(), 'Пробельный ввод доступен к отправке')
    })
    await run('название КТРУ, объяснения, ОКПД2, число позиций и воронка', async () => {
      await page.locator('#describe-text').fill('Тумба\nМатериал: глина')
      await page.getByRole('button', { name: /^Заполнить карточку/ }).click()
      await page.getByText('Тумба по КТРУ', { exact: true }).waitFor()
      await page.getByText('ОКПД2 31.01.12.150', { exact: true }).waitFor()
      const form = page.locator('main form')
      for (const text of ['Совпало: тумба', 'Нет в тексте: Высота', 'Противоречит: материал', '1 позиция КТРУ']) assert((await form.innerText()).includes(text), `Нет: ${text}`)
      assert(await form.locator('img').count() === 0, 'У кандидатов остались миниатюры')
      await form.getByRole('region', { name: 'Как выбрана позиция КТРУ' }).waitFor()
      await page.getByRole('radio').first().check({ force: true })
      await page.getByRole('button', { name: /^Выбрать тип товара/ }).click()
    })
    await run('разделы, точное число, нет в КТРУ и обязательные поля', async () => {
      for (const title of ['Общие сведения', 'Характеристики КТРУ', 'Характеристики портала', 'Новые характеристики']) await page.getByRole('heading', { name: title, exact: true }).waitFor()
      assert(await page.locator('[id="field-Высота"]').inputValue() === '', 'Вместо точного числа подставлен диапазон')
      assert((await page.locator('main').innerText()).includes('Нет в КТРУ'), 'Потеряна пометка глины')
      await page.getByRole('button', { name: 'Проверить и отправить', exact: true }).click()
      await page.getByText('Отправить пока нельзя', { exact: true }).waitFor()
      await page.getByRole('button', { name: 'Отправить на модерацию', exact: true }).click()
      await page.locator('[id="field-Высота"]').waitFor()
      assert(submissions.length === 0, 'Пустое обязательное поле ушло в заявку')
    })
    await run('итоговая сводка и отсутствие фото', async () => {
      await page.locator('[id="field-Высота"]').fill('500')
      await toReview()
      await page.getByRole('heading', { name: 'Проверка перед отправкой' }).waitFor()
      const summary = page.getByRole('region', { name: 'Сводка соответствия КТРУ' })
      await summary.getByText(/Обязательные: заполнено 2 из 2, вне КТРУ 1, новых 1/).waitFor()
      assert((await page.locator('main').innerText()).includes(`${cat} (по связи с КТРУ)`), 'Нет источника КПГЗ')
      await page.getByRole('button', { name: 'Отправить на модерацию', exact: true }).click()
      await page.getByText(/Заявка № 9001 у модератора/).waitFor()
      assert(submissions.length === 1, 'Не отправлена ровно одна заявка')
      const card = submissions[0].card ?? submissions[0]
      assert(card.photo_id == null, 'В заявку добавлено отсутствующее фото')
      assert(card.characteristics.some((c) => c.name === 'Материал' && c.value === 'глина'), 'Значение заменено при отправке')
    })
    await run('ошибка проверки не выдаётся за успешное соответствие', async () => {
      checkFails = true
      await fresh()
      await page.locator('#describe-text').fill('Тумба')
      await page.getByRole('button', { name: /^Заполнить карточку/ }).click()
      await page.getByRole('radio').first().check({ force: true })
      await page.getByRole('button', { name: /^Выбрать тип товара/ }).click()
      await page.locator('[id="field-Высота"]').fill('500')
      await toReview()
      await page.getByRole('button', { name: 'Повторить проверку' }).waitFor()
      assert(await page.getByRole('button', { name: 'Отправить на модерацию', exact: true }).isDisabled(), 'Ошибка проверки разрешает отправку')
      assert((await page.getByRole('region', { name: 'Сводка соответствия КТРУ' }).innerText()).includes('не проверено'), 'Ошибка показана как ноль отклонений')
    })
    await run('мобильный экран без горизонтальной прокрутки', async () => {
      await page.setViewportSize({ width: 390, height: 844 })
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Горизонтальная прокрутка')
    })
    assert(errors.length === 0, errors.join('\n'))
    return logs.join('\n')
  } finally {
    await page.unroute('**/api/**', handler)
    page.off('pageerror', onError)
    await page.setViewportSize({ width: 1366, height: 768 })
  }
}
