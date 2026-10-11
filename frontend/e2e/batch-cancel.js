// Проверка UI отмены с подменёнными ответами API: живые пакеты не затрагиваются.
// playwright-cli open http://localhost:8080/batch
// playwright-cli --raw run-code --filename=e2e/batch-cancel.js
// eslint-disable-next-line no-unused-expressions
async (page) => {
  const base = page.url().match(/^https?:\/\/[^/]+/)[0]
  const pattern = '**/api/v1/batch/ui-e2**'
  let cancelled = false
  let failCancel = false
  let processed = 1
  let gets = 0
  let deletes = 0
  let releaseStale
  let raceChecked = false
  let staleStarted
  const stalePending = new Promise((resolve) => { staleStarted = resolve })
  const row = (n) => ({ row: n, name: `Тестовый товар ${n}`, status: 'ready', ktru_code: 'test', category_code: 'test', filled: 1, hinted: 0, issues: [], degraded: [] })
  const state = (withRows = true) => ({ job_id: 'ui-e2', filename: 'test.csv', total: 4, processed, status: cancelled ? 'cancelled' : 'running', counts: { ready: processed }, rows: withRows ? Array.from({ length: processed }, (_, i) => row(i + 1)) : [] })
  await page.route(pattern, async (route) => {
    if (route.request().method() === 'DELETE') {
      deletes += 1
      if (failCancel) return route.fulfill({ status: 503, json: { detail: 'Отмена временно недоступна' } })
      cancelled = true
      return route.fulfill({ json: state(false) })
    }
    if (route.request().url().includes('/results?')) {
      return route.fulfill({ json: { rows: state().rows, matched: processed, next_after_row: null } })
    }
    gets += 1
    if (!raceChecked && gets === 3) {
      const stale = state()
      staleStarted()
      await new Promise((resolve) => { releaseStale = resolve })
      return route.fulfill({ json: stale }).catch(() => {})
    }
    return route.fulfill({ json: state() })
  })
  try {
    await page.goto(`${base}/batch`)
    await page.evaluate(() => sessionStorage.setItem('batch:last:v1', JSON.stringify({ jobId: 'ui-e2', filename: 'test.csv' })))
    await page.reload()
    await page.getByRole('button', { name: 'Прервать', exact: true }).waitFor()
    await stalePending
    await page.getByRole('button', { name: 'Прервать', exact: true }).click()
    await page.getByText('Отмена запрошена.', { exact: false }).waitFor()
    raceChecked = true
    releaseStale()
    await page.getByRole('button', { name: 'Прервать', exact: true }).waitFor({ state: 'hidden' })
    await page.getByRole('button', { name: 'Обновить результаты', exact: true }).click()
    await page.getByText('Показано 1 из 1.', { exact: false }).waitFor()
    if (await page.getByRole('button', { name: 'Прервать', exact: true }).count()) throw new Error('старый опрос отменил отмену')
    if ((await page.locator('main tbody tr').count()) !== 1) throw new Error('отмена потеряла уже сохранённую строку')
    processed = 2
    await page.getByRole('button', { name: 'Обновить результаты', exact: true }).click()
    await page.getByText('Показано 2 из 2.', { exact: false }).waitFor()
    if ((await page.locator('main tbody tr').count()) !== 2) throw new Error('завершённая после отмены строка не появилась')
    const link = page.getByRole('link', { name: 'Скачать частичный результат' })
    if ((await link.getAttribute('href')) !== '/api/v1/batch/ui-e2/result.xlsx') throw new Error('неверная ссылка на частичный результат')
    cancelled = false
    failCancel = true
    await page.reload()
    await page.getByRole('button', { name: 'Прервать', exact: true }).click()
    await page.getByText('Отмена временно недоступна', { exact: true }).waitFor()
    if (!(await page.getByRole('button', { name: 'Прервать', exact: true }).isEnabled())) throw new Error('невозможно повторить отмену после сбоя')
    failCancel = false
    await page.getByRole('button', { name: 'Прервать', exact: true }).click()
    await page.getByText('Отмена запрошена.', { exact: false }).waitFor()
    if (deletes !== 3) throw new Error(`неожиданное число команд отмены: ${deletes}`)
    return 'UI отмены: успех, сохранность строк, поздний опрос, обновление результатов, ошибка и повтор — OK'
  } finally {
    releaseStale?.()
    await page.unroute(pattern)
  }
}
