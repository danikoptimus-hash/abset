import { test, expect } from '@playwright/test'
import { loginViaUi, seedExperiment } from './helpers'

test('validation page runs A/A + A/B and shows FPR and power tables', async ({ page, request }) => {
  test.setTimeout(60_000)
  const name = `validation_e2e_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)

  await page.goto('/validation')
  await page.getByRole('combobox').first().click()
  // showSearch на Select (Validation.tsx) — печатаем имя, чтобы найти опцию
  // среди всех экспериментов в БД без опоры на виртуализированный список
  // (иначе на большом количестве экспериментов свежесозданная опция может
  // не попасть в изначально отрендеренное окно rc-virtual-list).
  await page.getByRole('combobox').first().fill(name)
  await page.getByTitle(name).click()

  const csv = 'user_id,revenue\n' + Array.from({ length: 200 }, (_, i) => `u${i},${100 + (i % 10)}.5`).join('\n')
  const fileChooserPromise = page.waitForEvent('filechooser')
  await page.getByText('Simulation data (CSV)').click()
  const fileChooser = await fileChooserPromise
  await fileChooser.setFiles({ name: 'sim.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) })

  // The component's default (2000) is statistically meaningful but too slow
  // for e2e — 100 (the InputNumber minimum) is enough to check the flow itself.
  await page.getByRole('spinbutton').first().fill('100')
  await page.getByRole('button', { name: 'Run Validation' }).click()

  await expect(page.getByText('A/A: empirical FPR')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('A/B: empirical vs analytical power')).toBeVisible()
  await expect(page.getByText(/honest|lying/).first()).toBeVisible()
})
