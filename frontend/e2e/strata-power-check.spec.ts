import { test, expect } from '@playwright/test'
import { loginViaUi, uploadDataset } from './helpers'

// Item 2 (strata power check): after "Calculate sample size" and setting
// proportions, the wizard's Parameters step gets a "Strata power check"
// Collapse — informational (never blocks Next), showing per-stratum-
// dimension achievable MDE at the CURRENT split. Fixture: an exactly
// balanced 50/50 binary "converted" column across two "segment" values
// (A/B), so both segment strata get identical, well-powered stats.
test('Strata power check shows per-dimension MDE and a status badge, without blocking Next', async ({
  page,
  request,
}) => {
  test.setTimeout(60_000)
  const n = 2000
  const rows = Array.from({ length: n }, (_, i) => {
    const segment = i % 2 === 0 ? 'A' : 'B'
    const converted = i % 4 < 2 ? 1 : 0
    return `u${i},${converted},${segment}`
  })
  const csv = 'user_id,converted,segment\n' + rows.join('\n')
  const filename = `strata_power_fixture_${Date.now()}.csv`
  await uploadDataset(request, csv, filename)

  await loginViaUi(page)
  await page.getByRole('button', { name: 'Create A/B Test' }).click()
  await expect(page).toHaveURL(/\/experiments\/new$/)

  const datasetSelect = page.getByRole('combobox', { name: 'design-dataset-select' })
  await datasetSelect.click()
  await datasetSelect.fill(filename)
  await page.getByTitle(filename).click()
  await expect(page.getByText(/Data loaded: 2000 rows/)).toBeVisible({ timeout: 10_000 })
  await page.getByRole('button', { name: 'Next' }).click()

  const expName = `strata_power_e2e_${Date.now()}`
  await page.getByPlaceholder('Experiment name').fill(expName)
  await page.locator('.ant-select', { hasText: 'Unit column' }).click()
  await page.getByTitle('user_id', { exact: true }).click()

  await page.locator('.ant-select', { hasText: 'continuous' }).first().click()
  await page.getByTitle('binary', { exact: true }).click()
  await page.locator('.ant-select', { hasText: 'Dataframe column' }).click()
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('Enter')
  await expect(page.locator('.ant-select', { hasText: 'converted' })).toBeVisible()

  await page.getByRole('button', { name: 'Next' }).click()

  // Step 3: pick "segment" as a stratum, isolation off, calculate.
  await page.locator('.ant-select', { hasText: 'Strata (optional)' }).click()
  await page.getByTitle('segment', { exact: true }).click()
  await page.keyboard.press('Escape')

  await page.getByText(/exclude — exclude participants/).click()
  await page.getByText(/off — exclude no one/).click()

  await page.getByRole('button', { name: 'Calculate sample size' }).click()
  await expect(page.getByText(/Required per group:|No MDE target set/)).toBeVisible({ timeout: 15_000 })

  // Strata power check: collapsed by default, opening it runs the check.
  const collapseHeader = page.getByText('Strata power check')
  await expect(collapseHeader).toBeVisible()
  await collapseHeader.click()

  // The dimension label ("segment") renders as a bold heading above its
  // table — getByText('segment') is ambiguous (also matches the Strata
  // select's own selected-value tag and dropdown option), so scope to the
  // <strong> heading specifically.
  await expect(page.getByRole('strong').filter({ hasText: 'segment' })).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('cell', { name: 'A', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'B', exact: true })).toBeVisible()
  // A balanced 50/50 fixture split evenly across two strata -> both should
  // read "ok" (well within 2x the overall achievable MDE).
  await expect(page.getByText('ok').first()).toBeVisible()

  // Purely informational — Next still works right after.
  await page.getByRole('button', { name: 'Next' }).click()
  await page.getByRole('button', { name: 'Design' }).click()
  await expect(page).toHaveURL(new RegExp(`/experiments/${expName}$`), { timeout: 20_000 })
})
