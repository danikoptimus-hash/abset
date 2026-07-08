import { test, expect } from '@playwright/test'
import { loginViaUi, seedExperiment } from './helpers'

test('validation page runs A/A + A/B and shows FPR and power tables', async ({ page, request }) => {
  test.setTimeout(60_000)
  const name = `validation_e2e_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)

  await page.goto('/validation')
  await page.getByRole('combobox').first().click()
  await page.getByTitle(name).click()

  const csv = 'user_id,revenue\n' + Array.from({ length: 200 }, (_, i) => `u${i},${100 + (i % 10)}.5`).join('\n')
  const fileChooserPromise = page.waitForEvent('filechooser')
  await page.getByText('Данные для симуляции (CSV)').click()
  const fileChooser = await fileChooserPromise
  await fileChooser.setFiles({ name: 'sim.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) })

  // Дефолт компонента (2000) статистически осмыслен, но слишком медленный для
  // e2e — 100 (минимум по InputNumber) достаточно для проверки самого потока.
  await page.getByRole('spinbutton').first().fill('100')
  await page.getByRole('button', { name: 'Запустить валидацию' }).click()

  await expect(page.getByText('A/A: эмпирический FPR')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('A/B: мощность эмпирическая vs аналитическая')).toBeVisible()
  await expect(page.getByText(/честный|врет/).first()).toBeVisible()
})
