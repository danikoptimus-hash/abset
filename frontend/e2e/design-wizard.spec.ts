import { test, expect } from '@playwright/test'
import { loginViaUi } from './helpers'

// FRONTEND.md §7 R5: "Playwright: e2e создание теста на демо-данных ->
// страница теста -> publish -> edit блока «Гипотеза»."
test('create experiment via wizard on demo data, then publish and edit hypothesis', async ({ page }) => {
  test.setTimeout(60_000)
  await loginViaUi(page)

  await page.getByRole('button', { name: 'Create A/B Test' }).click()
  await expect(page).toHaveURL(/\/experiments\/new$/)

  // Step 1: demo data
  await page.getByRole('button', { name: 'Demo Data' }).click()
  await expect(page.getByText(/Data loaded: 5000 rows/)).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: 'Next' }).click()

  // Step 2: experiment name (demo data already pre-fills groups/metrics)
  const expName = `wizard_e2e_${Date.now()}`
  await page.getByPlaceholder('Experiment name').fill(expName)
  await page.getByRole('button', { name: 'Next' }).click()

  // Step 3: parameters — isolation=off (robust to repeated e2e runs against
  // the same DB: demo data is deterministic, seed=0 -> the same 5000 users
  // every time; the default "exclude" would exclude users already occupied
  // by experiments from previous runs, leaving 0 candidates).
  await page.getByText(/exclude — exclude participants/).click()
  await page.getByText(/off — exclude no one/).click()
  await page.getByRole('button', { name: 'Next' }).click()

  // Step 4: run
  await page.getByRole('button', { name: 'Design' }).click()
  await expect(page).toHaveURL(new RegExp(`/experiments/${expName}$`), { timeout: 20_000 })

  // Experiment page: configuration and MDE table are visible
  await expect(page.getByRole('heading', { name: 'Design', exact: true })).toBeVisible()
  await expect(page.getByText('MDE Table')).toBeVisible()

  // Publish
  await expect(page.getByText('draft', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Publish' }).click()
  await expect(page.getByText('published', { exact: true })).toBeVisible()

  // Edit -> change the "Hypothesis" block -> Save
  await page.getByRole('button', { name: 'Edit' }).click()
  const hypothesisTextarea = page.locator('textarea').first()
  await hypothesisTextarea.fill('New hypothesis from the e2e test')
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('Saved')).toBeVisible()
  await expect(page.getByText('New hypothesis from the e2e test')).toBeVisible()
})
