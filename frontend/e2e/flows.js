// Живой сервер: без подмены ответов. Ожидания задаются до прогона.
// Требуется справочник мебели, одежды и инструментов; LLM может быть включена или выключена.
async (page) => {
  const base = new URL(page.url()).origin
  const logs = []
  const failures = []
  const assert = (ok, message) => { if (!ok) throw new Error(message) }
  const cases = [
    { name: 'Дрель ручная электрическая', description: 'Мощность 800 Вт, патрон 13 мм', type: /дрел/i },
    { name: 'Тумба офисная деревянная', description: 'Вид материала корпуса: глина', type: /тумб/i, preserve: 'глина' },
    { name: 'Куртка зимняя мужская', description: 'Ткань полиэстер, размер 52', type: /куртк/i },
  ]
  for (const item of cases) {
    try {
      await page.goto(base)
      await page.evaluate(() => sessionStorage.clear())
      await page.reload()
      await page.locator('#describe-text').fill(`${item.name}\n${item.description}`)
      const response = page.waitForResponse((r) => r.url().endsWith('/api/v1/autofill') && r.request().method() === 'POST', { timeout: 120000 })
      await page.getByRole('button', { name: /^Заполнить карточку/ }).click()
      const r = await response
      assert(r.ok(), `autofill HTTP ${r.status()}`)
      const out = await r.json()
      const candidates = out.ktru.candidates
      assert(candidates.some((c) => item.type.test(c.name)), 'Ожидаемый тип не найден')
      if (out.ktru.question?.characteristic === 'Позиция КТРУ') {
        await page.getByRole('heading', { name: /Какой это товар по КТРУ|Подтвердите тип товара/ }).waitFor()
        const radios = page.locator('main input[type=radio]')
        assert(await radios.count() > 0, 'Нет вариантов выбора')
        assert(await page.locator('main form img').count() === 0, 'Миниатюры СТЕ остались')
      }
      assert((await page.locator('main').innerText()).length > 0, 'Пустой экран')
      if (item.preserve) {
        const values = [...out.characteristics, ...(out.extra ?? [])]
        assert(values.some((c) => String(c.value ?? '').toLowerCase().includes(item.preserve)), `Сервер потерял введённое значение «${item.preserve}»`)
      }
      logs.push(`PASS ${item.name}`)
    } catch (e) { failures.push(`${item.name}: ${e.message}`) }
  }
  // Выключатель модели: у поставщика его нет, у модератора есть и действует на ответ /autofill.
  try {
    await page.goto(base)
    await page.evaluate(() => sessionStorage.clear())
    await page.reload()
    await page.locator('#describe-text').waitFor()
    assert(!(await page.getByRole('switch', { name: /^Модель \(LLM\)/ }).count()), 'Выключатель модели виден поставщику')
    const ask = async () => (await (await page.request.post(`${base}/api/v1/autofill`, { data: { name: 'Тумба прикроватная' } })).json()).llm
    // Настройка доходит до других процессов API не позже чем через 5 с: ждём, а не сравниваем с первого раза.
    const until = async (want) => {
      let got = ''
      for (let i = 0; i < 12; i += 1) {
        got = await ask()
        if (want(got)) return got
        await page.waitForTimeout(1000)
      }
      throw new Error(`llm: «${got}»`)
    }
    const sw = () => page.getByRole('switch', { name: /^Модель \(LLM\)/ })
    await page.goto(`${base}/moderation`)
    await sw().waitFor({ timeout: 30000 })
    try {
      if (!(await sw().isChecked())) await sw().click()
      await page.getByRole('switch', { name: 'Модель (LLM): вкл' }).waitFor({ timeout: 15000 })
      await sw().click()
      await page.getByRole('switch', { name: 'Модель (LLM): выкл' }).waitFor({ timeout: 15000 })
      await until((v) => v === 'off')
      await page.reload()
      await page.getByRole('switch', { name: 'Модель (LLM): выкл' }).waitFor({ timeout: 30000 })
    } finally {
      await page.goto(`${base}/moderation`)
      await sw().waitFor({ timeout: 30000 })
      if (!(await sw().isChecked())) await sw().click()
      await page.getByRole('switch', { name: 'Модель (LLM): вкл' }).waitFor({ timeout: 15000 })
    }
    logs.push(`PASS выключатель модели: выкл → off, вкл → ${await until((v) => v !== 'off')}`)
  } catch (e) { failures.push(`выключатель модели: ${e.message}`) }
  if (failures.length) throw new Error([...logs, ...failures.map((x) => `FAIL ${x}`)].join('\n'))
  return logs.join('\n')
}
