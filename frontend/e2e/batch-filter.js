// UI E3 на синтетическом пакете: фильтр, страницы, гонки и повтор после сбоя.
// playwright-cli --raw run-code --filename=e2e/batch-filter.js
// eslint-disable-next-line no-unused-expressions
async (page) => {
  const base = page.url().match(/^https?:\/\/[^/]+/)[0]
  const pattern = '**/api/v1/batch/ui-e3**'
  const statuses = { needs_fix: 1800, needs_answer: 1801, not_found: 1802, skipped: 1803 }
  const row = (n) => ({ row: n, name: `Тестовый товар ${n}`, status: n >= 1500 && n <= 1600 ? 'failed' : Object.entries(statuses).find(([, number]) => number === n)?.[0] ?? 'ready', ktru_code: null, filled: 0, hinted: 0, issues: [], degraded: [] })
  const rows = Array.from({ length: 2000 }, (_, i) => row(i + 1))
  const counts = rows.reduce((out, r) => ({ ...out, [r.status]: (out[r.status] ?? 0) + 1 }), {})
  let holdFailed = false
  let failStatus = null
  let releaseStale
  let staleReleased
  const requested = []
  await page.route(pattern, async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.endsWith('/results')) {
      if (url.searchParams.get('limit') !== '0') throw new Error('опрос скачивает все строки')
      return route.fulfill({ json: { job_id: 'ui-e3', filename: 'filter.csv', total: 2000, processed: 2000, status: 'done', counts, rows: [] } })
    }
    const status = url.searchParams.get('status')
    const after = Number(url.searchParams.get('after_row'))
    requested.push({ status, after })
    const found = rows.filter((r) => !status || r.status === status)
    const rest = found.filter((r) => r.row > after)
    const result = { rows: rest.slice(0, 50), matched: found.length, next_after_row: rest.length > 50 ? rest[49].row : null }
    if (holdFailed && status === 'failed') {
      holdFailed = false
      await new Promise((resolve) => { releaseStale = resolve })
      await route.fulfill({ json: result })
      staleReleased()
      return
    }
    if (failStatus && status === failStatus) return route.fulfill({ status: 503, json: { detail: 'Строки временно недоступны' } })
    return route.fulfill({ json: result })
  })
  const chip = (name) => page.getByRole('group', { name: 'Фильтр по статусу' }).getByRole('button', { name, exact: true })
  const shown = (text) => page.getByText(text, { exact: true }).waitFor()
  const numbers = () => page.locator('main tbody tr td:first-child').allTextContents()
  try {
    await page.goto(`${base}/batch`)
    await page.evaluate(() => sessionStorage.setItem('batch:last:v1', JSON.stringify({ jobId: 'ui-e3', filename: 'filter.csv' })))
    await page.reload()
    await shown('Показано 50 из 2000. Страница 1.')
    if ((await numbers()).join(',') !== Array.from({ length: 50 }, (_, i) => i + 1).join(',')) throw new Error('первая страница Все неверна')
    await chip('сбой: 101').click()
    await shown('Показано 50 из 101. Страница 1.')
    if ((await numbers())[0] !== '1500') throw new Error('фильтр потерял строку за первой тысячей')
    await page.getByRole('button', { name: 'Далее', exact: true }).click()
    await shown('Показано 50 из 101. Страница 2.')
    if ((await numbers())[0] !== '1550') throw new Error('курсор второй страницы неверен')
    await page.getByRole('button', { name: 'Далее', exact: true }).click()
    await shown('Показано 1 из 101. Страница 3.')
    if ((await numbers())[0] !== '1600') throw new Error('последняя страница неверна')
    if (await page.getByRole('button', { name: 'Далее', exact: true }).isEnabled()) throw new Error('лишняя страница')
    await page.getByRole('button', { name: 'Назад', exact: true }).click()
    await shown('Показано 50 из 101. Страница 2.')
    for (const [label, n] of [['нужно исправить: 1', 1800], ['нужен ответ: 1', 1801], ['нет в справочнике: 1', 1802], ['пустая строка: 1', 1803]]) {
      await chip(label).click()
      await shown('Показано 1 из 1. Страница 1.')
      if ((await numbers()).join(',') !== String(n)) throw new Error(`неверный фильтр ${label}`)
    }
    await chip('готово: 1895').click()
    await shown('Показано 50 из 1895. Страница 1.')
    holdFailed = true
    const staleDone = new Promise((resolve) => { staleReleased = resolve })
    const staleRequest = page.waitForRequest((r) => r.url().includes('status=failed'))
    await chip('сбой: 101').click()
    await staleRequest
    await chip('нужно исправить: 1').click()
    await shown('Показано 1 из 1. Страница 1.')
    releaseStale()
    await staleDone
    await page.waitForFunction(() => document.querySelector('main tbody tr td')?.textContent === '1800')
    if (await chip('нужно исправить: 1').getAttribute('aria-pressed') !== 'true') throw new Error('старый ответ заменил выбор')
    failStatus = 'not_found'
    await chip('нет в справочнике: 1').click()
    await page.getByText('Строки временно недоступны', { exact: true }).waitFor()
    if (await page.getByText('Обработанных строк с этим статусом пока нет.', { exact: true }).count()) throw new Error('сбой показан как пустой результат')
    failStatus = null
    await page.getByRole('button', { name: 'Повторить', exact: true }).click()
    await shown('Показано 1 из 1. Страница 1.')
    if ((await numbers())[0] !== '1802') throw new Error('повтор не восстановил результат')
    await chip('Все: 2000').click()
    await shown('Показано 50 из 2000. Страница 1.')
    if (!requested.some((r) => r.status === 'failed' && r.after === 1549)) throw new Error('страница не загружена с сервера')
    // Пустой статус — реальный ответ сервера, а не локальная фильтрация страницы.
    delete counts.skipped
    rows[1802].status = 'ready'
    counts.ready += 1
    await page.reload()
    await chip('пустая строка: 0').click()
    await shown('Обработанных строк с этим статусом пока нет.')
    await chip('сбой: 101').click()
    await shown('Показано 50 из 101. Страница 1.')
    return 'UI E3: все статусы, строка 1500, страницы, сброс курсора, поздний ответ, пустой статус, ошибка и повтор — OK'
  } finally {
    releaseStale?.()
    await page.unroute(pattern)
  }
}
