import { test, expect } from '@playwright/test'
import { loginViaUi, seedExperiment } from './helpers'

// FRONTEND.md §7 R6: "Playwright: демо пост-данные -> анализ -> вердикты и
// forest plot видны -> экспорт таблицы."
test('analyze with demo post-data shows verdicts and forest plot, then exports the table', async ({
  page,
  request,
}) => {
  test.setTimeout(60_000)
  const name = `analyze_e2e_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)

  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()

  await page.getByRole('button', { name: /Generate demo post-period data/ }).click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })

  await expect(page.getByRole('heading', { name: 'Forest plot' })).toBeVisible()
  // ECharts renders into a canvas — the chart itself can't be checked with a
  // text locator, but the container must exist and be visible.
  await expect(page.locator('canvas').first()).toBeVisible()

  // Detailed table and CSV export live on the Results tab (UX package,
  // section 2: Analysis has verdicts+charts, Results has the table).
  await page.getByRole('tab', { name: 'Results' }).click()
  await expect(page.getByText('Detailed Results Table')).toBeVisible()
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export CSV' }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toContain('detailed_results.csv')
})
