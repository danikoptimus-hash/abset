import { test, expect } from '@playwright/test'
import { loginViaUi, seedExperiment } from './helpers'

// UX package, Validation п.C: auto-datasource means no manual upload is
// needed anymore when the experiment already has its design data stored.
test('validation page runs A/A + A/B and shows FPR and power tables', async ({ page, request }) => {
  test.setTimeout(60_000)
  const name = `validation_e2e_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)

  await page.goto('/validation')
  await page.getByRole('combobox', { name: 'validation-experiment-select' }).click()
  // showSearch на Select (Validation.tsx) — печатаем имя, чтобы найти опцию
  // среди всех экспериментов в БД без опоры на виртуализированный список
  // (иначе на большом количестве экспериментов свежесозданная опция может
  // не попасть в изначально отрендеренное окно rc-virtual-list).
  await page.getByRole('combobox', { name: 'validation-experiment-select' }).fill(name)
  await page.getByTitle(name).click()
  await expect(page.getByText('From experiment design')).toBeVisible()

  // Дефолт компонента (2000) статистически осмыслен, но слишком медленный для
  // e2e — 100 (минимум по InputNumber) достаточно для проверки самого потока.
  await page.getByRole('spinbutton').first().fill('100')
  await page.getByRole('button', { name: 'Run Validation' }).click()

  await expect(page.getByText('A/A: empirical FPR')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('A/B: empirical vs analytical power')).toBeVisible()
  await expect(page.getByText(/honest|lying/).first()).toBeVisible()
  await expect(page.getByText(/Validated with data\.csv/)).toBeVisible()
})
