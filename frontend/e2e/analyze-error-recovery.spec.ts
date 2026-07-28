import { test, expect } from '@playwright/test'
import { loginViaUi, seedExperiment, uploadDataset } from './helpers'

// Part 1 bugfix: a validation error (here the reserved-column collision — a
// post-period dataset carrying a 'group' column that collides with ABSet's
// assignments join) must NOT replace the analysis form with a dead-end error
// banner. The dataset selector, options and Run button stay on screen so the
// user can switch datasets or fix the data and retry.
test('reserved-column collision keeps the analysis form usable and recovers', async ({ page, request }) => {
  test.setTimeout(60_000)
  const name = `analyze_recovery_e2e_${Date.now()}`
  await seedExperiment(request, name)

  // A post-period dataset that carries a reserved 'group' column — matching
  // user ids so it WOULD join if the column weren't reserved.
  const badCsv = ['user_id,revenue,group']
    .concat(Array.from({ length: 200 }, (_, i) => `u_${name}_${i},${100 + (i % 10)},${i % 2 === 0 ? 'A' : 'B'}`))
    .join('\n')
  const badFile = `results_with_group_${Date.now()}.csv`
  await uploadDataset(request, badCsv, badFile)

  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()

  // Select the offending dataset.
  const datasetSelect = page.getByRole('combobox', { name: 'post-period-dataset-select' })
  await datasetSelect.click()
  await datasetSelect.fill(badFile)
  await page.getByTitle(badFile).click()

  // The collision surfaces as an inline error — but the form is STILL there.
  await expect(page.getByText(/reserved column "group"/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Run analysis' })).toBeDisabled()
  // The dataset selector remains interactive (the whole point of the fix):
  // the user can pick another dataset.
  await expect(datasetSelect).toBeEnabled()
  // "Generate demo post-period data" is still available too.
  await expect(page.getByRole('button', { name: /Generate demo post-period data/ })).toBeEnabled()

  // Recover by generating clean demo data — the error clears immediately and
  // Run becomes enabled without any reload.
  await page.getByRole('button', { name: /Generate demo post-period data/ }).click()
  await expect(page.getByText(/Demo data generated:/)).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText(/reserved column/)).toHaveCount(0)
  const runButton = page.getByRole('button', { name: 'Run analysis' })
  await expect(runButton).toBeEnabled()

  await runButton.click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })

  // Reload preserves a working form: the analysis succeeded, so the tab shows
  // the results with a "Re-run analysis" entry point (never a stuck error).
  await page.reload()
  await page.getByRole('tab', { name: 'Analysis' }).click()
  await expect(page.getByText(/reserved column/)).toHaveCount(0)
  const rerun = page.getByRole('button', { name: 'Re-run analysis' })
  await expect(rerun).toBeVisible()
  await rerun.click()
  await expect(page.getByRole('button', { name: 'Run analysis' })).toBeVisible()
})
