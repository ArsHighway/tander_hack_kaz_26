// Сквозные сценарии мастера «Создание СТЕ» на поднятом стеке (docker compose, порт 8080, справочник стандартов портала).
// Запуск: playwright-cli open http://localhost:8080/ && playwright-cli --raw run-code --filename=e2e/flows.js
// Каждый шаг читает свой эффект со страницы; первая неудача останавливает сценарий с текстом шага.
// playwright-cli run-code ждёт в файле одно выражение-функцию, поэтому оно ничему не присваивается.
// В песочнице run-code нет глобалов Node и браузера (location, process): адреса — константами.
// Регрессии по ревью мастера — e2e/wizard.js.
// eslint-disable-next-line no-unused-expressions
async (page) => {
  const BASE = 'http://localhost:8080'
  // Пустой мастер: черновик вкладки (`createDraft.ts`) восстановил бы прошлую карточку.
  const fresh = async () => {
    await page.goto(`${BASE}/`)
    await page.evaluate(() => sessionStorage.removeItem('create:draft:v1'))
    await page.goto(`${BASE}/`)
  }
  const DOCS = '../backend/tests/fixtures/docs/' // от frontend/, откуда запускается npm run e2e
  const MIXER = 'Смеситель для кухни однорычажный с выдвижной лейкой, хром, латунь, керамический картридж 35 мм'
  // Наименование, под которым на портале уже есть СТЕ (индекс двойников `TWINS_PATH`).
  // Перчатки: без замечаний модуля — заявка «чистая», одобряется в один клик.
  const GLOVES = 'Перчатки смотровые нитриловые, размер M'
  const TWIN = 'Концентрированный усилитель стирки от жировых и масляных загрязнений с инновационной системой оптического осветлителя ДЕРВАЛ БРАЙТ'
  const log = []
  const errors = []
  page.on('console', (m) => (m.type() === 'error' || m.type() === 'warning') && errors.push(m.text()))
  page.on('pageerror', (e) => errors.push(String(e)))
  const step = async (name, fn) => {
    try {
      const got = await fn()
      log.push(`ok   ${name}${got !== undefined ? `: ${got}` : ''}`)
    } catch (e) {
      log.push(`FAIL ${name}: ${String(e).split('\n')[0]}`)
      throw new Error(log.join('\n'))
    }
  }
  const idle = () =>
    page.waitForFunction(() => !document.querySelector('[aria-busy="true"]') && !/Проверяем ответ|Заполняем…|Ищем категорию|Читаем файл…/.test(document.body.innerText), null, {
      timeout: 120000,
    })
  const path = async () => (await page.locator('nav[aria-label="Шаги создания СТЕ"] li').allInnerTexts()).map((t) => t.replace(/\s+/g, ' ').trim()).join(' | ')
  const fill = async (text) => {
    await fresh()
    await page.locator('#describe-text').fill(text)
    await page.getByRole('button', { name: /^Заполнить карточку/ }).click()
    await idle()
  }
  // «Одобрить чистые (N)» открывает список заявок; пачкой они уходят только после «Одобрить N».
  const approveClean = async (name) => {
    await page.getByRole('button', { name: /^Одобрить чистые \(\d+\)$/ }).click()
    const dialog = page.getByRole('dialog', { name: /^Одобрить без просмотра: \d+$/ })
    await dialog.waitFor({ timeout: 10000 })
    if (name) await dialog.getByRole('list', { name: 'Заявки к одобрению' }).getByText(name).first().waitFor({ timeout: 10000 })
    await dialog.getByRole('button', { name: /^Одобрить \d+$/ }).click()
    await page.getByText(/^Одобрено в один клик: \d+/).waitFor({ timeout: 30000 })
  }
  // Модуль всё решил и заполнил из текста — мастер сам открывает проверку (шаг 5); иначе шаг 4.
  const SEND = 'Отправить на модерацию'
  const checkOrSend = () => page.getByRole('button', { name: new RegExp(`^(Проверить и отправить|${SEND})$`) }).first().waitFor({ timeout: 30000 })
  const onReview = () => page.getByRole('button', { name: SEND }).isVisible()
  const choose = async (title) => {
    await page.getByText(title, { exact: true }).first().click()
    await page.locator('main button.MuiButton-contained').first().click()
    await idle()
  }
  // Обязательные из подсказок похожих СТЕ поставщик принимает сам: «Принять все N подсказок» группы или «Принять «…»».
  const acceptRequired = async () => {
    const section = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Обязательные', exact: true }) })
    const pick = section.getByRole('button', { name: /^Принять «/ })
    const n = await pick.count()
    const all = section.getByRole('button', { name: /^Принять все/ })
    if (await all.count()) await all.first().click()
    while (await pick.count()) await pick.first().click()
    return n
  }
  // Прикрепление фото остаётся отдельным необязательным сценарием.
  const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
  const attachPhoto = async () => {
    await page.waitForTimeout(300) // мастер мог ещё не перейти сам на шаг 5
    if (await onReview()) await page.getByRole('button', { name: 'Вернуться к характеристикам' }).click()
    await page.locator('input[type=file][accept^="image"]').setInputFiles({ name: 'photo.png', mimeType: 'image/png', buffer: Buffer.from(PNG, 'base64') })
    await page.getByRole('group', { name: 'Фото товара' }).locator('img').waitFor({ timeout: 30000 })
  }

  try {
    await step('шаг 1: поле текста и зона файла', async () => {
      await fresh()
      await page.locator('#describe-text').waitFor()
      await page.getByText('Перетащите файл или выберите его').waitFor()
      return page.title()
    })

    // 1. Вопрос категории и вопрос КТРУ: зерно пшеницы.
    await step('шаг 2: вопрос категории с похожими СТЕ', async () => {
      await fill('Зерно пшеницы продовольственное, ГОСТ 9353')
      await page.getByRole('heading', { name: 'Выберите категорию товара' }).waitFor()
      return path()
    })
    await step('шаг 3: вопрос позиции КТРУ', async () => {
      await choose('Зерно пшеницы')
      await page.getByText('Мягкая озимая белозерная', { exact: true }).waitFor({ timeout: 30000 })
      return path()
    })
    await step('шаг 4: позиция КТРУ в полосе шагов', async () => {
      await choose('Мягкая озимая белозерная')
      await checkOrSend()
      const p = await path()
      if (!p.includes('01.11.10.000-00000003')) throw new Error(p)
      return p
    })
    await step('живая карточка: код КТРУ и готовность', async () => {
      const card = (await page.locator('aside').innerText()).replace(/\s+/g, ' ')
      if (!card.includes('01.11.10.000-00000003')) throw new Error(card.slice(0, 200))
      return card.match(/Готово \d+ из \d+ обязательных/)?.[0] ?? card.slice(0, 80)
    })

    // 2. Товар без позиции КТРУ в справочнике: смеситель сразу на шаге 4, отправка, «Мои заявки», модератор.
    let submissionId = ''
    await step('смеситель: категория и позиция КТРУ из текста («однорычажный» — «Рычажный»)', async () => {
      await fill(MIXER)
      await checkOrSend()
      const p = await path()
      if (!/28\.14\.12\.110-00000019/.test(p)) throw new Error(p)
      return p
    })
    await step('черновик: ушёл со страницы и вернулся — карточка на месте', async () => {
      await page.goto(`${BASE}/metrics`)
      await page.goto(`${BASE}/`)
      await checkOrSend()
      const p = await path()
      if (!/28\.14\.12\.110-00000019/.test(p)) throw new Error(p)
      return 'та же позиция КТРУ после «Метрик»'
    })
    await step('шаг 4: подсказки обязательных — «Принять», обязательных не осталось', async () => {
      // Без подсказок похожих СТЕ мастер сразу на проверке (шаг 5). У смесителя обязательные («Монтаж», «Материал»,
      // «Тип управления») подсказывают похожие СТЕ: поля пустые, пока поставщик не примет.
      if (await onReview()) return 'сразу проверка: обязательных нет, подсказок нет'
      const before = await page.getByRole('status').filter({ hasText: /Обязательных|Готово/ }).innerText()
      const accepted = await acceptRequired()
      await page.getByRole('status').filter({ hasText: /Обязательных полей нет|Готово (\d+) из \1/ }).waitFor({ timeout: 15000 })
      return `${before} → принято подсказок: ${accepted} → ${await page.getByRole('status').filter({ hasText: /Обязательных|Готово/ }).innerText()}`
    })
    await step('фото необязательно: проверка не требует загрузить изображение', async () => {
      if (!(await onReview())) await page.getByRole('button', { name: 'Проверить и отправить' }).click()
      await page.getByText(/Правила модерации портала пройдены|Проверьте перед отправкой/).first().waitFor({ timeout: 30000 })
      const red = (await page.locator('.MuiAlert-colorError').allInnerTexts()).join(' ').replace(/\s+/g, ' ')
      if (red.includes('Фото товара') || !(await page.getByRole('button', { name: SEND }).isEnabled())) throw new Error(`проверка без фото: ${red.slice(0, 160)}`)
      return 'отправка без фото доступна'
    })
    await step('шаг 5: проверка модерации и вид «как на портале»', async () => {
      if (!(await onReview())) await page.getByRole('button', { name: 'Проверить и отправить' }).click()
      // У смесителя предупреждение «В наименовании «35 мм»»: оранжевое «Проверьте перед отправкой», отправить можно.
      await page.getByText(/Правила модерации портала пройдены|Проверьте перед отправкой/).first().waitFor({ timeout: 30000 })
      const warned = (await page.locator('.MuiAlert-colorWarning').allInnerTexts()).join(' ').replace(/\s+/g, ' ')
      await page.getByRole('tab', { name: 'Как на портале' }).click()
      await page.getByText('Так карточку увидят заказчики после модерации', { exact: false }).first().waitFor()
      const rows = await page.locator('main table tbody tr').count()
      await page.getByRole('tab', { name: 'Сводка' }).click()
      return `строк в превью: ${rows}; ${warned ? `проверьте: ${warned.slice(0, 120)}` : 'пройдены'}`
    })
    await step('отправка на модерацию', async () => {
      await page.getByRole('button', { name: 'Отправить на модерацию' }).click()
      const done = page.getByText(/№\s*\d+ у модератора/)
      await done.waitFor({ timeout: 30000 })
      submissionId = ((await done.innerText()).match(/№\s*(\d+)/) || [])[1]
      return `№ ${submissionId}`
    })
    await step('заявка в «Моих заявках»', async () => {
      await page.getByRole('button', { name: 'Открыть «Мои заявки»' }).click()
      const item = page.locator('main li').filter({ hasText: `№${String.fromCharCode(160)}${submissionId},` })
      await item.getByText('на модерации').waitFor({ timeout: 30000 })
      return 'на модерации'
    })
    await step('модератор возвращает с замечаниями', async () => {
      await page.getByRole('button', { name: 'Модератор' }).click()
      await page.getByRole('heading', { name: 'Заявки на модерацию' }).waitFor()
      await page.goto(`${BASE}/moderation?id=${submissionId}`)
      const note = page.getByLabel('Текст возврата поставщику')
      await note.waitFor({ timeout: 30000 })
      if (!(await note.inputValue()).trim()) await note.fill('Укажите производителя.')
      await page.getByRole('button', { name: 'Вернуть с замечаниями' }).click()
      await page.getByText('Заявка возвращена поставщику').waitFor({ timeout: 30000 })
      return 'возвращена'
    })
    await step('поставщик исправляет возвращённую заявку', async () => {
      await page.getByRole('button', { name: 'Поставщик' }).click()
      await page.getByRole('tab', { name: 'Мои заявки' }).click()
      const item = page.locator('main li').filter({ hasText: `№${String.fromCharCode(160)}${submissionId},` })
      await item.getByText('возвращена').waitFor({ timeout: 30000 })
      await item.getByRole('button', { name: 'Исправить' }).click()
      await idle()
      await page.getByText('Замечания модератора').waitFor({ timeout: 30000 })
      // Значения заявки подписаны под полями (видны обязательные; необязательные свёрнуты).
      await page.getByText('Из вашей заявки').first().waitFor({ timeout: 15000 })
      return `полей из заявки на экране: ${await page.getByText('Из вашей заявки').count()}`
    })
    await step('исправленная заявка снова у модератора', async () => {
      await page.getByRole('button', { name: 'Проверить и отправить' }).click()
      await page.getByText(/Правила модерации портала пройдены|Проверьте перед отправкой/).first().waitFor({ timeout: 30000 })
      await page.getByRole('button', { name: 'Отправить исправленную заявку' }).click()
      await page.getByText(/Исправленная заявка № \d+ у модератора/).waitFor({ timeout: 30000 })
      return 'ждёт решения'
    })
    await step('модератор: чистые — в один клик, остальные — с причиной', async () => {
      await page.goto(`${BASE}/moderation?id=${submissionId}`)
      const item = page.locator('main li').filter({ hasText: `№${String.fromCharCode(160)}${submissionId},` })
      await item.waitFor({ timeout: 30000 })
      await page.getByLabel('Текст возврата поставщику').waitFor({ timeout: 30000 })
      if (!(await item.getByText('без замечаний модуля').count())) {
        // Подсказки без правки или замечания: модератор видит, почему заявку не одобрить в один клик.
        return page.getByText(/^Почему не в один клик:/).innerText()
      }
      await approveClean()
      await item.getByText('принята').waitFor({ timeout: 30000 })
      return (await page.getByText(/^Одобрено в один клик: \d+/).innerText()).split('.')[0]
    })
    // Кнопка одобряет все чистые заявки из списка подтверждения, не только эту: стенд для показа общий.
    await step('чистая заявка: модератор подтверждает список и одобряет в один клик', async () => {
      await fill(GLOVES)
      await checkOrSend()
      await attachPhoto()
      await page.getByRole('button', { name: 'Проверить и отправить' }).click()
      await page.getByText('Правила модерации портала пройдены').waitFor({ timeout: 30000 })
      await page.getByRole('button', { name: SEND }).click()
      const done = page.getByText(/№\s*\d+ у модератора/)
      await done.waitFor({ timeout: 30000 })
      const id = ((await done.innerText()).match(/№\s*(\d+)/) || [])[1]
      await page.goto(`${BASE}/moderation?id=${id}`)
      const item = page.locator('main li').filter({ hasText: `№${String.fromCharCode(160)}${id},` })
      await item.getByText('без замечаний модуля').waitFor({ timeout: 30000 })
      await approveClean(GLOVES)
      await item.getByText('принята').waitFor({ timeout: 30000 })
      return `№ ${id}: ${(await page.getByText(/^Одобрено в один клик: \d+/).innerText()).split('.')[0]}`
    })

    // 3. Категория не найдена.
    await step('товар вне справочника: «Категория не найдена»', async () => {
      await fill('Ремонт автомобиля')
      await page.getByText(/не найден/i).first().waitFor({ timeout: 30000 })
      return path()
    })

    // 3а. Такая СТЕ уже есть на портале: категория без вопроса, поля по ней, ссылка и замечание о дубле.
    await step('такая СТЕ уже есть на портале: ссылка, поля по ней, замечание', async () => {
      await fill(TWIN)
      await checkOrSend()
      await page.getByRole('heading', { name: /^Такие? СТЕ уже есть на портале$/ }).waitFor({ timeout: 30000 })
      const href = await page.locator('section[aria-labelledby="twins-title"] a').first().getAttribute('href')
      if (!href?.startsWith('https://zakupki.mos.ru/sku/view/')) throw new Error(`ссылка: ${href}`)
      await page.getByText(/^Подсказка: как у (такой же СТЕ №\d+ на портале|\d+ из \d+ таких же СТЕ портала)/).first().waitFor()
      await page.getByText(/^Такая СТЕ уже есть на портале: №\d+/).first().waitFor()
      // Подсказка — предложение: поле пустое, значение кладёт «Принять «…»».
      const pick = await page.getByRole('button', { name: /^Принять «/ }).count()
      if (!pick) throw new Error('нет кнопок «Принять» у подсказок')
      return `${href}, подсказок по ней: ${await page.getByText(/^Подсказка: как у (такой же|\d+ из \d+ таких же)/).count()}, кнопок «Принять»: ${pick}`
    })

    // 4. Файл вместо текста: Word с одним товаром и Word со списком товаров.
    await step('Word с одним товаром: текст в поле, без автозаполнения', async () => {
      await fresh()
      await page.locator('input[type=file][aria-label="Файл с описанием товара"]').setInputFiles(DOCS + 'spec.docx')
      await page.getByText('Текст взят из «spec.docx»', { exact: false }).waitFor({ timeout: 30000 })
      return (await page.locator('#describe-text').inputValue()).split('\n')[0]
    })
    await step('Word со списком товаров: массовая загрузка', async () => {
      await page.locator('input[type=file][aria-label="Файл с описанием товара"]').setInputFiles(DOCS + 'goods.docx')
      await page.getByText(/В «goods\.docx» 6 товаров/).waitFor({ timeout: 30000 })
      await page.getByRole('button', { name: 'Открыть массовую загрузку' }).click()
      await page.getByText(/Обработано \d+ из 6/).waitFor({ timeout: 60000 })
      return page.getByText(/Обработано \d+ из 6/).innerText()
    })
    await step('пакет обработан, строка открывается в мастере', async () => {
      await page.getByRole('link', { name: 'Скачать файл с результатом' }).waitFor({ timeout: 180000 })
      // Ушёл со страницы и вернулся — тот же пакет (`lastBatch.ts`), а не пустая загрузка.
      await page.goto(`${BASE}/metrics`)
      await page.goto(`${BASE}/batch`)
      await page.getByText('goods.docx').first().waitFor({ timeout: 30000 })
      await page.getByRole('link', { name: 'Скачать файл с результатом' }).waitFor({ timeout: 30000 })
      await page.getByRole('button', { name: 'Открыть в форме' }).first().click()
      await idle()
      return path()
    })

    // 5. Метрики, API, BPMN, 404.
    await step('метрики', async () => {
      await page.getByRole('link', { name: 'Метрики' }).click()
      await page.getByRole('heading', { name: 'Отложенная выборка СТЕ портала' }).waitFor({ timeout: 30000 })
      await page.getByRole('heading', { name: /Золотой набор/ }).waitFor({ timeout: 30000 })
      return `${await page.locator('tbody tr').count()} строк`
    })
    await step('страница API', async () => {
      await page.getByRole('link', { name: 'API' }).click()
      await page.getByText('POST /api/v1/autofill').first().waitFor()
      return `${await page.locator('main ul li code').count()} методов`
    })
    await step('BPMN доступна', async () => {
      const r = await page.request.get(`${BASE}/bpmn/module.svg`)
      if (r.status() !== 200) throw new Error(String(r.status()))
      return `${r.status()} ${r.headers()['content-type']}`
    })
    await step('неизвестный адрес', async () => {
      await page.goto(`${BASE}/nope`)
      await page.getByRole('heading', { name: 'Страница не найдена' }).waitFor()
      return '404'
    })
    await step('ошибок и предупреждений консоли нет', async () => {
      // 404 картинок похожих СТЕ — нормальный ответ прокси (фото нет или портал не ответил), не ошибка страницы.
      const real = errors.filter((e) => !/images\/(sku|category)\/\d+.*404|status of 404/.test(e))
      if (real.length) throw new Error(real.slice(0, 3).join(' | '))
      return `${errors.length - real.length} ответов 404 картинок`
    })
  } catch (e) {
    return String(e.message || e)
  }
  return log.join('\n')
}
