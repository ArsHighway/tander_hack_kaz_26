// Регрессии мастера «Создание СТЕ» по ревью задач 5–7 (07.10.2026): сброс и возврат в каждом режиме входа,
// запрос в полёте, сбой проверки, повторные ответы, переход от фрагмента текста к полю; честный шаг 5
// (сценарии 20–21: «Заполните», «Проверьте перед отправкой», «Отправить» ведёт к полю; 22 — отправка без фото; 23 — необязательное фото;
// 24–25 — подсказки похожих СТЕ только по «Принять»).
// Запуск на поднятом стеке: playwright-cli open http://localhost:8080/ && playwright-cli --raw run-code --filename=e2e/wizard.js
// (на dev-сервере — BASE = 'http://127.0.0.1:5173/'). Каждый сценарий печатает PASS или FAIL с подробностью.
// eslint-disable-next-line no-unused-expressions
async page => {
  const BASE = 'http://localhost:8080/'
  // Пустой мастер: черновик вкладки (`createDraft.ts`) восстановил бы прошлую карточку.
  const fresh = async () => {
    await page.goto(BASE)
    await page.evaluate(() => sessionStorage.removeItem('create:draft:v1'))
    await page.goto(BASE)
  }
  const out = []
  const idle = () =>
    page.waitForFunction(() => !document.querySelector('[aria-busy="true"]') && !/Проверяем ответ|Заполняем…|Ищем категорию/.test(document.body.innerText), null, { timeout: 120000 })
  const start = async (text, width = 1366) => {
    await page.unrouteAll({ behavior: 'ignoreErrors' })
    await page.goto('about:blank')
    await fresh()
    await page.evaluate(() => sessionStorage.clear())
    await page.setViewportSize({ width, height: 800 })
    if (text) {
      await page.locator('#describe-text').fill(text)
      await page.locator('#describe-text').press('Control+Enter')
      await idle()
      await lever(text)
    }
  }
  // Смеситель, у которого позиция КТРУ по тексту не определилась, доходит до вопроса «Тип управления»:
  // сценарии про шаг 4 отвечают на него сами.
  const lever = async (text) => {
    if (/^Смеситель/.test(text) && (await page.getByText('Рычажный', { exact: true }).count())) await choose('Рычажный')
  }
  const choose = async (title) => {
    await page.getByText(title, { exact: true }).click()
    await page.locator('main button.MuiButton-contained').first().click()
    await idle()
  }
  const flashed = () => page.evaluate(() => [...document.querySelectorAll('aside dl > div')].filter((d) => getComputedStyle(d).backgroundColor !== 'rgba(0, 0, 0, 0)').length)
  const run = async (name, body) => {
    try {
      const r = await body()
      out.push(`${r.ok ? 'PASS' : 'FAIL'} ${name}: ${r.why}`)
    } catch (e) {
      out.push(`FAIL ${name}: ${String(e).split('\n')[0]}`)
    }
  }

  await run('1 «Изменить описание» после префилла', async () => {
    await page.goto('about:blank')
    await fresh()
    await page.evaluate(() => {
      sessionStorage.clear()
      sessionStorage.setItem('create:prefill', JSON.stringify({ name: 'Смеситель для кухни однорычажный', description: '', characteristics: [], revise: { id: 7, note: 'Уточните материал.' }, photoId: null }))
    })
    await page.goto(BASE)
    await idle()
    await page.locator('aside').getByRole('tab', { name: 'Ваш текст' }).click()
    await page.locator('aside').getByRole('button', { name: 'Изменить описание' }).click()
    await page.waitForTimeout(2500)
    const field = await page.locator('#describe-text').count()
    const busy = await page.locator('[aria-busy="true"]').count()
    return { ok: field === 1 && busy === 0, why: `поле шага 1: ${field}, загрузка: ${busy}` }
  })

  await run('2 ответ после «Начать заново» не возвращает товар', async () => {
    await start('Зерно пшеницы продовольственное, ГОСТ 9353')
    await page.route('**/api/v1/autofill/*/answer', async (route) => {
      await new Promise((r) => setTimeout(r, 2500))
      await route.continue()
    })
    await page.getByText('Зерно пшеницы', { exact: true }).click()
    await page.locator('main button.MuiButton-contained').first().click()
    await page.waitForTimeout(300)
    await page.getByRole('button', { name: 'Начать заново' }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Начать заново' }).click()
    await page.locator('#describe-text').fill('Тест')
    await page.waitForTimeout(5000)
    const h1 = await page.locator('h1').innerText()
    const text = await page.locator('#describe-text').inputValue().catch(() => '(поля нет)')
    return { ok: h1 === 'Новая СТЕ' && text === 'Тест', why: `заголовок «${h1}», поле «${text}»` }
  })

  await run('3 Enter вне вопроса не отправляет ответ', async () => {
    await start('Зерно пшеницы продовольственное, ГОСТ 9353')
    await choose('Зерно пшеницы')
    await page.getByText('Мягкая озимая белозерная', { exact: true }).click()
    const sent = []
    page.on('request', (r) => {
      if (r.url().includes('/answer') && r.method() === 'POST') sent.push(r.url())
    })
    await page.evaluate(() => {
      const s = document.createElement('span')
      s.tabIndex = 0
      s.id = 'probe-outside'
      s.textContent = 'вне вопроса'
      document.querySelector('aside').appendChild(s)
      s.focus()
    })
    await page.keyboard.press('Enter')
    await page.waitForTimeout(1200)
    return { ok: sent.length === 0, why: `запросов /answer: ${sent.length}` }
  })

  await run('4 сбой /check — не «пройдены», отправка выключена', async () => {
    await start('Смеситель для кухни однорычажный с выдвижной лейкой, хром, латунь, керамический картридж 35 мм')
    await page.route('**/api/v1/check', (route) => route.fulfill({ status: 500, body: '{"detail":"boom"}', contentType: 'application/json' }))
    await page.getByRole('button', { name: 'Проверить и отправить' }).click()
    await page.waitForTimeout(1500)
    const passed = await page.getByText('Правила модерации портала пройдены').count()
    const send = page.getByRole('button', { name: 'Отправить на модерацию' })
    const disabled = await send.isDisabled()
    const retry = await page.getByRole('button', { name: 'Повторить проверку' }).count()
    return { ok: passed === 0 && disabled && retry === 1, why: `«пройдены»: ${passed}, отправка выключена: ${disabled}, «Повторить проверку»: ${retry}` }
  })

  await run('5 повторный ответ на шаге 3 запоминается', async () => {
    await start('Зерно пшеницы продовольственное, ГОСТ 9353')
    await choose('Зерно пшеницы')
    await choose('Мягкая озимая белозерная')
    await page.getByRole('button', { name: /^Позиция КТРУ: .*Изменить$/ }).click()
    await choose('Мягкая яровая белозерная')
    await page.getByRole('button', { name: /^Позиция КТРУ: .*Изменить$/ }).click()
    const checked = await page.evaluate(() => {
      const r = document.querySelector('main input[type=radio]:checked')
      return r ? r.closest('label').innerText.split('\n')[0] : '(ничего)'
    })
    return { ok: checked === 'Мягкая яровая белозерная', why: `отмечено «${checked}»` }
  })

  await run('6 фрагмент ведёт к свёрнутому необязательному полю', async () => {
    await start('Смеситель для кухни однорычажный с выдвижной лейкой, хром, латунь, керамический картридж 35 мм')
    await page.locator('aside').getByRole('tab', { name: 'Ваш текст' }).click()
    const mark = page.locator('aside mark', { hasText: 'хром' }).first()
    const label = (await mark.getAttribute('aria-label')) ?? ''
    const field = label.match(/поле «(.+)»/)?.[1] ?? '?'
    await mark.click()
    await page.waitForTimeout(400)
    const focused = await page.evaluate(() => document.activeElement?.id ?? '')
    return { ok: focused === 'field-' + field.replace(/\s+/g, '_'), why: `поле «${field}», фокус на «${focused}»` }
  })

  await run('6b на шагах без полей фрагменты не кнопки', async () => {
    await start('Смеситель для кухни однорычажный с выдвижной лейкой, хром, латунь, керамический картридж 35 мм')
    await page.locator('aside').getByRole('tab', { name: 'Ваш текст' }).click()
    const onStep4 = await page.locator('aside mark[role=button]').count()
    await page.getByRole('button', { name: 'Проверить и отправить' }).click()
    await page.waitForTimeout(800)
    await page.locator('aside').getByRole('tab', { name: 'Ваш текст' }).click()
    const onStep5 = await page.locator('aside mark[role=button]').count()
    const marks = await page.locator('aside mark').count()
    return { ok: onStep4 > 0 && onStep5 === 0 && marks > 0, why: `кнопок на шаге 4: ${onStep4}, на шаге 5: ${onStep5} из ${marks}` }
  })

  await run('9 тот же вариант на пройденном шаге 2 не начинает заново', async () => {
    await start('Зерно пшеницы продовольственное, ГОСТ 9353')
    await choose('Зерно пшеницы')
    const posts = []
    page.on('request', (r) => {
      if (r.url().endsWith('/api/v1/autofill') && r.method() === 'POST') posts.push(r.url())
    })
    await page.getByRole('button', { name: /^Категория: .*Изменить$/ }).click()
    await page.locator('main button.MuiButton-contained').first().click()
    await page.waitForTimeout(1500)
    const h2 = await page.locator('main h2').first().innerText().catch(() => '')
    return { ok: posts.length === 0, why: `новых /autofill: ${posts.length}, на экране «${h2}»` }
  })

  await run('11 подсветка гаснет и после правки поля', async () => {
    // Ответ про позицию КТРУ заполняет «Наименование типа» — строка карточки подсвечивается
    await start('Зерно пшеницы продовольственное, ГОСТ 9353')
    await choose('Зерно пшеницы')
    await choose('Мягкая озимая белозерная')
    await page.waitForTimeout(450) // подсветка нарастает за 400 мс
    const at = await flashed()
    await page.getByRole('textbox', { name: 'Наименование', exact: true }).fill('Зерно пшеницы 3 класса')
    await page.waitForTimeout(2500)
    const after = await flashed()
    return { ok: at > 0 && after === 0, why: `сразу: ${at}, через 2,5 с после правки: ${after}` }
  })

  await run('12 наименование в карточке целиком', async () => {
    await start('Бумага офисная для лазерных и струйных принтеров и копировальных аппаратов Снегурочка А4 80 г/м2 500 листов белизна 146% CIE класс C, упаковка 5 пачек, артикул SNEGUROCHKA-A4-80G-500L-BOX5-0012345678')
    const clamp = await page.evaluate(() => {
      const el = [...document.querySelectorAll('aside p')].find((p) => p.textContent.startsWith('Бумага офисная для лазерных'))
      return el ? getComputedStyle(el).webkitLineClamp : 'нет'
    })
    return { ok: clamp === 'none', why: `line-clamp: ${clamp}` }
  })

  await run('13 «не найдено» — без «ждёт ответа»', async () => {
    await start('Ремонт автомобиля')
    const waiting = await page.locator('aside').getByText('ждёт ответа').count()
    return { ok: waiting === 0, why: `«ждёт ответа»: ${waiting}` }
  })

  const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
  const photo = () => ({ name: 'photo.png', mimeType: 'image/png', buffer: Buffer.from(PNG, 'base64') })
  const MIXER = 'Смеситель для кухни однорычажный с выдвижной лейкой, хром, латунь, керамический картридж 35 мм'

  await run('14 фото, загруженное до «Начать заново», не цепляется к новой карточке', async () => {
    await start(MIXER)
    await page.route('**/api/demo/photos', async (route) => {
      await new Promise((r) => setTimeout(r, 2500))
      await route.continue()
    })
    await page.locator('input[type=file][accept^="image"]').setInputFiles(photo())
    await page.waitForTimeout(300)
    await page.getByRole('button', { name: 'Начать заново' }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Начать заново' }).click()
    await page.locator('#describe-text').fill(MIXER)
    await page.locator('#describe-text').press('Control+Enter')
    await idle()
    await lever(MIXER)
    await page.waitForTimeout(3000)
    const thumbs = await page.locator('[role=group] img').count()
    const label = await page.getByRole('group', { name: 'Фото товара' }).getByRole('button').first().innerText()
    return { ok: thumbs === 0 && label === 'Загрузить фото', why: `миниатюр: ${thumbs}, кнопка «${label}»` }
  })

  await run('15 пока фото загружается, к отправке не перейти', async () => {
    await start(MIXER)
    await page.route('**/api/demo/photos', async (route) => {
      await new Promise((r) => setTimeout(r, 2000))
      await route.continue()
    })
    await page.locator('input[type=file][accept^="image"]').setInputFiles(photo())
    await page.waitForTimeout(300)
    const during = await page.getByRole('button', { name: 'Проверить и отправить' }).isDisabled()
    await page.waitForTimeout(2600)
    const after = await page.getByRole('button', { name: 'Проверить и отправить' }).isDisabled()
    return { ok: during && !after, why: `во время загрузки выключена: ${during}, после: ${after ? 'выключена' : 'включена'}` }
  })

  await run('16 сбой ответа модуля — «Повторить» отправляет тот же ответ', async () => {
    await start('Зерно пшеницы продовольственное, ГОСТ 9353')
    let failed = 0
    await page.route('**/api/v1/autofill/*/answer', async (route) => {
      if (failed++ === 0) return route.fulfill({ status: 500, body: '{"detail":"сбой модуля"}', contentType: 'application/json' })
      return route.continue()
    })
    await page.getByText('Зерно пшеницы', { exact: true }).click()
    await page.locator('main button.MuiButton-contained').first().click()
    await page.getByRole('button', { name: 'Повторить' }).click({ timeout: 10000 })
    await idle()
    const question = await page.getByText('Мягкая озимая белозерная', { exact: true }).count()
    return { ok: failed === 2 && question === 1, why: `запросов /answer: ${failed}, вопрос шага 3 на экране: ${question}` }
  })

  await run('17 на телефоне пройденный шаг тоже открывается', async () => {
    await start('Зерно пшеницы продовольственное, ГОСТ 9353', 390)
    await page.getByText('Зерно пшеницы', { exact: true }).click()
    await page.locator('main button.MuiButton-contained').first().click()
    await idle()
    const nav = page.getByRole('navigation', { name: 'Шаги создания СТЕ' })
    await nav.getByRole('button', { name: /^Категория: .*Изменить$/ }).click({ timeout: 10000 })
    const heading = await page.locator('main h2').first().innerText()
    await page.setViewportSize({ width: 1366, height: 800 })
    return { ok: heading === 'Выберите категорию товара', why: `на экране «${heading}»` }
  })

  await run('18 после «Ни одна не подходит» на шаге 3 ответ можно изменить', async () => {
    await start('Зерно пшеницы продовольственное, ГОСТ 9353')
    await choose('Зерно пшеницы')
    await page.getByRole('button', { name: 'Ни одна не подходит' }).click()
    await idle()
    await page.getByRole('button', { name: /^Позиция КТРУ: .*Изменить$/ }).click({ timeout: 10000 })
    await choose('Мягкая озимая белозерная')
    const strip = (await page.locator('nav[aria-label="Шаги создания СТЕ"]').innerText()).replace(/\s+/g, ' ')
    return { ok: strip.includes('01.11.10.000-00000003'), why: strip.slice(0, 160) }
  })

  await run('19 фото, загружавшееся во время «Изменить описание», остаётся у товара', async () => {
    await start(MIXER)
    await page.route('**/api/demo/photos', async (route) => {
      await new Promise((r) => setTimeout(r, 2500))
      await route.continue()
    })
    await page.locator('input[type=file][accept^="image"]').setInputFiles(photo())
    await page.waitForTimeout(300)
    await page.locator('aside').getByRole('tab', { name: 'Ваш текст' }).click()
    await page.locator('aside').getByRole('button', { name: 'Изменить описание' }).click()
    await page.locator('#describe-text').press('Control+Enter')
    await idle()
    await lever(MIXER)
    await page.waitForTimeout(3000)
    const thumbs = await page.locator('[role=group] img').count()
    const send = await page.getByRole('button', { name: 'Проверить и отправить' }).isDisabled()
    return { ok: thumbs === 1 && !send, why: `миниатюр: ${thumbs}, «Проверить и отправить» выключена: ${send}` }
  })

  // Шаг 5 не говорит «пройдены», пока пусты обязательные поля или есть предупреждения.
  // Вопросы категории и КТРУ — первым вариантом (клавиши 1 и Enter): сценарии про шаг 5, а не про сужение.
  const SEND = 'Отправить на модерацию'
  const toFields = async () => {
    for (let k = 0; k < 6; k++) {
      if (await page.getByRole('button', { name: new RegExp(`^(Проверить и отправить|${SEND})$`) }).count()) return true
      if (!(await page.locator('main input[type=radio]').count())) return false
      await page.keyboard.press('1')
      await page.keyboard.press('Enter')
      await idle()
    }
    return false
  }
  const reviewed = () => page.waitForFunction(() => !/Проверяем карточку/.test(document.body.innerText), null, { timeout: 30000 })
  // Обязательные из подсказок похожих СТЕ (у смесителя «Монтаж», «Материал», «Тип управления») поставщик принимает
  // сам: сценарии 21–23 — о шаге 5, а не о подсказках.
  const acceptRequired = async () => {
    await page.waitForTimeout(300) // мастер мог ещё не перейти сам на шаг 5
    if (await page.getByRole('button', { name: SEND }).count()) await page.getByRole('button', { name: 'Вернуться к характеристикам' }).click()
    const section = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Обязательные', exact: true }) })
    const all = section.getByRole('button', { name: /^Принять все/ })
    if (await all.count()) await all.first().click()
    const pick = section.getByRole('button', { name: /^Принять «/ })
    while (await pick.count()) await pick.first().click()
  }
  const attachPhoto = async () => {
    await page.waitForTimeout(300) // мастер мог ещё не перейти сам на шаг 5
    if (await page.getByRole('button', { name: SEND }).count()) await page.getByRole('button', { name: 'Вернуться к характеристикам' }).click()
    await page.locator('input[type=file][accept^="image"]').setInputFiles(photo())
    await page.getByRole('group', { name: 'Фото товара' }).locator('img').waitFor({ timeout: 30000 })
  }

  await run('20 шаг 5: пустые обязательные — красное «Заполните», «Отправить» ведёт к полю', async () => {
    await start('Куртка мужская зимняя чёрная\nЦвет: синий. Материал: хлопок. Размер 52. Вес 15 кг')
    if (!(await toFields())) return { ok: false, why: `не дошли до характеристик: ${(await page.locator('main').innerText()).slice(0, 160)}` }
    // Фото загружаем отдельно; характеристики не трогаем.
    await attachPhoto()
    const review = page.getByRole('button', { name: 'Проверить и отправить' })
    if (await review.count()) await review.click()
    await page.getByRole('button', { name: SEND }).waitFor()
    await reviewed()
    const red = (await page.locator('.MuiAlert-colorError').allInnerTexts()).join(' | ').replace(/\s+/g, ' ')
    const green = await page.getByText('Правила модерации портала пройдены').count()
    // Первая ссылка красной плашки — первое пустое обязательное поле: туда же должна вести «Отправить».
    const link = page.locator('.MuiAlert-colorError button').first()
    const target = (await link.count()) ? (await link.innerText()).trim() : ''
    const want = `field-${target.replace(/\s+/g, '_')}`
    await page.getByRole('button', { name: SEND }).click()
    await page.waitForTimeout(500)
    const focused = await page.evaluate(() => document.activeElement?.id ?? '')
    const note = (await page.getByRole('status').filter({ hasText: 'Не отправлено' }).allInnerTexts()).join(' | ')
    const sent = await page.getByText(/у модератора/).count()
    const ok = red.includes('Заполните:') && target !== '' && green === 0 && focused === want && note.includes('заполните') && sent === 0
    return { ok, why: `красная: ${red.slice(0, 160)}; «пройдены»: ${green}; фокус: ${focused} (ждали ${want}); у кнопки: ${note}; отправлено: ${sent}` }
  })

  // Предупреждение подставлено в ответ /check: сценарий о шаге 5, а не о том, какие замечания даёт модуль.
  await run('21 шаг 5: предупреждение модуля — оранжевое «Проверьте перед отправкой», отправить можно', async () => {
    await start(MIXER)
    if (!(await toFields())) return { ok: false, why: `не дошли до характеристик: ${(await page.locator('main').innerText()).slice(0, 160)}` }
    // Проверяем предупреждение о характеристике с прикреплённым фото.
    await acceptRequired()
    await attachPhoto()
    await page.route('**/api/v1/check', async (route) => {
      const res = await route.fetch()
      const body = await res.json()
      const first = (route.request().postDataJSON().characteristics || [])[0]
      const name = first ? first.name : 'Высота'
      const warn = {
        field: `characteristics.${name}`,
        kind: 'mismatch',
        severity: 'warn',
        message: `«${name}: 7500 мм» — необычно для категории: у СТЕ портала не больше 1800 мм.`,
        why: 'e2e',
        rule: 'число в пределах, обычных для СТЕ категории',
        fix: null,
      }
      await route.fulfill({ response: res, json: { ...body, issues: [...body.issues.filter((i) => i.severity !== 'block'), warn] } })
    })
    // Мастер мог сам открыть шаг 5 до подмены: проверка — заново.
    if (await page.getByRole('button', { name: SEND }).count()) await page.getByRole('button', { name: 'Вернуться к характеристикам' }).click()
    await page.getByRole('button', { name: 'Проверить и отправить' }).click()
    await page.getByRole('button', { name: SEND }).waitFor()
    await reviewed()
    const orange = (await page.locator('.MuiAlert-colorWarning').allInnerTexts()).join(' | ').replace(/\s+/g, ' ')
    const green = await page.getByText('Правила модерации портала пройдены').count()
    const red = await page.locator('.MuiAlert-colorError').count()
    const enabled = await page.getByRole('button', { name: SEND }).isEnabled()
    await page.unrouteAll({ behavior: 'ignoreErrors' })
    const ok = orange.includes('Проверьте перед отправкой') && orange.includes('необычно для категории') && green === 0 && red === 0 && enabled && orange.includes('Если всё верно')
    return { ok, why: `оранжевая: ${orange.slice(0, 200)}; «пройдены»: ${green}; красных: ${red}; отправка включена: ${enabled}` }
  })

  await run('22 фото необязательно: карточка отправляется без изображения', async () => {
    await start(MIXER)
    if (!(await toFields())) return { ok: false, why: `не дошли до характеристик: ${(await page.locator('main').innerText()).slice(0, 160)}` }
    await acceptRequired()
    const label = await page.getByRole('group', { name: 'Фото товара' }).innerText().catch(() => '')
    const review = page.getByRole('button', { name: 'Проверить и отправить' })
    if (await review.count()) await review.click()
    await page.getByRole('button', { name: SEND }).waitFor()
    await reviewed()
    const red = (await page.locator('.MuiAlert-colorError').allInnerTexts()).join(' | ').replace(/\s+/g, ' ')
    const response = page.waitForResponse((r) => r.url().endsWith('/api/demo/submissions') && r.request().method() === 'POST', { timeout: 30000 })
    await page.getByRole('button', { name: SEND }).click()
    const sent = await response
    const body = sent.request().postDataJSON()
    await page.getByText(/№\s*\d+ у модератора/).waitFor({ timeout: 30000 })
    const ok = label.includes('необязательно') && !red.includes('Фото товара') && sent.ok() && body.card.photo_id == null
    return { ok, why: `фото необязательно: ${label.includes('необязательно')}; ответ отправки: ${sent.status()}; photo_id: ${body.card.photo_id}` }
  })

  await run('23 необязательное фото можно прикрепить на шаге характеристик', async () => {
    await start(MIXER)
    if (!(await toFields())) return { ok: false, why: `не дошли до характеристик: ${(await page.locator('main').innerText()).slice(0, 160)}` }
    await acceptRequired()
    await attachPhoto()
    const request = page.waitForRequest((r) => r.url().endsWith('/api/v1/check') && r.method() === 'POST')
    await page.getByRole('button', { name: 'Проверить и отправить' }).click()
    const checked = await request
    await page.getByRole('button', { name: SEND }).waitFor()
    await reviewed()
    const red = await page.locator('.MuiAlert-colorError').count()
    const onStep5 = await page.getByRole('button', { name: SEND }).count()
    const enabled = onStep5 ? await page.getByRole('button', { name: SEND }).isEnabled() : false
    const photoId = checked.postDataJSON()?.photo_id
    const ok = photoId != null && red === 0 && onStep5 === 1 && enabled
    return { ok, why: `/check с photo_id: ${photoId}; красных: ${red}; на шаге 5: ${onStep5}; отправка включена: ${enabled}` }
  })

  // Подсказка похожих СТЕ и двойника — предложение. Поле пустое, рядом «Принять «…»»;
  // в /check и в заявку идут только принятые. Товар, по которому на портале есть такая же СТЕ, — подсказок больше
  // десятка, а из текста ни одной характеристики (без «Принять» не отправить: меньше 4). Кровать — из текста 11
  // характеристик и 8 подсказок: отправляется и без них.
  const TWIN = 'Концентрированный усилитель стирки от жировых и масляных загрязнений с инновационной системой оптического осветлителя ДЕРВАЛ БРАЙТ'
  const BED = 'Кровать металлическая одноярусная\nСпальное место 1900х800 мм, каркас металлический'
  // Подсказки ответа модуля: имя поля → значение (`method: similar`, `twin`).
  const hintsOf = (body) => new Map([...body.characteristics, ...(body.extra || [])].filter((c) => (c.method === 'similar' || c.method === 'twin') && c.value).map((c) => [c.name, c.value]))
  const startWithHints = async (text) => {
    let hints = new Map()
    const seen = (r) => {
      if (r.url().endsWith('/api/v1/autofill') && r.request().method() === 'POST') r.json().then((b) => (hints = hintsOf(b))).catch(() => undefined)
    }
    page.on('response', seen)
    await start(text)
    await page.waitForTimeout(300) // тело ответа читается асинхронно
    page.off('response', seen)
    return hints
  }
  const lastCheck = () => {
    const bodies = []
    const onReq = (r) => r.url().endsWith('/api/v1/check') && r.method() === 'POST' && bodies.push(r.postDataJSON())
    page.on('request', onReq)
    return { bodies, stop: () => page.off('request', onReq) }
  }
  const PICK = /^Принять «/

  await run('24 без «Принять» значения подсказок не уходят ни в проверку, ни в заявку', async () => {
    const hints = await startWithHints(BED)
    if (!(await toFields())) return { ok: false, why: `не дошли до характеристик: ${(await page.locator('main').innerText()).slice(0, 160)}` }
    const buttons = await page.getByRole('button', { name: PICK }).count()
    // Строки живой карточки: «имя значение»; наименование товара в шапке карточки не в счёт.
    const rows = (await page.locator('aside dl > div').allInnerTexts()).map((r) => r.replace(/\s+/g, ' '))
    const inCard = [...hints].filter(([n, v]) => rows.some((r) => r.startsWith(n) && r.includes(v)))
    const checks = lastCheck()
    const sent = []
    const onSubmit = (r) => r.url().endsWith('/api/demo/submissions') && r.method() === 'POST' && sent.push(r.postDataJSON())
    page.on('request', onSubmit)
    await attachPhoto()
    await page.getByRole('button', { name: 'Проверить и отправить' }).click()
    await page.getByRole('button', { name: SEND }).waitFor()
    await reviewed()
    await page.getByRole('button', { name: SEND }).click()
    await page.getByText(/№\s*\d+ у модератора/).waitFor({ timeout: 30000 })
    page.off('request', onSubmit)
    checks.stop()
    const checked = checks.bodies.flatMap((b) => (b.characteristics || []).map((c) => c.name)).filter((n) => hints.has(n))
    const submitted = sent.flatMap((s) => [...s.card.characteristics, ...s.accepted].map((c) => c.name)).filter((n) => hints.has(n))
    const ok = hints.size > 1 && buttons === hints.size && inCard.length === 0 && checks.bodies.length > 0 && checked.length === 0 && sent.length === 1 && submitted.length === 0
    return { ok, why: `подсказок в ответе: ${hints.size}, кнопок «Принять»: ${buttons}; в живой карточке: ${inCard.length}; в /check: ${checked.length} из ${checks.bodies.length} запросов; в заявке: ${submitted.length} (${sent.length} отправка)` }
  })

  await run('25 «Принять все» кладёт подсказки группы в поля, карточку и проверку', async () => {
    const hints = await startWithHints(TWIN)
    if (!(await toFields())) return { ok: false, why: `не дошли до характеристик: ${(await page.locator('main').innerText()).slice(0, 160)}` }
    const before = await page.getByRole('button', { name: PICK }).count()
    const all = page.getByRole('button', { name: /^Принять все \d+ подсказ.* в группе «/ })
    const groups = await all.count()
    while (await all.count()) {
      await all.first().click()
      await page.waitForTimeout(200)
    }
    const after = await page.getByRole('button', { name: PICK }).count()
    const accepted = (await page.getByText(/^Принято из подсказки: /).count())
    const checks = lastCheck()
    await page.getByRole('button', { name: 'Проверить и отправить' }).click()
    await page.getByRole('button', { name: SEND }).waitFor()
    await reviewed()
    checks.stop()
    const body = checks.bodies.at(-1) || { characteristics: [] }
    const put = body.characteristics.filter((c) => hints.get(c.name) === c.value).length
    const ok = groups > 0 && before === hints.size && after < before && accepted === before - after && put === before - after
    return { ok, why: `групп с «Принять все»: ${groups}; кнопок «Принять» до: ${before}, после: ${after}; подписей «Принято из подсказки»: ${accepted}; в /check значений подсказок: ${put}` }
  })

  await page.unrouteAll({ behavior: 'ignoreErrors' })
  return out.join('\n')
}
