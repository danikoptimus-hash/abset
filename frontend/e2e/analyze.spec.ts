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
  await expect(page.getByRole('heading', { name: 'Анализ' })).toBeVisible()

  await page.getByRole('button', { name: /Сгенерировать демо пост-данные/ }).click()
  await expect(
    page.getByText(/значимо позитивный|значимо негативный|эффект не обнаружен/).first(),
  ).toBeVisible({ timeout: 20_000 })

  await expect(page.getByRole('heading', { name: 'Forest plot' })).toBeVisible()
  // ECharts рисует в canvas — сам чарт не проверить текстовым локатором,
  // но контейнер должен существовать и быть видимым.
  await expect(page.locator('canvas').first()).toBeVisible()

  await expect(page.getByText('Детальная таблица результатов')).toBeVisible()
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Экспорт CSV' }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toContain('detailed_results.csv')
})
